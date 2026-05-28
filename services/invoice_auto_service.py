"""invoice_auto_service.py — 취소 확인 → CJ 송장 자동 생성 파이프라인.

sync_scheduler.py 에서 run_order_processing() 완료 후 호출.

흐름:
  1. cancel_check_service.check_and_mark_cancellations() — 취소 주문 마킹
  2. cj_shipping_service.query_orders_without_invoice()  — 유효 주문 조회
  3. cj_shipping_service.generate_cj_invoices()          — CJ 채번 + 예약접수
  4. DB order_shipping.invoice_no 업데이트 (generate_cj_invoices 내부에서 처리)

Returns:
    {
        'cancel_check': {checked, cancelled, valid, skipped},
        'invoice':      {total, success, failed, results: [...]},
    }
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def run_invoice_auto(db, mgr) -> dict:
    """취소 확인 후 CJ 송장 자동 생성 전체 실행.

    Args:
        db:  SupabaseDB 인스턴스
        mgr: MarketplaceManager 인스턴스 (취소 API 호출용)

    Returns:
        {cancel_check: ..., invoice: ...}
    """
    from services.cancel_check_service import check_and_mark_cancellations
    from services.cj_shipping_service import (
        query_orders_without_invoice,
        generate_cj_invoices,
    )

    # ── 1. 취소 확인 ────────────────────────────────────────────────────────
    try:
        cancel_result = check_and_mark_cancellations(db, mgr)
    except Exception as e:
        logger.error(f'[InvoiceAuto] 취소 확인 오류: {e}', exc_info=True)
        cancel_result = {'checked': 0, 'cancelled': 0, 'valid': 0, 'skipped': 0, 'error': str(e)}

    cancelled = cancel_result.get('cancelled', 0)
    if cancelled:
        logger.info(f'[InvoiceAuto] 취소 주문 {cancelled}건 — CJ 송장 생성에서 제외됨')

    # ── 2. CJ 송장 미생성 주문 조회 ──────────────────────────────────────────
    try:
        orders = query_orders_without_invoice(db)
    except Exception as e:
        logger.error(f'[InvoiceAuto] 미배정 주문 조회 오류: {e}', exc_info=True)
        return {'cancel_check': cancel_result, 'invoice': {'total': 0, 'success': 0, 'failed': 0, 'error': str(e)}}

    if not orders:
        logger.info('[InvoiceAuto] CJ 송장 생성 대상 없음')
        return {'cancel_check': cancel_result, 'invoice': {'total': 0, 'success': 0, 'failed': 0}}

    logger.info(f'[InvoiceAuto] CJ 송장 생성 시작 — {len(orders)}건')

    # ── 3. CJ 송장 생성 ────────────────────────────────────────────────────
    try:
        invoice_result = generate_cj_invoices(db, orders)
        logger.info(f'[InvoiceAuto] CJ 송장 결과: '
                    f'성공={invoice_result.get("success", 0)}, '
                    f'실패={invoice_result.get("failed", 0)}')
    except Exception as e:
        logger.error(f'[InvoiceAuto] CJ 송장 생성 오류: {e}', exc_info=True)
        invoice_result = {'total': len(orders), 'success': 0, 'failed': len(orders), 'error': str(e)}

    return {
        'cancel_check': cancel_result,
        'invoice': invoice_result,
    }
