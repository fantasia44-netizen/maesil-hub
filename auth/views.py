"""Auth views — 회원가입/로그인/로그아웃/회사선택."""
import bcrypt
from datetime import datetime, timezone, timedelta
from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, session, jsonify, g, current_app,
)
from flask_login import login_user, logout_user, login_required, current_user

from db.client import get_admin_client
from .models import HubUser
from .helpers import log_audit
from .decorators import login_required as hub_login_required

# 로그인 잠금 정책 (doc/AUTH_AND_TENANCY.md §1-5)
_MAX_FAILED_LOGINS = 5
_LOCKOUT_MINUTES = 5

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')

# 역할 레이블 (team.py와 동기화)
_ROLE_LABELS = {
    'owner': '오너', 'admin': '관리자', 'manager': '매니저',
    'logistics': '물류팀', 'sales': '영업팀', 'viewer': '뷰어',
}


# ─── 비밀번호 해싱 ───
def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode('utf-8'), bcrypt.gensalt(rounds=12)).decode('utf-8')


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode('utf-8'), hashed.encode('utf-8'))
    except Exception:
        return False


# ─── 회원가입 ───
@auth_bp.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'GET':
        return render_template('auth/signup.html')

    email = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '')
    name = request.form.get('name', '').strip()
    biz_name = request.form.get('biz_name', '').strip()
    biz_reg_no = request.form.get('biz_reg_no', '').strip()
    industry = request.form.get('industry', 'food')

    if not email or not password or not biz_name:
        flash('이메일/비밀번호/회사명은 필수', 'danger')
        return redirect(url_for('auth.signup'))

    if len(password) < 10:
        flash('비밀번호는 최소 10자', 'danger')
        return redirect(url_for('auth.signup'))

    client = get_admin_client()

    # 이메일 중복 체크
    existing = client.table('app_users').select('id').eq('email', email).eq('is_deleted', False).execute()
    if existing.data:
        flash('이미 가입된 이메일', 'danger')
        return redirect(url_for('auth.signup'))

    # 1) app_users 생성
    user_res = client.table('app_users').insert({
        'email': email,
        'password_hash': hash_password(password),
        'name': name or email.split('@')[0],
        'email_verified': True,  # Phase 0: 이메일 인증 생략
    }).execute()
    user_id = user_res.data[0]['id']

    # 2) businesses 생성
    biz_res = client.table('businesses').insert({
        'name': biz_name,
        'biz_reg_no': biz_reg_no or None,
        'industry': industry,
        'status': 'active',
    }).execute()
    biz_id = biz_res.data[0]['id']

    # 3) user_business_map (owner)
    client.table('user_business_map').insert({
        'user_id': user_id,
        'biz_id': biz_id,
        'role': 'owner',
        'is_primary': True,
    }).execute()

    # 4) trial 구독 (Starter 플랜, 14일 trial)
    from datetime import datetime, timedelta, timezone
    starter_plan = client.table('plans').select('id').eq('code', 'starter').single().execute()
    if starter_plan.data:
        now = datetime.now(timezone.utc)
        client.table('subscriptions').insert({
            'biz_id': biz_id,
            'plan_id': starter_plan.data['id'],
            'status': 'trial',
            'current_period_start': now.isoformat(),
            'current_period_end': (now + timedelta(days=14)).isoformat(),
        }).execute()

    # 자동 로그인
    user = HubUser(client.table('app_users').select('*').eq('id', user_id).single().execute().data)
    login_user(user)
    session['current_biz_id'] = biz_id

    log_audit('signup', detail={'email': email, 'biz_id': biz_id}, user_id=user_id, biz_id=biz_id)

    flash(f'환영합니다, {biz_name}!', 'success')
    return redirect(url_for('main.dashboard'))


