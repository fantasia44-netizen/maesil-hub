# maesil-hub 핵심 표준 (Conventions)

**모든 코드/설계 시작 시 이 문서 우선.** 위반 발견 시 fail-fast.

## 1. 시간 — KST 고정

### 원칙
- **DB 저장**: `TIMESTAMPTZ` (내부적으로 UTC 저장) — PostgreSQL 표준
- **Python 처리**: `services.tz_utils`의 `now_kst()`, `today_kst()` 만 사용
- **화면 표시**: 무조건 KST (UTC 노출 절대 금지)
- **로그 타임스탬프**: KST
- **Sentry 이벤트**: KST 변환 후 전송

### 강제 사용 헬퍼
```python
from services.tz_utils import KST, now_kst, today_kst, to_kst, days_ago_kst

# OK
now = now_kst()                          # tz-aware datetime in KST
today_str = today_kst()                  # 'YYYY-MM-DD'

# 금지
now = datetime.now()                     # naive (서버 시간대 의존)
now = datetime.utcnow()                  # naive UTC

# 외부 API에서 받은 datetime 변환
kst_dt = to_kst(api_response['created_at'])
```

### Jinja2 필터
```python
# templates/base.html 또는 app.py에 등록
@app.template_filter('kst')
def kst_filter(dt, fmt='%Y-%m-%d %H:%M'):
    from services.tz_utils import to_kst
    if not dt: return ''
    return to_kst(dt).strftime(fmt)
```
사용:
```html
<td>{{ row.created_at | kst }}</td>
<td>{{ row.created_at | kst('%Y-%m-%d') }}</td>
```

### 로그 KST
```python
# app.py 시작 시
import logging
import time
logging.Formatter.converter = lambda *args: time.localtime(time.time() + 9*3600)  # KST
```

### DB RPC에서 KST 반환
```sql
-- TIMESTAMPTZ를 KST로 변환해서 반환
SELECT (created_at AT TIME ZONE 'Asia/Seoul')::TIMESTAMP AS created_kst
FROM stock_ledger;
```

## 2. 인코딩 — UTF-8 일관

### 원칙
- **소스 코드**: UTF-8 (Python 3 기본)
- **파일 I/O**: 모두 `encoding='utf-8'` 명시 (default 의존 금지)
- **DB 연결**: `client_encoding=UTF8` (psycopg2/Supabase)
- **HTTP 응답**: `Content-Type: text/html; charset=utf-8`
- **로그**: UTF-8 stream
- **SQL 마이그레이션 파일**: **ASCII only** (한글은 `U&'\XXXX'` Unicode escape)
- **클립보드**: UTF-16 LE (Windows clip.exe 한글 안전)
- **subprocess**: `encoding='utf-8'`

### 강제 사용 패턴
```python
# 파일 읽기/쓰기
with open(path, encoding='utf-8') as f:    # 항상 명시
    content = f.read()
with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

# subprocess
subprocess.run(cmd, encoding='utf-8', errors='replace')

# CSV
import csv
with open(path, encoding='utf-8-sig') as f:  # BOM 자동 처리

# DB 연결
conn = psycopg2.connect(url)
conn.set_client_encoding('UTF8')
```

### Flask 응답
```python
# app.py
app.config['JSON_AS_ASCII'] = False         # jsonify 한글 escape 금지
app.config['JSONIFY_MIMETYPE'] = 'application/json; charset=utf-8'
```

### SQL 파일 규칙 (★중요)
```sql
-- 한글 리터럴은 무조건 Unicode escape (인코딩 사고 영구 차단)
-- 정상 = U&'\C815\C0C1'
-- 전체 = U&'\C804\CCB4'
-- 기타 = U&'\AE30\D0C0'

-- OK
WHERE status = U&'\C815\C0C1'

-- 금지
WHERE status = '정상'      -- 클립보드/cp949 경유 시 깨짐
```

