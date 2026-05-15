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

import logging
from datetime import datetime, timezone

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    jsonify, flash, session, abort,
)
from flask_login import current_user

from auth.decorators import super_admin_required, login_required
from auth.helpers import log_audit
from db.client import get_admin_client

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
