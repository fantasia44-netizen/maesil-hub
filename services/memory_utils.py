"""메모리 / 캐시 / 임시파일 주기적 정리

start_cleanup_scheduler(app) 를 앱 시작 시 1회 호출.
10분마다:
  1. 임시 엑셀/CSV 파일 삭제 (output/ 폴더, 10분 이상 경과)
  2. 인메모리 캐시 초기화 (작업 중이면 건너뜀)
  3. gc.collect() 강제 실행

BusyContext:
  무거운 작업(엑셀생성 등) 진행 중 캐시 삭제 방지.
  with BusyContext(): 으로 감싸면 is_busy() == True → 캐시 삭제 skip.
"""
import gc
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

_INTERVAL = 600        # 10분
_FILE_MAX_AGE = 600    # 10분 이상 된 파일 삭제


# ──────────────────────────────────────
# BusyContext — 작업 중 캐시 삭제 방지
# ──────────────────────────────────────

_busy_count = 0
_busy_lock = threading.Lock()


class BusyContext:
    """무거운 작업(엑셀 생성 등) 진행 중 캐시 삭제를 방지하는 컨텍스트 매니저.

    Usage:
        from services.memory_utils import BusyContext
        with BusyContext():
            # 엑셀 생성 등 무거운 작업
    """
    def __enter__(self):
        global _busy_count
        with _busy_lock:
            _busy_count += 1
        return self

    def __exit__(self, *args):
        global _busy_count
        with _busy_lock:
            _busy_count -= 1


def is_busy() -> bool:
    """현재 무거운 작업이 진행 중이면 True."""
    with _busy_lock:
        return _busy_count > 0


# ──────────────────────────────────────
# 파일 정리
# ──────────────────────────────────────

def _cleanup_output_files(output_dir: str) -> int:
    """output 폴더에서 오래된 엑셀/CSV 삭제. 삭제 건수 반환."""
    if not output_dir or not os.path.isdir(output_dir):
        return 0
    deleted = 0
    now = time.time()
    for fname in os.listdir(output_dir):
        if not fname.endswith(('.xlsx', '.xls', '.csv')):
            continue
        fpath = os.path.join(output_dir, fname)
        try:
            age = now - os.path.getmtime(fpath)
            if age > _FILE_MAX_AGE:
                os.remove(fpath)
                deleted += 1
        except Exception:
            pass
    return deleted


# ──────────────────────────────────────
# 캐시 초기화
# ──────────────────────────────────────

def _clear_caches():
    """모듈별 인메모리 캐시 초기화."""
    cleared = []
    try:
        from services.dashboard_service import _dashboard_cache
        _dashboard_cache['data'] = None
        _dashboard_cache['ts'] = 0
        cleared.append('dashboard')
    except Exception:
        pass

    try:
        from services.stock_service import _unmanaged_cache
        _unmanaged_cache['data'] = None
        _unmanaged_cache['ts'] = 0
        cleared.append('stock')
    except Exception:
        pass

    try:
        from services.render_api import _CACHE
        _CACHE.clear()
        cleared.append('render_api')
    except Exception:
        pass

    # SupabaseDB 인스턴스 내부 캐시 (테넌트별 biz 키잉/ TTL 없는 것 포함) 강제 초기화.
    # hub 는 total 의 app.db_pool(사업자별 인스턴스) 대신 get_db() 프로세스 싱글톤 1개를 쓰므로
    # 그 단일 인스턴스의 캐시만 비우면 된다. background thread 라 app_context 불필요.
    try:
        from db_utils import get_db
        db = get_db()
        if db is not None:
            if isinstance(getattr(db, '_option_cache', None), dict):
                db._option_cache['data'] = None
                db._option_cache['data_list'] = None
                db._option_cache['ts'] = 0
            if isinstance(getattr(db, '_price_cache', None), dict):
                db._price_cache['data'] = None
                db._price_cache['ts'] = 0
            if hasattr(db, '_option_cache_by_biz'):
                db._option_cache_by_biz = {}          # 테넌트별 옵션 캐시 전체 무효화
            if hasattr(db, '_product_norm_cache'):
                db._product_norm_cache = None          # TTL 없음 — 강제 초기화 필수
            cleared.append('db_caches')
    except Exception:
        pass

    return cleared


# ──────────────────────────────────────
# GC
# ──────────────────────────────────────

def _get_memory_mb() -> float:
    try:
        import psutil
        return round(psutil.Process(os.getpid()).memory_info().rss / 1048576, 1)
    except Exception:
        return 0.0


def force_gc(label: str = '') -> dict:
    before = _get_memory_mb()
    collected = gc.collect(generation=2)
    after = _get_memory_mb()
    freed = round(before - after, 1)
    logger.info(f'[GC] {label} collected={collected} '
                f'before={before}MB after={after}MB freed={freed}MB')
    return {'before': before, 'after': after, 'freed': freed}


# ──────────────────────────────────────
# 스케줄러
# ──────────────────────────────────────

