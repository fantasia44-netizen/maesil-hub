"""
maesil-hub — 식품·축산 ERP/WMS SaaS.
Phase 0 minimal Flask app for Render initial deploy.
"""
import os
import logging
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template_string
from dotenv import load_dotenv

load_dotenv()

# ─── Sentry (선택, env DSN 있을 때만 활성화) ───
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
    app.config.update(
        SECRET_KEY=os.environ.get('SECRET_KEY', 'dev-only-change-me'),
        SESSION_COOKIE_SECURE=os.environ.get('APP_ENV') == 'production',
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
    )

    # ─── 헬스체크 (매실에이전시 외부 폴링용) ───
    @app.route('/health')
    def health():
        return jsonify({
            'status': 'ok',
            'service': 'maesil-hub',
            'env': os.environ.get('APP_ENV', 'development'),
            'time': datetime.now(timezone.utc).isoformat(),
        })

    # ─── 임시 랜딩 (Phase 0) ───
    @app.route('/')
    def index():
        return render_template_string(
            """
            <!DOCTYPE html>
            <html lang="ko"><head>
            <meta charset="utf-8"><title>매실 허브 (Maesil Hub)</title>
            <style>
              body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                     max-width: 720px; margin: 60px auto; padding: 20px; color: #1a1a1a; }
              h1 { color: #2d7a3e; }
              .badge { display: inline-block; background: #e8f5e9; color: #2d7a3e;
                       padding: 4px 10px; border-radius: 4px; font-size: 14px; }
              .meta { color: #666; font-size: 14px; margin-top: 24px; }
              code { background: #f5f5f5; padding: 2px 6px; border-radius: 3px; }
            </style></head>
            <body>
              <h1>🌿 매실 허브 (Maesil Hub)</h1>
              <p class="badge">Phase 0 — 골격 배포 단계</p>
              <p>식품·축산업 전용 ERP/WMS SaaS. 개발 진행 중.</p>
              <ul>
                <li>헬스체크: <a href="/health">/health</a></li>
                <li>레포: <a href="https://github.com/fantasia44-netizen/maesil-hub">github.com/fantasia44-netizen/maesil-hub</a></li>
              </ul>
              <div class="meta">
                <p>Env: {{ env }} · Deployed: {{ time }}</p>
              </div>
            </body></html>
            """,
            env=os.environ.get('APP_ENV', 'development'),
            time=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
        )

    return app


app = create_app()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=True)
