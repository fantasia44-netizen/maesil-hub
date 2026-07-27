"""revenue_settlement_service.py — 정산 기준 매출관리 집계.

매출관리 화면용으로 "실제 정산" 기준 매출을 채널별/일자별로 집계한다.

데이터 소스 (검증 완료 — 관리손익표 순정산과 일치):
  ① insight 정산 (api_settlements, 브릿지)  → 스마트스토어/쿠팡/Cafe24 등
       실제 수수료·쿠폰·포인트·기타차감·순정산. _SETTLE_PREFIXES 로 중복(쿠팡 revenue_/WEEKLY)제거.
  ② total 추가채널 (order_transactions)      → 오아시스/경남몰/옥션/11번가 등
       플랫폼 정산서 없음 → 수수료 0, 순정산 = 매출. (정산서는 추후 반영 예정)
  ③ 거래처(기타)매출 (daily_revenue '거래처매출') → 수수료 0, 순정산 = 매출.

중복제거 (insight 에 이미 포함되어 total 에서 빼는 채널):
  - 스마트스토어_배마마 / 스마트스토어_해미애찬
  - 쿠팡(일반) / 쿠팡로켓  (insight 쿠팡_배마마 / 쿠팡_배마마_1P)
  - 자사몰  (insight Cafe24_배마마)
  - N배송 / 도착보장  (insight 가 스마트스토어_배마마 로 통합 — total은 용인창고 재고관리용 분리)
  - daily_revenue '로켓'  (insight 쿠팡_배마마_1P 와 중복)
"""
from flask import g
from collections import defaultdict
from datetime import datetime, timedelta

from services.pnl_service import _month_range
from services.maesil_bridge import (
    get_maesil_settlements_raw_by_month, get_maesil_ad_cost_by_month,
    get_maesil_order_revenue_by_month, get_maesil_cancel_by_month)


# insight RPC 원본 채널 → 매출관리 표시 채널명 (쿠팡/쿠팡로켓 분리)
_CH_DISPLAY = {
    '쿠팡_배마마': '쿠팡',
    '쿠팡_배마마_1P': '쿠팡로켓',
    'Cafe24_배마마': '자사몰',
    '스마트스토어_배마마': '스마트스토어_배마마',
    '스마트스토어_해미애찬': '스마트스토어_해미애찬',
}


# insight 정산에 이미 포함되어 total order_transactions 에서 제외할 채널
_INSIGHT_COVERED = {
    '스마트스토어_배마마', '스마트스토어_해미애찬',
    '쿠팡', '쿠팡로켓', '쿠팡_배마마', '쿠팡_배마마_1P',
    '자사몰', 'Cafe24_배마마',
}


def _is_insight_covered(channel):
    """이 채널이 insight 정산에 이미 잡혀 total 에서 빼야 하는지."""
    c = (channel or '').strip()
    if c in _INSIGHT_COVERED:
        return True
    if 'N배송' in c or '도착보장' in c:
        return True
    return False


def _blank_row():
    # 순정산 = 매출 − 취소 − 수수료 − 차감(net_ded). 차감은 파생계산(매출−취소−수수료−순정산).
    return {'매출': 0, '취소': 0, '수수료': 0, '순정산': 0, '주문건수': 0}


