"""
auto_pipeline.py — 무인 자동화 파이프라인 (주문 자동수집 + CJ 송장 자동채번).

기존에는 직원이 화면에서 [수집] 버튼 → [CJ 송장 보내기] 버튼을 수동으로 눌러야 했다.
이 모듈이 두 단계를 백그라운드 스레드로 자동 수행한다.

  1) run_order_collection : 네이버/쿠팡/자사몰 API 주문 자동수집 → DB 저장 + 재고차감
     (blueprints/orders_api.py 의 api_collect 와 동일한 처리, 단 Flask 요청/세션 없이 동작)
  2) run_cj_invoicing     : 송장 미배정 주문 자동 채번 + 예약접수 + 마켓 push
     ★ 화면 토글 auto_cj_enabled(DB) 이 ON 일 때만 동작 (기본 OFF).

■ ★ hub 멀티테넌트판 — total(단일 DEFAULT_BUSINESS 전제)을 전 테넌트 순회로 재작성
  total 은 app.db_pool.get(DEFAULT_BUSINESS) 단일 처리였다. hub 는 단일 Supabase +
  행단위 biz_id 격리이므로 sync_scheduler.py 패턴을 따라:
    - MarketplaceManager(db=db) 로 전 테넌트 활성 채널(marketplace_api_config)을 로드,
    - 채널을 biz_id 별로 묶어 각 테넌트를 순회,
    - 테넌트 처리 전 flask.g.biz_id 를 세팅(OrderProcessor/db 내부 _resolve_biz_id 참조),
    - app_settings 토글(get/set_app_setting)은 (biz_id,key) 스코프 → biz_id 를 명시 전달.
  force-run-now(화면 '지금 즉시 실행') 는 요청 테넌트 1곳만 돌려야 하므로 biz_id 인자로 좁힌다.

■ 절대시각 경계 스케줄링
  gunicorn --max-requests 는 요청 N건마다 워커를 재시작한다.
  상대 interval(time.sleep(3600)) 방식은 재시작 때 타이머가 리셋돼 실행이 계속 밀린다.
  그래서 상대시간이 아니라 "다음 정시(또는 N분 경계)"를 목표로 남은 초를 계산해 대기한다.
  → 워커가 언제 재시작하든 경계 시각에 정확히 실행된다.

■ 워커 중복 주의
  Procfile workers 1 전제(앱 부팅 시 스케줄러 1회 기동). 워커가 늘면 이중 수집·이중 채번
  위험이 있으므로 워커수 증설 시 워커0 전용 가드가 선행돼야 한다.
"""
import os
import io
import time
import logging
import tempfile
import threading
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def _kst():
    try:
        from zoneinfo import ZoneInfo
    except ImportError:  # py<3.9
        from backports.zoneinfo import ZoneInfo
    return ZoneInfo('Asia/Seoul')


def _flag_on(db, key, biz_id, default=True) -> bool:
    """app_settings 의 {"on": bool} 토글 조회(테넌트별). DB 오류 시 default."""
    try:
        v = db.get_app_setting(key, {'on': default}, biz_id=biz_id)
        if isinstance(v, dict):
            return bool(v.get('on', default))
        return bool(v)
    except Exception:
        return default


def _record_run(db, key, payload, biz_id):
    """마지막 실행 이력 기록 (화면 표시용, 테넌트별)."""
    try:
        from datetime import timezone
        data = {'at': datetime.now(timezone.utc).isoformat()}
        data.update(payload)
        db.set_app_setting(key, data, updated_by='scheduler', biz_id=biz_id)
    except Exception:
        pass


