"""PortOne v2 server-side wrapper for maesil-hub.

Auth: `Authorization: PortOne {api_secret}` (server token; no JWT exchange).

Configuration is loaded from saas_config table via services.saas_config:
    portone_store_id        (plain)
    portone_api_secret      (encrypted)
    portone_channel_card    (plain)
    portone_channel_kakao   (plain)
    portone_webhook_secret  (encrypted)

Functions:
    - get_billing_key_info(billing_key)
    - charge_subscription(biz_id, billing_key, amount, order_name, customer, pg)
    - get_payment(payment_id)
    - cancel_payment(payment_id, reason, amount=None)
    - delete_billing_key(billing_key, reason)
    - verify_webhook(payload_bytes, headers)   ← Standard Webhooks HMAC
"""
from __future__ import annotations

import base64
import hashlib
import hmac as _hmac
import logging
import time
import uuid

import requests

from services.saas_config import get_config

logger = logging.getLogger(__name__)

_PORTONE_BASE = 'https://api.portone.io'


def _api_secret() -> str:
    return (get_config('portone_api_secret') or '').strip()


def _store_id() -> str:
    return (get_config('portone_store_id') or '').strip()


def _channel_key(pg: str) -> str:
    if pg == 'kakaopay':
        return (get_config('portone_channel_kakao') or '').strip()
    return (get_config('portone_channel_card') or '').strip()


def _headers(api_secret: str) -> dict:
    return {
        'Authorization': f'PortOne {api_secret}',
        'Content-Type': 'application/json',
    }


# ─────────────── billing-key info ───────────────

def get_billing_key_info(billing_key: str) -> dict | None:
    api_secret = _api_secret()
    if not api_secret:
        return None
    try:
        r = requests.get(
            f'{_PORTONE_BASE}/billing-keys/{billing_key}',
            headers=_headers(api_secret), timeout=8,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f'[PortOne] billing key info failed: {e}')
        return None


# ─────────────── charge ───────────────

def charge_subscription(biz_id: int, billing_key: str, amount: int,
                        order_name: str, customer: dict | None = None,
                        pg: str = 'card') -> dict:
    """Charge a subscription using a stored billing key.

    Returns dict with success/payment_id/error/data.
    """
    api_secret = _api_secret()
    if not api_secret:
        return {'success': False, 'error': 'portone api_secret missing'}
    channel_key = _channel_key(pg)
    if not channel_key:
        return {'success': False, 'error': f'channel key missing (pg={pg})'}

    payment_id = f'sub_{biz_id}_{uuid.uuid4().hex[:12]}'
    payload = {
        'storeId': _store_id(),
        'channelKey': channel_key,
        'billingKey': billing_key,
        'orderName': order_name,
        'amount': {'total': amount},
        'currency': 'KRW',
        'customer': customer or {},
    }
    try:
        r = requests.post(
            f'{_PORTONE_BASE}/payments/{payment_id}/billing-key',
            headers=_headers(api_secret), json=payload, timeout=15,
        )
        data = r.json() if r.content else {}
        payment = data.get('payment', {}) if isinstance(data, dict) else {}
        paid = r.status_code == 200 and (
            payment.get('status') == 'PAID' or payment.get('paidAt')
        )
        if paid:
            return {'success': True, 'payment_id': payment_id, 'data': data}
        err = (
            payment.get('message')
            or (payment.get('failureReason') or {}).get('message')
            or data.get('message')
            or data.get('type')
            or f'http {r.status_code}'
        )
        return {'success': False, 'payment_id': payment_id, 'error': err, 'data': data}
    except Exception as e:
        logger.error(f'[PortOne] charge failed: {e}')
        return {'success': False, 'error': str(e)}


# ─────────────── single payment lookup ───────────────

def get_payment(payment_id: str) -> dict | None:
    api_secret = _api_secret()
    if not api_secret:
        return None
    try:
        r = requests.get(
            f'{_PORTONE_BASE}/payments/{payment_id}',
            headers=_headers(api_secret), timeout=8,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f'[PortOne] get_payment failed: {e}')
        return None


