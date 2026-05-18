"""
marketplace/ — 마켓플레이스 API 연동 패키지.

MarketplaceManager: 채널별 API 클라이언트 싱글톤 관리.
app.py에서 app.marketplace = MarketplaceManager(app.db) 형태로 초기화.
플랫폼 기반: DB의 channel → get_platform() → 클라이언트 클래스 자동 매핑.
"""
import logging

from .naver_client import NaverCommerceClient
from .coupang_client import CoupangWingClient
from .cafe24_client import Cafe24Client
from .st11_client import St11Client
from .esm_client import EsmClient
from .kakao_client import KakaoClient
from services.channel_config import get_platform

logger = logging.getLogger(__name__)

# 플랫폼 → 클라이언트 클래스 매핑 (채널명 하드코딩 제거)
# API 키 등록하면 자동으로 활성화됨
_PLATFORM_CLIENT_MAP = {
    'naver':    NaverCommerceClient,
    'coupang':  CoupangWingClient,
    'cafe24':   Cafe24Client,
    '11st':     St11Client,
    'auction':  EsmClient,       # 옥션/G마켓 통합 (ESM)
    'kakao':    KakaoClient,
}


class MarketplaceManager:
    """채널별 마켓플레이스 API 클라이언트 관리자.

    db=None 이면 get_db() 자동 사용 (스케줄러·앱 초기화 모두 호환).
    biz_id를 전달하면 해당 업체 채널만 로드 (멀티테넌트 격리).
    """

    def __init__(self, db=None, biz_id=None):
        self.clients = {}
        self.biz_id = biz_id
        if db is None:
            try:
                from db_utils import get_db
                db = get_db()
            except Exception as e:
                logger.warning(f'[Marketplace] get_db() 실패: {e}')
        if db:
            self._load_configs(db, biz_id=biz_id)

    def _load_configs(self, db, biz_id=None):
        """DB에서 API 설정 로드 → 플랫폼 기반 클라이언트 인스턴스 생성."""
        try:
            configs = db.query_marketplace_api_configs(biz_id=biz_id)
        except Exception as e:
            logger.warning(f'[Marketplace] config 로드 실패: {e}')
            configs = []

        for cfg in configs:
            channel = cfg.get('channel', '')
            if not cfg.get('is_active', False):
                continue  # 비활성 채널 스킵
            platform = get_platform(channel)
            cls = _PLATFORM_CLIENT_MAP.get(platform)
            if cls:
                self.clients[channel] = cls(cfg)
                logger.info(f'[Marketplace] {channel} 클라이언트 로드 '
                            f'(platform={platform}, biz={biz_id or "all"})')

    def get_client(self, channel):
        """채널명으로 클라이언트 반환."""
        return self.clients.get(channel)

    def get_active_channels(self) -> list:
        """활성화 + 준비된 채널 목록."""
        return [ch for ch, c in self.clients.items() if c.is_active and c.is_ready]

    def get_all_channels(self) -> list:
        """전체 채널 상태 목록 (UI용)."""
        result = []
        for channel, client in self.clients.items():
            result.append({
                'channel': channel,
                'is_active': getattr(client, 'is_active', False),
                'is_ready': client.is_ready,
                'last_synced_at': client.config.get('last_synced_at'),
            })
        return result

    def reload(self, db=None, biz_id=None):
        """클라이언트 목록 재로드 (설정 변경 후 호출)."""
        self.clients.clear()
        if db is None:
            try:
                from db_utils import get_db
                db = get_db()
            except Exception:
                return
        self._load_configs(db, biz_id=biz_id or self.biz_id)
