"""Billing blueprint — subscribe / billing-key / webhook / cancel / history.

Routes:
    GET  /billing                       — current plan + plan picker
    GET  /billing/history               — payment history page
    POST /billing/billing-key/save      — store billing key from PortOne JS SDK
    POST /billing/billing-key/delete    — remove billing key (PortOne + DB)
    POST /billing/subscribe             — change plan (charge via billingKey)
    POST /billing/cancel                — cancel auto-renewal (effective at period end)
    POST /billing/refund                — request refund (auto if < 7 days, else manual)
    POST /billing/webhook               — PortOne v2 webhook receiver
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone, timedelta

from flask import (
    Blueprint, render_template, request, jsonify, current_app, g,
)
from flask_login import current_user

from auth.decorators import login_required, biz_required
from auth.helpers import log_audit
from db.client import get_admin_client

logger = logging.getLogger(__name__)

billing_bp = Blueprint('billing', __name__, url_prefix='/billing')

# VAT: display amount = supply + tax(10%); tax = amount / 11
_VAT_DIVISOR = 11.0


def _split_vat(amount: int) -> tuple[int, int]:
    if amount <= 0:
        return 0, 0
    tax = round(amount / _VAT_DIVISOR)
    return amount - tax, tax


def _get_subscription(client, biz_id: int) -> dict | None:
    try:
        res = client.table('subscriptions').select('*') \
            .eq('biz_id', biz_id).limit(1).execute()
        return (res.data or [None])[0]
    except Exception:
        return None


def _get_business(client, biz_id: int) -> dict | None:
    try:
        return client.table('businesses').select('*') \
            .eq('id', biz_id).single().execute().data
    except Exception:
        return None


# ─────────────── pages ───────────────

@billing_bp.route('/')
@login_required
@biz_required
def billing_home():
    client = get_admin_client()
    biz = _get_business(client, g.biz_id) or {}
    sub = _get_subscription(client, g.biz_id) or {}
    plans = client.table('plans').select('*') \
        .eq('is_active', True).order('sort_order').execute().data or []

    cur_plan = {}
    if sub.get('plan_id'):
        for p in plans:
            if p['id'] == sub['plan_id']:
                cur_plan = p
                break

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

    return render_template(
        'billing/subscribe.html',
        biz=biz, subscription=sub, current_plan=cur_plan, plans=plans,
        portone_store_id=portone_store_id,
        portone_channel_card=portone_channel_card,
        portone_channel_kakao=portone_channel_kakao,
    )


@billing_bp.route('/history')
@login_required
@biz_required
def billing_history():
    client = get_admin_client()
    biz = _get_business(client, g.biz_id) or {}
    sub = _get_subscription(client, g.biz_id) or {}
    payments = []
    try:
        res = client.table('payments').select('*') \
            .eq('biz_id', g.biz_id).order('created_at', desc=True) \
            .limit(50).execute()
        payments = res.data or []
    except Exception:
        pass
    return render_template('billing/history.html',
                           biz=biz, subscription=sub, payments=payments)


# ─────────────── billing-key save / delete ───────────────

@billing_bp.route('/billing-key/save', methods=['POST'])
@login_required
@biz_required
def save_billing_key():
    """Persist a billingKey returned by PortOne JS SDK to subscriptions row."""
    data = request.get_json(force=True) or {}
    billing_key = (data.get('billing_key') or '').strip()
    pg = (data.get('pg') or 'card').strip().lower()
    if pg not in ('card', 'kakaopay'):
        pg = 'card'
    if not billing_key:
        return jsonify({'status': 'error', 'message': 'billing_key required'}), 400

    client = get_admin_client()

    # fetch card metadata
    card_info: dict = {}
    try:
        from services.portone import get_billing_key_info
        info = get_billing_key_info(billing_key) or {}
        methods = info.get('methods') or []
        if methods:
            m = methods[0] or {}
            if pg == 'kakaopay':
                card_info = {'pg': 'kakaopay', 'label': 'KakaoPay'}
            else:
                card = m.get('card') or {}
                card_info = {
                    'pg': 'card',
                    'brand': card.get('brand', ''),
                    'last4': (card.get('number', '') or '')[-4:] if card.get('number') else '',
                    'expiry': f"{card.get('expiryYear', '')}/{card.get('expiryMonth', '')}",
                }
    except Exception as e:
        logger.warning(f'[Billing] card info fetch failed: {e}')

    try:
        # ensure subscription row exists, then update
        sub = _get_subscription(client, g.biz_id)
        if not sub:
            # create empty trial-style subscription pointing at first active plan
            plans = client.table('plans').select('id').eq('is_active', True) \
                .order('sort_order').limit(1).execute().data or []
            if not plans:
                return jsonify({'status': 'error', 'message': 'no plans available'}), 500
            now = datetime.now(timezone.utc)
            client.table('subscriptions').insert({
                'biz_id': g.biz_id,
                'plan_id': plans[0]['id'],
                'status': 'trial',
                'current_period_start': now.isoformat(),
                'current_period_end': (now + timedelta(days=14)).isoformat(),
                'portone_billing_key': billing_key,
                'billing_key_pg': pg,
                'card_info': card_info,
            }).execute()
        else:
            client.table('subscriptions').update({
                'portone_billing_key': billing_key,
                'billing_key_pg': pg,
                'card_info': card_info,
                'updated_at': datetime.now(timezone.utc).isoformat(),
            }).eq('biz_id', g.biz_id).execute()

        log_audit('billing_key_saved', detail={'pg': pg})
        return jsonify({'status': 'ok', 'card_info': card_info, 'pg': pg})
    except Exception as e:
        logger.error(f'[Billing] save billing key failed: {e}')
        return jsonify({'status': 'error', 'message': 'save failed'}), 500


@billing_bp.route('/billing-key/delete', methods=['POST'])
@login_required
@biz_required
def delete_billing_key_route():
    from services.portone import delete_billing_key as _del_pone
    client = get_admin_client()
    sub = _get_subscription(client, g.biz_id)
    bk = (sub or {}).get('portone_billing_key')
    if bk:
        try:
            _del_pone(bk, reason='user requested')
        except Exception as e:
            logger.warning(f'[Billing] PortOne delete failed (continuing): {e}')
    try:
        client.table('subscriptions').update({
            'portone_billing_key': None,
            'billing_key_pg': None,
            'card_info': None,
            'auto_renewal': False,
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }).eq('biz_id', g.biz_id).execute()
        log_audit('billing_key_deleted')
        return jsonify({'status': 'ok'})
    except Exception as e:
        logger.error(f'[Billing] delete billing key DB failed: {e}')
        return jsonify({'status': 'error', 'message': 'delete failed'}), 500


# ─────────────── subscribe / change plan ───────────────

@billing_bp.route('/subscribe', methods=['POST'])
@login_required
@biz_required
def subscribe():
    """Change plan & charge via billing key.

    Body: { plan_code: "pro" }
    """
    from services.portone import charge_subscription
    from dateutil.relativedelta import relativedelta

    data = request.get_json(force=True) or {}
    plan_code = (data.get('plan_code') or '').strip()
    if not plan_code:
        return jsonify({'status': 'error', 'message': 'plan_code required'}), 400

    client = get_admin_client()
    plan = client.table('plans').select('*') \
        .eq('code', plan_code).eq('is_active', True).single().execute().data
    if not plan:
        return jsonify({'status': 'error', 'message': 'invalid plan'}), 400

    biz = _get_business(client, g.biz_id) or {}
    sub = _get_subscription(client, g.biz_id) or {}
    billing_key = (sub.get('portone_billing_key') or '').strip()
    if not billing_key:
        return jsonify({
            'status': 'error', 'need_card': True,
            'message': 'register a payment method first',
        }), 400

    pg = (sub.get('billing_key_pg') or 'card').lower()
    amount = int(plan.get('monthly_price') or 0)
    order_name = f"maesil-hub {plan.get('name', plan_code)}"

    # zero-amount plan (free): just flip plan, no charge
    payment_id = None
    pay_data = None
    if amount > 0:
        result = charge_subscription(
            biz_id=g.biz_id, billing_key=billing_key,
            amount=amount, order_name=order_name,
            customer={
                'customerId': str(g.biz_id),
                'fullName': biz.get('name') or '',
            },
            pg=pg,
        )
        if not result.get('success'):
            err = result.get('error') or 'charge failed'
            log_audit('subscribe_failed', detail={'plan': plan_code, 'err': err})
            return jsonify({
                'status': 'error',
                'message': f'payment failed: {err}',
            }), 400
        payment_id = result.get('payment_id')
        pay_data = result.get('data')

    now = datetime.now(timezone.utc)
    period_end = (now + relativedelta(months=1)).isoformat()

    try:
        client.table('subscriptions').upsert({
            'biz_id': g.biz_id,
            'plan_id': plan['id'],
            'status': 'active',
            'auto_renewal': True,
            'current_period_start': now.isoformat(),
            'current_period_end': period_end,
            'next_billing_at': period_end,
            'failed_attempt_count': 0,
            'last_retry_at': None,
            'cancelled_at': None,
            'cancel_reason': None,
            'portone_billing_key': billing_key,
            'billing_key_pg': pg,
            'updated_at': now.isoformat(),
        }, on_conflict='biz_id').execute()

        client.table('businesses').update({
            'plan_id': plan['id'],
            'subscription_status': 'active',
            'updated_at': now.isoformat(),
        }).eq('id', g.biz_id).execute()

        if payment_id and amount > 0:
            supply, tax = _split_vat(amount)
            client.table('payments').insert({
                'biz_id': g.biz_id,
                'subscription_id': None,  # set by trigger or future query
                'portone_payment_id': payment_id,
                'portone_merchant_uid': payment_id,
                'amount': amount,
                'supply_amount': supply,
                'vat_amount': tax,
                'order_name': order_name,
                'payment_type': 'subscription',
                'status': 'paid',
                'raw_response': pay_data,
                'paid_at': now.isoformat(),
            }).execute()

        log_audit('subscribe', detail={
            'plan_code': plan_code, 'amount': amount, 'payment_id': payment_id,
        })
        return jsonify({
            'status': 'ok',
            'payment_id': payment_id,
            'amount': amount,
            'period_end': period_end,
        })
    except Exception as e:
        logger.error(f'[Billing] subscribe DB write failed biz={g.biz_id}: {e}')
        return jsonify({
            'status': 'error',
            'message': 'payment succeeded but DB update failed; contact support',
            'payment_id': payment_id,
        }), 500


# ─────────────── cancel auto-renewal ───────────────

@billing_bp.route('/cancel', methods=['POST'])
@login_required
@biz_required
def cancel_subscription():
    data = request.get_json(force=True) or {}
    reason = (data.get('reason') or '')[:200]
    client = get_admin_client()
    try:
        now = datetime.now(timezone.utc).isoformat()
        client.table('subscriptions').update({
            'auto_renewal': False,
            'cancel_reason': reason,
            'cancelled_at': now,
            'updated_at': now,
        }).eq('biz_id', g.biz_id).execute()
        log_audit('subscription_cancelled', detail={'reason': reason})
        return jsonify({'status': 'ok'})
    except Exception as e:
        logger.error(f'[Billing] cancel failed: {e}')
        return jsonify({'status': 'error', 'message': 'cancel failed'}), 500


# ─────────────── refund (auto < 7 days, else manual) ───────────────

@billing_bp.route('/refund', methods=['POST'])
@login_required
@biz_required
def refund():
    from services.portone import cancel_payment as _cancel
    data = request.get_json(force=True) or {}
    payment_id = (data.get('payment_id') or '').strip()
    reason = (data.get('reason') or '')[:500]
    refund_amount = int(data.get('amount') or 0)
    if not reason:
        return jsonify({'status': 'error', 'message': 'reason required'}), 400

    client = get_admin_client()
    now_utc = datetime.now(timezone.utc)

    pay = None
    if payment_id:
        try:
            pay_res = client.table('payments').select('*') \
                .eq('portone_payment_id', payment_id) \
                .eq('biz_id', g.biz_id).limit(1).execute()
            pay = (pay_res.data or [None])[0]
        except Exception as e:
            logger.warning(f'[Refund] lookup failed: {e}')

    auto_ok = False
    if pay and pay.get('status') == 'paid' and not pay.get('refund_status'):
        try:
            from dateutil.parser import parse as _parse
            paid_dt = _parse(pay.get('paid_at')) if pay.get('paid_at') else None
            if paid_dt and (now_utc - paid_dt).days <= 7:
                auto_ok = True
        except Exception:
            pass

    if auto_ok and pay:
        original = int(pay.get('amount') or 0)
        cancel_amt = refund_amount if (0 < refund_amount < original) else None
        result = _cancel(payment_id, reason, amount=cancel_amt)
        if result.get('success'):
            cancelled = int(result.get('cancelled_amount') or original)
            try:
                client.table('payments').update({
                    'refund_status': 'completed',
                    'refund_reason': reason,
                    'refund_amount': cancelled,
                    'refund_payment_id': result.get('cancellation_id'),
                    'refund_requested_at': now_utc.isoformat(),
                    'refunded_at': now_utc.isoformat(),
                    'updated_at': now_utc.isoformat(),
                }).eq('portone_payment_id', payment_id) \
                  .eq('biz_id', g.biz_id).execute()
            except Exception as e:
                logger.error(f'[Refund] DB update failed: {e}')
            log_audit('refund_auto', detail={
                'payment_id': payment_id, 'amount': cancelled,
            })
            return jsonify({'status': 'ok', 'auto': True, 'refund_amount': cancelled})

        # auto refund failed → fall through to manual
        try:
            client.table('payments').update({
                'refund_status': 'requested',
                'refund_reason': f'[auto failed: {result.get("error", "")[:120]}] {reason}',
                'refund_amount': refund_amount or original,
                'refund_requested_at': now_utc.isoformat(),
                'updated_at': now_utc.isoformat(),
            }).eq('portone_payment_id', payment_id) \
              .eq('biz_id', g.biz_id).execute()
        except Exception:
            pass
        log_audit('refund_manual', detail={'payment_id': payment_id})
        return jsonify({'status': 'ok', 'auto': False})

    # manual review path
    try:
        if pay:
            client.table('payments').update({
                'refund_status': 'requested',
                'refund_reason': reason,
                'refund_amount': refund_amount or None,
                'refund_requested_at': now_utc.isoformat(),
                'updated_at': now_utc.isoformat(),
            }).eq('portone_payment_id', payment_id) \
              .eq('biz_id', g.biz_id).execute()
        else:
            # standalone refund request (no payment match)
            req_id = f'refund_req_{uuid.uuid4().hex[:12]}'
            client.table('payments').insert({
                'biz_id': g.biz_id,
                'portone_payment_id': req_id,
                'portone_merchant_uid': req_id,
                'amount': refund_amount,
                'order_name': 'refund request (manual)',
                'payment_type': 'refund_request',
                'status': 'refund_requested',
                'refund_status': 'requested',
                'refund_reason': reason,
                'refund_requested_at': now_utc.isoformat(),
            }).execute()
        log_audit('refund_manual', detail={'payment_id': payment_id, 'reason': reason})
    except Exception as e:
        logger.error(f'[Refund] manual record failed: {e}')

    return jsonify({'status': 'ok', 'auto': False})


# ─────────────── webhook ───────────────

@billing_bp.route('/webhook', methods=['POST'])
def webhook():
    """PortOne v2 webhook. HMAC verified via Standard Webhooks headers."""
    from services.portone import verify_webhook
    raw = request.get_data() or b''
    headers = {k: v for k, v in request.headers.items()}
    if not verify_webhook(raw, headers):
        logger.warning(f'[Webhook] signature failed ip={request.remote_addr}')
        return jsonify({'status': 'unauthorized'}), 401

    try:
        payload = request.get_json(force=True) or {}
    except Exception:
        return jsonify({'status': 'bad_payload'}), 400

    tx_type = payload.get('type', '')
    logger.info(f'[Webhook] received type={tx_type}')

    if tx_type == 'Transaction.Paid':
        _handle_paid(payload.get('data', {}))
    elif tx_type == 'Transaction.Failed':
        _handle_failed(payload.get('data', {}))
    elif tx_type == 'BillingKey.Issued':
        logger.info(f'[Webhook] BillingKey.Issued: {payload.get("data", {})}')

    return jsonify({'status': 'ok'})


def _extract_biz_id(payment_id: str) -> int | None:
    """payment_id format: sub_{biz_id}_{hex}"""
    try:
        parts = payment_id.split('_')
        if len(parts) >= 3 and parts[0] == 'sub':
            return int(parts[1])
    except Exception:
        return None
    return None


def _handle_paid(data: dict):
    payment_id = data.get('paymentId') or data.get('id', '')
    biz_id = _extract_biz_id(payment_id)
    if not biz_id:
        logger.warning(f'[Webhook] biz_id parse failed: {payment_id}')
        return

    amount = 0
    if isinstance(data.get('amount'), dict):
        amount = int(data['amount'].get('total', 0) or 0)
    pg_provider = ''
    if isinstance(data.get('channel'), dict):
        pg_provider = data['channel'].get('pgProvider', '')
    receipt_url = data.get('receiptUrl', '')
    method_str = ''
    try:
        method = data.get('method') or {}
        card = method.get('card') or {}
        method_str = f"{card.get('brand', '')} *{(card.get('number', '') or '')[-4:]}"
    except Exception:
        pass

    supply, tax = _split_vat(amount)
    now = datetime.now(timezone.utc).isoformat()

    client = get_admin_client()
    try:
        client.table('payments').upsert({
            'biz_id': biz_id,
            'portone_payment_id': payment_id,
            'portone_merchant_uid': payment_id,
            'amount': amount,
            'supply_amount': supply,
            'vat_amount': tax,
            'order_name': data.get('orderName', 'subscription'),
            'method': method_str,
            'status': 'paid',
            'pg_provider': pg_provider,
            'receipt_url': receipt_url,
            'raw_response': data,
            'paid_at': data.get('paidAt') or now,
            'updated_at': now,
        }, on_conflict='portone_payment_id').execute()

        client.table('businesses').update({
            'subscription_status': 'active', 'status': 'active',
        }).eq('id', biz_id).execute()
        client.table('subscriptions').update({
            'status': 'active',
            'failed_attempt_count': 0,
            'last_retry_at': None,
        }).eq('biz_id', biz_id).execute()

        logger.info(f'[Webhook] paid biz={biz_id} amt={amount}')
    except Exception as e:
        logger.error(f'[Webhook] _handle_paid failed: {e}')


def _handle_failed(data: dict):
    payment_id = data.get('paymentId') or data.get('id', '')
    biz_id = _extract_biz_id(payment_id)
    now = datetime.now(timezone.utc)
    client = get_admin_client()
    try:
        client.table('payments').upsert({
            'biz_id': biz_id or 0,
            'portone_payment_id': payment_id,
            'portone_merchant_uid': payment_id,
            'amount': 0,
            'order_name': data.get('orderName', ''),
            'status': 'failed',
            'raw_response': data,
            'failed_at': now.isoformat(),
            'updated_at': now.isoformat(),
        }, on_conflict='portone_payment_id').execute()

        if not biz_id:
            return
        sub = _get_subscription(client, biz_id) or {}
        cur = int(sub.get('failed_attempt_count') or 0)
        new_attempt = cur + 1
        retry_at = (now + timedelta(days=3)).isoformat()
        if new_attempt >= 3:
            client.table('subscriptions').update({
                'status': 'cancelled',
                'auto_renewal': False,
                'failed_attempt_count': new_attempt,
                'last_retry_at': now.isoformat(),
                'cancelled_at': now.isoformat(),
                'cancel_reason': 'payment failed 3 times',
            }).eq('biz_id', biz_id).execute()
            client.table('businesses').update({
                'subscription_status': 'past_due',
                'status': 'suspended',
            }).eq('id', biz_id).execute()
            logger.warning(f'[Webhook] biz={biz_id} suspended after 3 failures')
        else:
            client.table('subscriptions').update({
                'status': 'past_due',
                'failed_attempt_count': new_attempt,
                'last_retry_at': now.isoformat(),
                'next_billing_at': retry_at,
            }).eq('biz_id', biz_id).execute()
            client.table('businesses').update({
                'subscription_status': 'past_due',
            }).eq('id', biz_id).execute()
    except Exception as e:
        logger.error(f'[Webhook] _handle_failed: {e}')