# ─── 로그인 ───
@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template('auth/login.html')

    email = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '')
    # Open Redirect 방지: 같은 호스트인 경우만 허용
    from urllib.parse import urlparse, urljoin
    def _is_safe_url(target):
        ref = urlparse(request.host_url)
        test = urlparse(urljoin(request.host_url, target))
        return test.scheme in ('http', 'https') and ref.netloc == test.netloc
    _next = request.args.get('next', '')
    next_url = _next if (_next and _is_safe_url(_next)) else None  # 로그인 후 결정

    if not email or not password:
        flash('이메일/비밀번호 입력', 'danger')
        return redirect(url_for('auth.login'))

    client = get_admin_client()
    res = client.table('app_users').select('*') \
        .eq('email', email).eq('is_deleted', False).execute()

    # 1) 사용자 존재 여부 + 잠금 확인 (타이밍 공격 완화를 위해 비밀번호 검증 전에 체크)
    user_row = res.data[0] if res.data else None
    now_utc = datetime.now(timezone.utc)

    if user_row:
        locked_until = user_row.get('locked_until')
        if locked_until:
            # ISO 문자열 또는 datetime 객체 양쪽 지원
            try:
                lock_dt = locked_until if isinstance(locked_until, datetime) \
                    else datetime.fromisoformat(str(locked_until).replace('Z', '+00:00'))
                if lock_dt > now_utc:
                    remain_sec = int((lock_dt - now_utc).total_seconds())
                    log_audit('login_locked', detail={'email': email, 'remain_sec': remain_sec})
                    flash(f'{_MAX_FAILED_LOGINS}회 실패로 잠금 중 (남은 {remain_sec}초)', 'danger')
                    return redirect(url_for('auth.login'))
            except Exception:
                pass  # 잠금 시각 파싱 실패 시 일반 흐름 진행

    # 2) 비밀번호 검증
    if not user_row or not verify_password(password, user_row['password_hash']):
        # 실패 횟수 증가 + 5회 도달 시 잠금
        if user_row:
            fail_count = (user_row.get('failed_login_count') or 0) + 1
            update_payload = {'failed_login_count': fail_count}
            if fail_count >= _MAX_FAILED_LOGINS:
                update_payload['locked_until'] = (now_utc + timedelta(minutes=_LOCKOUT_MINUTES)).isoformat()
                log_audit('login_lockout', user_id=user_row['id'],
                          detail={'email': email, 'fail_count': fail_count})
            client.table('app_users').update(update_payload).eq('id', user_row['id']).execute()
        log_audit('login_failed', detail={'email': email})
        flash('이메일 또는 비밀번호 오류', 'danger')
        return redirect(url_for('auth.login'))

    # 3) 로그인 성공
    user = HubUser(user_row)
    login_user(user)

    # primary 회사 자동 선택 (슈퍼어드민은 스킵 → 어드민 대시보드로)
    if user.is_super_admin:
        if next_url is None:
            next_url = url_for('admin_saas.dashboard')
    else:
        ubm = client.table('user_business_map').select('biz_id') \
            .eq('user_id', user.id).order('is_primary', desc=True).order('id').execute()
        if ubm.data:
            session['current_biz_id'] = ubm.data[0]['biz_id']
        if next_url is None:
            next_url = url_for('main.dashboard')

    # 4) 실패 카운터 리셋 + last_login 업데이트
    client.table('app_users').update({
        'last_login_at': now_utc.isoformat(),
        'failed_login_count': 0,
        'locked_until': None,
    }).eq('id', user.id).execute()

    log_audit('login', user_id=user.id, biz_id=session.get('current_biz_id'))

    return redirect(next_url)


# ─── 로그아웃 ───
@auth_bp.route('/logout', methods=['GET', 'POST'])
@login_required
def logout():
    log_audit('logout', user_id=current_user.id, biz_id=session.get('current_biz_id'))
    logout_user()
    session.clear()
    return redirect(url_for('main.index'))


# ─── 비밀번호 변경 ───
@auth_bp.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'GET':
        return render_template('auth/change_password.html')

    current_pw = request.form.get('current_password', '')
    new_pw     = request.form.get('new_password', '')
    confirm_pw = request.form.get('confirm_password', '')

    if len(new_pw) < 10:
        flash('새 비밀번호는 최소 10자', 'danger')
        return redirect(url_for('auth.change_password'))
    if new_pw != confirm_pw:
        flash('새 비밀번호가 일치하지 않습니다.', 'danger')
        return redirect(url_for('auth.change_password'))

    client = get_admin_client()
    res = client.table('app_users').select('password_hash') \
        .eq('id', current_user.id).single().execute()
    if not res.data or not verify_password(current_pw, res.data['password_hash']):
        flash('현재 비밀번호가 틀립니다.', 'danger')
        return redirect(url_for('auth.change_password'))

    client.table('app_users').update({
        'password_hash': hash_password(new_pw),
    }).eq('id', current_user.id).execute()

    log_audit('change_password', user_id=current_user.id)
    flash('비밀번호가 변경되었습니다.', 'success')
    return redirect(url_for('main.dashboard'))


