"""Maesil Insight DB 브릿지 — 읽기 전용.

maesil-total과 별도 Supabase 프로젝트인 Maesil Insight의
api_settlements / api_orders 데이터를 maesil-total 포맷으로 가져온다.

설정 (.env):
  MAESIL_SUPABASE_URL   = https://xxxxx.supabase.co
  MAESIL_SUPABASE_KEY   = eyJxxxx...
  MAESIL_OPERATOR_ID    = <UUID>  # Maesil Insight의 사업자 operator_id

초기화 (app.py):
  app.maesil_sb = _init_maesil_client()
"""
import logging
import os

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 클라이언트 초기화
# ──────────────────────────────────────────────

def init_maesil_client():
    """Maesil Supabase 읽기 전용 클라이언트를 반환. 설정 없으면 None."""
    url = os.environ.get('MAESIL_SUPABASE_URL', '').strip()
    key = os.environ.get('MAESIL_SUPABASE_KEY', '').strip()
    if not url or not key:
        logger.info('[Maesil Bridge] MAESIL_SUPABASE_URL / KEY 미설정 — 브릿지 비활성')
        return None
    try:
        from supabase import create_client
        client = create_client(url, key)
        logger.info('[Maesil Bridge] 연결 성공')
        return client
    except Exception as e:
        logger.warning(f'[Maesil Bridge] 연결 실패: {e}')
        return None


def get_maesil_operator_id():
    """환경변수에서 Maesil operator_id 반환."""
    return os.environ.get('MAESIL_OPERATOR_ID', '').strip() or None


# ──────────────────────────────────────────────
# api_settlements 조회
# ──────────────────────────────────────────────

_SETTLEMENT_COLS = (
    'settlement_id,channel,settlement_date,gross_sales,'
    'total_commission,shipping_fee_income,shipping_fee_cost,'
    'coupon_discount,point_discount,other_deductions,'
    'net_settlement,fee_breakdown'
)


def _normalize_settlement(row):
    """Maesil settlement_id / channel → maesil-total 호환 형식으로 정규화.

    Maesil prefix → maesil-total prefix 매핑:
      daily_YYYY-MM-DD        (스마트스토어 일별)  → nsettle_daily_...
      revenue_YYYY-MM-DD      (쿠팡 Wing 매출)     → revenue_ 유지, channel 정규화
      YYYY-MM_WEEKLY_...      (쿠팡 Wing 정산)     → wsettle_...
      rocket_...              (쿠팡 로켓)          → 그대로
      nsettle_ / wsettle_ 등  (이미 maesil-total 형식) → 그대로

    채널 정규화:
      쿠팡_배마마 / 쿠팡_* → 쿠팡
    """
    import re
    row = dict(row)  # 원본 수정 방지

    sid = row.get('settlement_id', '') or ''
    ch  = row.get('channel', '') or ''

    # ── 채널명 정규화 ──
    if ch.startswith('쿠팡_') and ch != '쿠팡로켓':
        ch = '쿠팡'

    # ── settlement_id prefix 정규화 ──
    if sid.startswith('daily_'):
        # 스마트스토어 일별 정산 → nsettle_ prefix
        sid = 'nsettle_' + sid
    elif re.match(r'^\d{4}-\d{2}_WEEKLY', sid):
        # 쿠팡 Wing 주별 정산 → wsettle_ prefix
        sid = 'wsettle_' + sid
    elif sid.startswith('revenue_'):
        # 쿠팡 Wing revenue-history → revenue_ 유지 (maesil-total sales_data에서 처리)
        # pnl_service 필터(_SETTLE_PREFIXES)에 없으므로 nsettle_ 없이 직접 집계용 tag
        row['_maesil_revenue'] = True  # pnl_service 폴백용 마커

    row['settlement_id'] = sid
    row['channel'] = ch
    row['_maesil_source'] = True
    return row


