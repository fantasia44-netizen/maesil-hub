"""sync_scheduler.py — 마켓플레이스 주문/정산 자동 수집 + 처리 스케줄러.

앱 시작 시 start_sync_scheduler(app) 1회 호출.
기본: 30분마다 전체 채널 주문 수집 → 자동 변환 → 재고차감, 6시간마다 정산 수집.

환경변수:
    SYNC_ORDER_INTERVAL_MIN   주문 수집 주기 (분, 기본 30)
    SYNC_SETTLE_INTERVAL_MIN  정산 수집 주기 (분, 기본 360)
    SYNC_DAYS_BACK            수집 기준 일수 (기본 2 — 오늘+전일)
    SYNC_ENABLED              '0'이면 스케줄러 비활성 (기본 '1')
    SYNC_AUTO_PROCESS         '0'이면 수집 후 자동처리 비활성 (기본 '1')
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_ORDER_INTERVAL  = int(os.environ.get('SYNC_ORDER_INTERVAL_MIN', '30')) * 60
_SETTLE_INTERVAL = int(os.environ.get('SYNC_SETTLE_INTERVAL_MIN', '360')) * 60
_DAYS_BACK       = int(os.environ.get('SYNC_DAYS_BACK', '2'))
_ENABLED         = os.environ.get('SYNC_ENABLED', '1') != '0'
_AUTO_PROCESS    = os.environ.get('SYNC_AUTO_PROCESS', '1') != '0'  # 수집 후 자동 변환·재고차감

_last_order_sync: datetime | None = None
_last_settle_sync: datetime | None = None
_lock = threading.Lock()


def _date_range(days_back: int = _DAYS_BACK) -> tuple[str, str]:
    today = datetime.utcnow().date()
    date_from = (today - timedelta(days=days_back)).isoformat()
    date_to = today.isoformat()
    return date_from, date_to


def run_order_sync(app) -> dict:
    """전체 채널 주문 수집 1회 실행. app context 필요."""
    from db_utils import get_db
    from services.marketplace import MarketplaceManager

    date_from, date_to = _date_range()
    results = {}
    try:
        with app.app_context():
            db = get_db()
            mgr = MarketplaceManager(db=db)  # 전체 테넌트 채널 로드 (biz_id 필터 없음)
            channels = [ch['channel'] for ch in mgr.get_all_channels()
                        if ch.get('is_active', True)]
            if not channels:
                logger.info('[SyncScheduler] 활성 채널 없음 — 주문 수집 스킵')
                return {}
            logger.info(f'[SyncScheduler] 주문 수집 시작 — '
                        f'{len(channels)}채널 {date_from}~{date_to}')
            from services.marketplace_sync_service import sync_orders
            for ch in channels:
                try:
                    r = sync_orders(db, mgr, ch, date_from, date_to,
                                    triggered_by='scheduler')
                    results[ch] = r
                    logger.info(f'[SyncScheduler] {ch} 주문: {r}')
                except Exception as e:
                    logger.warning(f'[SyncScheduler] {ch} 주문 수집 실패: {e}')
                    results[ch] = {'error': str(e)}
    except Exception as e:
        logger.error(f'[SyncScheduler] run_order_sync 오류: {e}', exc_info=True)
    return results


def run_order_processing(app, date_from: str, date_to: str) -> dict:
    """api_orders → order_transactions 변환 + 재고차감 자동 처리.

    run_order_sync() 성공 후 자동 호출됨.
    채널별로 api_orders → OrderProcessor → order_transactions → process_orders_to_stock.

    Returns:
        dict: {channel: {converted, saved, outbound_count, errors}, ...}
    """
    results = {}
    try:
        with app.app_context():
            from db_utils import get_db
            from services.marketplace import MarketplaceManager
            from services.api_order_converter import api_orders_to_excel_df
            from services.order_processor import OrderProcessor
            from services.order_to_stock_service import process_orders_to_stock
            import io

            db = get_db()
            mgr = MarketplaceManager(db=db)
            active_channels = [ch['channel'] for ch in mgr.get_all_channels()
                               if ch.get('is_active', True)]
            if not active_channels:
                return {}

            logger.info(f'[SyncScheduler] 주문 자동처리 시작 — {len(active_channels)}채널 {date_from}~{date_to}')

            for channel in active_channels:
                ch_result = {'converted': 0, 'saved': 0, 'outbound_count': 0, 'errors': []}
                try:
                    # 1) api_orders 조회 (해당 채널 + 날짜 범위)
                    client = mgr.get_client(channel)
                    biz_id = client.config.get('biz_id') if client else None
                    orders = db.query_api_orders(
                        channel=channel,
                        date_from=date_from,
                        date_to=date_to,
                        biz_id=biz_id,
                    )
                    if not orders:
                        logger.info(f'[SyncScheduler] {channel} api_orders 없음 — 스킵')
                        continue

                    ch_result['converted'] = len(orders)

                    # 2) api_orders → 채널별 엑셀 DataFrame
                    df = api_orders_to_excel_df(orders, channel)
                    if df.empty:
                        logger.warning(f'[SyncScheduler] {channel} DataFrame 변환 실패')
                        ch_result['errors'].append('DataFrame 변환 실패')
                        results[channel] = ch_result
                        continue

                    # 3) DataFrame → BytesIO Excel → OrderProcessor
                    excel_buf = io.BytesIO()
                    df.to_excel(excel_buf, index=False, engine='openpyxl')
                    excel_buf.seek(0)
                    excel_buf.name = f'{channel}_auto.xlsx'

                    # g.biz_id 설정 — OrderProcessor 내부에서 db._resolve_biz_id가 참조
                    import flask
                    flask.g.biz_id = biz_id

                    proc = OrderProcessor()
                    proc_result = proc.run(
                        mode=channel,
                        order_file=excel_buf,
                        option_file=None,
                        invoice_file=None,
                        target_type='송장',   # DB 저장 + 재고차감 포함 경로
                        output_dir=None,
                        db=db,
                        option_source='db',
                        save_to_db=True,
                        uploaded_by='(자동수집)',
                        biz_id=biz_id,
                    )

                    # success 플래그 또는 db_result 기준으로 저장 여부 판단
                    db_res = (proc_result or {}).get('db_result', {}) or {}
                    saved = db_res.get('inserted', 0) + db_res.get('updated', 0)
                    if proc_result and (proc_result.get('success') or saved > 0):
                        ch_result['saved'] = saved
                        logger.info(f'[SyncScheduler] {channel} order_transactions 저장: '
                                    f'신규 {db_res.get("inserted",0)}건, '
                                    f'갱신 {db_res.get("updated",0)}건')
                    else:
                        err = (proc_result or {}).get('error') or '알 수 없는 오류'
                        ch_result['errors'].append(f'OrderProcessor: {err}')
                        logger.warning(f'[SyncScheduler] {channel} OrderProcessor 실패: {err}')

                except Exception as e:
                    logger.error(f'[SyncScheduler] {channel} 처리 오류: {e}', exc_info=True)
                    ch_result['errors'].append(str(e))

                results[channel] = ch_result

            # 4) 전체 채널 처리 완료 후 재고차감 일괄 실행
            try:
                stock_result = process_orders_to_stock(db, date_from=date_from, date_to=date_to)
                outbound = stock_result.get('outbound_count', 0)
                logger.info(f'[SyncScheduler] 재고차감 완료: {outbound}건')
                for ch in results:
                    results[ch]['outbound_count'] = outbound
                if stock_result.get('shortage'):
                    logger.warning(f'[SyncScheduler] 재고부족: {len(stock_result["shortage"])}품목')
            except Exception as e:
                logger.error(f'[SyncScheduler] 재고차감 오류: {e}', exc_info=True)

    except Exception as e:
        logger.error(f'[SyncScheduler] run_order_processing 오류: {e}', exc_info=True)

    return results


def run_settlement_sync(app) -> dict:
    """전체 채널 정산 수집 1회 실행."""
    from db_utils import get_db
    from services.marketplace import MarketplaceManager

    date_from, date_to = _date_range(days_back=7)
    results = {}
    try:
        with app.app_context():
            db = get_db()
            mgr = MarketplaceManager(db=db)  # 전체 테넌트 채널 로드
            channels = [ch['channel'] for ch in mgr.get_all_channels()
                        if ch.get('is_active', True)]
            if not channels:
                return {}
            logger.info(f'[SyncScheduler] 정산 수집 시작 — '
                        f'{len(channels)}채널 {date_from}~{date_to}')
            from services.marketplace_sync_service import sync_settlements
            for ch in channels:
                try:
                    r = sync_settlements(db, mgr, ch, date_from, date_to,
                                         triggered_by='scheduler')
                    results[ch] = r
                    logger.info(f'[SyncScheduler] {ch} 정산: {r}')
                except Exception as e:
                    logger.warning(f'[SyncScheduler] {ch} 정산 수집 실패: {e}')
                    results[ch] = {'error': str(e)}
    except Exception as e:
        logger.error(f'[SyncScheduler] run_settlement_sync 오류: {e}', exc_info=True)
    return results


def start_sync_scheduler(app) -> None:
    """백그라운드 스레드 기반 스케줄러 시작. app 시작 시 1회 호출."""
    if not _ENABLED:
        logger.info('[SyncScheduler] SYNC_ENABLED=0 — 스케줄러 비활성')
        return

    global _last_order_sync, _last_settle_sync

    def _worker():
        global _last_order_sync, _last_settle_sync
        # 앱 완전 기동 후 2분 대기
        time.sleep(120)
        logger.info('[SyncScheduler] 스케줄러 워커 시작')

        while True:
            now = datetime.utcnow()
            with _lock:
                run_orders = (_last_order_sync is None or
                              (now - _last_order_sync).total_seconds() >= _ORDER_INTERVAL)
                run_settle = (_last_settle_sync is None or
                              (now - _last_settle_sync).total_seconds() >= _SETTLE_INTERVAL)

            if run_orders:
                date_from, date_to = _date_range()
                try:
                    sync_results = run_order_sync(app)
                    # 수집된 채널이 있으면 자동 변환·재고차감
                    if _AUTO_PROCESS and sync_results:
                        has_new = any(
                            (r.get('new', 0) + r.get('updated', 0)) > 0
                            for r in sync_results.values()
                            if isinstance(r, dict)
                        )
                        if has_new:
                            logger.info('[SyncScheduler] 신규 주문 감지 → 자동 처리 시작')
                            run_order_processing(app, date_from, date_to)
                        else:
                            logger.info('[SyncScheduler] 신규 주문 없음 — 자동처리 스킵')
                except Exception as e:
                    logger.error(f'[SyncScheduler] 주문 수집/처리 예외: {e}')
                with _lock:
                    _last_order_sync = datetime.utcnow()

            if run_settle:
                try:
                    run_settlement_sync(app)
                except Exception as e:
                    logger.error(f'[SyncScheduler] 정산 수집 예외: {e}')
                with _lock:
                    _last_settle_sync = datetime.utcnow()

            time.sleep(60)  # 1분마다 조건 체크

    t = threading.Thread(target=_worker, daemon=True, name='marketplace-sync-scheduler')
    t.start()
    logger.info(f'[SyncScheduler] 시작 (주문={_ORDER_INTERVAL//60}분, '
                f'정산={_SETTLE_INTERVAL//60}분, days_back={_DAYS_BACK})')


def get_sync_status() -> dict:
    """현재 동기화 상태 반환 (대시보드/API용)."""
    with _lock:
        return {
            'enabled': _ENABLED,
            'auto_process': _AUTO_PROCESS,
            'order_interval_min': _ORDER_INTERVAL // 60,
            'settle_interval_min': _SETTLE_INTERVAL // 60,
            'last_order_sync': _last_order_sync.isoformat() if _last_order_sync else None,
            'last_settle_sync': _last_settle_sync.isoformat() if _last_settle_sync else None,
        }
