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


def create_app():
    app = Flask(__name__)
    from config import Config
    app.config.from_object(Config)

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
        if not g.biz_id:
            return redirect(url_for('auth.select_business'))
        return render_template('dashboard.html', biz_id=g.biz_id)

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

    # ─── Tenant context ───
    @app.before_request
    def set_tenant_context():
        g.biz_id = None
        g.is_impersonating = False
        if not current_user.is_authenticated:
            return
        # impersonation 우선
        if session.get('impersonating_biz_id'):
            g.biz_id = session['impersonating_biz_id']
            g.is_impersonating = True
        else:
            g.biz_id = session.get('current_biz_id')

        # Supabase RLS 컨텍스트 (anon 클라이언트용, Phase 1+ 활성화)
        # from db.client import get_supabase_client, set_tenant_context
        # if g.biz_id:
        #     set_tenant_context(get_supabase_client(), g.biz_id)

    return app


app = create_app()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=True)
