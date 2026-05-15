"""
maesil-total Supabase -> maesil-hub Supabase 데이터 마이그레이션.

biz_id=1 (배마마) 로 모든 데이터 주입.

사용법:
    python scripts/migrate_total_to_hub.py --dry-run     # 행수 미리보기
    python scripts/migrate_total_to_hub.py --table product_costs   # 단일 테이블
    python scripts/migrate_total_to_hub.py --all          # 전체

필요 환경변수 (.env):
    SUPABASE_URL, SUPABASE_SERVICE_KEY     (hub 측)
    SOURCE_SUPABASE_URL, SOURCE_SUPABASE_SERVICE_KEY    (total 측)

또는 --source-url, --source-key CLI 옵션
"""
import os
import sys
import argparse
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / '.env')

from supabase import create_client


# 마이그레이션 대상 테이블 + 의존성 순서
# (이전 테이블이 먼저 - FK 충돌 방지)
TABLES_TO_MIGRATE = [
    'product_costs',         # 마스터
    'option_master',         # 마스터
    'business_partners',     # 마스터
    'my_business',           # 자사 정보 (있을지 확인)
    'manual_trades',         # 거래
    'stock_ledger',          # 수불장 (큰 테이블)
    'import_runs',           # 주문 import
    'order_transactions',    # 주문
    'order_shipping',        # 송장
    'daily_revenue',         # 매출
    'packing_jobs',          # 패킹
    'purchase_orders',       # 발주
]

# biz_id 컬럼이 source에 없으면 destination에서 강제 주입
TARGET_BIZ_ID = 1  # 배마마

# ID 충돌 회피: source의 id를 그대로 보존 (FK 보존), 또는 새로 생성?
# → 새로 생성 (hub의 BIGSERIAL이 자동 부여) — id 컬럼 제거하고 INSERT
DROP_ID_ON_INSERT = True

PAGE_SIZE = 1000


def get_clients():
    """source (maesil-total) + destination (maesil-hub) 클라이언트."""
    src_url = os.environ.get('SOURCE_SUPABASE_URL', '').strip()
    src_key = os.environ.get('SOURCE_SUPABASE_SERVICE_KEY', '').strip()
    if not src_url or not src_key:
        print('[ERROR] SOURCE_SUPABASE_URL / SOURCE_SUPABASE_SERVICE_KEY env required')
        print('Add to .env or pass --source-url --source-key')
        sys.exit(1)
    src = create_client(src_url, src_key)

    dst_url = os.environ['SUPABASE_URL']
    dst_key = os.environ['SUPABASE_SERVICE_KEY']
    dst = create_client(dst_url, dst_key)

    return src, dst


def fetch_all(client, table):
    """source 테이블 전체 행 (페이지네이션)."""
    all_rows = []
    offset = 0
    while True:
        res = client.table(table).select('*').range(offset, offset + PAGE_SIZE - 1).execute()
        rows = res.data or []
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return all_rows


def insert_chunked(client, table, rows, chunk_size=500):
    """destination 테이블에 chunk 단위 INSERT."""
    inserted = 0
    failed = 0
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i:i + chunk_size]
        try:
            client.table(table).insert(chunk).execute()
            inserted += len(chunk)
        except Exception as e:
            err = str(e)[:100]
            print(f'  chunk {i}-{i+len(chunk)} FAIL: {err}')
            # 행별 재시도
            for r in chunk:
                try:
                    client.table(table).insert(r).execute()
                    inserted += 1
                except Exception:
                    failed += 1
    return inserted, failed


_DST_COLUMNS_CACHE = {}

def get_dst_columns(dst, table):
    """dst 테이블의 컬럼 목록 (psycopg2로 information_schema 조회)."""
    if table in _DST_COLUMNS_CACHE:
        return _DST_COLUMNS_CACHE[table]
    # 빈 select로 우선 시도
    try:
        r = dst.table(table).select('*').limit(1).execute()
        if r.data:
            cols = set(r.data[0].keys())
            _DST_COLUMNS_CACHE[table] = cols
            return cols
    except Exception:
        pass
    # 빈 테이블 → psycopg2 직접 조회
    try:
        import psycopg2, urllib.parse
        url = os.environ.get('DATABASE_URL', '')
        p = urllib.parse.urlparse(url)
        conn = psycopg2.connect(
            host=p.hostname, port=p.port or 5432,
            user=urllib.parse.unquote(p.username or ''),
            password=urllib.parse.unquote(p.password or ''),
            dbname=(p.path or '').lstrip('/') or 'postgres',
            connect_timeout=10,
        )
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name=%s",
                    (table,)
                )
                cols = set(row[0] for row in cur.fetchall())
                _DST_COLUMNS_CACHE[table] = cols
                return cols
        finally:
            conn.close()
    except Exception as e:
        print(f'  [WARN] cannot get columns for {table}: {str(e)[:80]}')
    return None


