"""
render_env.py — Render API로 환경변수 일괄 등록.

사용법:
    # 로컬 .env -> Render service 동기화
    python scripts/render_env.py sync --service maesil-hub-staging

    # 단일 키 설정
    python scripts/render_env.py set SECRET_KEY xxxxxx --service maesil-hub-staging

    # 현재 등록된 env 조회
    python scripts/render_env.py list --service maesil-hub-staging

필요 환경변수 (.env):
    RENDER_API_KEY=rnd_xxxxxxxxxxxx     (Render Dashboard -> Account Settings -> API Keys)
"""
import os
import sys
import json
import argparse
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RENDER_API_BASE = 'https://api.render.com/v1'

# 동기화 시 제외할 키 (서버에 노출 금지)
EXCLUDE_KEYS = {
    'DATABASE_URL_LOCAL',  # 로컬 전용 등이 있으면 추가
}


def load_env():
    env_path = ROOT / '.env'
    if env_path.exists():
        with open(env_path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def api_request(method, path, body=None):
    api_key = os.environ.get('RENDER_API_KEY', '').strip()
    if not api_key:
        print('[ERROR] RENDER_API_KEY env required')
        print('Get it from: https://dashboard.render.com/u/settings#api-keys')
        sys.exit(1)
    url = f'{RENDER_API_BASE}{path}'
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Accept': 'application/json',
    }
    data = None
    if body is not None:
        headers['Content-Type'] = 'application/json'
        data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            txt = resp.read().decode('utf-8')
            return json.loads(txt) if txt else None
    except urllib.error.HTTPError as e:
        body_err = e.read().decode('utf-8', errors='replace')
        print(f'[HTTP {e.code}] {body_err[:300]}')
        raise


def find_service_id(service_name):
    """서비스명으로 ID 검색."""
    try:
        services = api_request('GET', '/services?limit=100')
    except Exception as e:
        print(f'[ERROR] list services failed: {e}')
        sys.exit(1)
    items = services if isinstance(services, list) else services.get('items', services)
    for item in items:
        svc = item.get('service', item)
        if svc.get('name') == service_name:
            return svc.get('id')
    print(f'[ERROR] service not found: {service_name}')
    print(f'Available services:')
    for item in items[:20]:
        svc = item.get('service', item)
        print(f'  - {svc.get("name")} (id: {svc.get("id")})')
    sys.exit(1)


def list_env_vars(service_id):
    """현재 등록된 env vars 조회."""
    try:
        items = api_request('GET', f'/services/{service_id}/env-vars?limit=100')
    except Exception as e:
        print(f'[ERROR] list env vars failed: {e}')
        return []
    rows = items if isinstance(items, list) else items.get('items', items)
    result = []
    for item in rows:
        ev = item.get('envVar', item)
        result.append((ev.get('key'), ev.get('value', '')))
    return result


def set_env_vars(service_id, env_dict):
    """env vars 일괄 PUT (REPLACE all)."""
    payload = [{'key': k, 'value': str(v)} for k, v in env_dict.items()
               if k not in EXCLUDE_KEYS and not k.startswith('#')]
    try:
        return api_request('PUT', f'/services/{service_id}/env-vars', payload)
    except Exception as e:
        print(f'[ERROR] PUT env vars failed: {e}')
        sys.exit(1)


def cmd_list(args):
    sid = find_service_id(args.service)
    print(f'Service: {args.service} ({sid})')
    rows = list_env_vars(sid)
    print(f'Total env vars: {len(rows)}')
    for k, v in sorted(rows):
        masked = v[:10] + '***' if v and len(v) > 10 else v
        print(f'  {k} = {masked}')


def cmd_sync(args):
    """로컬 .env -> Render service env vars."""
    load_env()
    env_path = ROOT / '.env'
    if not env_path.exists():
        print('[ERROR] .env not found')
        sys.exit(1)

    env_dict = {}
    with open(env_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k.startswith('REPLACE_ME') or v.startswith('REPLACE_ME'):
                continue
            env_dict[k] = v

    print(f'Loaded from .env: {len(env_dict)} vars')
    for k in sorted(env_dict):
        masked = env_dict[k][:10] + '***' if len(env_dict[k]) > 10 else env_dict[k]
        print(f'  {k} = {masked}')

    if args.dry_run:
        print('[DRY-RUN] not pushing')
        return

    sid = find_service_id(args.service)
    print(f'\nPushing to: {args.service} ({sid})')
    set_env_vars(sid, env_dict)
    print('[OK] env vars synced')


def cmd_set(args):
    sid = find_service_id(args.service)
    current = dict(list_env_vars(sid))
    current[args.key] = args.value
    set_env_vars(sid, current)
    print(f'[OK] {args.key} updated on {args.service}')


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='cmd', required=True)

    p_list = sub.add_parser('list')
    p_list.add_argument('--service', required=True)

    p_sync = sub.add_parser('sync')
    p_sync.add_argument('--service', required=True)
    p_sync.add_argument('--dry-run', action='store_true')

    p_set = sub.add_parser('set')
    p_set.add_argument('key')
    p_set.add_argument('value')
    p_set.add_argument('--service', required=True)

    args = parser.parse_args()
    load_env()

    {'list': cmd_list, 'sync': cmd_sync, 'set': cmd_set}[args.cmd](args)


if __name__ == '__main__':
    main()