# ─────────────── cancel / refund ───────────────

def cancel_payment(payment_id: str, reason: str, amount: int | None = None) -> dict:
    """Cancel (full) or refund (partial) a paid payment."""
    api_secret = _api_secret()
    if not api_secret:
        return {'success': False, 'error': 'portone api_secret missing'}
    payload: dict = {'reason': (reason or '')[:200]}
    if amount and amount > 0:
        payload['amount'] = amount
    try:
        r = requests.post(
            f'{_PORTONE_BASE}/payments/{payment_id}/cancel',
            headers=_headers(api_secret), json=payload, timeout=15,
        )
        data = r.json() if r.content else {}
        if r.status_code == 200:
            cn = data.get('cancellation') or {}
            return {
                'success': True,
                'cancellation_id': cn.get('id', ''),
                'cancelled_amount': cn.get('totalAmount', amount or 0),
                'data': data,
            }
        return {
            'success': False,
            'error': data.get('message') or data.get('type') or f'http {r.status_code}',
            'data': data,
        }
    except Exception as e:
        logger.error(f'[PortOne] cancel failed: {e}')
        return {'success': False, 'error': str(e)}


# ─────────────── billing-key delete ───────────────

def delete_billing_key(billing_key: str, reason: str = 'subscription cancelled') -> bool:
    api_secret = _api_secret()
    if not api_secret:
        return False
    try:
        r = requests.delete(
            f'{_PORTONE_BASE}/billing-keys/{billing_key}',
            headers=_headers(api_secret),
            json={'reason': (reason or '')[:200]},
            timeout=10,
        )
        return r.status_code in (200, 204)
    except Exception as e:
        logger.error(f'[PortOne] billing key delete failed: {e}')
        return False


# ─────────────── webhook verify (Standard Webhooks) ───────────────

def verify_webhook(payload_bytes: bytes, headers: dict) -> bool:
    """Verify PortOne v2 webhook signature (Standard Webhooks spec).

    Required headers (case-insensitive):
        webhook-id        - message id
        webhook-timestamp - unix epoch sec
        webhook-signature - "v1,<base64-hmac-sha256> ..."

    sigPayload = f"{id}.{ts}.{payload}"
    expected   = base64( hmac_sha256(secret, sigPayload) )

    Fail-closed: returns False if secret missing.
    """
    secret = (get_config('portone_webhook_secret') or '').strip()
    if not secret:
        logger.error('[PortOne] webhook_secret missing — verify rejected')
        return False

    if secret.startswith('whsec_'):
        try:
            secret_bytes = base64.b64decode(secret[6:])
        except Exception:
            logger.error('[PortOne] webhook secret base64 decode failed')
            return False
    else:
        secret_bytes = secret.encode()

    def _h(name):
        return (
            headers.get(name)
            or headers.get(name.title())
            or headers.get(name.lower())
            or ''
        )

    msg_id = _h('webhook-id')
    msg_ts = _h('webhook-timestamp')
    msg_sig = _h('webhook-signature')

    if not (msg_id and msg_ts and msg_sig):
        logger.warning('[PortOne] webhook headers missing')
        return False

    try:
        ts_int = int(msg_ts)
        if abs(int(time.time()) - ts_int) > 300:
            logger.warning('[PortOne] webhook timestamp expired')
            return False
    except (TypeError, ValueError):
        return False

    sig_payload = f'{msg_id}.{msg_ts}.{payload_bytes.decode("utf-8", errors="replace")}'
    expected = base64.b64encode(
        _hmac.new(secret_bytes, sig_payload.encode(), hashlib.sha256).digest()
    ).decode()

    for part in msg_sig.split():
        if ',' not in part:
            continue
        ver, sig = part.split(',', 1)
        if ver == 'v1' and _hmac.compare_digest(sig, expected):
            return True

    logger.warning('[PortOne] webhook signature mismatch')
    return False
