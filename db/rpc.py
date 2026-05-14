"""
db/rpc.py — RPC 호출 표준 헬퍼.

CONVENTIONS.md 3 (화면 출력은 DB RPC 우선) 강제용.
모든 RPC 호출은 이 헬퍼 통과 → biz_id 자동 주입, 에러 표준화.
"""
import logging
from flask import g
from db.client import get_admin_client

logger = logging.getLogger(__name__)


def call_rpc(name: str, params: dict = None, biz_id: int = None, raise_on_error: bool = False):
    """RPC 호출 표준 헬퍼.

    - p_biz_id 자동 주입 (params에 없으면 g.biz_id에서)
    - 호출 실패 시 raise_on_error=True면 raise, 아니면 None 반환
    - 결과 .data 그대로 반환

    Args:
        name: RPC 함수명 (예: 'rpc_get_outbound_list')
        params: 파라미터 dict (p_xxx 형식)
        biz_id: 명시 전달 시 g.biz_id 무시
        raise_on_error: True면 예외 발생, False면 silent

    Returns:
        RPC 결과 (list 또는 dict 또는 None)
    """
    params = dict(params or {})
    if 'p_biz_id' not in params:
        bid = biz_id if biz_id is not None else getattr(g, 'biz_id', None)
        if bid:
            params['p_biz_id'] = bid

    try:
        res = get_admin_client().rpc(name, params).execute()
        return res.data
    except Exception as e:
        logger.warning(f'[RPC] {name} failed: {e}')
        if raise_on_error:
            raise
        return None


def call_rpc_dict(name: str, params: dict = None, biz_id: int = None, default: dict = None):
    """JSONB dict 반환 RPC 전용 — 결과가 dict가 아니면 default."""
    data = call_rpc(name, params, biz_id)
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return default or {}


def call_rpc_list(name: str, params: dict = None, biz_id: int = None):
    """list 반환 RPC 전용 — 결과가 list가 아니면 []."""
    data = call_rpc(name, params, biz_id)
    if isinstance(data, list):
        return data
    return []
