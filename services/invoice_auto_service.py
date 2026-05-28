"""invoice_auto_service.py — 취소 확인 → CJ 송장 자동 생성 → 마켓 발송처리 파이프라인.

sync_scheduler.py 에서 run_order_processing() 완료 후 호출.

흐름:
  1. cancel_check_service.check_and_mark_cancellations()  — 취소 주문 마킹
  2. cj_shipping_service.query_orders_without_invoice()   — 유효 주문 조회 (접수 + 미배정)
  3. cj_shipping_service.generate_cj_invoices()           — CJ 채번 + 예약접수
     → DB: invoice_no 저장 + shipping_status = '대기'
  4. marketplace_sync_service.push_invoices()             — 마켓에 송장번호 역업로드
     → DB: shipping_status = '발송'

Returns:
    {
        'cancel_check': {checked, cancelled, valid, skipped},
        'invoice':      {total, success, failed, results: [...]},
        'push':         {channel: {total, success, failed}, ...},
    }
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def run_invoice_auto(db, mgr) -> dict:
    """취소 확인 → CJ 송장 생성 → 마켓 발송처리 전체 실행.

    Args:
        db:  SupabaseDB 인스턴스
        mgr: MarketplaceManager 인스턴스

    Returns:
        {cancel_check: ..., invoice: ..., push: {channel: ...}}
    """
    from services.cancel_check_service import check_and_mark_cancellations
    from services.cj_shipping_service import (
        query_orders_without_invoice,
        generate_cj_invoices,
    )
    from services.marketplace_sync_service import push_invoices

    # ── 1. 취소 확인 ────────────────────────────────────────────────────────
    try:
        cancel_result = check_and_mark_cancellations(db, mgr)
    except Exception as e:
        logger.error(f'[InvoiceAuto] 취소 확인 오류: {e}', exc_info=True)
        cancel_result = {'checked': 0, 'cancelled': 0, 'valid': 0, 'skipped': 0, 'error': str(e)}

    cancelled = cancel_result.get('cancelled', 0)
    if cancelled:
        logger.info(f'[InvoiceAuto] 취소 주문 {cancelled}건 — CJ 송장 제외됨')

    # ── 2. CJ 송장 미배정 주문 조회 ─────────────────────────────────────────
    try:
        orders = query_orders_without_invoice(db)
    except Exception as e:
        logger.error(f'[InvoiceAuto] 미배정 주문 조회 오류: {e}', exc_info=True)
        return {
            'cancel_check': cancel_result,
            'invoice': {'total': 0, 'success': 0, 'failed': 0, 'error': str(e)},
            'push': {},
        }

    if not orders:
        logger.info('[InvoiceAuto] CJ 송장 생성 대상 없음')
        return {'cancel_check': cancel_result,
                'invoice': {'total': 0, 'success': 0, 'failed': 0},
                'push': {}}

    logger.info(f'[InvoiceAuto] CJ 송장 생성 시작 — {len(orders)}건')

    # ── 3. CJ 채번 + 예약접수 → shipping_status='대기' ──────────────────────
    try:
        invoice_result = generate_cj_invoices(db, orders)
        logger.info(f'[InvoiceAuto] CJ 송장: '
                    f'성공={invoice_result.get("success", 0)}, '
                    f'실패={invoice_result.get("failed", 0)}')
    except Exception as e:
        logger.error(f'[InvoiceAuto] CJ 송장 생성 오류: {e}', exc_info=True)
        return {
            'cancel_check': cancel_result,
            'invoice': {'total': len(orders), 'success': 0, 'failed': len(orders), 'error': str(e)},
            'push': {},
        }

    # CJ 성공 건이 없으면 push 스킵
    if invoice_result.get('success', 0) == 0:
        logger.info('[InvoiceAuto] CJ 성공 0건 — 마켓 발송처리 스킵')
        return {'cancel_check': cancel_result, 'invoice': invoice_result, 'push': {}}

    # ── 4. 마켓별 발송처리 (송장번호 역업로드) → shipping_status='발송' ───────
    push_results = {}
    channels = list(set(o['channel'] for o in orders))
    for ch in channels:
        try:
            client = mgr.get_client(ch)
            if not client or not client.is_ready():
                logger.info(f'[InvoiceAuto] {ch} push 스킵 (클라이언트 미준비)')
                continue
            push_res = push_invoices(db, mgr, ch, triggered_by='scheduler')
            push_results[ch] = push_res
            logger.info(f'[InvoiceAuto] {ch} push: '
                        f'성공={push_res.get("success", 0)}, '
                        f'실패={push_res.get("failed", 0)}')
        except Exception as e:
            logger.error(f'[InvoiceAuto] {ch} push 오류: {e}', exc_info=True)
            push_results[ch] = {'error': str(e)}

    return {
        'cancel_check': cancel_result,
        'invoice': invoice_result,
        'push': push_results,
    }
