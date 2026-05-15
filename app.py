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

        # 오늘 주문
        try:
            r = c.table('order_transactions').select('id', count='exact') \
                .eq('biz_id', biz_id).eq('order_date', today).limit(1).execute()
            today_orders = r.count or 0
        except Exception:
            today_orders = '-'

        # 미출고 (is_outbound_done=False, 상태 정상)
        try:
            r = c.table('order_transactions').select('id', count='exact') \
                .eq('biz_id', biz_id).eq('is_outbound_done', False) \
                .neq('status', '취소').limit(1).execute()
            pending_ship = r.count or 0
        except Exception:
            pending_ship = '-'

        # 재고 품목 수 (stock_ledger 기준 품목 종류)
        try:
            r = c.rpc('get_stock_summary', {'p_biz_id': biz_id}).execute()
            stock_items = len(r.data) if r.data else 0
        except Exception:
            try:
                r = c.table('stock_ledger').select('product_name') \
                    .eq('biz_id', biz_id).limit(1000).execute()
                stock_items = len(set(x['product_name'] for x in (r.data or [])))
            except Exception:
                stock_items = '-'

        # 이달 매출
        try:
            r = c.table('order_transactions').select('settlement') \
                .eq('biz_id', biz_id) \
                .gte('order_date', month_start).lte('order_date', today) \
                .neq('status', '취소').limit(5000).execute()
            month_revenue = sum(x.get('settlement') or 0 for x in (r.data or []))
        except Exception:
            month_revenue = '-'

        return render_template('dashboard.html',
            biz_id=biz_id,
            today=today,
            today_orders=today_orders,
            pending_ship=pending_ship,
            stock_items=stock_items,
            month_revenue=month_revenue,
        )

    app.register_blueprint(main_bp)

    # ─── ERP/WMS Blueprints (40개 일괄 등록) ───
    try:
        from blueprints import register_all as register_erp_blueprints
        registered, failed = register_erp_blueprints(app)
        logging.info(f'ERP blueprints: {len(registered)} registered, {len(failed)} failed')
    except Exception as e:
        logging.warning(f'ERP blueprints registration failed: {e}')

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

    # ─── MarketplaceManager (g.marketplace) ───
    try:
        from services.marketplace import MarketplaceManager
        app._marketplace_default = MarketplaceManager()   # API 키 없으면 빈 매니저
    except Exception as e:
        logging.warning(f'MarketplaceManager init failed: {e}')
        app._marketplace_default = None

    # ─── Tenant context ───
    @app.before_request
    def set_tenant_context():
        g.biz_id = None
        g.biz_name = None
        g.is_impersonating = False
        # g.marketplace — 레거시 blueprints 호환 (빈 매니저)
        g.marketplace = getattr(app, '_marketplace_default', None) or _NullMarketplace()
        if not current_user.is_authenticated:
            return
        # impersonation 우선
        if session.get('impersonating_biz_id'):
            g.biz_id = session['impersonating_biz_id']
            g.is_impersonating = True
        else:
            g.biz_id = session.get('current_biz_id')

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

        # Supabase RLS 컨텍스트 (anon 클라이언트용, Phase 1+ 활성화)
        # from db.client import get_supabase_client, set_tenant_context
        # if g.biz_id:
        #     set_tenant_context(get_supabase_client(), g.biz_id)

    return app


app = create_app()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=True)
