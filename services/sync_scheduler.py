"""sync_scheduler.py — 마켓플레이스 주문/정산 자동 수집 스케줄러.

앱 시작 시 start_sync_scheduler(app) 1회 호출.
기본: 30분마다 전체 채널 주문 수집, 6시간마다 정산 수집.

환경변수:
    SYNC_ORDER_INTERVAL_MIN   주문 수집 주기 (분, 기본 30)
    SYNC_SETTLE_INTERVAL_MIN  정산 수집 주기 (분, 기본 360)
    SYNC_DAYS_BACK            수집 기준 일수 (기본 2 — 오늘+전일)
    SYNC_ENABLED              '0'이면 스케줄러 비활성 (기본 '1')
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_ORDER_INTERVAL = int(os.environ.get('SYNC_ORDER_INTERVAL_MIN', '30')) * 60
_SETTLE_INTERVAL = int(os.environ.get('SYNC_SETTLE_INTERVAL_MIN', '360')) * 60
_DAYS_BACK = int(os.environ.get('SYNC_DAYS_BACK', '2'))
_ENABLED = os.environ.get('SYNC_ENABLED', '1') != '0'

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
                try:
                    run_order_sync(app)
                except Exception as e:
                    logger.error(f'[SyncScheduler] 주문 수집 예외: {e}')
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
            'order_interval_min': _ORDER_INTERVAL // 60,
            'settle_interval_min': _SETTLE_INTERVAL // 60,
            'last_order_sync': _last_order_sync.isoformat() if _last_order_sync else None,
            'last_settle_sync': _last_settle_sync.isoformat() if _last_settle_sync else None,
        }