def migrate_table(src, dst, table, dry_run=False):
    """1 테이블 마이그레이션."""
    print(f'\n=== {table} ===')
    try:
        rows = fetch_all(src, table)
    except Exception as e:
        print(f'  source fetch failed: {str(e)[:100]}')
        return 0, 0
    print(f'  source rows: {len(rows)}')

    if not rows:
        return 0, 0

    # dst 컬럼 목록 (biz_id 주입 후 빈 테이블이라 select 못 할 수도)
    dst_cols = get_dst_columns(dst, table)
    if dst_cols is None:
        # 빈 테이블 — 첫 행 시도 후 fail 컬럼 누적 제거
        dst_cols = set(rows[0].keys()) | {'biz_id'}
    print(f'  dst columns known: {len(dst_cols)}')

    # date 컬럼 식별 (PostgreSQL DATE 타입은 빈 문자열 거부)
    DATE_COLS = {'transaction_date', 'expiry_date', 'manufacture_date',
                 'order_date', 'outbound_date', 'collection_date',
                 'trade_date', 'revenue_date', 'cancelled_at',
                 'paid_at', 'failed_at', 'started_at', 'completed_at',
                 'updated_at', 'created_at', 'last_login_at',
                 'current_period_start', 'current_period_end',
                 'expected_date', 'received_date'}

    # 변환: id 제거 + biz_id 주입 + dst에 없는 컬럼 제거 + 빈 문자열 date → None
    transformed = []
    dropped_cols = set()
    for r in rows:
        new = {}
        for k, v in r.items():
            if k == 'id' and DROP_ID_ON_INSERT:
                continue
            if k not in dst_cols:
                dropped_cols.add(k)
                continue
            # 빈 문자열을 date/timestamp 컬럼에 넣으면 PostgreSQL fail
            if k in DATE_COLS and (v == '' or v is None):
                new[k] = None
            else:
                new[k] = v
        new['biz_id'] = TARGET_BIZ_ID
        transformed.append(new)

    if dropped_cols:
        print(f'  dropped source-only columns: {sorted(dropped_cols)[:10]}')

    if dry_run:
        print(f'  [DRY-RUN] would insert {len(transformed)} rows')
        return len(transformed), 0

    # 기존 행 삭제 (재실행 안전, biz_id=1 한정)
    try:
        dst.table(table).delete().eq('biz_id', TARGET_BIZ_ID).execute()
    except Exception as e:
        print(f'  pre-delete failed (may be OK): {str(e)[:80]}')

    inserted, failed = insert_chunked(dst, table, transformed)
    print(f'  inserted: {inserted}, failed: {failed}')
    return inserted, failed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--all', action='store_true')
    parser.add_argument('--table', help='single table')
    parser.add_argument('--source-url', help='override SOURCE_SUPABASE_URL')
    parser.add_argument('--source-key', help='override SOURCE_SUPABASE_SERVICE_KEY')
    args = parser.parse_args()

    if args.source_url:
        os.environ['SOURCE_SUPABASE_URL'] = args.source_url
    if args.source_key:
        os.environ['SOURCE_SUPABASE_SERVICE_KEY'] = args.source_key

    src, dst = get_clients()
    print(f'Source: {os.environ["SOURCE_SUPABASE_URL"][:50]}')
    print(f'Dest:   {os.environ["SUPABASE_URL"][:50]}')
    print(f'Target biz_id: {TARGET_BIZ_ID}')
    if args.dry_run:
        print('Mode: DRY-RUN')

    tables = [args.table] if args.table else (TABLES_TO_MIGRATE if args.all else [])
    if not tables:
        print('\nUsage: --table <name> OR --all')
        sys.exit(1)

    total_in = 0
    total_fail = 0
    for tbl in tables:
        ins, fail = migrate_table(src, dst, tbl, dry_run=args.dry_run)
        total_in += ins
        total_fail += fail
        time.sleep(0.5)

    print()
    print('=' * 60)
    print(f'TOTAL: inserted={total_in}, failed={total_fail}')


if __name__ == '__main__':
    main()