# ─── 비밀번호 찾기 ───
@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    """비밀번호 재설정 요청 — 이메일 입력 → 토큰 발급 → 메일 발송 (또는 화면 표시)."""
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))

    if request.method == 'GET':
        return render_template('auth/forgot_password.html')

    import secrets
    from datetime import datetime, timezone, timedelta

    email = request.form.get('email', '').strip().lower()
    if not email:
        flash('이메일을 입력해 주세요.', 'danger')
        return render_template('auth/forgot_password.html')

    client = get_admin_client()

    # 가입된 이메일인지 확인
    res = client.table('app_users').select('id, name, email') \
        .eq('email', email).eq('is_deleted', False).limit(1).execute()

    # 보안상 존재 여부 노출 안 함 — 항상 동일 메시지
    if not res.data:
        flash('입력하신 이메일로 재설정 링크를 발송했습니다. (등록된 경우)', 'info')
        return render_template('auth/forgot_password.html')

    user_row = res.data[0]

    # 기존 미사용 토큰 무효화
    try:
        client.table('password_reset_tokens') \
            .update({'used_at': datetime.now(timezone.utc).isoformat()}) \
            .eq('email', email).is_('used_at', 'null').execute()
    except Exception:
        pass

    # 새 토큰 생성 (1시간 유효)
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

    client.table('password_reset_tokens').insert({
        'user_id':    user_row['id'],
        'email':      email,
        'token':      token,
        'expires_at': expires_at,
    }).execute()

    # 재설정 URL
    reset_url = url_for('auth.reset_password', token=token, _external=True)

    # 이메일 발송 시도 (SMTP 환경변수 있을 때만)
    mail_sent = _send_reset_email(email, user_row.get('name', ''), reset_url)

    if mail_sent:
        flash('비밀번호 재설정 링크를 이메일로 발송했습니다. (유효시간 1시간)', 'success')
        return render_template('auth/forgot_password.html')
    else:
        # SMTP 미설정: 관리자 전용 — 링크 직접 표시
        flash('이메일 발송 설정이 없습니다. 아래 링크를 복사해 사용하세요.', 'warning')
        return render_template('auth/forgot_password.html',
                               reset_url=reset_url, show_link=True)


def _send_reset_email(to_email: str, name: str, reset_url: str) -> bool:
    """SMTP로 비밀번호 재설정 이메일 발송. 환경변수 없으면 False 반환."""
    import os, smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    smtp_host = os.environ.get('SMTP_HOST', '')
    smtp_port = int(os.environ.get('SMTP_PORT', 587))
    smtp_user = os.environ.get('SMTP_USER', '')
    smtp_pass = os.environ.get('SMTP_PASS', '')

    if not smtp_host or not smtp_user or not smtp_pass:
        return False

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = '[배마마] 비밀번호 재설정 안내'
        msg['From']    = smtp_user
        msg['To']      = to_email

        html = f"""
<p>안녕하세요 {name}님,</p>
<p>비밀번호 재설정 요청이 접수되었습니다.<br>
아래 버튼을 클릭해 새 비밀번호를 설정하세요. (유효시간: 1시간)</p>
<p>
  <a href="{reset_url}"
     style="display:inline-block;padding:12px 24px;background:#198754;
            color:#fff;text-decoration:none;border-radius:6px;font-size:15px;">
    비밀번호 재설정
  </a>
</p>
<p style="font-size:12px;color:#666;">
  이 링크는 1시간 후 만료됩니다.<br>
  본인이 요청하지 않은 경우 이 이메일을 무시하세요.
</p>
"""
        msg.attach(MIMEText(html, 'html', 'utf-8'))

        with smtplib.SMTP(smtp_host, smtp_port) as s:
            s.ehlo()
            s.starttls()
            s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, [to_email], msg.as_string())
        return True
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f'[Auth] 이메일 발송 실패: {e}')
        return False


# ─── 비밀번호 재설정 ───
@auth_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token: str):
    """토큰 검증 → 새 비밀번호 설정."""
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))

    from datetime import datetime, timezone

    client = get_admin_client()

    def _get_token_row():
        try:
            res = client.table('password_reset_tokens').select('*') \
                .eq('token', token).is_('used_at', 'null').limit(1).execute()
            return res.data[0] if res.data else None
        except Exception:
            return None

    token_row = _get_token_row()

    if not token_row:
        flash('유효하지 않거나 이미 사용된 링크입니다.', 'danger')
        return redirect(url_for('auth.forgot_password'))

    # 만료 확인
    expires_at = token_row.get('expires_at', '')
    try:
        exp_dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
        if datetime.now(timezone.utc) > exp_dt:
            flash('링크가 만료되었습니다. 다시 요청해 주세요.', 'danger')
            return redirect(url_for('auth.forgot_password'))
    except Exception:
        pass

    if request.method == 'GET':
        return render_template('auth/reset_password.html', token=token)

    new_pw     = request.form.get('new_password', '')
    confirm_pw = request.form.get('confirm_password', '')

    if len(new_pw) < 10:
        flash('비밀번호는 최소 10자 이상이어야 합니다.', 'danger')
        return render_template('auth/reset_password.html', token=token)
    if new_pw != confirm_pw:
        flash('비밀번호가 일치하지 않습니다.', 'danger')
        return render_template('auth/reset_password.html', token=token)

    # 비밀번호 업데이트
    user_id = token_row['user_id']
    client.table('app_users').update({
        'password_hash': hash_password(new_pw),
    }).eq('id', user_id).execute()

    # 토큰 소진
    client.table('password_reset_tokens').update({
        'used_at': datetime.now(timezone.utc).isoformat(),
    }).eq('token', token).execute()

    log_audit('reset_password', user_id=user_id)
    flash('비밀번호가 성공적으로 변경되었습니다. 로그인해 주세요.', 'success')
    return redirect(url_for('auth.login'))


