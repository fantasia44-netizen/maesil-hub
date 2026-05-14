"""Auth 헬퍼 — 권한 조회, 감사 로그."""
from flask import g, request
from flask_login import current_user
from db.client import get_admin_client


def get_user_role(user_id, biz_id):
    """user x biz 역할 조회 (owner/manager/staff/viewer)."""
    if not user_id or not biz_id:
        return None
    try:
        client = get_admin_client()
        res = client.table('user_business_map').select('role') \
            .eq('user_id', user_id).eq('biz_id', biz_id) \
            .single().execute()
        return res.data['role'] if res.data else None
    except Exception:
        return None


def get_current_subscription(biz_id):
    """biz_id의 현재 구독 + 플랜 정보."""
    if not biz_id:
        return None
    try:
        client = get_admin_client()
        sub_res = client.table('subscriptions').select('*') \
            .eq('biz_id', biz_id).single().execute()
        sub = sub_res.data if sub_res.data else None
        if not sub:
            return None
        plan_res = client.table('plans').select('*') \
            .eq('id', sub['plan_id']).single().execute()
        sub['plan'] = plan_res.data if plan_res.data else {}
        return sub
    except Exception:
        return None


def log_audit(action, detail=None, biz_id=None, user_id=None, operator_id=None):
    """audit_logs INSERT (예외 발생해도 silent)."""
    try:
        client = get_admin_client()
        payload = {
            'user_id': user_id or (current_user.id if current_user.is_authenticated else None),
            'biz_id': biz_id or getattr(g, 'biz_id', None),
            'operator_id': operator_id,
            'action': action,
            'detail': detail or {},
            'ip_address': (request.remote_addr if request else None),
            'user_agent': (request.user_agent.string if request and request.user_agent else None),
        }
        client.table('audit_logs').insert(payload).execute()
    except Exception:
        pass  # audit 실패는 무시