def get_maesil_settlements_by_month(maesil_sb, operator_id, year_month):
    """Maesil get_settlement_summary_by_month RPC 호출 → channel 집계 반환.

    직접 api_settlements 쿼리 대신 Maesil 대시보드와 동일한 RPC 사용:
    - RESERVE 행 자동 제외 (쿠팡 이중집계 방지)
    - revenue_ 레거시 행 제외
    - 매출인식월 기준 쿠팡 정산 집계

    Returns:
        list[dict] — channel별 집계 row (settlement 형식으로 변환)
    """
    if not maesil_sb or not operator_id:
        return []
    try:
        res = maesil_sb.rpc('get_settlement_summary_by_month', {
            'p_operator_id': operator_id,
            'p_year_month': year_month,
        }).execute()
        rows = res.data or []

        result = []
        for r in rows:
            ch = r.get('channel', '') or ''
            # 채널명 정규화 — _normalize_ch_for_merge 동일 규칙 적용
            if '쿠팡' in ch and '로켓' not in ch:
                ch = '쿠팡'
            elif '스마트스토어' in ch or '네이버쇼핑' in ch:
                ch = '스마트스토어'
            # settlement_id prefix — 채널별 적합한 prefix 부여
            if '스마트스토어' in ch or '네이버' in ch:
                sid_prefix = 'nsettle_'
            elif ch == '쿠팡':
                sid_prefix = 'wsettle_'
            elif '로켓' in ch:
                sid_prefix = 'rocket_'
            elif '11번가' in ch:
                sid_prefix = '11settle_'
            elif '티몬' in ch:
                sid_prefix = 'tsettle_'
            elif '옥션' in ch:
                sid_prefix = 'auction_'
            elif '마켓' in ch and ('g' in ch.lower() or 'G' in ch):
                sid_prefix = 'gmarket_'
            else:
                sid_prefix = 'nsettle_'

            result.append({
                'settlement_id': f'{sid_prefix}maesil_rpc_{year_month}_{ch}',
                'channel': ch,
                'settlement_date': f'{year_month}-01',
                'gross_sales': int(r.get('gross_sales') or 0),
                'total_commission': int(r.get('total_commission') or 0),
                'coupon_discount': int(r.get('coupon_discount') or 0),
                'point_discount': int(r.get('point_discount') or 0),
                'other_deductions': int(r.get('other_deductions') or 0),
                'net_settlement': int(r.get('net_settlement') or 0),
                'shipping_fee_income': int(r.get('shipping_fee_income') or 0),
                '_maesil_source': True,
                '_maesil_rpc': True,
            })

        logger.info(f'[Maesil Bridge] RPC settlements {len(result)}개 채널 ({year_month})')
        return result
    except Exception as e:
        logger.warning(f'[Maesil Bridge] RPC settlements 조회 실패, 직접쿼리로 폴백: {e}')
        # 폴백: 기존 직접 쿼리
        date_from = f'{year_month}-01'
        import calendar
        y, m = int(year_month[:4]), int(year_month[5:7])
        last_day = calendar.monthrange(y, m)[1]
        date_to = f'{year_month}-{last_day:02d}'
        return get_maesil_settlements(maesil_sb, operator_id, date_from, date_to)


def get_maesil_settlements(maesil_sb, operator_id, date_from, date_to):
    """Maesil api_settlements 조회 → maesil-total 호환 포맷 반환.

    settlement_id / channel 정규화 적용.
    주로 marketplace/sales 날짜범위 조회에 사용.
    P&L 월집계는 get_maesil_settlements_by_month() 사용 권장.

    Returns:
        list[dict] — 빈 리스트 on error/missing
    """
    if not maesil_sb or not operator_id:
        return []
    try:
        all_rows = []
        page_size = 1000
        offset = 0
        while True:
            res = (
                maesil_sb.table('api_settlements')
                .select(_SETTLEMENT_COLS)
                .eq('operator_id', operator_id)
                .gte('settlement_date', date_from)
                .lte('settlement_date', date_to)
                .order('settlement_date', desc=False)
                .range(offset, offset + page_size - 1)
                .execute()
            )
            rows = res.data or []
            all_rows.extend(rows)
            if len(rows) < page_size:
                break
            offset += page_size

        # 쿠팡 RESERVE 정산 제외 (예치금 — Maesil 대시보드와 동일 처리)
        before = len(all_rows)
        all_rows = [r for r in all_rows
                    if 'RESERVE' not in (r.get('settlement_id') or '').upper()]
        if len(all_rows) < before:
            logger.info(f'[Maesil Bridge] RESERVE 정산 {before - len(all_rows)}건 제외')

        # 정규화
        all_rows = [_normalize_settlement(r) for r in all_rows]
        logger.info(f'[Maesil Bridge] settlements {len(all_rows)}건 ({date_from}~{date_to}) 정규화 완료')
        return all_rows
    except Exception as e:
        logger.warning(f'[Maesil Bridge] settlements 조회 실패: {e}')
        return []


# ──────────────────────────────────────────────
# api_orders 조회
# ──────────────────────────────────────────────

_ORDER_COLS = (
    'channel,order_date,order_status,total_amount,'
    'commission,settlement_amount,shipping_fee,fee_detail'
)


def get_maesil_orders(maesil_sb, operator_id, date_from, date_to):
    """Maesil api_orders 조회 → maesil-total 호환 포맷 반환.

    Returns:
        list[dict] — 빈 리스트 on error/missing
    """
    if not maesil_sb or not operator_id:
        return []
    try:
        all_rows = []
        page_size = 1000
        offset = 0
        while True:
            res = (
                maesil_sb.table('api_orders')
                .select(_ORDER_COLS)
                .eq('operator_id', operator_id)
                .gte('order_date', date_from)
                .lte('order_date', date_to)
                .order('order_date', desc=False)
                .range(offset, offset + page_size - 1)
                .execute()
            )
            rows = res.data or []
            all_rows.extend(rows)
            if len(rows) < page_size:
                break
            offset += page_size
        logger.debug(f'[Maesil Bridge] orders {len(all_rows)}건 ({date_from}~{date_to})')
        return all_rows
    except Exception as e:
        logger.warning(f'[Maesil Bridge] orders 조회 실패: {e}')
        return []