def build_settlement_revenue(db, year_month, maesil_sb=None, maesil_op_id=None):
    """정산 기준 매출관리 집계.

    Returns:
        {
          'year_month': 'YYYY-MM',
          'channels': [ {channel, source, 매출, 수수료, 차감, 순정산, 순정산율, 광고비, 순매출, 순매출률, 주문건수} ],
            # 매출=주문(결제)기준, 차감=매출−수수료−순정산, 순매출=순정산−광고비
          'daily':    [ {date, 매출, 순정산} ],            # 일자별 합계
          'daily_by_channel': { date: { channel: {매출, 순정산} } },
          'total':    {매출, 수수료, 차감, 순정산, 주문건수, 순정산율},
        }
    """
    date_from, date_to = _month_range(year_month)

    ch_agg = defaultdict(_blank_row)        # channel -> row  (월별 채널요약 = 정산기준)
    ch_source = {}                          # channel -> '정산' | '추가' | '거래처'
    # 일자별은 정산일(settlement_date)이 아닌 판매일(order_date/revenue_date) 매출 기준
    daily_sales = defaultdict(int)          # date -> 매출
    daily_sales_ch = defaultdict(lambda: defaultdict(int))  # date -> channel -> 매출

    def _add(channel, source, *, 매출=0, 취소=0, 수수료=0, 순정산=0, 건수=0):
        row = ch_agg[channel]
        row['매출'] += 매출; row['취소'] += 취소; row['수수료'] += 수수료
        row['순정산'] += 순정산; row['주문건수'] += 건수
        ch_source.setdefault(channel, source)

    # ── ① insight 정산 — 결제일 기준 (insight get_payment_summary 공식) ──
    #    순정산 = 매출 − 취소 − 수수료 − 차감(net_deductions)  ← 전부 결제일 매출에서 파생.
    #    api_settlements net_settlement(정산일) 미사용 → 월경계 이월문제 제거, 매월 일관.
    #    매출/수수료=get_daily_trend(결제), 취소=api_orders CANCELED(결제),
    #    차감=쿠폰+포인트+기타(쿠팡 other=선정산/유보금이라 제외).
    order_data = get_maesil_order_revenue_by_month(maesil_sb, maesil_op_id, year_month) \
        if maesil_sb else {}
    cancel = get_maesil_cancel_by_month(maesil_sb, maesil_op_id, year_month) \
        if maesil_sb else {}
    raw = get_maesil_settlements_raw_by_month(maesil_sb, maesil_op_id, year_month) \
        if maesil_sb else []
    for s in raw:
        ch_raw = (s.get('channel') or '기타').strip()
        ch = _CH_DISPLAY.get(ch_raw, ch_raw)
        od = order_data.get(ch_raw) or {}
        cc = cancel.get(ch_raw) or {}
        # 매출·수수료 = 결제일(get_daily_trend). 1P(로켓)은 미포함 → 정산값 fallback.
        매출 = od.get('revenue') or int(s.get('gross_sales') or 0)
        취소 = int(cc.get('revenue') or 0)
        # 수수료 = 결제수수료 − 취소주문 수수료 (취소건엔 수수료 미발생, insight 동일).
        base_comm = od.get('commission') or int(s.get('total_commission') or 0)
        수수료 = max(0, base_comm - int(cc.get('commission') or 0))
        pt = int(s.get('point_discount') or 0)
        oth = int(s.get('other_deductions') or 0)
        # 차감(net_ded) = insight _aggregate_channels 동일:
        #   쿠폰 제외(결제금액에 반영) / 쿠팡=0(유보금) / 네이버 = point + other
        net_ded = 0 if ch in ('쿠팡', '쿠팡로켓') else (pt + oth)
        순정산 = 매출 - 취소 - 수수료 - net_ded
        _add(ch, '정산', 매출=매출, 취소=취소, 수수료=수수료, 순정산=순정산,
             건수=int(s.get('record_count') or 0))

    # ── ② total 추가채널 (order_transactions, insight 미커버) ──
    #    채널요약: 수수료 0, 순정산 = 매출 (insight 미커버 채널만).
    #    일자별: 판매일(order_date) 기준 매출 — 전 채널 누적 (추이용).
    def _consume_ot(rows):
        for x in rows:
            ch = (x.get('channel') or '?').strip()
            amt = int(x.get('total_amount') or 0)
            od = str(x.get('order_date', ''))[:10]
            cnt = int(x.get('_cnt') or 1)  # RPC 집계행=품목일자 묶음, 폴백=행당1
            if od:                          # 일자별: 전 채널 판매일 매출 누적
                daily_sales[od] += amt
                daily_sales_ch[od][ch] += amt
            if not _is_insight_covered(ch):  # 채널요약: insight 미커버만
                _add(ch, '추가', 매출=amt, 순정산=amt, 건수=cnt)

    # 테넌트 격리: 매출관리는 UI 전용이라 g.biz_id 존재. RPC/raw 쿼리에 명시.
    _biz = getattr(g, 'biz_id', None)
    # get_order_revenue_agg RPC 우선 (23k행 → ~5.5k행 집계, migration 027)
    ot_agg = None
    try:
        res = db.client.rpc('get_order_revenue_agg', {
            'p_date_from': date_from, 'p_date_to': date_to,
            'p_biz_id': _biz}).execute()
        d = res.data
        if isinstance(d, str):
            import json as _json
            d = _json.loads(d)
        ot_agg = d or []
    except Exception:
        ot_agg = None

    if ot_agg is not None:
        _consume_ot(ot_agg)
    else:
        # 폴백: 청크 페이지네이션 (RPC 미배포/실패)
        cur = datetime.strptime(date_from, '%Y-%m-%d')
        end = datetime.strptime(date_to, '%Y-%m-%d')
        while cur <= end:
            chunk_to = min(cur + timedelta(days=4), end)
            offset = 0
            while True:
                res = db.client.table('order_transactions') \
                    .select('channel,total_amount,order_date') \
                    .eq('biz_id', _biz) \
                    .eq('status', '정상') \
                    .gte('order_date', cur.strftime('%Y-%m-%d')) \
                    .lte('order_date', chunk_to.strftime('%Y-%m-%d')) \
                    .order('id').range(offset, offset + 999).execute()
                rows = res.data or []
                _consume_ot(rows)
                if len(rows) < 1000:
                    break
                offset += 1000
            cur = chunk_to + timedelta(days=1)

    # ── ③ daily_revenue: 거래처(기타)매출 → 채널요약 + 일자별 / 로켓 → 일자별만 ──
    #    (order_transactions에 없는 수기매출. 로켓은 insight 쿠팡1P 중복이라 채널요약 제외, 추이엔 포함)
    try:
        offset = 0
        while True:
            res = db.client.table('daily_revenue') \
                .select('revenue_date,revenue,channel,category') \
                .eq('biz_id', _biz) \
                .gte('revenue_date', date_from).lte('revenue_date', date_to) \
                .order('id').range(offset, offset + 999).execute()
            rows = res.data or []
            for x in rows:
                d = str(x.get('revenue_date', ''))[:10]
                amt = int(x.get('revenue') or 0)
                cat = x.get('category') or ''
                # 일자별 추이엔 모두 포함 (판매일 매출)
                if d:
                    daily_sales[d] += amt
                    daily_sales_ch[d][cat or '기타'] += amt
                # 채널요약엔 거래처매출만 (로켓은 insight 중복)
                if cat == '거래처매출':
                    _add('거래처(기타)', '거래처', 매출=amt, 순정산=amt, 건수=1)
            if len(rows) < 1000:
                break
            offset += 1000
    except Exception:
        pass

    # ── 광고비 (insight naver/coupang ad_spend_daily, 채널별) ──
    ad_by_ch = get_maesil_ad_cost_by_month(maesil_sb, maesil_op_id, year_month) \
        if maesil_sb else {}

    # ── 결과 정리 (insight 컬럼 일치) ──
    #   순정산 = 매출 − 취소 − 수수료 − 차감(net_ded)
    #   차감 = 매출 − 취소 − 수수료 − 순정산 (쿠폰외 기타차감, 취소는 별도 컬럼)
    #   순매출 = 순정산 − 광고비
    channels = []
    T = _blank_row()
    T_ad = 0
    for ch in sorted(ch_agg.keys(), key=lambda x: -ch_agg[x]['매출']):
        r = ch_agg[ch]
        # ★순매출 = 매출−취소−수수료−차감 (광고비 빼기 전) / 순정산 = 순매출−광고비 (최종)
        매출 = r['매출']; 취소 = r['취소']; 수수료 = r['수수료']
        순매출 = r['순정산']            # 내부키 '순정산'에 저장된 값 = 매출−취소−수수료−차감
        광고비 = ad_by_ch.get(ch, 0)
        차감 = 매출 - 취소 - 수수료 - 순매출
        순정산 = 순매출 - 광고비
        nm_rate = round(순매출 / 매출 * 100, 1) if 매출 else 0
        rate = round(순정산 / 매출 * 100, 1) if 매출 else 0
        channels.append({
            'channel': ch, 'source': ch_source.get(ch, ''),
            '매출': 매출, '취소': 취소, '수수료': 수수료, '차감': 차감,
            '순매출': 순매출, '순매출률': nm_rate,
            '광고비': 광고비, '순정산': 순정산, '순정산율': rate,
            '주문건수': r['주문건수'],
        })
        for k in ('매출', '취소', '수수료', '순정산', '주문건수'):
            T[k] += r[k]
        T_ad += 광고비

    # 정산엔 없지만 광고비만 발생한 채널도 누락 없이 합산
    matched_ad = sum(ad_by_ch.get(c['channel'], 0) for c in channels)
    leftover_ad = sum(ad_by_ch.values()) - matched_ad
    T_ad += leftover_ad

    total_sales = T['순정산']           # 순매출 합 (매출−취소−수수료−차감)
    total_settle = total_sales - T_ad   # 순정산 합 (광고비까지 뺀 최종)
    total = {
        '매출': T['매출'], '취소': T['취소'], '수수료': T['수수료'],
        '차감': T['매출'] - T['취소'] - T['수수료'] - total_sales,
        '순매출': total_sales, '주문건수': T['주문건수'],
        '순매출률': round(total_sales / T['매출'] * 100, 1) if T['매출'] else 0,
        '광고비': T_ad, '순정산': total_settle,
        '순정산율': round(total_settle / T['매출'] * 100, 1) if T['매출'] else 0,
    }

    # 일자별 = 판매일(order_date/revenue_date) 기준 매출 추이
    daily_list = [{'date': d, '매출': daily_sales[d]} for d in sorted(daily_sales.keys())]

    return {
        'year_month': year_month,
        'channels': channels,
        'daily': daily_list,
        'daily_by_channel': {d: dict(v) for d, v in daily_sales_ch.items()},
        'total': total,
    }
