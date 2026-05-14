"""송장출고 기준 일자별 완제품 수량 집계 서비스.

기존 `get_channel_orders_agg`(주문수집 기준)와 대조하기 위한 B집계:
  - order_shipping.invoice_no IS NOT NULL  (송장 발행 확정)
  - JOIN order_transactions (channel, order_no)
  - option_master에 등록된 완제품(product_name)만 포함
  - 일자축: order_transactions.collection_date (없으면 order_date)
"""
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


def _channel_group(channel):
    """aggregation.py 와 동일 규칙."""
    ch = (channel or '').strip()
    if ch in ('N배송_수동', 'N배송'):
        return 'N배송'
    if ch == '쿠팡':
        return '쿠팡매출'
    return '일반매출'


def _fetch_completed_product_names(db):
    """option_master에 등록된 완제품명 집합 반환."""
    try:
        rows = db.query_option_master(use_cache=True) or []
    except Exception as e:
        logger.warning(f'[InvoiceAgg] option_master 조회 실패: {e}')
        return set()
    names = set()
    for r in rows:
        pn = (r.get('product_name') or '').strip()
        if pn:
            names.add(pn)
    return names


def _fetch_invoiced_order_keys(db, date_from, date_to):
    """기간 내 송장 발행된 (channel, order_no) 집합.

    기준일: order_shipping.created_at (송장 발행 시점, YYYY-MM-DD로 추출).
    기간은 넓게 조회 후 Python에서 필터 (order_transactions의 collection_date는
    더 늦을 수 있으므로 송장 기준 기간에서 마진 필요 → ±14일 여유).
    """
    keys = set()
    # 넓게 조회: 기간 시작 14일 전 ~ 종료 14일 후 (collection_date 지연 커버)
    from datetime import datetime, timedelta
    try:
        d0 = datetime.strptime(date_from, '%Y-%m-%d') - timedelta(days=14)
        d1 = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=14)
        from_ts = d0.strftime('%Y-%m-%dT00:00:00')
        to_ts = d1.strftime('%Y-%m-%dT23:59:59')
    except Exception:
        from_ts = f'{date_from}T00:00:00'
        to_ts = f'{date_to}T23:59:59'

    offset = 0
    while True:
        res = (db.client.table('order_shipping')
               .select('channel,order_no,invoice_no,created_at')
               .not_.is_('invoice_no', 'null')
               .neq('invoice_no', '')
               .gte('created_at', from_ts)
               .lte('created_at', to_ts)
               .order('id')
               .range(offset, offset + 999).execute())
        rows = res.data or []
        if not rows:
            break
        for r in rows:
            ch = (r.get('channel') or '').strip()
            no = (r.get('order_no') or '').strip()
            if ch and no:
                keys.add((ch, no))
        if len(rows) < 1000:
            break
        offset += 1000
    return keys


def _fetch_order_transactions(db, date_from, date_to):
    """기간 내 order_transactions 조회 (collection_date 또는 order_date 기준)."""
    rows = []
    offset = 0
    while True:
        res = (db.client.table('order_transactions')
               .select('channel,order_no,product_name,qty,status,'
                       'order_date,collection_date')
               .gte('order_date', date_from)
               .lte('order_date', date_to)
               .order('id')
               .range(offset, offset + 999).execute())
        chunk = res.data or []
        if not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        offset += 1000
    return rows


