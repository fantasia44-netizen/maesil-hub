"""Super-admin SaaS console.

Manages the platform itself (businesses / subscriptions / payments / saas_config),
not a single tenant. Only `current_user.is_super_admin == True` may access.

Routes (all under /admin-saas):
    GET  /                          — dashboard summary
    GET  /businesses                — list, search, filter
    POST /businesses/<id>/suspend   — flip status to 'suspended'
    POST /businesses/<id>/activate  — flip back to 'active'
    POST /businesses/<id>/impersonate     — start impersonation
    POST /impersonate/stop          — end impersonation
    GET  /payments                  — global payments table
    GET  /config                    — saas_config CRUD
    POST /config/save               — upsert one key
    POST /config/delete             — delete one key
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timezone, timedelta

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    jsonify, flash, session, abort, current_app,
)
from flask_login import current_user

from auth.decorators import super_admin_required, login_required
from auth.helpers import log_audit
from db.client import get_admin_client

# 역할 라벨 (auth/views._ROLE_LABELS 동기화)
_ROLE_LABELS = {
    'owner': '오너', 'admin': '관리자', 'manager': '매니저',
    'logistics': '물류팀', 'sales': '영업팀', 'viewer': '뷰어',
}
_ALLOWED_ROLES = set(_ROLE_LABELS.keys())
# subscriptions.status 허용값 (마이그 signup='trial')
_ALLOWED_SUB_STATUS = {'trial', 'active', 'past_due', 'cancelled', 'suspended'}

logger = logging.getLogger(__name__)

admin_saas_bp = Blueprint('admin_saas', __name__, url_prefix='/admin-saas')


# ─────────────── dashboard ───────────────

@admin_saas_bp.route('/')
@admin_saas_bp.route('/dashboard')
@login_required
@super_admin_required
def dashboard():
    client = get_admin_client()
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    summary = {
        'businesses_total': 0, 'businesses_active': 0, 'businesses_suspended': 0,
        'subs_active': 0, 'subs_trial': 0, 'subs_past_due': 0, 'subs_cancelled': 0,
        'mrr_this_month': 0, 'paid_count_this_month': 0,
    }
    try:
        biz_rows = client.table('businesses').select('status,is_deleted').execute().data or []
        summary['businesses_total'] = sum(1 for r in biz_rows if not r.get('is_deleted'))
        summary['businesses_active'] = sum(
            1 for r in biz_rows if r.get('status') == 'active' and not r.get('is_deleted'))
        summary['businesses_suspended'] = sum(
            1 for r in biz_rows if r.get('status') == 'suspended' and not r.get('is_deleted'))

        sub_rows = client.table('subscriptions').select('status').execute().data or []
        for r in sub_rows:
            s = r.get('status') or ''
            key = f'subs_{s}'
            if key in summary:
                summary[key] += 1

        pay_rows = client.table('payments').select('amount,refund_amount,refund_status,status') \
            .eq('status', 'paid').gte('paid_at', month_start.isoformat()).execute().data or []
        revenue = sum(int(r.get('amount') or 0) for r in pay_rows)
        refunded = sum(int(r.get('refund_amount') or 0)
                       for r in pay_rows if r.get('refund_status') == 'completed')
        summary['mrr_this_month'] = max(revenue - refunded, 0)
        summary['paid_count_this_month'] = len(pay_rows)
    except Exception as e:
        logger.error(f'[admin_saas] dashboard summary failed: {e}')

    return render_template('admin_saas/dashboard.html', summary=summary)


# ─────────────── businesses ───────────────

@admin_saas_bp.route('/businesses')
@login_required
@super_admin_required
def businesses_list():
    client = get_admin_client()
    q = (request.args.get('q') or '').strip()
    status_filter = (request.args.get('status') or '').strip()

    query = client.table('businesses').select('*').eq('is_deleted', False)
    if status_filter:
        query = query.eq('status', status_filter)
    if q:
        query = query.ilike('name', f'%{q}%')
    rows = query.order('created_at', desc=True).limit(200).execute().data or []

    # decorate with current plan name
    plan_map = {p['id']: p['name'] for p in
                (client.table('plans').select('id,name').execute().data or [])}
    for r in rows:
        r['plan_name'] = plan_map.get(r.get('plan_id'), '-')

    return render_template('admin_saas/businesses.html',
                           businesses=rows, q=q, status=status_filter)


@admin_saas_bp.route('/businesses/<int:biz_id>/suspend', methods=['POST'])
@login_required
@super_admin_required
def suspend_business(biz_id: int):
    if request.is_json:
        reason = (request.json or {}).get('reason') or 'admin action'
    else:
        reason = request.form.get('reason') or 'admin action'
    client = get_admin_client()
    try:
        client.table('businesses').update({
            'status': 'suspended',
            'subscription_status': 'past_due',
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }).eq('id', biz_id).execute()
        log_audit('biz_suspended', detail={'biz_id': biz_id, 'reason': reason}, biz_id=biz_id)
    except Exception as e:
        logger.error(f'[admin_saas] suspend failed: {e}')
        flash(f'suspend failed: {e}', 'danger')
        return redirect(url_for('admin_saas.businesses_list'))
    flash(f'biz {biz_id} suspended', 'success')
    return redirect(url_for('admin_saas.businesses_list'))


@admin_saas_bp.route('/businesses/<int:biz_id>/activate', methods=['POST'])
@login_required
@super_admin_required
def activate_business(biz_id: int):
    client = get_admin_client()
    try:
        client.table('businesses').update({
            'status': 'active',
            'subscription_status': 'active',
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }).eq('id', biz_id).execute()
        log_audit('biz_activated', detail={'biz_id': biz_id}, biz_id=biz_id)
    except Exception as e:
        logger.error(f'[admin_saas] activate failed: {e}')
        flash(f'activate failed: {e}', 'danger')
        return redirect(url_for('admin_saas.businesses_list'))
    flash(f'biz {biz_id} activated', 'success')
    return redirect(url_for('admin_saas.businesses_list'))


# ─────────────── impersonation ───────────────

@admin_saas_bp.route('/businesses/<int:biz_id>/impersonate', methods=['POST'])
@login_required
@super_admin_required
def impersonate(biz_id: int):
    client = get_admin_client()
    biz = client.table('businesses').select('id,name') \
        .eq('id', biz_id).single().execute().data
    if not biz:
        abort(404)
    session['pre_impersonate_biz_id'] = session.get('current_biz_id')  # 원래 biz 저장
    session['impersonating_biz_id'] = biz_id
    log_audit('impersonate_start', detail={'biz_id': biz_id, 'name': biz.get('name')},
              biz_id=biz_id)
    flash(f'위장 중: {biz.get("name")} (biz_id={biz_id})', 'warning')
    return redirect(url_for('main.dashboard'))


@admin_saas_bp.route('/impersonate/stop', methods=['POST'])   # GET 제거 (CSRF 방어)
@login_required
@super_admin_required
def stop_impersonate():
    biz_id = session.pop('impersonating_biz_id', None)
    # 원래 biz_id 복원
    prev = session.pop('pre_impersonate_biz_id', None)
    if prev:
        session['current_biz_id'] = prev
    if biz_id:
        log_audit('impersonate_stop', detail={'biz_id': biz_id})
    return redirect(url_for('admin_saas.dashboard'))


# ─────────────── payments ───────────────

@admin_saas_bp.route('/payments')
@login_required
@super_admin_required
def payments_list():
    client = get_admin_client()
    biz_q = request.args.get('biz_id')
    query = client.table('payments').select('*')
    if biz_q:
        try:
            query = query.eq('biz_id', int(biz_q))
        except ValueError:
            pass
    rows = query.order('created_at', desc=True).limit(200).execute().data or []
    return render_template('admin_saas/payments.html', payments=rows, biz_id=biz_q)


# ─────────────── saas_config CRUD ───────────────

# Default keys shown even when DB row doesn't exist yet, so admin can fill in.
_DEFAULT_KEYS = [
    {'key': 'portone_store_id',         'category': 'payment',       'description': 'PortOne Store ID',                   'encrypted': False},
    {'key': 'portone_api_secret',       'category': 'payment',       'description': 'PortOne API secret',                 'encrypted': True},
    {'key': 'portone_channel_card',     'category': 'payment',       'description': 'PortOne channel key (card)',         'encrypted': False},
    {'key': 'portone_channel_kakao',    'category': 'payment',       'description': 'PortOne channel key (kakaopay)',     'encrypted': False},
    {'key': 'portone_webhook_secret',   'category': 'payment',       'description': 'PortOne webhook signing secret',     'encrypted': True},
    {'key': 'sentry_dsn',               'category': 'observability', 'description': 'Sentry DSN',                         'encrypted': True},
    {'key': 'render_api_key',           'category': 'infra',         'description': 'Render API key',                     'encrypted': True},
    {'key': 'support_email',            'category': 'general',       'description': 'Support contact email',              'encrypted': False},
]


@admin_saas_bp.route('/config')
@login_required
@super_admin_required
def config_list():
    from services.saas_config import list_configs
    rows = list_configs()
    existing = {r['key'] for r in rows}
    for d in _DEFAULT_KEYS:
        if d['key'] not in existing:
            rows.append({
                'key': d['key'], 'category': d['category'],
                'description': d['description'],
                'value_plain': None, 'value_encrypted': None,
            })

    # group by category
    by_cat: dict[str, list] = {}
    for r in rows:
        by_cat.setdefault(r.get('category') or 'general', []).append(r)

    # tag whether key is treated as secret (for UI)
    secret_keys = {d['key'] for d in _DEFAULT_KEYS if d['encrypted']}
    return render_template('admin_saas/config.html',
                           groups=by_cat, secret_keys=secret_keys)


@admin_saas_bp.route('/config/save', methods=['POST'])
@login_required
@super_admin_required
def config_save():
    from services.saas_config import set_config

    key = (request.form.get('key') or '').strip()
    value = request.form.get('value', '')
    category = (request.form.get('category') or 'general').strip()
    description = request.form.get('description') or None
    encrypted_flag = request.form.get('encrypted') in ('1', 'true', 'on', True)

    if not key:
        flash('key required', 'danger')
        return redirect(url_for('admin_saas.config_list'))

    if not value:
        flash(f'{key}: empty value, skipped', 'warning')
        return redirect(url_for('admin_saas.config_list'))

    ok = set_config(key, value, encrypted=encrypted_flag,
                    category=category, description=description,
                    updated_by=current_user.id)
    if ok:
        log_audit('saas_config_saved', detail={'key': key, 'encrypted': encrypted_flag})
        flash(f'{key} saved', 'success')
    else:
        flash(f'{key} save failed', 'danger')
    return redirect(url_for('admin_saas.config_list'))


@admin_saas_bp.route('/config/delete', methods=['POST'])
@login_required
@super_admin_required
def config_delete():
    from services.saas_config import delete_config
    key = (request.form.get('key') or '').strip()
    if not key:
        return redirect(url_for('admin_saas.config_list'))
    if delete_config(key):
        log_audit('saas_config_deleted', detail={'key': key})
        flash(f'{key} deleted', 'success')
    else:
        flash(f'{key} delete failed', 'danger')
    return redirect(url_for('admin_saas.config_list'))


# ═══════════════════════════════════════════════════════════════
# P1: 회원사 상세 + 사용자 관리
# ═══════════════════════════════════════════════════════════════

@admin_saas_bp.route('/businesses/<int:biz_id>')
@login_required
@super_admin_required
def business_detail(biz_id: int):
    """회원사 상세 — 정보/구독/플랜/소속 사용자/최근 활동 통합."""
    client = get_admin_client()

    biz = client.table('businesses').select('*').eq('id', biz_id).limit(1).execute().data
    biz = biz[0] if biz else None
    if not biz:
        abort(404)

    # 구독 (biz당 1행 가정, 없을 수 있음)
    sub = None
    try:
        sub_rows = client.table('subscriptions').select('*') \
            .eq('biz_id', biz_id).order('id', desc=True).limit(1).execute().data or []
        sub = sub_rows[0] if sub_rows else None
    except Exception as e:
        logger.warning(f'[admin_saas] detail sub 조회 실패: {e}')

    # 플랜 목록 + 이름 매핑
    plans = client.table('plans').select('id,code,name,monthly_price') \
        .order('sort_order').execute().data or []
    plan_map = {p['id']: p for p in plans}
    biz['plan_name'] = (plan_map.get(biz.get('plan_id')) or {}).get('name', '-')
    if sub:
        sub['plan_name'] = (plan_map.get(sub.get('plan_id')) or {}).get('name', '-')

    # 소속 사용자 (user_business_map JOIN app_users)
    maps = client.table('user_business_map').select('*') \
        .eq('biz_id', biz_id).execute().data or []
    user_ids = [m['user_id'] for m in maps]
    users = []
    if user_ids:
        users = client.table('app_users').select(
            'id,email,name,phone,last_login_at,failed_login_count,'
            'locked_until,is_deleted,email_verified'
        ).in_('id', user_ids).execute().data or []
    umap = {u['id']: u for u in users}
    now_utc = datetime.now(timezone.utc)
    members = []
    for m in maps:
        u = umap.get(m['user_id'], {})
        locked = False
        lu = u.get('locked_until')
        if lu:
            try:
                lock_dt = lu if isinstance(lu, datetime) else \
                    datetime.fromisoformat(str(lu).replace('Z', '+00:00'))
                locked = lock_dt > now_utc
            except Exception:
                locked = False
        members.append({
            'map_id': m['id'], 'user_id': m['user_id'],
            'role': m.get('role', 'viewer'),
            'role_label': _ROLE_LABELS.get(m.get('role', ''), m.get('role', '')),
            'is_primary': m.get('is_primary', False),
            'email': u.get('email', '-'), 'name': u.get('name', ''),
            'phone': u.get('phone', ''),
            'last_login_at': u.get('last_login_at'),
            'is_locked': locked, 'is_deleted': u.get('is_deleted', False),
            'email_verified': u.get('email_verified', False),
        })

    # 최근 활동 (audit_logs)
    activity = []
    try:
        activity = client.table('audit_logs').select('action,detail,created_at,user_id') \
            .eq('biz_id', biz_id).order('created_at', desc=True).limit(30).execute().data or []
    except Exception as e:
        logger.warning(f'[admin_saas] detail activity 조회 실패: {e}')

    return render_template('admin_saas/business_detail.html',
                           biz=biz, sub=sub, plans=plans,
                           members=members, activity=activity,
                           role_labels=_ROLE_LABELS)


@admin_saas_bp.route('/businesses/<int:biz_id>/users/<int:user_id>/unlock', methods=['POST'])
@login_required
@super_admin_required
def api_user_unlock(biz_id: int, user_id: int):
    """로그인 잠금 해제 — failed_login_count/locked_until 초기화."""
    client = get_admin_client()
    try:
        client.table('app_users').update({
            'failed_login_count': 0, 'locked_until': None,
        }).eq('id', user_id).execute()
        log_audit('admin_user_unlock', detail={'biz_id': biz_id, 'user_id': user_id}, biz_id=biz_id)
        return jsonify({'ok': True})
    except Exception as e:
        logger.error(f'[admin_saas] unlock 실패: {e}')
        return jsonify({'ok': False, 'error': str(e)[:200]}), 500


@admin_saas_bp.route('/businesses/<int:biz_id>/users/<int:user_id>/reset-password', methods=['POST'])
@login_required
@super_admin_required
def api_user_reset_password(biz_id: int, user_id: int):
    """비밀번호 재설정 링크 발급 (이메일 발송 + 링크 직접 반환).

    이메일 미도달 대비 링크를 응답에 포함 — 슈퍼어드민이 직접 전달 가능.
    """
    client = get_admin_client()
    try:
        u = client.table('app_users').select('id,email,name') \
            .eq('id', user_id).limit(1).execute().data
        u = u[0] if u else None
        if not u or not u.get('email'):
            return jsonify({'ok': False, 'error': '사용자 이메일 없음'}), 400
        email = u['email']

        # 기존 미사용 토큰 무효화
        try:
            client.table('password_reset_tokens') \
                .update({'used_at': datetime.now(timezone.utc).isoformat()}) \
                .eq('email', email).is_('used_at', 'null').execute()
        except Exception:
            pass

        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        client.table('password_reset_tokens').insert({
            'user_id': user_id, 'email': email,
            'token': token_hash, 'expires_at': expires_at,
        }).execute()

        # 잠금도 함께 해제
        client.table('app_users').update({
            'failed_login_count': 0, 'locked_until': None,
        }).eq('id', user_id).execute()

        reset_url = url_for('auth.reset_password', token=token, _external=True)

        # 이메일 발송 시도 (실패해도 링크는 반환)
        mail_sent = False
        try:
            from services.email_service import send_password_reset_email
            mail_sent = bool(send_password_reset_email(email, u.get('name', ''), reset_url))
        except Exception as e:
            logger.warning(f'[admin_saas] reset 메일 발송 실패: {e}')

        log_audit('admin_user_reset_password',
                  detail={'biz_id': biz_id, 'user_id': user_id, 'email': email,
                          'mail_sent': mail_sent}, biz_id=biz_id)
        return jsonify({'ok': True, 'email': email, 'reset_url': reset_url,
                        'mail_sent': mail_sent, 'expires_in': '1시간'})
    except Exception as e:
        logger.error(f'[admin_saas] reset-password 실패: {e}')
        return jsonify({'ok': False, 'error': str(e)[:200]}), 500


@admin_saas_bp.route('/businesses/<int:biz_id>/users/<int:user_id>/role', methods=['POST'])
@login_required
@super_admin_required
def api_user_role(biz_id: int, user_id: int):
    """소속 역할 변경 (user_business_map.role)."""
    data = request.get_json(force=True) or {}
    role = (data.get('role') or '').strip()
    if role not in _ALLOWED_ROLES:
        return jsonify({'ok': False, 'error': f'허용되지 않는 역할: {role}'}), 400
    client = get_admin_client()
    try:
        client.table('user_business_map').update({'role': role}) \
            .eq('user_id', user_id).eq('biz_id', biz_id).execute()
        log_audit('admin_user_role', detail={'biz_id': biz_id, 'user_id': user_id, 'role': role}, biz_id=biz_id)
        return jsonify({'ok': True, 'role': role, 'role_label': _ROLE_LABELS.get(role, role)})
    except Exception as e:
        logger.error(f'[admin_saas] role 변경 실패: {e}')
        return jsonify({'ok': False, 'error': str(e)[:200]}), 500


@admin_saas_bp.route('/businesses/<int:biz_id>/users/<int:user_id>/remove', methods=['POST'])
@login_required
@super_admin_required
def api_user_remove(biz_id: int, user_id: int):
    """소속 해제 — 해당 회원사에서 사용자 제거 (user_business_map 행 삭제).

    계정 자체는 유지 (다른 회원사 소속일 수 있음). owner는 마지막 1명이면 거부.
    """
    client = get_admin_client()
    try:
        maps = client.table('user_business_map').select('id,role') \
            .eq('biz_id', biz_id).execute().data or []
        # owner 마지막 1명 보호
        owners = [m for m in maps if m.get('role') == 'owner']
        this = client.table('user_business_map').select('id,role') \
            .eq('user_id', user_id).eq('biz_id', biz_id).limit(1).execute().data
        this = this[0] if this else None
        if this and this.get('role') == 'owner' and len(owners) <= 1:
            return jsonify({'ok': False, 'error': '마지막 오너는 제거할 수 없습니다.'}), 400

        client.table('user_business_map').delete() \
            .eq('user_id', user_id).eq('biz_id', biz_id).execute()
        log_audit('admin_user_remove', detail={'biz_id': biz_id, 'user_id': user_id}, biz_id=biz_id)
        return jsonify({'ok': True})
    except Exception as e:
        logger.error(f'[admin_saas] user remove 실패: {e}')
        return jsonify({'ok': False, 'error': str(e)[:200]}), 500


# ═══════════════════════════════════════════════════════════════
# P2: 구독 직접제어 + 접속 현황
# ═══════════════════════════════════════════════════════════════

@admin_saas_bp.route('/businesses/<int:biz_id>/subscription', methods=['POST'])
@login_required
@super_admin_required
def api_subscription(biz_id: int):
    """구독/플랜/체험만료일 직접 변경 (businesses + subscriptions 동기화)."""
    data = request.get_json(force=True) or {}
    plan_id = data.get('plan_id')
    sub_status = (data.get('status') or '').strip() or None
    trial_ends_at = (data.get('trial_ends_at') or '').strip() or None   # 'YYYY-MM-DD'
    period_end = (data.get('current_period_end') or '').strip() or None  # 'YYYY-MM-DD'

    if sub_status and sub_status not in _ALLOWED_SUB_STATUS:
        return jsonify({'ok': False, 'error': f'허용되지 않는 상태: {sub_status}'}), 400

    client = get_admin_client()
    try:
        # businesses 갱신
        biz_payload = {'updated_at': datetime.now(timezone.utc).isoformat()}
        if plan_id:
            biz_payload['plan_id'] = int(plan_id)
        if sub_status:
            biz_payload['subscription_status'] = sub_status
        if trial_ends_at:
            biz_payload['trial_ends_at'] = f'{trial_ends_at}T23:59:59+09:00'
        client.table('businesses').update(biz_payload).eq('id', biz_id).execute()

        # subscriptions upsert (없으면 생성)
        sub_payload = {'biz_id': biz_id, 'updated_at': datetime.now(timezone.utc).isoformat()}
        if plan_id:
            sub_payload['plan_id'] = int(plan_id)
        if sub_status:
            sub_payload['status'] = sub_status
        if period_end:
            sub_payload['current_period_end'] = f'{period_end}T23:59:59+09:00'
        existing = client.table('subscriptions').select('id') \
            .eq('biz_id', biz_id).limit(1).execute().data
        if existing:
            client.table('subscriptions').update(sub_payload).eq('id', existing[0]['id']).execute()
        else:
            client.table('subscriptions').insert(sub_payload).execute()

        log_audit('admin_subscription_update',
                  detail={'biz_id': biz_id, 'biz': biz_payload, 'sub': sub_payload}, biz_id=biz_id)
        return jsonify({'ok': True})
    except Exception as e:
        logger.error(f'[admin_saas] subscription 변경 실패: {e}')
        return jsonify({'ok': False, 'error': str(e)[:200]}), 500


@admin_saas_bp.route('/api/recent-users')
@login_required
@super_admin_required
def api_recent_users():
    """최근 로그인 사용자 (app_users.last_login_at 기준 최신 30명).

    hub는 last_seen_at 하트비트가 없어 last_login_at 기반. 회원사명 조인.
    """
    client = get_admin_client()
    try:
        users = client.table('app_users') \
            .select('id,email,name,last_login_at,is_super_admin') \
            .not_.is_('last_login_at', 'null') \
            .order('last_login_at', desc=True).limit(30).execute().data or []
        uids = [u['id'] for u in users]
        # 소속 회원사명
        biz_by_user = {}
        if uids:
            maps = client.table('user_business_map').select('user_id,biz_id') \
                .in_('user_id', uids).execute().data or []
            bids = list({m['biz_id'] for m in maps})
            bmap = {}
            if bids:
                brows = client.table('businesses').select('id,name').in_('id', bids).execute().data or []
                bmap = {b['id']: b['name'] for b in brows}
            for m in maps:
                biz_by_user.setdefault(m['user_id'], []).append(bmap.get(m['biz_id'], f"biz{m['biz_id']}"))
        rows = []
        for u in users:
            rows.append({
                'email': u.get('email'), 'name': u.get('name', ''),
                'last_login_at': u.get('last_login_at'),
                'is_super_admin': u.get('is_super_admin', False),
                'businesses': ', '.join(biz_by_user.get(u['id'], [])) or ('슈퍼어드민' if u.get('is_super_admin') else '-'),
            })
        return jsonify({'ok': True, 'rows': rows, 'count': len(rows)})
    except Exception as e:
        logger.error(f'[admin_saas] recent-users 실패: {e}')
        return jsonify({'ok': False, 'error': str(e)[:200]}), 500


@admin_saas_bp.route('/api/activity-log')
@login_required
@super_admin_required
def api_activity_log():
    """전체 감사 로그 최근 50건 (회원사명 포함)."""
    client = get_admin_client()
    try:
        rows = client.table('audit_logs') \
            .select('action,detail,created_at,user_id,biz_id') \
            .order('created_at', desc=True).limit(50).execute().data or []
        bids = list({r['biz_id'] for r in rows if r.get('biz_id')})
        bmap = {}
        if bids:
            brows = client.table('businesses').select('id,name').in_('id', bids).execute().data or []
            bmap = {b['id']: b['name'] for b in brows}
        for r in rows:
            r['biz_name'] = bmap.get(r.get('biz_id'), '-')
        return jsonify({'ok': True, 'rows': rows})
    except Exception as e:
        logger.error(f'[admin_saas] activity-log 실패: {e}')
        return jsonify({'ok': False, 'error': str(e)[:200]}), 500


# ═══════════════════════════════════════════════════════════════
# P3: 공지/점검 브로드캐스트
# ═══════════════════════════════════════════════════════════════

@admin_saas_bp.route('/notices')
@login_required
@super_admin_required
def notices_list():
    """공지 관리 화면."""
    client = get_admin_client()
    notices = []
    try:
        notices = client.table('system_notices').select('*') \
            .order('created_at', desc=True).limit(50).execute().data or []
    except Exception as e:
        logger.error(f'[admin_saas] notices 조회 실패: {e}')
    return render_template('admin_saas/notices.html', notices=notices)


@admin_saas_bp.route('/api/notices', methods=['POST'])
@login_required
@super_admin_required
def api_notice_create():
    """공지 생성 (기간 지정 가능)."""
    data = request.get_json(force=True) or {}
    title = (data.get('title') or '').strip()
    if not title:
        return jsonify({'ok': False, 'error': '제목 필수'}), 400
    client = get_admin_client()
    try:
        row = {
            'type': data.get('type', 'info'),
            'title': title,
            'body': (data.get('body') or '').strip(),
            'starts_at': data.get('starts_at') or None,
            'ends_at': data.get('ends_at') or None,
            'is_active': bool(data.get('is_active', True)),
            'created_by': str(current_user.name or current_user.email or ''),
        }
        res = client.table('system_notices').insert(row).execute()
        log_audit('admin_notice_create', detail={'title': title, 'type': row['type']})
        return jsonify({'ok': True, 'id': (res.data or [{}])[0].get('id')})
    except Exception as e:
        logger.error(f'[admin_saas] notice 생성 실패: {e}')
        return jsonify({'ok': False, 'error': str(e)[:200]}), 500


@admin_saas_bp.route('/api/notices/<int:notice_id>/toggle', methods=['POST'])
@login_required
@super_admin_required
def api_notice_toggle(notice_id: int):
    """공지 활성/비활성 토글."""
    data = request.get_json(force=True) or {}
    active = bool(data.get('is_active', False))
    client = get_admin_client()
    try:
        client.table('system_notices').update({'is_active': active}).eq('id', notice_id).execute()
        log_audit('admin_notice_toggle', detail={'id': notice_id, 'is_active': active})
        return jsonify({'ok': True, 'is_active': active})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]}), 500


@admin_saas_bp.route('/api/broadcast', methods=['POST'])
@login_required
@super_admin_required
def api_broadcast():
    """긴급 브로드캐스트 — 단기 공지(기본 30분) 즉시 생성."""
    data = request.get_json(force=True) or {}
    msg = (data.get('message') or '').strip()
    if not msg:
        return jsonify({'ok': False, 'error': '메시지 필수'}), 400
    try:
        minutes = int(data.get('minutes', 30))
    except (TypeError, ValueError):
        minutes = 30
    notice_type = data.get('type', 'warning')
    client = get_admin_client()
    try:
        now = datetime.now(timezone.utc)
        client.table('system_notices').insert({
            'type': notice_type, 'title': msg, 'body': data.get('body', ''),
            'starts_at': now.isoformat(),
            'ends_at': (now + timedelta(minutes=minutes)).isoformat(),
            'is_active': True,
            'created_by': str(current_user.name or current_user.email or ''),
        }).execute()
        log_audit('admin_broadcast', detail={'message': msg, 'minutes': minutes})
        return jsonify({'ok': True})
    except Exception as e:
        logger.error(f'[admin_saas] broadcast 실패: {e}')
        return jsonify({'ok': False, 'error': str(e)[:200]}), 500


# ═══════════════════════════════════════════════════════════════
# P3: 서버 헬스 모니터
# ═══════════════════════════════════════════════════════════════

@admin_saas_bp.route('/api/health')
@login_required
@super_admin_required
def api_health():
    """서버 + DB 통합 상태 (대시보드 폴링용). 항상 JSON 반환."""
    import os as _os
    import time as _time
    import threading as _threading

    def _proc_stats():
        """psutil 없이 Linux /proc 기반 프로세스 통계 (Render=Linux)."""
        out = {'status': 'ok', 'pid': _os.getpid(),
               'num_threads': _threading.active_count()}
        # 메모리 (VmRSS) + 스레드 수
        try:
            with open(f'/proc/{_os.getpid()}/status') as f:
                for ln in f:
                    if ln.startswith('VmRSS:'):
                        out['memory_mb'] = round(int(ln.split()[1]) / 1024, 1)  # kB→MB
                    elif ln.startswith('Threads:'):
                        out['num_threads'] = int(ln.split()[1])
        except Exception:
            pass
        # 업타임 = 시스템업타임 - 프로세스시작(clock ticks)
        try:
            clk = _os.sysconf('SC_CLK_TCK')
            with open(f'/proc/{_os.getpid()}/stat') as f:
                starttime = int(f.read().split()[21])
            with open('/proc/uptime') as f:
                sys_up = float(f.read().split()[0])
            out['uptime_seconds'] = int(sys_up - starttime / clk)
        except Exception:
            pass
        return out

    web = {}
    try:
        import psutil
        p = psutil.Process(_os.getpid())
        mem = p.memory_info()
        web = {
            'status': 'ok', 'pid': p.pid,
            'memory_mb': round(mem.rss / 1048576, 1),
            'memory_percent': round(p.memory_percent(), 1),
            'num_threads': p.num_threads(),
            'uptime_seconds': int(_time.time() - p.create_time()),
        }
    except ImportError:
        web = _proc_stats()   # psutil 없으면 /proc 폴백
    except Exception as e:
        web = {'status': 'error', 'error': str(e)[:150]}

    # DB 상태 — 간단 카운트 + latency
    db = {'status': 'ok'}
    try:
        client = get_admin_client()
        t0 = _time.time()
        biz_cnt = client.table('businesses').select('id', count='exact').eq('is_deleted', False).execute()
        db['latency_ms'] = int((_time.time() - t0) * 1000)
        db['businesses'] = biz_cnt.count or 0
        usr_cnt = client.table('app_users').select('id', count='exact').eq('is_deleted', False).execute()
        db['users'] = usr_cnt.count or 0
    except Exception as e:
        db = {'status': 'error', 'error': str(e)[:150]}

    config = {
        'workers': int(_os.environ.get('GUNICORN_WORKERS', '1')),
        'threads': int(_os.environ.get('GUNICORN_THREADS', '4')),
        'region': _os.environ.get('RENDER_REGION', '-'),
        'service': _os.environ.get('RENDER_SERVICE_NAME', 'maesil-hub'),
    }
    overall = 'ok' if web.get('status') != 'error' and db.get('status') != 'error' else 'degraded'
    return jsonify({'status': overall, 'web': web, 'db': db, 'config': config})
