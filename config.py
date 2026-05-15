"""maesil-hub 설정."""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ── Flask ──
    _secret = os.environ.get('SECRET_KEY', '').strip()
    if not _secret:
        raise RuntimeError('SECRET_KEY 환경변수가 설정되지 않았습니다. .env를 확인하세요.')
    SECRET_KEY = _secret
    APP_ENV = os.environ.get('APP_ENV', 'development')
    DEBUG = APP_ENV == 'development'

    # ── Session ──
    SESSION_COOKIE_SECURE = APP_ENV == 'production'
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = 86400  # 24h

    # ── Supabase ──
    SUPABASE_URL = os.environ.get('SUPABASE_URL', '')
    SUPABASE_KEY = os.environ.get('SUPABASE_KEY', '')
    SUPABASE_SERVICE_KEY = os.environ.get('SUPABASE_SERVICE_KEY', SUPABASE_KEY)
    DATABASE_URL = os.environ.get('DATABASE_URL', '')

    # ── 멀티테넌시 ──
    SAAS_MODE = os.environ.get('SAAS_MODE', 'multi')  # single | multi
    DEFAULT_BIZ_ID = int(os.environ.get('DEFAULT_BIZ_ID', '0') or '0')

    # ── 모니터링 ──
    SENTRY_DSN = os.environ.get('SENTRY_DSN', '')

    # ── 결제 (PortOne v2) ──
    PORTONE_API_KEY = os.environ.get('PORTONE_API_KEY', '')
    PORTONE_API_SECRET = os.environ.get('PORTONE_API_SECRET', '')
    PORTONE_STORE_ID = os.environ.get('PORTONE_STORE_ID', '')
    PORTONE_WEBHOOK_SECRET = os.environ.get('PORTONE_WEBHOOK_SECRET', '')

    # ── Fernet (saas_config 암호화) ──
    FERNET_KEY = os.environ.get('FERNET_KEY', '').strip()
    if not FERNET_KEY:
        import warnings
        warnings.warn(
            'FERNET_KEY 미설정 — saas_config 비밀값이 평문으로 저장됩니다. '
            '운영 환경에서는 반드시 설정하세요: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"',
            RuntimeWarning, stacklevel=2,
        )

    # ── 파일 저장 경로 ──
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
    OUTPUT_FOLDER = os.path.join(BASE_DIR, 'outputs')
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB

    # ── 외부 API (saas_config DB에서 동적 로드 권장) ──
    CJ_CUST_ID = os.environ.get('CJ_CUST_ID', '')
    NAVER_COMMERCE_CLIENT_ID = os.environ.get('NAVER_COMMERCE_CLIENT_ID', '')
    NAVER_COMMERCE_CLIENT_SECRET = os.environ.get('NAVER_COMMERCE_CLIENT_SECRET', '')


def get_config():
    return Config


# ── 레거시 호환 (db_supabase.py 등 root-level 모듈) ──
SUPABASE_URL = Config.SUPABASE_URL
SUPABASE_KEY = Config.SUPABASE_KEY
SUPABASE_SERVICE_KEY = Config.SUPABASE_SERVICE_KEY
