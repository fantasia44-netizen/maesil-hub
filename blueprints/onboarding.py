"""Onboarding wizard.

Steps:
    1. company info (already collected at signup; review/edit)
    2. plan select
    3. payment (PortOne JS SDK billing-key issue → /billing/billing-key/save)
    4. seed (skippable — sample data load placeholder)
    5. → main dashboard

Persists progress in businesses.onboarding_step / .onboarding_completed.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    jsonify, flash, g,
)
from flask_login import current_user

from auth.decorators import login_required, biz_required
from auth.helpers import log_audit
from db.client import get_admin_client

logger = logging.getLogger(__name__)

onboarding_bp = Blueprint('onboarding', __name__, url_prefix='/onboarding')


def _get_biz(client, biz_id: int):
    try:
        return client.table('businesses').select('*').eq('id', biz_id).single().execute().data
    except Exception:
        return None


def _set_step(biz_id: int, step: int, completed: bool = False):
    client = get_admin_client()
    payload = {
        'onboarding_step': step,
        'updated_at': datetime.now(timezone.utc).isoformat(),
    }
    if completed:
        payload['onboarding_completed'] = True
    try:
        client.table('businesses').update(payload).eq('id', biz_id).execute()
    except Exception as e:
        logger.error(f'[onboarding] step update failed: {e}')


@onboarding_bp.route('/')
@login_required
@biz_required
def home():
    """Resume from current step."""
    client = get_admin_client()
    biz = _get_biz(client, g.biz_id) or {}
    if biz.get('onboarding_completed'):
        return redirect(url_for('main.dashboard'))
    step = int(biz.get('onboarding_step') or 0)
    if step <= 0:
        return redirect(url_for('onboarding.step_company'))
    if step == 1:
        return redirect(url_for('onboarding.step_plan'))
    if step == 2:
        return redirect(url_for('onboarding.step_payment'))
    return redirect(url_for('onboarding.step_seed'))


@onboarding_bp.route('/company', methods=['GET', 'POST'])
@login_required
@biz_required
def step_company():
    client = get_admin_client()
    biz = _get_biz(client, g.biz_id) or {}
    if request.method == 'POST':
        try:
            client.table('businesses').update({
                'name': (request.form.get('name') or biz.get('name') or '').strip(),
                'biz_reg_no': (request.form.get('biz_reg_no') or '').strip() or None,
                'representative': (request.form.get('representative') or '').strip() or None,
                'address': (request.form.get('address') or '').strip() or None,
                'industry': (request.form.get('industry') or biz.get('industry') or 'food').strip(),
                'updated_at': datetime.now(timezone.utc).isoformat(),
            }).eq('id', g.biz_id).execute()
        except Exception as e:
            logger.error(f'[onboarding] company update failed: {e}')
            flash('save failed', 'danger')
            return redirect(url_for('onboarding.step_company'))
        _set_step(g.biz_id, 1)
        return redirect(url_for('onboarding.step_plan'))
    return render_template('onboarding/company.html', biz=biz, step=1, total=4)


@onboarding_bp.route('/plan', methods=['GET', 'POST'])
@login_required
@biz_required
def step_plan():
    client = get_admin_client()
    plans = client.table('plans').select('*') \
        .eq('is_active', True).order('sort_order').execute().data or []
    if request.method == 'POST':
        plan_code = (request.form.get('plan_code') or '').strip()
        plan = next((p for p in plans if p['code'] == plan_code), None)
        if not plan:
            flash('select a plan', 'danger')
            return redirect(url_for('onboarding.step_plan'))
        try:
            sub_q = client.table('subscriptions').select('id') \
                .eq('biz_id', g.biz_id).limit(1).execute()
            if sub_q.data:
                client.table('subscriptions').update({
                    'plan_id': plan['id'],
                    'updated_at': datetime.now(timezone.utc).isoformat(),
                }).eq('biz_id', g.biz_id).execute()
            client.table('businesses').update({
                'plan_id': plan['id'],
            }).eq('id', g.biz_id).execute()
        except Exception as e:
            logger.error(f'[onboarding] plan save failed: {e}')
        _set_step(g.biz_id, 2)
        # free plan skips payment
        if int(plan.get('monthly_price') or 0) == 0:
            _set_step(g.biz_id, 3)
            return redirect(url_for('onboarding.step_seed'))
        return redirect(url_for('onboarding.step_payment'))
    return render_template('onboarding/plan.html', plans=plans, step=2, total=4)


@onboarding_bp.route('/payment', methods=['GET'])
@login_required
@biz_required
def step_payment():
    client = get_admin_client()
    biz = _get_biz(client, g.biz_id) or {}
    sub = (client.table('subscriptions').select('*').eq('biz_id', g.biz_id)
           .limit(1).execute().data or [{}])[0]
    plan = {}
    if sub.get('plan_id'):
        plan = (client.table('plans').select('*').eq('id', sub['plan_id'])
                .single().execute().data) or {}

    portone_store_id = ''
    portone_channel_card = ''
    portone_channel_kakao = ''
    try:
        from services.saas_config import get_config
        portone_store_id = get_config('portone_store_id') or ''
        portone_channel_card = get_config('portone_channel_card') or ''
        portone_channel_kakao = get_config('portone_channel_kakao') or ''
    except Exception:
        pass

    return render_template('onboarding/payment.html',
                           biz=biz, plan=plan, subscription=sub,
                           portone_store_id=portone_store_id,
                           portone_channel_card=portone_channel_card,
                           portone_channel_kakao=portone_channel_kakao,
                           step=3, total=4)


@onboarding_bp.route('/payment/done', methods=['POST'])
@login_required
@biz_required
def payment_done():
    """Marker the SPA hits after a successful PortOne issue + /billing/billing-key/save."""
    _set_step(g.biz_id, 3)
    return jsonify({'status': 'ok', 'next': url_for('onboarding.step_seed')})


@onboarding_bp.route('/seed', methods=['GET', 'POST'])
@login_required
@biz_required
def step_seed():
    if request.method == 'POST':
        action = (request.form.get('action') or 'skip').strip()
        # actual seed logic is out of scope here; placeholder for future
        if action == 'load':
            log_audit('onboarding_seed_requested')
        _set_step(g.biz_id, 4, completed=True)
        log_audit('onboarding_completed')
        flash('onboarding complete', 'success')
        return redirect(url_for('main.dashboard'))
    return render_template('onboarding/seed.html', step=4, total=4)


@onboarding_bp.route('/skip')
@login_required
@biz_required
def skip_all():
    _set_step(g.biz_id, 4, completed=True)
    log_audit('onboarding_skipped')
    return redirect(url_for('main.dashboard'))