# ─── 초대 수락 ───
@auth_bp.route('/join/<token>', methods=['GET', 'POST'])
def join_invite(token: str):
    """이메일 초대 링크 수락.

    GET:
        - 로그인 상태 → 즉시 처리 후 대시보드로
        - 비로그인 → join 폼 표시 (기존 계정 로그인 / 신규 가입)
    POST:
        - mode=login: 기존 계정으로 로그인 후 합류
        - mode=signup: 신규 계정 생성 후 합류
    """
    client = get_admin_client()
    from datetime import datetime, timezone

    # 토큰 조회 + 유효성 검증
    def _get_invite():
        try:
            res = client.table('invitations').select('*') \
                .eq('token', token).limit(1).execute()
            if not res.data:
                return None, '유효하지 않은 초대 링크입니다.'
            inv = res.data[0]
            if inv.get('used_at'):
                return None, '이미 사용된 초대 링크입니다.'
            exp = inv.get('expires_at', '')
            if exp:
                from datetime import datetime, timezone
                try:
                    exp_dt = datetime.fromisoformat(exp.replace('Z', '+00:00'))
                    if datetime.now(timezone.utc) > exp_dt:
                        return None, '초대 링크가 만료되었습니다. (7일 유효)'
                except Exception:
                    pass
            return inv, None
        except Exception as e:
            return None, f'초대 조회 오류: {e}'

    def _apply_invite(inv, user_id):
        """user_business_map에 등록하고 초대를 소진 처리."""
        biz_id = inv['biz_id']
        role   = inv.get('role', 'viewer')

        # 이미 멤버인지 확인
        existing = client.table('user_business_map').select('id') \
            .eq('biz_id', biz_id).eq('user_id', user_id).limit(1).execute().data
        if not existing:
            client.table('user_business_map').insert({
                'user_id':    user_id,
                'biz_id':     biz_id,
                'role':       role,
                'is_primary': False,
            }).execute()
        # 소진
        client.table('invitations').update({
            'used_at': datetime.now(timezone.utc).isoformat(),
        }).eq('id', inv['id']).execute()

        # 세션에 biz 세팅 (기존 primary가 없으면)
        if not session.get('current_biz_id'):
            session['current_biz_id'] = biz_id
        return biz_id, role

    # ─── 로그인 상태에서 GET ───
    if current_user.is_authenticated and request.method == 'GET':
        inv, err = _get_invite()
        if err:
            flash(err, 'danger')
            return redirect(url_for('main.dashboard'))
        try:
            biz_id, role = _apply_invite(inv, current_user.id)
            biz_name = client.table('businesses').select('name') \
                .eq('id', biz_id).single().execute().data.get('name', str(biz_id))
            log_audit('team_join', detail={'token': token[:8] + '...', 'biz_id': biz_id, 'role': role})
            flash(f'🎉 {biz_name} 팀에 합류했습니다! (역할: {_ROLE_LABELS.get(role, role)})', 'success')
            session['current_biz_id'] = biz_id
        except Exception as e:
            flash(f'합류 처리 중 오류: {e}', 'danger')
        return redirect(url_for('main.dashboard'))

    # ─── GET (비로그인) ───
    if request.method == 'GET':
        inv, err = _get_invite()
        if err:
            flash(err, 'danger')
            return redirect(url_for('auth.login'))
        # 업체명 + 역할 표시용
        biz_row = client.table('businesses').select('name') \
            .eq('id', inv['biz_id']).single().execute().data or {}
        return render_template('auth/join.html',
                               token=token,
                               invite=inv,
                               biz_name=biz_row.get('name', ''),
                               role_label=_ROLE_LABELS.get(inv.get('role', ''), inv.get('role', '')))

    # ─── POST ───
    inv, err = _get_invite()
    if err:
        flash(err, 'danger')
        return redirect(url_for('auth.login'))

    mode = request.form.get('mode', 'login')

    if mode == 'login':
        # 기존 계정으로 합류
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        res = client.table('app_users').select('*') \
            .eq('email', email).eq('is_deleted', False).execute()
        if not res.data or not verify_password(password, res.data[0]['password_hash']):
            flash('이메일 또는 비밀번호 오류', 'danger')
            return redirect(url_for('auth.join_invite', token=token))
        user = HubUser(res.data[0])
        login_user(user)
        biz_id, role = _apply_invite(inv, user.id)
        log_audit('team_join', detail={'mode': 'login', 'biz_id': biz_id, 'role': role},
                  user_id=user.id, biz_id=biz_id)
        biz_name = client.table('businesses').select('name') \
            .eq('id', biz_id).single().execute().data.get('name', str(biz_id))
        flash(f'🎉 {biz_name} 팀에 합류했습니다! (역할: {_ROLE_LABELS.get(role, role)})', 'success')
        session['current_biz_id'] = biz_id
        return redirect(url_for('main.dashboard'))

    elif mode == 'signup':
        # 신규 계정 생성 후 합류
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        name     = request.form.get('name', '').strip()

        # 초대 이메일과 불일치 방지
        if email != inv['email']:
            flash(f'초대받은 이메일({inv["email"]})로 가입해야 합니다.', 'danger')
            return redirect(url_for('auth.join_invite', token=token))
        if len(password) < 10:
            flash('비밀번호는 최소 10자', 'danger')
            return redirect(url_for('auth.join_invite', token=token))

        # 중복 이메일 확인
        dup = client.table('app_users').select('id') \
            .eq('email', email).eq('is_deleted', False).execute()
        if dup.data:
            flash('이미 가입된 이메일입니다. 로그인으로 합류하세요.', 'warning')
            return redirect(url_for('auth.join_invite', token=token))

        # 계정 생성
        user_res = client.table('app_users').insert({
            'email':         email,
            'password_hash': hash_password(password),
            'name':          name or email.split('@')[0],
            'email_verified': True,
        }).execute()
        user_id = user_res.data[0]['id']
        user = HubUser(client.table('app_users').select('*').eq('id', user_id).single().execute().data)
        login_user(user)

        biz_id, role = _apply_invite(inv, user_id)
        log_audit('team_join', detail={'mode': 'signup', 'biz_id': biz_id, 'role': role},
                  user_id=user_id, biz_id=biz_id)
        biz_name = client.table('businesses').select('name') \
            .eq('id', biz_id).single().execute().data.get('name', str(biz_id))
        flash(f'🎉 {biz_name} 팀에 합류했습니다! (역할: {_ROLE_LABELS.get(role, role)})', 'success')
        session['current_biz_id'] = biz_id
        return redirect(url_for('main.dashboard'))

    # 알 수 없는 mode
    flash('알 수 없는 요청입니다.', 'danger')
    return redirect(url_for('auth.join_invite', token=token))