def start_cleanup_scheduler(app=None):
    """백그라운드 스레드로 10분마다 정리 실행."""
    output_dir = ''
    if app:
        with app.app_context():
            output_dir = app.config.get('OUTPUT_FOLDER', 'output')

    def _run():
        time.sleep(60)  # 앱 시작 후 1분 대기
        while True:
            try:
                # 1. 파일 정리 (작업 중이어도 파일은 삭제 — 사용 중인 파일은 OS가 막음)
                deleted = _cleanup_output_files(output_dir)

                # 2. 캐시 초기화 (작업 중이면 건너뜀)
                if is_busy():
                    logger.info('[Cleanup] 작업 진행 중 — 캐시 초기화 건너뜀')
                    cleared = []
                else:
                    cleared = _clear_caches()

                # 3. GC
                result = force_gc('periodic')

                logger.info(
                    f'[Cleanup] 완료 — 파일삭제={deleted}개 '
                    f'캐시초기화={cleared} 메모리={result["after"]}MB'
                )
            except Exception as e:
                logger.debug(f'[Cleanup] 실패: {e}')

            time.sleep(_INTERVAL)

    t = threading.Thread(target=_run, daemon=True, name='cleanup-scheduler')
    t.start()
    logger.info(f'[Cleanup] 스케줄러 시작 (interval={_INTERVAL}s)')


# ──────────────────────────────────────
# 미처리 주문 catchup 스케줄러 (매일 새벽 3시 KST)
# ──────────────────────────────────────

_CATCHUP_INTERVAL = 86400  # 24시간


def _active_biz_ids(db) -> list:
    """catchup 대상 테넌트 biz_id 목록. businesses(삭제 제외)를 직접 조회.

    businesses 는 테넌트 레지스트리(자체엔 biz_id 컬럼 없음) → tenant_guard 대상 아님.
    scheduler 의 db.client 는 service_role(admin) 클라이언트라 전 테넌트 조회 가능.
    """
    try:
        res = db.client.table('businesses').select('id') \
            .eq('is_deleted', False).limit(1000).execute()
        return [r['id'] for r in (res.data or []) if r.get('id') is not None]
    except Exception as e:
        logger.warning(f'[OutboundCatchup] 테넌트 목록 조회 실패: {e}')
        return []


def start_outbound_catchup_scheduler(app):
    """매일 새벽 3시 KST에 날짜 필터 없이 미처리 주문 전체를 테넌트별로 출고 처리.

    날짜 범위 누락 또는 행 한계로 처리 못한 누락분을 자동 복구.
    ★ hub 멀티테넌트판: 전 테넌트를 순회하며 flask.g.biz_id 를 세팅한 뒤
      process_orders_to_stock(테넌트 스코프) 를 호출한다.
    """
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo

    KST = ZoneInfo('Asia/Seoul')

    def _seconds_until_3am():
        """다음 KST 03:00까지 남은 초."""
        from datetime import datetime, timedelta
        now = datetime.now(KST)
        target = now.replace(hour=3, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        return (target - now).total_seconds()

    def _run():
        # 첫 실행: 다음 새벽 3시까지 대기
        wait = _seconds_until_3am()
        logger.info(f'[OutboundCatchup] 스케줄러 시작 — 첫 실행까지 '
                    f'{int(wait/3600)}시간 {int((wait%3600)/60)}분 대기')
        time.sleep(wait)

        while True:
            try:
                logger.info('[OutboundCatchup] 미처리 주문 catchup 시작 (전 테넌트)')
                import flask
                from db_utils import get_db
                from services.order_to_stock_service import process_orders_to_stock
                with app.app_context():
                    db = get_db()
                    if db is None:
                        logger.warning('[OutboundCatchup] DB 없음 — 스킵')
                    else:
                        biz_ids = _active_biz_ids(db)
                        for bz in biz_ids:
                            flask.g.biz_id = bz  # 테넌트 스코프
                            try:
                                # 날짜 필터 없이 해당 테넌트 미처리 주문 처리
                                result = process_orders_to_stock(db, force_shortage=True)
                                processed = result.get('processed_orders', 0)
                                outbound  = result.get('outbound_count', 0)
                                errors    = result.get('errors', [])
                                if processed or outbound or errors:
                                    logger.info(
                                        f'[OutboundCatchup] biz={bz} 완료 — '
                                        f'주문 {processed}건 처리, 출고 {outbound}건'
                                        + (f', 오류 {len(errors)}건' if errors else '')
                                    )
                            except Exception as be:
                                logger.error(f'[OutboundCatchup] biz={bz} 오류: {be}',
                                             exc_info=True)
                        flask.g.biz_id = None
            except Exception as e:
                logger.error(f'[OutboundCatchup] 오류: {e}', exc_info=True)

            # 다음 새벽 3시까지 대기
            time.sleep(_seconds_until_3am())

    t = threading.Thread(target=_run, daemon=True, name='outbound-catchup')
    t.start()
    logger.info('[OutboundCatchup] 스케줄러 등록 완료 (매일 KST 03:00, 전 테넌트)')
