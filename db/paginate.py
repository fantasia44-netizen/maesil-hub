"""페이지네이션 헬퍼 — Supabase REST 1000행 limit 회피."""
from typing import Callable, List, Dict, Any


def paginate_all(query_builder: Callable[[int, int], Any], page_size: int = 1000) -> List[Dict]:
    """페이지네이션 루프로 전체 행 수집.

    Args:
        query_builder: lambda offset, page_end: <supabase query>.range(offset, page_end).execute()
        page_size: 한 페이지 크기 (기본 1000)

    Returns:
        모든 행을 합친 list

    Example:
        rows = paginate_all(
            lambda o, e: client.table('stock_ledger').select('*')
                .eq('biz_id', biz_id).eq('status', 'active')
                .range(o, e).execute()
        )
    """
    all_rows = []
    offset = 0
    while True:
        res = query_builder(offset, offset + page_size - 1)
        rows = getattr(res, 'data', None) or []
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < page_size:
            break
        offset += page_size
    return all_rows
