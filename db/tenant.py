"""멀티테넌트 안전 가드 — SupabaseDB 메서드 호출 시 biz_id 자동 주입.

목적:
- 레거시 blueprints/services가 `get_db().query_xxx(...)` 형태로 biz_id 없이
  호출해도, 함수 시그니처에 `biz_id` kwarg가 있으면 자동으로 g.biz_id를 채워
  사업자 간 데이터 누출을 차단한다.

원칙:
- 호출자가 명시적으로 biz_id를 전달하면 그대로 통과 (운영자 임퍼소네이션 등).
- 호출자가 전달하지 않았고 g.biz_id가 있으면 → 자동 주입.
- g.biz_id가 없으면 → 그대로 통과 (배치 스크립트, 어드민 통계 등).

사용:
- app.create_app() 시작 시 install_tenant_guard() 1회 호출.
"""
import inspect
from functools import wraps

try:
    from flask import g, has_request_context
except Exception:  # 배치 스크립트에서 Flask 없이 임포트되는 경우
    has_request_context = lambda: False
    g = None


_INSTALLED = False


def _wrap(method):
    sig = inspect.signature(method)
    if 'biz_id' not in sig.parameters:
        return method  # biz_id를 받지 않는 메서드는 그대로

    @wraps(method)
    def wrapper(self, *args, **kwargs):
        if 'biz_id' not in kwargs:
            try:
                if has_request_context() and getattr(g, 'biz_id', None) is not None:
                    kwargs['biz_id'] = g.biz_id
            except Exception:
                pass
        return method(self, *args, **kwargs)
    wrapper.__wrapped__ = method
    return wrapper


def install_tenant_guard():
    """SupabaseDB 클래스의 모든 public 메서드를 biz_id 자동 주입 wrapper로 교체."""
    global _INSTALLED
    if _INSTALLED:
        return
    from db_supabase import SupabaseDB
    for name, val in list(vars(SupabaseDB).items()):
        if name.startswith('_'):
            continue
        if not callable(val):
            continue
        try:
            wrapped = _wrap(val)
            if wrapped is not val:
                setattr(SupabaseDB, name, wrapped)
        except (TypeError, ValueError):
            pass
    _INSTALLED = True
