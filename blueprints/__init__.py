"""Blueprint 일괄 등록 — app.py에서 register_all(app) 호출."""
import logging
import importlib
import pkgutil
import inspect
from flask import Blueprint

logger = logging.getLogger(__name__)


def register_all(app):
    """blueprints/ 폴더의 모든 *_bp 변수를 자동 등록.

    - 등록 실패한 모듈은 경고만 남기고 계속 (개별 모듈 깨져도 앱 시작 가능)
    """
    import blueprints
    registered = []
    failed = []

    for finder, name, ispkg in pkgutil.iter_modules(blueprints.__path__):
        if name.startswith('_'):
            continue
        full_name = f'blueprints.{name}'
        try:
            mod = importlib.import_module(full_name)
        except Exception as e:
            failed.append((name, f'import: {e}'))
            logger.warning(f'[blueprints] import failed: {name}: {e}')
            continue

        # 모듈 안의 Blueprint 인스턴스 찾기
        bps = [
            obj for n, obj in inspect.getmembers(mod)
            if isinstance(obj, Blueprint) and n.endswith('_bp')
        ]
        for bp in bps:
            try:
                app.register_blueprint(bp)
                registered.append(f'{name}:{bp.name}')
            except Exception as e:
                failed.append((f'{name}:{bp.name}', str(e)))
                logger.warning(f'[blueprints] register failed: {name}:{bp.name}: {e}')

    logger.info(f'[blueprints] registered {len(registered)}, failed {len(failed)}')
    return registered, failed
