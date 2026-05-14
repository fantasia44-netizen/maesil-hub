"""
product_lookup.py — 품목 식별 표준 (SKU 우선, canonical name 보조).

CONVENTIONS.md "품목 식별" 표준 구현.

탐색 우선순위:
  1. sku 정확 일치
  2. barcode 정확 일치
  3. canonical(product_name) 정확 일치
  4. canonical(product_name) 부분 일치 (ILIKE %canonical%)

모든 비교는 canonical() 통과 — 띄어쓰기/대소문자/특수공백 무관.
"""
import logging
from typing import Optional, List, Dict, Any
from db.client import get_admin_client
from services.product_name import canonical

logger = logging.getLogger(__name__)


def find_product(biz_id: int, query: str) -> Optional[Dict[str, Any]]:
    """단일 품목 찾기 — SKU/barcode/이름 자동 식별.

    Args:
        biz_id: 테넌트
        query: 사용자 입력 (SKU 번호, 바코드, 또는 품목명)

    Returns:
        product_costs 1행 dict, 없으면 None.

    탐색 순서:
        1. sku 정확 일치
        2. barcode 정확 일치
        3. canonical(product_name) 정확 일치
        4. canonical(product_name) 부분 일치 (단일 결과만)
    """
    if not query or not biz_id:
        return None
    q = str(query).strip()
    if not q:
        return None

    client = get_admin_client()
    base = client.table('product_costs').select('*') \
        .eq('biz_id', biz_id).eq('is_deleted', False)

    # 1) SKU 정확 일치
    res = base.eq('sku', q).limit(1).execute()
    if res.data:
        return res.data[0]

    # 2) barcode 정확 일치
    res = base.eq('barcode', q).limit(1).execute()
    if res.data:
        return res.data[0]

    # 3) canonical 이름 정확 일치
    canon = canonical(q)
    if not canon:
        return None
    res = base.eq('product_name', canon).limit(1).execute()
    if res.data:
        return res.data[0]

    # 4) canonical 부분 일치 (단일이면 채택)
    res = base.ilike('product_name', f'%{canon}%').limit(2).execute()
    if res.data and len(res.data) == 1:
        return res.data[0]

    return None


def search_products(biz_id: int, query: str, limit: int = 20) -> List[Dict[str, Any]]:
    """다건 검색 — 자동완성/검색박스용.

    SKU/barcode/canonical 이름으로 동시 검색하여 합집합 반환.
    """
    if not query or not biz_id:
        return []
    q = str(query).strip()
    if not q:
        return []

    client = get_admin_client()
    base = client.table('product_costs').select('*') \
        .eq('biz_id', biz_id).eq('is_deleted', False)

    results = {}

    # SKU 시작 일치
    try:
        r = base.ilike('sku', f'{q}%').limit(limit).execute()
        for row in (r.data or []):
            results[row['id']] = row
    except Exception:
        pass

    # barcode 시작 일치
    try:
        r = base.ilike('barcode', f'{q}%').limit(limit).execute()
        for row in (r.data or []):
            results[row['id']] = row
    except Exception:
        pass

    # canonical 부분 일치 (공백 제거 후 검색)
    canon = canonical(q)
    if canon:
        try:
            r = base.ilike('product_name', f'%{canon}%').limit(limit).execute()
            for row in (r.data or []):
                results[row['id']] = row
        except Exception:
            pass

    rows = list(results.values())
    rows.sort(key=lambda x: (x.get('sku') or '', x.get('product_name') or ''))
    return rows[:limit]


def resolve_or_create(biz_id: int, query: str, defaults: dict = None) -> Dict[str, Any]:
    """품목 식별 후 없으면 자동 생성 (옵션매핑 자동 등록 등).

    Args:
        biz_id: 테넌트
        query: 입력 (이름 또는 SKU)
        defaults: 신규 생성 시 기본값 (cost_price 등)

    Returns:
        product_costs 1행 (기존 또는 신규)
    """
    found = find_product(biz_id, query)
    if found:
        return found

    canon = canonical(query)
    if not canon:
        raise ValueError('빈 품목명')

    payload = dict(defaults or {})
    payload.update({
        'biz_id': biz_id,
        'product_name': canon,
    })
    client = get_admin_client()
    res = client.table('product_costs').insert(payload).execute()
    if res.data:
        logger.info(f'[product_lookup] auto-created: biz={biz_id} name={canon}')
        return res.data[0]
    raise RuntimeError(f'auto-create failed for {canon}')


def normalize_for_storage(name: str) -> str:
    """저장 직전 항상 호출 — canonical 강제."""
    return canonical(name)