def _seconds_until_next_slot(interval_min: int, KST) -> float:
    """다음 N분 경계(자정 기준)까지 남은 초. 예: interval=60 → 다음 정시, 30 → 다음 :00/:30."""
    now = datetime.now(KST)
    mins_since_midnight = now.hour * 60 + now.minute
    next_slot_min = ((mins_since_midnight // interval_min) + 1) * interval_min
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    target = midnight + timedelta(minutes=next_slot_min)
    return max((target - now).total_seconds(), 1.0)


def _biz_channel_map(mgr) -> dict:
    """활성 채널을 biz_id 별로 묶는다. {biz_id: [channel, ...]}.

    MarketplaceManager 는 marketplace_api_config row(cfg)를 client.config 로 보관하며
    cfg['biz_id'] 로 테넌트를 판별한다. biz_id 미상 채널은 격리 위반 방지를 위해 스킵.
    """
    out = {}
    for ch in mgr.get_active_channels():
        client = mgr.get_client(ch)
        if not client:
            continue
        bz = (client.config or {}).get('biz_id')
        if bz is None:
            logger.warning(f'[AutoPipeline] 채널 {ch} biz_id 미상 — 스킵(격리 보호)')
            continue
        out.setdefault(bz, []).append(ch)
    return out


# ──────────────────────────────────────────────────────────
# 1) 주문 자동수집
# ──────────────────────────────────────────────────────────

def run_order_collection(app, force=False, biz_id=None) -> dict:
    """활성 채널 전체(전 테넌트)에서 최근 주문 자동수집 → api_orders 저장 + OrderProcessor.

    blueprints/orders_api.py::api_collect 와 동일한 채널별 처리(2차 보충수집 포함)를
    Flask 요청 컨텍스트 없이 재현한다. 테넌트/채널별 독립 처리 — 한 곳 실패가 다른 곳을 막지 않음.

    force=True 면 화면 토글 OFF 여도 강제 실행('지금 즉시 수집' 버튼용).
    biz_id 지정 시 해당 테넌트만 처리(force-run-now). None 이면 전 테넌트 순회(스케줄러).

    Returns: {biz_id: {channel: 결과문자열, ...}, ...}
    """
    import flask
    from services.marketplace import MarketplaceManager
    from services.order_processor import OrderProcessor
    from services.api_order_converter import api_orders_to_excel_df
    from services.tz_utils import days_ago_kst, today_kst
    from db_utils import get_db

    summary = {}
    with app.app_context():
        db = get_db()
        if db is None:
            logger.warning('[AutoCollect] db 없음 — 스킵')
            return {}
        mgr = MarketplaceManager(db=db, biz_id=biz_id)  # biz_id None → 전 테넌트
        biz_map = _biz_channel_map(mgr)
        if not biz_map:
            logger.info('[AutoCollect] 활성 채널 없음 — 스킵')
            return {}

        # 최근 3일 재수집 (신규주문은 대부분 최근 — 더 긴 누락분은 매일 03:00 catchup 이 커버).
        # api_orders 는 (channel,api_order_id,api_line_id) upsert, OrderProcessor 는 기존건 skip 이라 재실행 안전.
        date_from = days_ago_kst(3)
        date_to = today_kst()
        collection_date = today_kst()
        uploaded_by = '자동수집(스케줄러)'

        try:
            for bz, channels in biz_map.items():
                flask.g.biz_id = bz  # OrderProcessor/db 내부 _resolve_biz_id 참조

                # 화면 토글(DB, 테넌트별) OFF 면 스킵 — 수동 버튼은 이 게이트와 무관
                if not force and not _flag_on(db, 'auto_collect_enabled', bz, default=True):
                    logger.info(f'[AutoCollect] biz={bz} 토글 OFF — 스킵')
                    summary[bz] = {'skipped': 'off'}
                    continue

                output_dir = tempfile.mkdtemp(prefix='auto_collect_')
                biz_summary = {}

                for ch in channels:
                    client = mgr.get_client(ch)
                    if not client:
                        biz_summary[ch] = '클라이언트 없음'
                        continue

                    # 토큰 갱신 — is_ready 여도 항상 갱신 시도 (만료 access token 401 방지).
                    try:
                        client.refresh_token(db)
                    except Exception:
                        pass
                    if not client.is_ready:
                        biz_summary[ch] = '인증 미완료'
                        continue

                    try:
                        # 1) 1차 수집
                        orders = client.fetch_orders(date_from, date_to,
                                                     status_filter='invoice_target')
                        if not orders:
                            biz_summary[ch] = '0건'
                            continue

                        # 1.1) 2차 보충 수집 (네이버 API 간헐 누락 대응 — 3초 후 재호출)
                        first_ids = set(o.get('api_line_id', '') for o in orders)
                        time.sleep(3)
                        try:
                            orders_2nd = client.fetch_orders(date_from, date_to,
                                                             status_filter='invoice_target')
                            if orders_2nd:
                                new_orders = [o for o in orders_2nd
                                              if o.get('api_line_id', '') not in first_ids]
                                if new_orders:
                                    orders.extend(new_orders)
                        except Exception as e2:
                            logger.warning(f'[AutoCollect] {ch} 2차 보충 실패(무시): {e2}')

                        # 1.5) api_orders 원본 저장 (송장 매핑용) — biz_id 명시 주입
                        try:
                            api_rows = [{
                                'channel': ch,
                                'api_order_id': o.get('api_order_id', ''),
                                'api_line_id': o.get('api_line_id', ''),
                                'order_date': (o.get('order_date', '') or '')[:10] or collection_date,
                                'match_status': 'matched',
                                'raw_data': o.get('raw_data', {}),
                            } for o in orders]
                            if api_rows:
                                db.upsert_api_orders_batch(api_rows, biz_id=bz)
                        except Exception as api_err:
                            logger.error(f'[AutoCollect] {ch} api_orders 저장 실패: {api_err}',
                                         exc_info=True)

                        # 2) raw_data → 엑셀 DataFrame
                        df = api_orders_to_excel_df(orders, ch)
                        if df.empty:
                            biz_summary[ch] = '변환 실패'
                            continue

                        excel_buf = io.BytesIO()
                        df.to_excel(excel_buf, index=False, engine='openpyxl')
                        excel_buf.seek(0)
                        excel_buf.name = f'{ch}_api_orders.xlsx'

                        # 3) OrderProcessor 실행 (save_to_db=True → DB 저장 + 재고차감)
                        proc = OrderProcessor()
                        result = proc.run(
                            mode=ch, order_file=excel_buf, option_file=None, invoice_file=None,
                            target_type='송장', output_dir=output_dir, db=db,
                            option_source='db', save_to_db=True,
                            uploaded_by=uploaded_by, collection_date=collection_date,
                            biz_id=bz,
                        )

                        if result.get('success'):
                            dbr = result.get('db_result', {})
                            biz_summary[ch] = (f"신규{dbr.get('inserted', 0)}"
                                               f"/수정{dbr.get('updated', 0)}"
                                               f"/스킵{dbr.get('skipped', 0)}")
                        elif result.get('unmatched'):
                            # 미매칭 → OrderProcessor 가 저장 전 중단 (재고 미반영). 롤백 불필요, 기록만.
                            biz_summary[ch] = f"미매칭 {len(result['unmatched'])}건 — 저장안함"
                            logger.warning(f'[AutoCollect] biz={bz} {ch} 미매칭 '
                                           f'{len(result["unmatched"])}건 — 수동확인 필요')
                        else:
                            biz_summary[ch] = f"실패: {str(result.get('error', ''))[:40]}"

                    except Exception as e:
                        logger.error(f'[AutoCollect] biz={bz} {ch} 오류: {e}', exc_info=True)
                        biz_summary[ch] = f'오류: {str(e)[:40]}'

                logger.info(f'[AutoCollect] biz={bz} 완료 — {biz_summary}')
                _record_run(db, 'auto_collect_last_run', {'summary': biz_summary}, bz)
                summary[bz] = biz_summary
        finally:
            flask.g.biz_id = None

    return summary


# ──────────────────────────────────────────────────────────
# 2) CJ 송장 자동채번 + 마켓 push
# ──────────────────────────────────────────────────────────

def run_cj_invoicing(app, force=False, biz_id=None) -> dict:
    """송장 미배정 주문 자동 채번 + 예약접수 + 마켓 push (전 테넌트 순회).

    ★ 화면 토글 auto_cj_enabled(DB, 테넌트별) 이 ON 일 때만 동작 (기본 OFF).
      CJ 승인 + 라벨 출력검증이 끝난 뒤 사람이 화면에서 자동채번을 켠다.
      수동 [CJ 송장 보내기] 버튼은 이 토글과 무관하게 항상 동작(테스트 가능).

    biz_id 지정 시 해당 테넌트만 처리(force-run-now). None 이면 전 테넌트 순회(스케줄러).

    Returns: {biz_id: {total, success, failed} | {skipped}, ...}
    """
    import flask
    from services.marketplace import MarketplaceManager
    from services.cj_shipping_service import (
        query_orders_without_invoice, generate_cj_invoices,
    )
    from services.marketplace_sync_service import push_invoices
    from db_utils import get_db

    summary = {}
    cap = int(os.getenv('AUTO_CJ_LIMIT', '200'))  # 1회 실행당 상한 (폭주 방지)

    with app.app_context():
        db = get_db()
        if db is None:
            logger.warning('[AutoCJ] db 없음 — 스킵')
            return {}
        mgr = MarketplaceManager(db=db, biz_id=biz_id)
        biz_map = _biz_channel_map(mgr)
        if not biz_map:
            logger.debug('[AutoCJ] 활성 채널 없음 — 스킵')
            return {}

        try:
            for bz, channels in biz_map.items():
                flask.g.biz_id = bz

                if not force and not _flag_on(db, 'auto_cj_enabled', bz, default=False):
                    logger.debug(f'[AutoCJ] biz={bz} 토글 OFF — 스킵 (CJ 승인 후 화면에서 켜기)')
                    summary[bz] = {'skipped': 'off'}
                    continue

                # 송장 미배정 주문 조회 (db 내부 _resolve_biz_id 가 g.biz_id=bz 로 스코프)
                orders = query_orders_without_invoice(db, limit=cap)
                if not orders:
                    logger.debug(f'[AutoCJ] biz={bz} 미배정 주문 없음')
                    summary[bz] = {'total': 0, 'success': 0}
                    continue

                result = generate_cj_invoices(db, orders)
                logger.info(f'[AutoCJ] biz={bz} 채번 '
                            f'{result.get("success", 0)}/{result.get("total", 0)}건'
                            + (f', 실패 {result.get("failed", 0)}건'
                               if result.get('failed') else ''))

                # 성공 건 채널별 그룹 → 마켓 자동 push (발송처리)
                if result.get('success', 0) > 0:
                    order_channel_map = {o['order_no']: o['channel'] for o in orders}
                    by_channel = {}
                    for r in result.get('results', []):
                        if r.get('ok'):
                            ch = order_channel_map.get(r['order_no'])
                            if ch:
                                by_channel.setdefault(ch, []).append(r['order_no'])
                    for ch, order_nos in by_channel.items():
                        try:
                            push_res = push_invoices(db, mgr, ch,
                                                     triggered_by='자동채번(스케줄러)',
                                                     order_nos=order_nos)
                            logger.info(f'[AutoCJ] biz={bz} {ch} push '
                                        f'{push_res.get("success", 0)}/'
                                        f'{push_res.get("total", 0)}건')
                        except Exception as push_err:
                            logger.error(f'[AutoCJ] biz={bz} {ch} push 오류: {push_err}',
                                         exc_info=True)

                run_payload = {
                    'total': result.get('total', 0),
                    'success': result.get('success', 0),
                    'failed': result.get('failed', 0),
                }
                _record_run(db, 'auto_cj_last_run', {'result': run_payload}, bz)
                summary[bz] = run_payload
        finally:
            flask.g.biz_id = None

    return summary


# ──────────────────────────────────────────────────────────
# 스케줄러 시작
# ──────────────────────────────────────────────────────────

def start_auto_collect_scheduler(app):
    """주문 자동수집 스케줄러 — 매 N분 경계마다 전 테넌트 실행 (기본 60분)."""
    if os.getenv('AUTO_COLLECT_ENABLED', 'true').lower() != 'true':
        logger.info('[AutoCollect] 비활성 (AUTO_COLLECT_ENABLED=false)')
        return

    interval = max(int(os.getenv('AUTO_COLLECT_INTERVAL_MIN', '60')), 5)
    KST = _kst()

    def _run():
        time.sleep(120)  # 앱 기동 안정화 대기
        while True:
            time.sleep(_seconds_until_next_slot(interval, KST))  # 경계까지 대기
            try:
                run_order_collection(app)  # 함수 내부에서 app_context 관리
            except Exception as e:
                logger.error(f'[AutoCollect] 루프 오류: {e}', exc_info=True)

    threading.Thread(target=_run, daemon=True, name='auto-collect').start()
    logger.info(f'[AutoCollect] 스케줄러 시작 (매 {interval}분 경계)')


def start_cj_invoice_scheduler(app):
    """CJ 송장 자동채번 스케줄러 — 매 N분 경계마다 전 테넌트 실행 (기본 30분).

    스레드는 뜨지만 실제 실행 여부는 화면 토글 auto_cj_enabled(DB, 테넌트별, 기본 OFF)이 결정한다.
    AUTO_CJ_ENABLED=false 는 스레드 자체를 끄는 하드 킬스위치(비상용).
    """
    if os.getenv('AUTO_CJ_ENABLED', 'true').lower() != 'true':
        logger.info('[AutoCJ] 스케줄러 하드OFF (AUTO_CJ_ENABLED=false)')
        return

    interval = max(int(os.getenv('AUTO_CJ_INTERVAL_MIN', '30')), 5)
    KST = _kst()

    def _run():
        time.sleep(150)  # 자동수집보다 살짝 뒤에 기동
        while True:
            time.sleep(_seconds_until_next_slot(interval, KST))
            try:
                run_cj_invoicing(app)  # 함수 내부에서 app_context 관리
            except Exception as e:
                logger.error(f'[AutoCJ] 루프 오류: {e}', exc_info=True)

    threading.Thread(target=_run, daemon=True, name='cj-invoice').start()
    logger.info(f'[AutoCJ] 스케줄러 시작 (매 {interval}분 경계, 테넌트별 토글 게이트)')
