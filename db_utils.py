"""DB access helper — 멀티테넌트 SupabaseDB 인스턴스 제공.

레거시 blueprints/services 호환 레이어.
- get_db() 는 프로세스 전역 SupabaseDB 인스턴스를 반환 (service_role).
- 멀티테넌시 격리는 호출 시점에 biz_id 파라미터로 명시 (RLS는 service_role 우회 후
  app-level WHERE biz_id=g.biz_id 강제).

호출 패턴:
    from db_utils import get_db
    from flask import g
    rows = get_db().query_stock_ledger(date_from=..., biz_id=g.biz_id)
"""
import threading

_instance = None
_lock = threading.Lock()


def get_db():
    """현재 요청의 DB 인스턴스 (SupabaseDB) 반환. 프로세스 lifetime 싱글톤."""
    global _instance
    if _instance is not None:
        return _instance
    with _lock:
        if _instance is not None:
            return _instance
        from db_supabase import SupabaseDB
        from db.client import get_admin_client
        db = SupabaseDB()
        # admin client 직접 주입 (connect() 우회 — env 키 hub config로 이미 로드됨)
        db.client = get_admin_client()
        try:
            db._db_cols = db.get_db_columns()
        except Exception:
            db._db_cols = None
        _instance = db
        return _instance
