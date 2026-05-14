"""
run_sql.py — Supabase 마이그레이션 실행 (UTF-8 안전).

우선순위:
  1) DATABASE_URL 환경변수 + psycopg2 직접 실행 (가장 안전)
  2) 클립보드 폴백 (UTF-16 LE — Windows clip.exe 한글 안전)

사용법:
    python scripts/run_sql.py migrations/001_core_schema.sql
    python scripts/run_sql.py migrations/001_core_schema.sql --dry-run
    python scripts/run_sql.py migrations/001_core_schema.sql --env staging

규칙:
  - 한글 SQL 리터럴은 U&'\\XXXX' Unicode escape (인코딩 깨짐 차단)
  - 실행 후 migrations/STATUS.md 자동 갱신 권장
"""
import os
import sys
import re
import argparse
from pathlib import Path

# .env 로드
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load_env(env_name='development'):
    """env에 따라 .env / .env.staging / .env.production 로드."""
    candidates = ['.env']
    if env_name and env_name != 'development':
        candidates = [f'.env.{env_name}', '.env']
    for fname in candidates:
        path = ROOT / fname
        if path.exists():
            with open(path, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        k, v = line.split('=', 1)
                        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return str(path)
    return None


def fix_db_url(url):
    """비밀번호 특수문자(@!) URL 인코딩."""
    import urllib.parse
    m = re.match(r'^(postgresql://)([^:]+):(.+)@([^@]+)$', url)
    if not m:
        return url
    scheme, user, password, host_path = m.groups()
    return f"{scheme}{user}:{urllib.parse.quote(password, safe='')}@{host_path}"


def run_psycopg2(sql, db_url):
    try:
        import psycopg2
    except ImportError:
        print('[psycopg2] not installed, fallback to clipboard')
        return False
    print('[psycopg2] connecting...')
    try:
        conn = psycopg2.connect(fix_db_url(db_url), connect_timeout=15)
        conn.set_client_encoding('UTF8')
    except Exception as e:
        print(f'[psycopg2] connect failed: {e}')
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
        print('[psycopg2] OK — committed')
        return True
    except Exception as e:
        conn.rollback()
        print(f'[psycopg2] ERROR: {e}')
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def copy_clipboard_utf16(text):
    try:
        import subprocess
        proc = subprocess.Popen(['clip'], stdin=subprocess.PIPE)
        proc.communicate(text.encode('utf-16-le'))
        return True
    except Exception:
        return False


def verify_no_korean(sql, file_path):
    kor = [c for c in sql if '가' <= c <= '힣']
    if kor:
        print(f'[WARN] {file_path} has {len(kor)} Korean chars — encoding risk!')
        print('       Use U&\\\'\\\\XXXX\\\' Unicode escape for all Korean SQL literals.')
        return False
    print(f'[OK] {file_path}: 0 Korean chars (encoding safe)')
    return True


def update_status_md(file_path, env_name):
    """migrations/STATUS.md 자동 갱신."""
    status_path = ROOT / 'migrations' / 'STATUS.md'
    from datetime import datetime
    mig_name = Path(file_path).name
    line = f'- {mig_name} — {env_name} — {datetime.now().strftime("%Y-%m-%d %H:%M")}\n'
    if status_path.exists():
        with open(status_path, encoding='utf-8') as f:
            content = f.read()
    else:
        content = '# Migration Status\n\n'
    with open(status_path, 'w', encoding='utf-8') as f:
        f.write(content + line)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('sql_file')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--env', default='development', choices=['development', 'staging', 'production'])
    args = parser.parse_args()

    env_loaded = load_env(args.env)
    if env_loaded:
        print(f'env loaded: {env_loaded}')

    if not Path(args.sql_file).exists():
        print(f'[ERROR] file not found: {args.sql_file}')
        sys.exit(1)

    with open(args.sql_file, encoding='utf-8') as f:
        sql = f.read()
    print(f'file: {args.sql_file} ({len(sql)} chars)')

    verify_no_korean(sql, args.sql_file)

    if args.dry_run:
        print('--- DRY-RUN ---')
        return

    db_url = os.environ.get('DATABASE_URL', '').strip()
    if db_url:
        if run_psycopg2(sql, db_url):
            update_status_md(args.sql_file, args.env)
            return
        print('[fallback] psycopg2 failed → clipboard')

    if copy_clipboard_utf16(sql):
        print('[OK] clipboard copied (UTF-16 LE)')
        print('-> Supabase SQL Editor: Ctrl+V then Run')
        if not db_url:
            print('\n[TIP] Add DATABASE_URL to .env for direct execution')
    else:
        print('[ERROR] clipboard copy failed')
        sys.exit(1)


if __name__ == '__main__':
    main()
