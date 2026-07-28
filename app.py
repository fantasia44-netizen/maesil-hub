"""
maesil-hub — 식품·축산 ERP/WMS SaaS.
"""
import os
import logging
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template, g, session, redirect, url_for, Blueprint
from flask_login import LoginManager, current_user
from dotenv import load_dotenv

load_dotenv()

# ─── Sentry ───
SENTRY_DSN = os.environ.get('SENTRY_DSN', '').strip()
if SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            integrations=[FlaskIntegration()],
            traces_sample_rate=0.1,
            environment=os.environ.get('APP_ENV', 'development'),
        )
    except Exception as e:
        logging.warning(f'Sentry init failed: {e}')


class _NullMarketplace:
    """MarketplaceManager 초기화 실패 시 대체 (빈 객체)."""
    def get_all_channels(self): return []
    def get_client(self, *a, **kw): return None
    def __getattr__(self, name): return lambda *a, **kw: []


def create_app():
    app = Flask(__name__)
    from config import Config
    app.config.from_object(Config)

    # ─── 업로드/출력 폴더 자동 생성 ───
    import pathlib
    for folder_key in ('UPLOAD_FOLDER', 'OUTPUT_FOLDER'):
        folder = app.config.get(folder_key, '')
        if folder:
            pathlib.Path(folder).mkdir(parents=True, exist_ok=True)

    # ─── 인코딩 / 시간 표준 (CONVENTIONS.md 1, 2) ───
    # JSON 응답 UTF-8 (한글 escape 안 함)
    app.config['JSON_AS_ASCII'] = False
    app.json.ensure_ascii = False
    # 로그 타임스탬프 KST
    import time as _time
    logging.Formatter.converter = lambda *args: _time.localtime(_time.time() + 9 * 3600)

    # Jinja KST 필터
    from services.tz_utils import to_kst
    @app.template_filter('kst')
    def _kst_filter(dt, fmt='%Y-%m-%d %H:%M'):
        if not dt:
            return ''
        kst = to_kst(dt)
        return kst.strftime(fmt) if kst else str(dt)
    @app.template_filter('kst_date')
    def _kst_date_filter(dt):
        return _kst_filter(dt, '%Y-%m-%d')
    @app.template_filter('kst_full')
    def _kst_full_filter(dt):
        return _kst_filter(dt, '%Y-%m-%d %H:%M:%S KST')

    @app.template_filter('fmt_qty')
    def _fmt_qty(v):
        """수량 포맷: 소수점 없으면 정수, 있으면 소수 1자리."""
        try:
            f = float(v)
            return f'{int(f):,}' if f == int(f) else f'{f:,.1f}'
        except (TypeError, ValueError):
            return v if v is not None else '-'

    @app.template_filter('fmt_money')
    def _fmt_money(v):
        """금액 포맷: 천단위 콤마, None은 '-'."""
        try:
            return f'{int(round(float(v))):,}'
        except (TypeError, ValueError):
            return v if v is not None else '-'

    @app.template_filter('fmt_kst')
    def _fmt_kst(v, fmt='%Y-%m-%d %H:%M'):
        """UTC/ISO 문자열 → KST 표시 (kst 필터 별칭)."""
        return _kst_filter(v, fmt)

    # ─── 멀티테넌트 가드 ───
    # SupabaseDB 모든 메서드에 biz_id=g.biz_id 자동 주입.
    # 레거시 blueprints에서 biz_id 누락해도 사업자 격리 보장.
    try:
        from db.tenant import install_tenant_guard
        install_tenant_guard()
    except Exception as e:
        logging.warning(f'tenant_guard install failed: {e}')

    # ─── Logging ───
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    )

    # ─── Flask-WTF CSRF ───
    from flask_wtf.csrf import CSRFProtect
    csrf = CSRFProtect(app)

    # ─── Flask-Login ───
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'

    from auth.models import load_user_by_id
    @login_manager.user_loader
    def load_user(user_id):
        return load_user_by_id(user_id)

    # ─── Blueprints ───
    from auth.views import auth_bp
    app.register_blueprint(auth_bp)

    # main blueprint (홈/대시보드)
    main_bp = Blueprint('main', __name__)

    @main_bp.route('/')
    def index():
        if current_user.is_authenticated:
            return redirect(url_for('main.dashboard'))
        return render_template('landing.html')

    @main_bp.route('/dashboard')
    def dashboard():
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login'))
        # 슈퍼어드민은 회사 없어도 어드민 콘솔로
        if current_user.is_super_admin and not g.biz_id:
            return redirect(url_for('admin_saas.dashboard'))
        if not g.biz_id:
            return redirect(url_for('auth.select_business'))

        from db.client import get_admin_client
        from datetime import date
        c = get_admin_client()
        biz_id = g.biz_id
        today = date.today().isoformat()
        month_start = today[:8] + '01'

        today_orders = pending_ship = stock_items = month_revenue = '-'
        revenue_trend = []

        # ── KPI 통합 RPC (API 5회 → 2회) ──────────────────────────────
        try:
            kpi = c.rpc('get_dashboard_kpi', {
                'p_biz_id': biz_id,
                'p_today': today,
                'p_month_start': month_start,
            }).execute().data or {}
            today_orders  = int(kpi.get('today_orders',  0) or 0)
            pending_ship  = int(kpi.get('pending_ship',  0) or 0)
            stock_items   = int(kpi.get('stock_items',   0) or 0)
            month_revenue = int(kpi.get('month_revenue', 0) or 0)
        except Exception:
            # 폴백: 개별 쿼리
            try:
                r = c.table('order_transactions').select('id', count='exact') \
                    .eq('biz_id', biz_id).eq('order_date', today).limit(1).execute()
                today_orders = r.count or 0
            except Exception:
                pass
            try:
                r = c.table('order_transactions').select('id', count='exact') \
                    .eq('biz_id', biz_id).eq('is_outbound_done', False) \
                    .neq('status', '취소').limit(1).execute()
                pending_ship = r.count or 0
            except Exception:
                pass
            try:
                r = c.rpc('get_stock_summary', {'p_biz_id': biz_id}).execute()
                stock_items = len(r.data) if r.data else 0
            except Exception:
                pass
            try:
                r = c.rpc('get_revenue_summary_agg', {
                    'p_date_from': month_start, 'p_date_to': today,
                    'p_category': None, 'p_biz_id': biz_id,
                }).execute()
                _s = r.data[0] if (r.data and isinstance(r.data, list)) else (r.data or {})
                month_revenue = int(_s.get('total_settlement', 0) or 0)
            except Exception:
                pass

        # 7일 매출 추이
        try:
            from db_utils import get_db
            revenue_trend = get_db().query_revenue_trend(days=7, biz_id=biz_id)
        except Exception:
            pass

        return render_template('dashboard.html',
            biz_id=biz_id,
            today=today,
            today_orders=today_orders,
            pending_ship=pending_ship,
            stock_items=stock_items,
            month_revenue=month_revenue,
            revenue_trend=revenue_trend,
        )

    app.register_blueprint(main_bp)

    # ─── ERP/WMS Blueprints (40개 일괄 등록) ───
    try:
        from blueprints import register_all as register_erp_blueprints
        registered, failed = register_erp_blueprints(app)
        logging.info(f'ERP blueprints: {len(registered)} registered, {len(failed)} failed')
    except Exception as e:
        logging.warning(f'ERP blueprints registration failed: {e}')

    # ─── CSRF exempt: 외부 서버 webhook (HMAC 자체 검증) ───
    try:
        from blueprints.billing import webhook as billing_webhook
        csrf.exempt(billing_webhook)
    except Exception as e:
        logging.warning(f'billing webhook csrf exempt 실패: {e}')

    # ─── Health check (매실에이전시용) ───
    @app.route('/health')
    def health():
        db_ok = False
        try:
            from db.client import get_admin_client
            r = get_admin_client().table('plans').select('id').limit(1).execute()
            db_ok = bool(r.data is not None)
        except Exception:
            db_ok = False
        return jsonify({
            'status': 'ok' if db_ok else 'degraded',
            'service': 'maesil-hub',
            'env': os.environ.get('APP_ENV', 'development'),
            'db': 'ok' if db_ok else 'error',
            'time': datetime.now(timezone.utc).isoformat(),
        })

    # ─── 템플릿 전역 컨텍스트 ───
    def _get_active_notices():
        """활성 시스템 공지 (전역) — 60초 프로세스 캐시. 배너용.
        조건: is_active AND now BETWEEN starts_at AND ends_at (null=무제한)."""
        import time as _time
        from datetime import datetime, timezone
        cache = getattr(app, '_notice_cache', None)
        now = _time.time()
        if cache and (now - cache[0] < 60):
            return cache[1]
        notices = []
        try:
            from db.client import get_admin_client
            rows = get_admin_client().table('system_notices').select('*') \
                .eq('is_active', True).order('created_at', desc=True).limit(10).execute().data or []
            now_dt = datetime.now(timezone.utc)
            for r in rows:
                s, e = r.get('starts_at'), r.get('ends_at')
                try:
                    if s and datetime.fromisoformat(str(s).replace('Z', '+00:00')) > now_dt:
                        continue
                    if e and datetime.fromisoformat(str(e).replace('Z', '+00:00')) < now_dt:
                        continue
                except Exception:
                    pass
                notices.append(r)
        except Exception:
            notices = []
        app._notice_cache = (now, notices)
        return notices

    @app.context_processor
    def inject_globals():
        """모든 템플릿에 current_biz, active_notices 등 주입."""
        from flask import g as _g
        biz = None
        if hasattr(_g, 'biz_id') and _g.biz_id:
            biz = type('Biz', (), {
                'id': _g.biz_id,
                'name': getattr(_g, 'biz_name', None) or str(_g.biz_id),
            })()
        notices = []
        if current_user.is_authenticated:
            notices = _get_active_notices()
        return dict(current_biz=biz, active_notices=notices)

    # ─── MarketplaceManager 클래스 사전 임포트 (before_request에서 빠르게 사용) ───
    try:
        from services.marketplace import MarketplaceManager
        app._MarketplaceManager = MarketplaceManager
    except Exception as e:
        logging.warning(f'MarketplaceManager import failed: {e}')
        app._MarketplaceManager = None

    # ─── 마켓플레이스 자동 수집 스케줄러 ───
    try:
        from services.sync_scheduler import start_sync_scheduler
        start_sync_scheduler(app)
    except Exception as e:
        logging.warning(f'sync_scheduler 시작 실패: {e}')

    # ─── Tenant context ───
    @app.before_request
    def set_tenant_context():
        g.biz_id = None
        g.biz_name = None
        g.user_role = None
        g.is_impersonating = False
        g.marketplace = _NullMarketplace()  # 기본값; biz_id 확정 후 교체
        if not current_user.is_authenticated:
            return
        # impersonation 우선
        if session.get('impersonating_biz_id'):
            g.biz_id = session['impersonating_biz_id']
            g.is_impersonating = True
        else:
            g.biz_id = session.get('current_biz_id')

        # 현재 biz에서의 역할 해석 — current_user.role 프로퍼티가 g.user_role 참조.
        # 미설정 시 항상 'viewer'로 떨어져 packing_required 등 role 기반 접근이 깨짐.
        if g.biz_id:
            if current_user.is_super_admin:
                g.user_role = 'admin'
            else:
                try:
                    from auth.helpers import get_user_role
                    g.user_role = get_user_role(current_user.id, g.biz_id)
                except Exception:
                    g.user_role = None

        # 회사명 캐시 (session 활용)
        if g.biz_id:
            cache_key = f'biz_name_{g.biz_id}'
            if cache_key in session:
                g.biz_name = session[cache_key]
            else:
                try:
                    from db.client import get_admin_client
                    r = get_admin_client().table('businesses').select('name') \
                        .eq('id', g.biz_id).single().execute()
                    g.biz_name = (r.data or {}).get('name', '')
                    session[cache_key] = g.biz_name
                except Exception:
                    g.biz_name = str(g.biz_id)

            # MarketplaceManager: biz_id별 채널만 로드 (멀티테넌트 격리)
            _Mgr = getattr(app, '_MarketplaceManager', None)
            if _Mgr:
                try:
                    from db_utils import get_db
                    g.marketplace = _Mgr(db=get_db(), biz_id=g.biz_id)
                except Exception as _me:
                    logging.warning(f'MarketplaceManager per-request init failed: {_me}')
                    g.marketplace = _NullMarketplace()

    # ─── 전역 에러 핸들러 (프로덕션: 예외 상세 미노출) ───
    _is_production = os.environ.get('APP_ENV', 'development') == 'production'

    @app.errorhandler(500)
    def server_error(e):
        logging.error(f'500 Internal Server Error: {e}', exc_info=True)
        from flask import request as _req
        if _req.is_json or _req.headers.get('X-Requested-With') == 'XMLHttpRequest':
            msg = '서버 오류가 발생했습니다.' if _is_production else str(e)
            return jsonify({'error': msg}), 500
        return render_template('errors/500.html') if _is_production else str(e), 500

    @app.errorhandler(404)
    def not_found(e):
        from flask import request as _req
        if _req.is_json or _req.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'error': '리소스를 찾을 수 없습니다.'}), 404
        return render_template('errors/404.html'), 404

    @app.errorhandler(403)
    def forbidden(e):
        from flask import request as _req
        if _req.is_json or _req.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'error': '권한이 없습니다.'}), 403
        return render_template('errors/403.html'), 403

    return app


app = create_app()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)),
            debug=app.config.get('DEBUG', False))
