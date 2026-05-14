"""Supabase 클라이언트 — anon/service_role 분리.

기존 maesil-total/db_supabase.py 의 HTTP/1.1 강제 패턴 차용.
"""
import os
import logging
from supabase import create_client, Client
import httpx

logger = logging.getLogger(__name__)


def _make_http_client():
    """HTTP/1.1 강제 (Render의 일부 환경에서 HTTP/2 hang 회피)."""
    return httpx.Client(http2=False, timeout=30.0)


_anon_client = None
_admin_client = None


def get_supabase_client() -> Client:
    """Anon key 클라이언트 — RLS 적용 (사용자 컨텍스트)."""
    global _anon_client
    if _anon_client is None:
        url = os.environ.get('SUPABASE_URL', '').strip()
        key = os.environ.get('SUPABASE_KEY', '').strip()
        if not url or not key:
            raise RuntimeError('SUPABASE_URL / SUPABASE_KEY env required')
        _anon_client = create_client(url, key)
    return _anon_client


def get_admin_client() -> Client:
    """Service role 클라이언트 — RLS bypass (서버 백엔드용)."""
    global _admin_client
    if _admin_client is None:
        url = os.environ.get('SUPABASE_URL', '').strip()
        key = os.environ.get('SUPABASE_SERVICE_KEY', '').strip() or os.environ.get('SUPABASE_KEY', '').strip()
        if not url or not key:
            raise RuntimeError('SUPABASE_URL / SUPABASE_SERVICE_KEY env required')
        _admin_client = create_client(url, key)
    return _admin_client


def set_tenant_context(client: Client, biz_id: int):
    """Supabase RLS용 app.current_biz_id 세션 변수 세팅."""
    if not biz_id:
        return
    try:
        client.rpc('set_app_setting', {
            'p_key': 'app.current_biz_id',
            'p_value': str(biz_id),
        }).execute()
    except Exception as e:
        logger.warning(f'set_tenant_context failed: {e}')