마이그 파일 작성 시 자동 검증:
```bash
python scripts/run_sql.py migrations/0XX_*.sql
# → 한글 0개 검증 + psycopg2 직접 실행
```

### Python 코드 안 한글은 OK
```python
# Python 문자열 안 한글은 Python 3에서 안전
flash('가입 완료', 'success')              # OK (UTF-8 전송)
DEFAULT_CATEGORY = '완제품'                # OK
```
이건 SQL 리터럴이 아니라 Python str이라 안전. **DB 비교/저장 시점에서 canonical()** 거쳐 안전 강화.

## 3. 화면 출력 — DB RPC 우선

### 원칙
- **모든 화면 데이터는 RPC에서 산출**, Python은 단순 thin layer
- **Python 역할**: 라우트 + RPC 호출 + 템플릿 렌더링만
- **비즈니스 로직**: SQL RPC 안에서 처리
- **집계/필터/정렬**: RPC가 처리, Python에서 추가 계산 금지
- **페이지네이션**: RPC가 LIMIT/OFFSET 또는 keyset 처리
- **권한/biz_id 격리**: RPC 첫 파라미터 `p_biz_id`

### 표준 RPC 시그니처
```sql
CREATE OR REPLACE FUNCTION rpc_<page>_<action>(
    p_biz_id BIGINT,                       -- 항상 첫 파라미터
    p_date_from DATE DEFAULT NULL,
    p_date_to DATE DEFAULT NULL,
    p_filter_xxx TEXT DEFAULT NULL,
    p_limit INTEGER DEFAULT 100,
    p_offset INTEGER DEFAULT 0
)
RETURNS JSONB                              -- 또는 TABLE(...)
LANGUAGE sql STABLE SECURITY DEFINER SET statement_timeout = '20s'
AS $$
    -- 모든 비즈니스 로직 여기서
    -- biz_id 격리 강제
    ...
$$;
GRANT EXECUTE ON FUNCTION ... TO authenticated, service_role, anon;
```

### Python 라우트 (thin)
```python
@bp.route('/dashboard')
@biz_required
def dashboard():
    db = get_admin_client()
    data = db.rpc('rpc_dashboard_summary', {
        'p_biz_id': g.biz_id,
        'p_date': today_kst(),
    }).execute().data
    return render_template('dashboard.html', data=data)
```

비즈니스 로직, SUM, GROUP BY, 필터, 정렬 모두 RPC가 처리. Python은 데이터 전달.

### 페이지네이션
```sql
-- RPC가 LIMIT/OFFSET 받아서 처리
CREATE FUNCTION rpc_orders_list(
    p_biz_id BIGINT,
    p_limit INTEGER DEFAULT 50,
    p_offset INTEGER DEFAULT 0
) RETURNS JSONB AS $$
    WITH page AS (
        SELECT * FROM order_transactions
        WHERE biz_id = p_biz_id
        ORDER BY id DESC
        LIMIT p_limit OFFSET p_offset
    ),
    total AS (SELECT COUNT(*) AS n FROM order_transactions WHERE biz_id = p_biz_id)
    SELECT jsonb_build_object(
        'rows', (SELECT jsonb_agg(to_jsonb(page.*)) FROM page),
        'total', (SELECT n FROM total),
        'limit', p_limit,
        'offset', p_offset
    );
$$ LANGUAGE sql STABLE;
```

### 절대 금지 패턴
```python
# 금지: Python이 SUM/GROUP BY 처리
rows = db.table('stock_ledger').select('*').eq('biz_id', g.biz_id).execute().data
total = sum(r['qty'] for r in rows)        # ★금지★ — RPC에서 SUM 처리할 것
by_category = {}
for r in rows:
    by_category.setdefault(r['category'], 0)  # ★금지★ — RPC GROUP BY

# 금지: 1000행 limit 직격
rows = db.table('order_transactions').select('*').eq('biz_id', g.biz_id).execute()
# Supabase REST 1000행 잘림. RPC로 무제한 또는 페이지네이션.
```

