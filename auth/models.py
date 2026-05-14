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