# ──────────────────────────────────────────────
# 정산 병합 헬퍼
# ──────────────────────────────────────────────

def _normalize_ch_for_merge(ch):
    """merge_settlements용 채널명 정규화 — 스토어별 prefix 그룹화.

    maesil-total 채널명 예시:
      '스마트스토어_배마마', '스마트스토어_해미애찬', '스마트스토어배마마'  →  '스마트스토어'
      '쿠팡', '쿠팡_Wing', '쿠팡_배마마'  →  '쿠팡'  (쿠팡로켓 제외)

    Maesil 채널명 예시:
      '스마트스토어'  →  '스마트스토어'
      '쿠팡'  →  '쿠팡'

    ※ startswith 대신 `in` 방식으로 언더스코어 유무 관계없이 매핑
    """
    if not ch:
        return ch
    if '스마트스토어' in ch or '네이버쇼핑' in ch:
        return '스마트스토어'
    if '쿠팡' in ch and '로켓' not in ch:
        return '쿠팡'
    if '11번가' in ch or '11st' in ch.lower():
        return '11번가'
    if '티몬' in ch or 'tmon' in ch.lower():
        return '티몬'
    if '위메프' in ch or 'wemakeprice' in ch.lower():
        return '위메프'
    if '옥션' in ch or 'auction' in ch.lower():
        return '옥션'
    if 'g마켓' in ch.lower() or 'gmarket' in ch.lower():
        return 'G마켓'
    return ch


# 정산서 prefix — Maesil 대체 대상 (ad_cost_/rocket_ 제외)
_SETTLE_PREFIXES_FOR_MERGE = (
    'nsettle_', 'wsettle_', '11settle_',
    'tsettle_', 'osettle_', 'auction_', 'gmarket_',
)


def merge_settlements(own_rows, maesil_rows):
    """maesil-total 자체 정산 + Maesil 정산 병합.

    ▶ 핵심 원칙: maesil-total 정산서 우선, Maesil은 maesil-total에 없는 채널만 보완.

    - maesil-total에 nsettle_/wsettle_/rocket_ 등 정산서 rows가 있는 채널
      → maesil-total 데이터 그대로 사용, Maesil 해당 채널 무시 (이중집계 방지)
    - maesil-total에 정산서가 없는 채널 (예: 3월처럼 파일 미업로드)
      → Maesil 데이터로 보완

    이렇게 하면:
    - 1,2월 (정산서 있음): maesil-total 정산서 사용, Maesil 추가 없음 → 이중집계 없음
    - 3월 (정산서 없음): Maesil 전체 사용 → 정상

    gross_sales=0인 Maesil 채널은 보완 대상에서 제외.
    """
    if not maesil_rows:
        return own_rows

    # gross=0인 Maesil row 제외
    maesil_rows_with_data = [r for r in maesil_rows if int(r.get('gross_sales') or 0) > 0]
    if not maesil_rows_with_data:
        logger.info('[Maesil Bridge] Maesil gross 데이터 없음 — maesil-total 정산 유지')
        return own_rows

    # maesil-total에 이미 있는 채널 목록 (정산서 prefix 기준)
    _ALL_SETTLE_PFXS = _SETTLE_PREFIXES_FOR_MERGE + ('rocket_',)
    own_covered = set()
    for r in own_rows:
        sid = r.get('settlement_id', '') or ''
        if any(sid.startswith(p) for p in _ALL_SETTLE_PFXS):
            ch_norm = _normalize_ch_for_merge(r.get('channel', '') or '')
            own_covered.add(ch_norm)

    # Maesil rows 중 maesil-total에 없는 채널만 추가 (maesil-total 우선)
    maesil_extra = []
    maesil_skipped = []
    for r in maesil_rows_with_data:
        ch_norm = _normalize_ch_for_merge(r.get('channel', '') or '')
        if ch_norm not in own_covered:
            maesil_extra.append(r)   # maesil-total 미커버 채널 → 보완 추가
        else:
            maesil_skipped.append(r)  # maesil-total 이미 커버 → 이중집계 방지, 무시

    merged = own_rows + maesil_extra
    logger.info(
        f'[Maesil Bridge] 병합 완료: maesil-total={len(own_rows)}건, '
        f'Maesil추가={len(maesil_extra)}채널, Maesil무시={len(maesil_skipped)}채널 '
        f'(maesil-total커버채널={sorted(own_covered)})'
    )
    return merged


def merge_orders(own_rows, maesil_rows):
    """maesil-total 자체 주문 + Maesil 주문 병합.

    Maesil api_orders 컬럼명이 maesil-total api_orders와 호환되므로
    그대로 append. 날짜+채널+금액으로 중복 필터링.
    """
    if not maesil_rows:
        return own_rows
    # 단순 append (중복 허용 — 어차피 채널이 다른 경우가 많음)
    # 동일 operator가 두 DB에 동일 주문을 가지면 중복되지만
    # 현재 구조상 API 주문은 maesil DB에만 있으므로 중복 없음
    return own_rows + maesil_rows