### 예외 (RPC 안 써도 되는 경우)
- `.eq('id', N).single()` 단건 조회
- INSERT/UPDATE/DELETE (단순 mutation)
- 단순 lookup (1~10건 보장)

## 4. 멀티테넌시 — biz_id 강제

### 원칙
- **모든 RPC 첫 파라미터 `p_biz_id BIGINT`**
- **모든 INSERT/UPDATE/DELETE `biz_id` 명시**
- **모든 SELECT `biz_id` 필터** (RLS 또는 명시적)
- **g.biz_id** 가 None이면 비즈니스 라우트 진입 차단 (`@biz_required`)
- **테넌트 가드**: `db/tenant.py`의 `install_tenant_guard()` 자동 주입

### 검증 (CI)
```python
# scripts/check_biz_id.py
# 모든 .py에서 .table('xxx').select / update / insert가
# .eq('biz_id', ...) 또는 RPC 호출이 아니면 FAIL
```

## 5. 페이지네이션 — RPC 또는 paginate_all()

```python
# RPC가 limit/offset 처리하면 그대로 호출
data = db.rpc('rpc_xxx', {'p_biz_id': g.biz_id, 'p_limit': 50, 'p_offset': 0}).execute().data

# RPC 없는 단순 lookup은 paginate_all
from db.paginate import paginate_all
rows = paginate_all(
    lambda o, e: db.table('xxx').select('*')
        .eq('biz_id', g.biz_id).range(o, e).execute()
)
```

## 6. canonical product_name

```python
from services.product_name import canonical

# INSERT/UPDATE 전에 강제
product = canonical(user_input)            # 공백 제거, strip
db.table('product_costs').insert({'product_name': product, ...}).execute()
```

DB 레벨에서 trigger로 강화 가능 (Phase 2).

## 7. 보안

- 비밀번호: bcrypt cost=12
- SECRET_KEY: 환경마다 다름
- SUPABASE_SERVICE_KEY: 서버 환경변수만, 클라이언트 절대 금지
- PortOne webhook: HMAC 서명 검증 필수 (fail-closed)
- CSRF: 모든 POST 폼
- Session: HttpOnly + Secure (production) + SameSite=Strict
- Impersonate: audit_log 모든 액션

## 8. 운영 원칙

- main 직접 push 금지 — PR + 1 review + staging 검증
- DB 마이그: staging 먼저 → 검증 → prod
- 한글 SQL 리터럴: 0개 (run_sql.py 자동 검증)
- 직원 사용 시간엔 prod push 금지 (점검 공지 사전)

## 9. 위반 발견 시 즉시 fail

```python
# Python startup time check
def assert_kst():
    from services.tz_utils import KST, now_kst
    assert now_kst().tzinfo == KST, 'KST not configured'

# CI에서
def check_sql_korean():
    for f in glob('migrations/*.sql'):
        s = open(f, encoding='utf-8').read()
        kor = [c for c in s if '가' <= c <= '힣']
        assert len(kor) == 0, f'{f}: {len(kor)} Korean chars (use U&\'\\XXXX\')'
```

## 10. 새 기능/페이지 추가 체크리스트

- [ ] RPC 작성 (`p_biz_id` 첫 파라미터)
- [ ] RPC가 모든 집계/필터/페이지네이션 처리
- [ ] Python 라우트는 RPC 호출 + 렌더링만
- [ ] `@biz_required` 또는 `@role_required(...)` 데코레이터
- [ ] 시간 표시 `| kst` 필터 적용
- [ ] 한글 SQL 리터럴 `U&'\XXXX'`
- [ ] product_name 저장 시 `canonical()`
- [ ] 인코딩 `encoding='utf-8'` 명시
- [ ] 마이그 추가 시 `migrations/STATUS.md` 갱신
