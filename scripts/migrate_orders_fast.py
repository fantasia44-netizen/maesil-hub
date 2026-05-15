"""
order_transactions 71k 행을 psycopg2 COPY로 빠르게 마이그레이션.
fetch는 페이지네이션으로 점진적 + INSERT는 batch executemany.
"""
import os
import sys
import json
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / '.env')

from supabase import create_client
import psycopg2
import psycopg2.extras

TARGET_BIZ_ID = 1
TABLE = 'order_transactions'
PAGE_SIZE = 1000

# DATE 컬럼
DATE_COLS = {'transaction_date', 'expiry_date', 'manufacture_date',
             'order_date', 'outbound_date', 'collection_date',
             'trade_date', 'revenue_date'}

# FK 매핑이 깨진 컬럼 (source ID 그대로 옮기면 FK 위반) → NULL로 set
FK_NULL_COLS = {'import_run_id'}


def parse_db_url(url):
    p = urllib.parse.urlparse(url)
    return {
        'host': p.hostname, 'port': p.port or 5432,
        'user': urllib.parse.unquote(p.username or ''),
        'password': urllib.parse.unquote(p.password or ''),
        'dbname': (p.path or '').lstrip('/') or 'postgres',
    }


def get_dst_columns(conn, table):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=%s",
            (table,)
        )
        return [r[0] for r in cur.fetchall()]


def main():
    src = create_client(
        os.environ['SOURCE_SUPABASE_URL'],
        os.environ['SOURCE_SUPABASE_SERVICE_KEY'],
    )

    db_params = parse_db_url(os.environ['DATABASE_URL'])
    db_params['connect_timeout'] = 15
    conn = psycopg2.connect(**db_params)
    conn.set_client_encoding('UTF8')

    print(f'[init] target={TABLE} biz_id={TARGET_BIZ_ID}', flush=True)

    # dst 컬럼 (순서 보존)
    dst_cols = get_dst_columns(conn, TABLE)
    print(f'[init] dst columns: {len(dst_cols)}', flush=True)

    # 기존 biz_id=1 데이터 삭제
    with conn.cursor() as cur:
        cur.execute(f'DELETE FROM {TABLE} WHERE biz_id = %s', (TARGET_BIZ_ID,))
        deleted = cur.rowcount
    conn.commit()
    print(f'[init] pre-deleted biz_id={TARGET_BIZ_ID} rows: {deleted}', flush=True)

    # INSERT용 컬럼 (id 제외)
    insert_cols = [c for c in dst_cols if c != 'id']
    placeholders = ', '.join(['%s'] * len(insert_cols))
    insert_sql = (
        f"INSERT INTO {TABLE} ({', '.join(insert_cols)}) VALUES ({placeholders})"
        f" ON CONFLICT DO NOTHING"
    )

    offset = 0
    total_inserted = 0
    total_failed = 0
    while True:
        # source에서 chunk fetch
        res = src.table(TABLE).select('*').range(offset, offset + PAGE_SIZE - 1).execute()
        rows = res.data or []
        if not rows:
            break

        # 변환: dst 컬럼만, id 제거, biz_id 주입, 빈 date → None
        records = []
        for r in rows:
            record = []
            for col in insert_cols:
                if col == 'biz_id':
                    record.append(TARGET_BIZ_ID)
                    continue
                v = r.get(col)
                if col in DATE_COLS and (v == '' or v is None):
                    v = None
                elif col in FK_NULL_COLS:
                    v = None  # FK 매핑 깨진 컬럼 NULL로
                # JSONB 컬럼은 dict→json
                elif isinstance(v, (dict, list)):
                    v = json.dumps(v, ensure_ascii=False)
                record.append(v)
            records.append(tuple(record))

        # batch INSERT (ON CONFLICT DO NOTHING → UNIQUE 중복 자동 스킵)
        try:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(cur, insert_sql, records, page_size=500)
            conn.commit()
            total_inserted += len(records)
        except Exception as e:
            conn.rollback()
            print(f'[FAIL] offset={offset}: {str(e)[:160]}', flush=True)
            total_failed += len(records)

        print(f'[progress] offset={offset+len(rows)}, inserted={total_inserted}, failed={total_failed}', flush=True)

        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    conn.close()
    print(f'\n=== DONE: inserted={total_inserted}, failed={total_failed} ===', flush=True)


if __name__ == '__main__':
    main()
