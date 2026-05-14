"""권한 데코레이터."""
from functools import wraps
from flask import g, redirect, url_for, abort, jsonify, request
from flask_login import current_user, login_required as _flask_login_required
from .helpers import get_user_role


def login_required(f):
    """Flask-Login 그대로 + JSON 응답 분기."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': 'login required'}), 401
            return redirect(url_for('auth.login', next=request.path))
        return f(*args, **kwargs)
    return wrapper


def biz_required(f):
    """g.biz_id 세팅 필수."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login', next=request.path))
        if not getattr(g, 'biz_id', None):
            return redirect(url_for('auth.select_business'))
        return f(*args, **kwargs)
    return wrapper


def role_required(*allowed_roles):
    """현재 g.biz_id에서 사용자 역할이 allowed_roles 중 하나여야 함."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('auth.login', next=request.path))
            if current_user.is_super_admin:
                return f(*args, **kwargs)
            if not getattr(g, 'biz_id', None):
                abort(403)
            role = get_user_role(current_user.id, g.biz_id)
            if role not in allowed_roles:
                if request.is_json or request.path.startswith('/api/'):
                    return jsonify({'error': 'forbidden', 'required_roles': list(allowed_roles), 'your_role': role}), 403
                abort(403)
            return f(*args, **kwargs)
        return wrapper
    return decorator


def super_admin_required(f):
    """슈퍼어드민만."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login', next=request.path))
        if not current_user.is_super_admin:
            abort(403)
        return f(*args, **kwargs)
    return wrapper
