"""출고 데이터 검증 서비스.

두 경로로 출고 수량을 집계해 교차 검증:
  A) stock_ledger SALES_OUT (기존 통합집계 경로)
  B) order_shipping(송장 발행됨) × order_transactions 역산

기간 기준: order_transactions.order_date
  - stock_ledger.transaction_date가 아닌 order_date 기준으로 통일
    (출고일 = 주문일 + 며칠 차이 날 수 있어서 비교 혼란 방지)
"""
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


def _fetch_stock_ledger_out(db, date_from, date_to):
    """stock_ledger에서 기간 내 순출고량 집계.

    maesil-total 저장 규칙:
      - SALES_OUT.qty = 음수 (재고 차감)
      - SALES_RETURN.qty = 양수 (재고 복원)
    순출고량 = -(SALES_OUT sum + SALES_RETURN sum) = 실제 나간 양 - 돌아온 양
    """
    agg = defaultdict(int)
    for t in ('SALES_OUT', 'SALES_RETURN'):
        offset = 0
        while True:
            res = db.client.table('stock_ledger') \
                .select('product_name,qty') \
                .eq('type', t) \
                .gte('transaction_date', date_from) \
                .lte('transaction_date', date_to) \
                .or_('status.is.null,status.eq.active') \
                .order('id') \
                .range(offset, offset + 999).execute()
            rows = res.data or []
            if not rows:
                break
            for r in rows:
                pn = (r.get('product_name') or '').strip()
                if pn:
                    # 부호 반전: SALES_OUT(-) → +, SALES_RETURN(+) → -
                    agg[pn] += -int(r.get('qty', 0) or 0)
            if len(rows) < 1000:
                break
            offset += 1000
    return dict(agg)


def _fetch_invoice_reverse(db, date_from, date_to):
    """송장 발행된 주문을 역산해서 품목별 출고수량 집계.

    흐름:
      1) order_transactions에서 기간 내 order_date 레코드 조회
      2) 각 (channel, order_no)가 order_shipping에 invoice_no 있는지 확인
      3) 송장 있는 건만 상품별 qty 합산 (취소 제외)
    """
    # 1) 기간 내 order_transactions 수집
    ot_rows = []
    offset = 0
    while True:
        res = db.client.table('order_transactions') \
            .select('channel,order_no,product_name,qty,status') \
            .gte('order_date', date_from) \
            .lte('order_date', date_to) \
            .order('id') \
            .range(offset, offset + 999).execute()
        rows = res.data or []
        if not rows:
            break
        ot_rows.extend(rows)
        if len(rows) < 1000:
            break
        offset += 1000

    # 취소 제외
    ot_rows = [r for r in ot_rows if (r.get('status') or '').lower() not in ('cancelled', '취소')]

    # 2) (channel, order_no) 쌍 수집
    pairs = set()
    for r in ot_rows:
        ch = (r.get('channel') or '').strip()
        no = (r.get('order_no') or '').strip()
        if ch and no:
            pairs.add((ch, no))

    # 3) order_shipping에서 송장 있는 (channel, order_no) 조회 (100개씩 chunk)
    invoiced = set()  # {(channel, order_no), ...}
    pair_list = list(pairs)
    for i in range(0, len(pair_list), 200):
        chunk = pair_list[i:i + 200]
        order_nos = [p[1] for p in chunk]
        rows = []
        try:
            rpc_res = db.client.rpc('rpc_validate_outbound_invoices', {
                'p_order_nos': list(order_nos),
            }).execute()
            rows = rpc_res.data or []
        except Exception:
            res = db.client.table('order_shipping') \
                .select('channel,order_no,invoice_no') \
                .in_('order_no', order_nos) \
                .not_.is_('invoice_no', 'null') \
                .neq('invoice_no', '') \
                .range(0, 9999) \
                .execute()
            rows = res.data or []
        for s in rows:
            key = ((s.get('channel') or '').strip(),
                   (s.get('order_no') or '').strip())
            if key in pairs:
                invoiced.add(key)

    # 4) 송장 있는 주문만 품목별 집계
    agg = defaultdict(int)
    for r in ot_rows:
        ch = (r.get('channel') or '').strip()
        no = (r.get('order_no') or '').strip()
        if (ch, no) not in invoiced:
            continue
        pn = (r.get('product_name') or '').strip()
        if not pn:
            continue
        agg[pn] += int(r.get('qty', 0) or 0)

    return dict(agg), len(invoiced), len(pairs)


def validate_outbound(db, date_from, date_to):
    """출고 데이터 교차 검증.

    Args:
        db: SupabaseDB
        date_from, date_to: 'YYYY-MM-DD'

    Returns:
        dict: {
            'date_from', 'date_to',
            'stock_ledger_total': int,
            'invoice_reverse_total': int,
            'invoiced_orders': int,      # 송장 있는 주문 수
            'total_orders': int,         # 기간 전체 주문 수
            'items': [
                {'product_name', 'qty_ledger', 'qty_invoice', 'diff', 'status'}
            ],
            'diff_count': int,           # 불일치 품목 수
        }
    """
    ledger = _fetch_stock_ledger_out(db, date_from, date_to)
    invoice, invoiced_cnt, total_cnt = _fetch_invoice_reverse(db, date_from, date_to)

    all_products = set(ledger.keys()) | set(invoice.keys())
    items = []
    diff_count = 0
    for pn in all_products:
        ql = ledger.get(pn, 0)
        qi = invoice.get(pn, 0)
        diff = ql - qi
        if diff == 0:
            status = 'match'
        elif ql > qi:
            status = 'ledger_more'  # 출고 차감 > 송장
            diff_count += 1
        else:
            status = 'invoice_more'  # 송장 > 출고 차감
            diff_count += 1
        items.append({
            'product_name': pn,
            'qty_ledger': ql,
            'qty_invoice': qi,
            'diff': diff,
            'status': status,
        })

    # 차이 큰 순 + 품목명 순
    items.sort(key=lambda x: (-abs(x['diff']), x['product_name']))

    return {
        'date_from': date_from,
        'date_to': date_to,
        'stock_ledger_total': sum(ledger.values()),
        'invoice_reverse_total': sum(invoice.values()),
        'invoiced_orders': invoiced_cnt,
        'total_orders': total_cnt,
        'items': items,
        'diff_count': diff_count,
        'product_count': len(all_products),
    }
