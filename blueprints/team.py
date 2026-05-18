"""team.py — 업체 팀 멤버 관리 Blueprint.

URL prefix: /settings/team
권한: owner/admin만 초대·역할변경·제거 가능. 멤버 목록은 모든 인증 사용자.

초대 플로우:
    1) 어드민 → POST /settings/team/invite (이메일 + 역할 입력)
    2) 서버   → invitations 테이블에 토큰 저장 + Resend 이메일 발송
    3) 직원   → 이메일 링크 클릭 → GET /auth/join/<token>
    4) 직원   → 기존 계정 로그인 또는 신규 가입 → user_business_map 등록
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, jsonify, g, current_app,
)
from flask_login import login_required, current_user

from auth.decorators import biz_required, role_required
from auth.helpers import log_audit
from db.client import get_admin_client

logger = logging.getLogger(__name__)

team_bp = Blueprint('team', __name__, url_prefix='/settings/team')

# ─── 역할 레이블 ───
ROLE_LABELS = {
    'owner':     '오너',
    'admin':     '관리자',
    'manager':   '매니저',
    'logistics': '물류팀',
    'sales':     '영업팀',
    'viewer':    '뷰어',
}

# 초대 가능한 역할 (owner는 이전 불가)
INVITE_ROLES = ['admin', 'manager', 'logistics', 'sales', 'viewer']


# ══════════════════════════════════════════════
#  멤버 목록
# ══════════════════════════════════════════════

@team_bp.route('/')
@login_required
@biz_required
def index():
    """팀 멤버 목록 + 대기중 초대."""
    client = get_admin_client()
    biz_id = g.biz_id

    # 현재 멤버 (user_business_map JOIN app_users)
    members = []
    try:
        ubm_rows = client.table('user_business_map') \
            .select('user_id, role, is_primary, created_at') \
            .eq('biz_id', biz_id).execute().data or []
        if ubm_rows:
            uid_map = {r['user_id']: r for r in ubm_rows}
            user_rows = client.table('app_users') \
                .select('id, email, name, last_login_at, is_deleted') \
                .in_('id', list(uid_map.keys())).execute().data or []
            for u in user_rows:
                if u.get('is_deleted'):
                    continue
                ubm = uid_map.get(u['id'], {})
                members.append({
                    'user_id':    u['id'],
                    'email':      u['email'],
                    'name':       u['name'] or u['email'].split('@')[0],
                    'role':       ubm.get('role', 'viewer'),
                    'role_label': ROLE_LABELS.get(ubm.get('role', 'viewer'), ubm.get('role', '')),
                    'is_primary': ubm.get('is_primary', False),
                    'last_login': u.get('last_login_at'),
                    'is_me':      u['id'] == current_user.id,
                })
        members.sort(key=lambda x: (0 if x['role'] == 'owner' else 1, x['name']))
    except Exception as e:
        logger.error(f'[Team] 멤버 목록 조회 실패: {e}')
        flash('멤버 목록 조회 중 오류', 'danger')

    # 대기중 초대
    pending_invites = []
    try:
        inv_rows = client.table('invitations') \
            .select('id, email, role, expires_at, created_at') \
            .eq('biz_id', biz_id) \
            .is_('used_at', 'null') \
            .gte('expires_at', datetime.now(timezone.utc).isoformat()) \
            .order('created_at', desc=True).execute().data or []
        for r in inv_rows:
            r['role_label'] = ROLE_LABELS.get(r['role'], r['role'])
        pending_invites = inv_rows
    except Exception as e:
        logger.warning(f'[Team] 대기 초대 조회 실패: {e}')

    # 현재 내 역할 (초대/역할변경 버튼 노출 여부)
    my_role = next((m['role'] for m in members if m['is_me']), 'viewer')
    can_manage = current_user.is_super_admin or my_role in ('owner', 'admin')

    return render_template('team/index.html',
                           members=members,
                           pending_invites=pending_invites,
                           invite_roles=INVITE_ROLES,
                           role_labels=ROLE_LABELS,
                           can_manage=can_manage,
                           my_role=my_role)


# ══════════════════════════════════════════════
#  초대 발송
# ══════════════════════════════════════════════

@team_bp.route('/invite', methods=['POST'])
@login_required
@biz_required
def invite():
    """이메일 초대 발송. JSON 또는 Form 지원."""
    client = get_admin_client()
    biz_id = g.biz_id

    if request.is_json:
        data = request.get_json() or {}
    else:
        data = request.form

    email = (data.get('email') or '').strip().lower()
    role  = (data.get('role') or 'viewer').strip()

    if not email:
        return _err('이메일을 입력하세요.', request)
    if role not in INVITE_ROLES:
        return _err(f'유효하지 않은 역할: {role}', request)

    # 권한 확인 (owner/admin만)
    from auth.helpers import get_user_role
    my_role = 'admin' if current_user.is_super_admin else get_user_role(current_user.id, biz_id)
    if my_role not in ('owner', 'admin'):
        return _err('초대 권한이 없습니다.', request, 403)

    # 이미 멤버인지 확인
    try:
        existing_user = client.table('app_users').select('id') \
            .eq('email', email).eq('is_deleted', False).limit(1).execute().data
        if existing_user:
            already = client.table('user_business_map').select('id') \
                .eq('biz_id', biz_id).eq('user_id', existing_user[0]['id']) \
                .limit(1).execute().data
            if already:
                return _err(f'{email} 은(는) 이미 팀원입니다.', request)
    except Exception as e:
        logger.warning(f'[Team] 멤버 중복 확인 실패: {e}')

    # 기존 미사용 초대 삭제 (unique index 충돌 방지)
    try:
        client.table('invitations') \
            .delete() \
            .eq('biz_id', biz_id) \
            .eq('email', email) \
            .is_('used_at', 'null') \
            .execute()
    except Exception:
        pass

    # 토큰 생성 + 저장
    token = secrets.token_urlsafe(48)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    try:
        client.table('invitations').insert({
            'biz_id':     biz_id,
            'email':      email,
            'role':       role,
            'token':      token,
            'invited_by': current_user.id,
            'expires_at': expires_at,
        }).execute()
    except Exception as e:
        logger.error(f'[Team] 초대 저장 실패: {e}')
        return _err('초대 저장 중 오류가 발생했습니다.', request)

    # 이메일 발송
    join_url = url_for('auth.join_invite', token=token, _external=True)
    biz_name = getattr(g, 'biz_name', None) or str(biz_id)
    role_label = ROLE_LABELS.get(role, role)
    sent = False
    try:
        from services.email_service import send_invite_email
        sent = send_invite_email(
            to_email=email,
            biz_name=biz_name,
            inviter_name=current_user.username,
            role_label=role_label,
            join_url=join_url,
        )
    except Exception as e:
        logger.error(f'[Team] 이메일 발송 실패: {e}')

    log_audit('team_invite', detail={
        'email': email, 'role': role, 'sent': sent, 'biz_id': biz_id,
    }, biz_id=biz_id)

    msg = f'{email} 초대 완료 (역할: {role_label})'
    if not sent:
        msg += f' — 이메일 발송 실패. 직접 링크: {join_url}'
        logger.warning(f'[Team] 초대 링크 (이메일 미발송): {join_url}')

    if request.is_json:
        return jsonify({'status': 'ok', 'message': msg, 'join_url': join_url, 'sent': sent})
    flash(msg, 'success' if sent else 'warning')
    return redirect(url_for('team.index'))


# ══════════════════════════════════════════════
#  역할 변경
# ══════════════════════════════════════════════

@team_bp.route('/members/<int:target_uid>/role', methods=['POST'])
@login_required
@biz_required
def change_role(target_uid: int):
    """멤버 역할 변경. owner 역할은 변경 불가."""
    client = get_admin_client()
    biz_id = g.biz_id

    if request.is_json:
        data = request.get_json() or {}
    else:
        data = request.form

    new_role = (data.get('role') or '').strip()
    if new_role not in INVITE_ROLES:  # owner 변경 금지
        return _err(f'유효하지 않은 역할: {new_role}', request)

    # 권한 확인
    from auth.helpers import get_user_role
    my_role = 'admin' if current_user.is_super_admin else get_user_role(current_user.id, biz_id)
    if my_role not in ('owner', 'admin'):
        return _err('역할 변경 권한이 없습니다.', request, 403)

    # 대상 현재 역할 확인 (owner는 변경 불가)
    try:
        row = client.table('user_business_map').select('role') \
            .eq('biz_id', biz_id).eq('user_id', target_uid) \
            .limit(1).execute().data
        if not row:
            return _err('해당 멤버를 찾을 수 없습니다.', request, 404)
        if row[0]['role'] == 'owner':
            return _err('오너 역할은 변경할 수 없습니다.', request)
    except Exception as e:
        return _err(f'조회 실패: {e}', request)

    try:
        client.table('user_business_map') \
            .update({'role': new_role}) \
            .eq('biz_id', biz_id).eq('user_id', target_uid) \
            .execute()
    except Exception as e:
        return _err(f'역할 변경 실패: {e}', request)

    log_audit('team_role_change', detail={
        'target_uid': target_uid, 'new_role': new_role, 'biz_id': biz_id,
    }, biz_id=biz_id)

    if request.is_json:
        return jsonify({'status': 'ok', 'role': new_role,
                        'role_label': ROLE_LABELS.get(new_role, new_role)})
    flash(f'역할이 {ROLE_LABELS.get(new_role, new_role)}(으)로 변경되었습니다.', 'success')
    return redirect(url_for('team.index'))


# ══════════════════════════════════════════════
#  멤버 제거
# ══════════════════════════════════════════════

@team_bp.route('/members/<int:target_uid>/remove', methods=['POST'])
@login_required
@biz_required
def remove_member(target_uid: int):
    """멤버 제거 (user_business_map 행 삭제). owner는 제거 불가."""
    client = get_admin_client()
    biz_id = g.biz_id

    if target_uid == current_user.id:
        return _err('본인은 제거할 수 없습니다.', request)

    from auth.helpers import get_user_role
    my_role = 'admin' if current_user.is_super_admin else get_user_role(current_user.id, biz_id)
    if my_role not in ('owner', 'admin'):
        return _err('멤버 제거 권한이 없습니다.', request, 403)

    # owner 보호
    try:
        row = client.table('user_business_map').select('role') \
            .eq('biz_id', biz_id).eq('user_id', target_uid) \
            .limit(1).execute().data
        if not row:
            return _err('해당 멤버를 찾을 수 없습니다.', request, 404)
        if row[0]['role'] == 'owner':
            return _err('오너는 제거할 수 없습니다.', request)
    except Exception as e:
        return _err(f'조회 실패: {e}', request)

    try:
        client.table('user_business_map') \
            .delete() \
            .eq('biz_id', biz_id).eq('user_id', target_uid) \
            .execute()
    except Exception as e:
        return _err(f'제거 실패: {e}', request)

    log_audit('team_remove', detail={
        'target_uid': target_uid, 'biz_id': biz_id,
    }, biz_id=biz_id)

    if request.is_json:
        return jsonify({'status': 'ok'})
    flash('멤버가 제거되었습니다.', 'success')
    return redirect(url_for('team.index'))


# ══════════════════════════════════════════════
#  초대 취소
# ══════════════════════════════════════════════

@team_bp.route('/invites/<int:invite_id>/cancel', methods=['POST'])
@login_required
@biz_required
def cancel_invite(invite_id: int):
    """대기중 초대 취소 (행 삭제)."""
    client = get_admin_client()
    biz_id = g.biz_id

    from auth.helpers import get_user_role
    my_role = 'admin' if current_user.is_super_admin else get_user_role(current_user.id, biz_id)
    if my_role not in ('owner', 'admin'):
        return _err('초대 취소 권한이 없습니다.', request, 403)

    try:
        client.table('invitations') \
            .delete() \
            .eq('id', invite_id).eq('biz_id', biz_id) \
            .execute()
    except Exception as e:
        return _err(f'취소 실패: {e}', request)

    if request.is_json:
        return jsonify({'status': 'ok'})
    flash('초대가 취소되었습니다.', 'success')
    return redirect(url_for('team.index'))


# ──────────────────────────────────────────────
# 공통 에러 응답 헬퍼
# ──────────────────────────────────────────────

def _err(msg: str, req, code: int = 400):
    if req.is_json:
        return jsonify({'status': 'error', 'message': msg}), code
    flash(msg, 'danger')
    return redirect(url_for('team.index')), code
