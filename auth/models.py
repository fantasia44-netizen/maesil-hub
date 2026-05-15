"""HubUser — Flask-Login User 모델."""
from flask_login import UserMixin
from db.client import get_admin_client


class HubUser(UserMixin):
    """app_users 테이블 1행을 감싸는 User 모델."""

    def __init__(self, row: dict):
        self._row = row or {}

    @property
    def id(self):
        return self._row.get('id')

    def get_id(self):
        return str(self.id)

    @property
    def email(self):
        return self._row.get('email', '')

    @property
    def name(self):
        return self._row.get('name', '') or self.email.split('@')[0]

    @property
    def is_super_admin(self):
        return bool(self._row.get('is_super_admin', False))

    @property
    def email_verified(self):
        return bool(self._row.get('email_verified', False))

    @property
    def is_active(self):
        if self._row.get('is_deleted'):
            return False
        return True

    @property
    def username(self):
        """감사 로그·템플릿용 표시명 (email 앞부분)."""
        return self._row.get('name') or self.email.split('@')[0]

    def is_admin(self):
        """admin 역할 여부 — 슈퍼어드민은 항상 True."""
        if self.is_super_admin:
            return True
        from flask import g
        try:
            return g.get('user_role', '') in ('admin', 'owner')
        except RuntimeError:
            return False

    @property
    def role(self):
        """현재 세션 biz의 역할. 슈퍼어드민은 항상 admin."""
        if self.is_super_admin:
            return 'admin'
        from flask import g
        try:
            return g.get('user_role', 'viewer')
        except RuntimeError:
            return 'viewer'


def load_user_by_id(user_id):
    """Flask-Login user loader."""
    if not user_id:
        return None
    try:
        client = get_admin_client()
        res = client.table('app_users').select('*') \
            .eq('id', int(user_id)).eq('is_deleted', False) \
            .single().execute()
        if res.data:
            return HubUser(res.data)
    except Exception:
        return None
    return None