# ─── 회사 선택 ───
@auth_bp.route('/select-business', methods=['GET', 'POST'])
@login_required
def select_business():
    """여러 사업자에 소속된 유저의 회사 선택 화면."""
    client = get_admin_client()

    if request.method == 'POST':
        biz_id = request.form.get('biz_id', type=int)
        if not biz_id:
            flash('회사를 선택해주세요.', 'danger')
            return redirect(url_for('auth.select_business'))
        # 해당 user가 실제로 속한 biz인지 검증
        check = client.table('user_business_map').select('id') \
            .eq('user_id', current_user.id).eq('biz_id', biz_id).limit(1).execute()
        if not check.data:
            flash('접근 권한이 없는 사업자입니다.', 'danger')
            return redirect(url_for('auth.select_business'))
        session['current_biz_id'] = biz_id
        return redirect(url_for('main.dashboard'))

    # GET: 소속 사업자 목록 조회
    ubm_rows = client.table('user_business_map').select('biz_id, role, is_primary') \
        .eq('user_id', current_user.id).execute().data or []

    if not ubm_rows:
        flash('소속된 사업자가 없습니다. 관리자에게 초대를 요청하세요.', 'warning')
        return redirect(url_for('auth.logout'))

    biz_ids = [r['biz_id'] for r in ubm_rows]
    biz_rows = client.table('businesses').select('id, name, industry') \
        .in_('id', biz_ids).execute().data or []

    ubm = {r['biz_id']: r for r in ubm_rows}

    return render_template('auth/select_business.html',
                           businesses=biz_rows,
                           ubm=ubm)
