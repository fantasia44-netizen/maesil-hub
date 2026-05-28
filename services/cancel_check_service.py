"""cancel_check_service.py — CJ 송장 생성 전 마켓플레이스 취소 여부 확인.

흐름:
  1. order_shipping 에서 invoice_no 없는 '접수' 상태 주문 조회
  2. 채널별 marketplace API 로 현재 주문 상태 조회 (fetch_order_statuses)
  3. 취소 상태 주문 → order_shipping.shipping_status = '취소',
                        order_transactions.status = '취소' 업데이트
  4. 유효(취소 아닌) 주문 목록 반환 → CJ 송장 생성 대상

카페24는 현재 개별 상태 조회 API 미구현 → 스킵 (취소 확인 생략, 그대로 송장 생성).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ── 마켓별 취소로 간주할 order_status 값 ──────────────────────────────────────
CANCEL_STATUSES: dict[str, frozenset[str]] = {
    'naver': frozenset([
        'CANCEL_REQUEST', 'CANCEL', 'CANCEL_DONE',
        'RETURN_REQUEST', 'RETURN', 'RETURN_DONE',
        'EXCHANGE_REQUEST', 'EXCHANGE', 'EXCHANGE_DONE',
    ]),
    'coupang': frozenset([
        'CANCEL_REQUEST', 'CANCEL_DONE',
        'RETURN_REQUEST', 'RETURN_DONE',
        'EXCHANGE_REQUEST', 'EXCHANGE_DONE',
    ]),
}


def _get_platform(client) -> str | None:
    """클라이언트 클래스명으로 플랫폼 추론."""
    name = type(client).__name__.lower()
    if 'naver' in name:
        return 'naver'
    if 'coupang' in name:
        return 'coupang'
    if 'cafe24' in name:
        return 'cafe24'
    return None


def check_and_mark_cancellations(db, mgr, channel: str | None = None) -> dict:
    """invoice_no 없는 접수 주문의 취소 여부를 마켓 API로 확인 후 DB 업데이트.

    Args:
        db:      SupabaseDB 인스턴스
        mgr:     MarketplaceManager 인스턴스
        channel: 특정 채널만 처리 (None이면 전체)

    Returns:
        {
            'checked':   int,  # API 조회된 주문 수
            'cancelled': int,  # 취소 처리된 주문 수
            'valid':     int,  # 유효(CJ 송장 생성 가능) 주문 수
            'skipped':   int,  # API 미지원 채널 주문 수 (카페24 등)
        }
    """
    # ── 1. invoice_no 없는 '접수' 주문 조회 ──────────────────────────────────
    try:
        q = (db.client.table("order_shipping")
             .select("channel, order_no")
             .eq("shipping_status", "접수")
             .or_("invoice_no.is.null,invoice_no.eq."))
        if channel:
            q = q.eq("channel", channel)
        ships = (q.range(0, 999).execute()).data or []
    except Exception as e:
        logger.error(f'[CancelCheck] order_shipping 조회 오류: {e}')
        return {'checked': 0, 'cancelled': 0, 'valid': 0, 'skipped': 0}

    if not ships:
        logger.info('[CancelCheck] 확인 대상 주문 없음')
        return {'checked': 0, 'cancelled': 0, 'valid': 0, 'skipped': 0}

    # ── 2. 채널별 그룹핑 ────────────────────────────────────────────────────
    by_channel: dict[str, list[str]] = {}
    for s in ships:
        ch = s['channel']
        by_channel.setdefault(ch, []).append(s['order_no'])

    cancelled_order_nos: set[str] = set()
    checked = 0
    skipped = 0

    # ── 3. 채널별 마켓 API 조회 ─────────────────────────────────────────────
    for ch, order_nos in by_channel.items():
        try:
            client = mgr.get_client(ch)
            if not client or not client.is_ready():
                logger.info(f'[CancelCheck] {ch} 클라이언트 준비 안 됨 — 스킵')
                skipped += len(order_nos)
                continue

            platform = _get_platform(client)
            if platform not in CANCEL_STATUSES:
                # 카페24 등 취소 확인 미지원
                logger.info(f'[CancelCheck] {ch} ({platform}) 취소 확인 미지원 — 스킵')
                skipped += len(order_nos)
                continue

            logger.info(f'[CancelCheck] {ch} {len(order_nos)}건 상태 조회')
            statuses = client.fetch_order_statuses(order_nos)
            cancel_set = CANCEL_STATUSES[platform]

            for s in statuses:
                oid = s.get('api_order_id', '')
                status_raw = s.get('status_raw', '')
                if status_raw in cancel_set:
                    logger.info(f'[CancelCheck] 취소 감지: {ch} {oid} ({status_raw})')
                    cancelled_order_nos.add(oid)

            checked += len(order_nos)

        except Exception as e:
            logger.warning(f'[CancelCheck] {ch} 상태 조회 실패: {e}')
            skipped += len(order_nos)

    # ── 4. 취소 주문 DB 업데이트 ───────────────────────────────────────────
    cancelled_count = 0
    for order_no in cancelled_order_nos:
        try:
            db.client.table("order_shipping") \
                .update({"shipping_status": "취소"}) \
                .eq("order_no", order_no) \
                .execute()
            # order_transactions에도 반영
            db.client.table("order_transactions") \
                .update({"status": "취소"}) \
                .eq("order_no", order_no) \
                .execute()
            cancelled_count += 1
        except Exception as e:
            logger.error(f'[CancelCheck] {order_no} DB 업데이트 실패: {e}')

    valid = len(ships) - cancelled_count - skipped
    result = {
        'checked': checked,
        'cancelled': cancelled_count,
        'valid': valid,
        'skipped': skipped,
    }
    logger.info(f'[CancelCheck] 완료: {result}')
    return result