def get_invoice_daily_aggregate(db, date_from, date_to):
    """송장출고 기준 일자×그룹 수량 집계.

    Returns:
        dict: {
            'rows': [
                {'date': 'YYYY-MM-DD',
                 'groups': {'일반매출': n, '쿠팡매출': n, 'N배송': n},
                 'total': n},
                ...
            ],
            'totals': {'일반매출': n, '쿠팡매출': n, 'N배송': n, '합계': n},
        }
    """
    completed = _fetch_completed_product_names(db)
    invoiced_keys = _fetch_invoiced_order_keys(db, date_from, date_to)
    ot_rows = _fetch_order_transactions(db, date_from, date_to)

    # (date, group) → qty
    agg = defaultdict(int)
    # (date, product_name) → qty (drilldown 용)
    prod_agg = defaultdict(int)
    unmatched_products = defaultdict(int)  # option_master에 없는 것들

    for r in ot_rows:
        status = (r.get('status') or '').strip()
        if status == '취소' or status == 'cancelled':
            continue
        ch = (r.get('channel') or '').strip()
        no = (r.get('order_no') or '').strip()
        if (ch, no) not in invoiced_keys:
            continue

        pn = (r.get('product_name') or '').strip()
        if not pn:
            continue
        # 완제품 필터 — option_master에 등록된 것만
        if completed and pn not in completed:
            qty = int(r.get('qty') or 0)
            unmatched_products[pn] += qty
            continue

        qty = int(r.get('qty') or 0)
        if qty == 0:
            continue

        d = (r.get('collection_date') or r.get('order_date') or '').strip()
        if not d:
            continue
        d = d[:10]
        if d < date_from or d > date_to:
            continue

        grp = _channel_group(ch)
        agg[(d, grp)] += qty
        prod_agg[(d, pn)] += qty

    # 출력용 rows 구성
    groups = ['일반매출', '쿠팡매출', 'N배송']
    all_dates = sorted(set(d for (d, _) in agg.keys()))
    rows = []
    totals = {g: 0 for g in groups}
    totals['합계'] = 0

    for d in all_dates:
        row = {'date': d}
        row_total = 0
        for g in groups:
            v = agg.get((d, g), 0)
            row[g] = v
            row_total += v
            totals[g] += v
        row['합계'] = row_total
        totals['합계'] += row_total
        rows.append(row)

    # 품목별 drilldown: {date: [{product, qty}, ...]}
    product_by_date = defaultdict(list)
    for (d, pn), q in prod_agg.items():
        product_by_date[d].append({'product_name': pn, 'qty': q})
    for d in product_by_date:
        product_by_date[d].sort(key=lambda x: -x['qty'])

    return {
        'rows': rows,
        'totals': totals,
        'product_by_date': dict(product_by_date),
        'unmatched_products': dict(unmatched_products),
        'invoiced_orders': len(invoiced_keys),
        'product_master_count': len(completed),
    }


def compare_invoice_vs_order(db, date_from, date_to):
    """주문수집 통합집계(A) vs 송장출고 기준(B) 비교.

    Returns:
        dict: {
            'date_from', 'date_to',
            'dates': ['YYYY-MM-DD', ...],
            'comparison': [
                {'date': d,
                 'order_total': A,      # 주문수집 합계
                 'invoice_total': B,    # 송장출고 합계
                 'diff': A-B,
                 'order_by_group': {...}, 'invoice_by_group': {...}}
            ],
            'total_order': A총합,
            'total_invoice': B총합,
            'total_diff': A총합 - B총합,
            'invoice_detail': {...},  # 드릴다운용
        }
    """
    # B: 송장출고 집계
    inv = get_invoice_daily_aggregate(db, date_from, date_to)

    # A: 주문수집 집계 (기존 RPC)
    try:
        res = db.client.rpc('get_channel_orders_agg', {
            'p_date_from': date_from, 'p_date_to': date_to,
        }).execute()
        a_json = res.data or {}
        if isinstance(a_json, list):
            a_json = a_json[0] if a_json else {}
        a_raw_rows = a_json.get('rows') or []
    except Exception as e:
        logger.warning(f'[InvoiceAgg] 주문수집 RPC 실패: {e}')
        a_raw_rows = []

    # A rows를 일자별 dict로
    a_by_date = {}
    for rr in a_raw_rows:
        d = rr.get('date', '')
        g_map = rr.get('groups') or {}
        total = sum(int(v or 0) for v in g_map.values())
        a_by_date[d] = {'groups': {k: int(v or 0) for k, v in g_map.items()},
                        'total': total}

    # B rows를 일자별 dict로
    b_by_date = {}
    for r in inv['rows']:
        d = r['date']
        groups = {k: v for k, v in r.items() if k not in ('date', '합계')}
        b_by_date[d] = {'groups': groups, 'total': r['합계']}

    # 병합
    all_dates = sorted(set(a_by_date.keys()) | set(b_by_date.keys()))
    comparison = []
    total_a = total_b = 0
    for d in all_dates:
        a = a_by_date.get(d, {'groups': {}, 'total': 0})
        b = b_by_date.get(d, {'groups': {}, 'total': 0})
        comparison.append({
            'date': d,
            'order_total': a['total'],
            'invoice_total': b['total'],
            'diff': a['total'] - b['total'],
            'order_by_group': a['groups'],
            'invoice_by_group': b['groups'],
        })
        total_a += a['total']
        total_b += b['total']

    return {
        'date_from': date_from,
        'date_to': date_to,
        'comparison': comparison,
        'total_order': total_a,
        'total_invoice': total_b,
        'total_diff': total_a - total_b,
        'invoiced_orders': inv['invoiced_orders'],
        'product_master_count': inv['product_master_count'],
        'unmatched_products': inv['unmatched_products'],
        'product_by_date': inv['product_by_date'],
    }
