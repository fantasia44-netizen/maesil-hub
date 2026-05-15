"""Auth views — 회원가입/로그인/로그아웃/회사선택."""
import bcrypt
from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, session, jsonify, g, current_app,
)
from flask_login import login_user, logout_user, login_required, current_user

from db.client import get_admin_client
from .models import HubUser
from .helpers import log_audit
from .decorators import login_required as hub_login_required

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')


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
    next_url = _next if (_next and _is_safe_url(_next)) else url_for('main.dashboard')

    if not email or not password:
        flash('이메일/비밀번호 입력', 'danger')
        return redirect(url_for('auth.login'))

    client = get_admin_client()
    res = client.table('app_users').select('*') \
        .eq('email', email).eq('is_deleted', False).execute()

    if not res.data or not verify_password(password, res.data[0]['password_hash']):
        log_audit('login_failed', detail={'email': email})
        flash('이메일 또는 비밀번호 오류', 'danger')
        return redirect(url_for('auth.login'))

    user_row = res.data[0]
    user = HubUser(user_row)
    login_user(user)

    # primary 회사 자동 선택
    ubm = client.table('user_business_map').select('biz_id') \
        .eq('user_id', user.id).order('is_primary', desc=True).order('id').execute()
    if ubm.data:
        session['current_biz_id'] = ubm.data[0]['biz_id']

    # last_login 업데이트
    from datetime import datetime, timezone
    client.table('app_users').update({
        'last_login_at': datetime.now(timezone.utc).isoformat(),
    }).eq('id', user.id).execute()

    log_audit('login', user_id=user.id, biz_id=session.get('current_biz_id'))

    return redirect(next_url)


# ─── 로그아웃 ───
@auth_bp.route('/logout')
@login_required
def logout():
    log_audit('logout', user_id=current_user.id, biz_id=session.get('current_biz_id'))
    logout_user()
    session.clear()
    return redirect(url_for('main.index'))


# ─── 회사 선택 ───
@auth_bp.route('/select-business', methods=['GET', 'POST'])
@login_required
def select_business():
    client = get_admin_client()
    if request.method == 'POST':
        biz_id = int(request.form.get('biz_id', 0))
        # 권한 확인
        ubm = client.table('user_business_map').select('biz_id') \
            .eq('user_id', current_user.id).eq('biz_id', biz_id).execute()
        if not ubm.data:
            flash('해당 회사 권한 없음', 'danger')
            return redirect(url_for('auth.select_business'))
        session['current_biz_id'] = biz_id
        log_audit('switch_business', detail={'biz_id': biz_id})
        return redirect(url_for('main.dashboard'))

    # GET: 회사 목록
    ubm = client.table('user_business_map').select('biz_id, role, is_primary') \
        .eq('user_id', current_user.id).execute()
    biz_ids = [r['biz_id'] for r in (ubm.data or [])]
    if not biz_ids:
        # 슈퍼어드민은 회사 없어도 어드민 콘솔로
        if current_user.is_super_admin:
            return redirect(url_for('admin_saas.dashboard'))
        flash('소속된 회사가 없습니다', 'warning')
        return redirect(url_for('auth.signup'))

    bizs = client.table('businesses').select('id, name, industry, status') \
        .in_('id', biz_ids).execute()
    return render_template('auth/select_business.html',
                           businesses=bizs.data or [],
                           ubm={r['biz_id']: r for r in (ubm.data or [])})
