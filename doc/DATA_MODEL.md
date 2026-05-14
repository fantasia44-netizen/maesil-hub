# maesil-hub 데이터 모델

## 핵심 원칙

1. **모든 비즈니스 테이블 `biz_id BIGINT NOT NULL`** + FK to `businesses(id)`
2. **모든 인덱스 `(biz_id, ...)` 시작** — RLS 효율 + 격리 보장
3. **모든 RLS 정책 `biz_id` 필터** — 우회 불가
4. **모든 한글 SQL 리터럴 `U&'\XXXX'`** Unicode escape (인코딩 사고 차단)
5. **`canonical product_name`**: 공백 제거 강제 (`canonical()` 함수, `(biz_id, product_name)` UNIQUE)
6. **모든 RPC 첫 파라미터 `p_biz_id BIGINT`**

## 테이블 분류

### A. 공통 (biz_id 없음, 시스템 전역)

| 테이블 | 목적 |
|---|---|
| `businesses` | 회원사 마스터 (= biz_id 원본) |
| `app_users` | 사용자 마스터 (이메일·비밀번호) |
| `user_business_map` | user ↔ biz ↔ role 다대다 매핑 |
| `plans` | 요금제 정의 (Starter/Pro/Enterprise) |
| `subscriptions` | biz별 구독 상태 |
| `payments` | 결제 이력 (PortOne) |
| `saas_config` | 시스템 설정 (key-value, Fernet 암호화) |
| `audit_logs` | 시스템 감사 (impersonate 포함) |

### B. 비즈니스 (biz_id 필수)

| 테이블 | 목적 |
|---|---|
| `product_costs` | 상품/원료/부자재 마스터 |
| `option_master` | 채널 옵션명 → 표준품목 매핑 |
| `stock_ledger` | 수불장 (모든 입출고 이벤트) |
| `import_runs` | 주문 업로드 이력 |
| `order_transactions` | 주문 (다채널 통합) |
| `order_shipping` | 송장 정보 |
| `order_change_log` | 주문 변경 이력 |
| `manual_trades` | 거래처 직접 출고 |
| `daily_revenue` | 일일 매출 집계 |
| `packing_jobs` | 패킹센터 작업 |
| `business_partners` | 거래처 정보 |
| `purchase_orders` | 발주서 |
| `repack_jobs` | 소분 작업 |
| `transfers` | 창고이동 헤더 |

## 마이그레이션 순번

```
001_core_schema.sql       — 공통 8개 테이블 생성
002_rls_policies.sql      — RLS enable + 공통 정책
003_business_schema.sql   — 비즈니스 테이블 14개 생성
004_business_rls.sql      — 비즈니스 RLS 정책
005_seed_plans.sql        — 기본 요금제 시드
006_seed_admin.sql        — 슈퍼어드민 계정 시드
007_inventory_rpcs.sql    — 재고/생산 RPC
008_order_rpcs.sql        — 주문/출고 RPC
009_revenue_rpcs.sql      — 매출/대시보드 RPC
010_billing_rpcs.sql      — 결제/구독 RPC
```

## SQL 정의 — 공통 테이블

### businesses
```sql
CREATE TABLE businesses (
    id              BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    biz_reg_no      TEXT,                          -- 사업자등록번호
    representative  TEXT,
    address         TEXT,
    industry        TEXT,                          -- food / livestock / etc
    plan_id         BIGINT REFERENCES plans(id),
    status          TEXT NOT NULL DEFAULT 'active', -- active/suspended/cancelled
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_deleted      BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX idx_businesses_status ON businesses(status) WHERE NOT is_deleted;
```

### app_users
```sql
CREATE TABLE app_users (
    id              BIGSERIAL PRIMARY KEY,
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,                 -- bcrypt cost=12
    name            TEXT,
    phone           TEXT,
    is_super_admin  BOOLEAN NOT NULL DEFAULT FALSE,
    email_verified  BOOLEAN NOT NULL DEFAULT FALSE,
    last_login_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_deleted      BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX idx_app_users_email ON app_users(email) WHERE NOT is_deleted;
```

### user_business_map
```sql
CREATE TABLE user_business_map (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES app_users(id),
    biz_id      BIGINT NOT NULL REFERENCES businesses(id),
    role        TEXT NOT NULL,                     -- owner/manager/staff/viewer
    is_primary  BOOLEAN NOT NULL DEFAULT FALSE,    -- 기본 회사
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, biz_id)
);
CREATE INDEX idx_ubm_user ON user_business_map(user_id);
CREATE INDEX idx_ubm_biz ON user_business_map(biz_id);
```

### plans
```sql
CREATE TABLE plans (
    id              BIGSERIAL PRIMARY KEY,
    code            TEXT NOT NULL UNIQUE,          -- starter/pro/enterprise
    name            TEXT NOT NULL,                 -- "스타터" / "프로" 표시용
    monthly_price   INTEGER NOT NULL,              -- 원
    features        JSONB NOT NULL DEFAULT '{}',   -- {"channels": 3, "users": 5, "ai_diagnose": true}
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order      INTEGER NOT NULL DEFAULT 0
);
```

### subscriptions
```sql
CREATE TABLE subscriptions (
    id                 BIGSERIAL PRIMARY KEY,
    biz_id             BIGINT NOT NULL REFERENCES businesses(id) UNIQUE,
    plan_id            BIGINT NOT NULL REFERENCES plans(id),
    status             TEXT NOT NULL,               -- trial/active/past_due/cancelled
    current_period_start  TIMESTAMPTZ NOT NULL,
    current_period_end    TIMESTAMPTZ NOT NULL,
    cancelled_at       TIMESTAMPTZ,
    portone_billing_key TEXT,                       -- 빌링키
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_subscriptions_status ON subscriptions(status);
```

### payments
```sql
CREATE TABLE payments (
    id              BIGSERIAL PRIMARY KEY,
    biz_id          BIGINT NOT NULL REFERENCES businesses(id),
    subscription_id BIGINT REFERENCES subscriptions(id),
    portone_imp_uid TEXT NOT NULL UNIQUE,
    portone_merchant_uid TEXT NOT NULL UNIQUE,
    amount          INTEGER NOT NULL,              -- VAT 포함
    vat_amount      INTEGER NOT NULL,
    status          TEXT NOT NULL,                 -- paid/failed/cancelled/refunded
    paid_at         TIMESTAMPTZ,
    raw_response    JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_payments_biz ON payments(biz_id, created_at DESC);
```

### saas_config
```sql
CREATE TABLE saas_config (
    key             TEXT PRIMARY KEY,
    value_encrypted BYTEA,                          -- Fernet 암호화
    value_plain     TEXT,                           -- 비민감
    description     TEXT,
    updated_by      BIGINT REFERENCES app_users(id),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### audit_logs
```sql
CREATE TABLE audit_logs (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT REFERENCES app_users(id),
    biz_id      BIGINT REFERENCES businesses(id),  -- 작업이 어느 회사에 가해졌는지
    operator_id BIGINT REFERENCES app_users(id),   -- impersonate 시 원본 admin
    action      TEXT NOT NULL,                     -- 'login', 'impersonate', 'plan_change' 등
    detail      JSONB,
    ip_address  INET,
    user_agent  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_logs_biz ON audit_logs(biz_id, created_at DESC);
CREATE INDEX idx_audit_logs_user ON audit_logs(user_id, created_at DESC);
```

## SQL 정의 — 비즈니스 테이블 (대표 5개)

### product_costs
```sql
CREATE TABLE product_costs (
    id              BIGSERIAL PRIMARY KEY,
    biz_id          BIGINT NOT NULL REFERENCES businesses(id),
    product_name    TEXT NOT NULL,                 -- canonical (공백 제거)
    cost_price      NUMERIC(12,2) DEFAULT 0,
    unit            TEXT,
    weight          NUMERIC(10,2) DEFAULT 0,
    weight_unit     TEXT DEFAULT 'g',
    cost_type       TEXT,                          -- 매입/생산
    material_type   TEXT,                          -- 완제품/반제품/원료/부자재
    category        TEXT,
    purchase_unit   TEXT,
    standard_unit   TEXT,
    conversion_ratio NUMERIC(10,4) DEFAULT 1,
    safety_stock    NUMERIC(10,2) DEFAULT 0,
    storage_method  TEXT,                          -- 냉동/냉장/실온
    food_type       TEXT,                          -- 축산물/수산물/농산물 등
    is_stock_managed BOOLEAN DEFAULT TRUE,
    memo            TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_deleted      BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE(biz_id, product_name)
);
CREATE INDEX idx_product_costs_biz ON product_costs(biz_id) WHERE NOT is_deleted;
CREATE INDEX idx_product_costs_biz_cat ON product_costs(biz_id, category) WHERE NOT is_deleted;
```

### stock_ledger
```sql
CREATE TABLE stock_ledger (
    id              BIGSERIAL PRIMARY KEY,
    biz_id          BIGINT NOT NULL REFERENCES businesses(id),
    transaction_date DATE NOT NULL,
    type            TEXT NOT NULL,                 -- INIT/INBOUND/PROD_OUT/PRODUCTION/SALES_OUT/MOVE_IN/MOVE_OUT/REPACK_OUT/REPACK_IN/ETC_OUT/ETC_IN/ADJUST/SALES_RETURN
    product_name    TEXT NOT NULL,                 -- canonical
    qty             NUMERIC(12,2) NOT NULL,        -- 부호 있음 (PROD_OUT은 음수)
    unit            TEXT,
    location        TEXT,                          -- 창고
    category        TEXT,
    storage_method  TEXT,
    lot_number      TEXT,                          -- 이력번호
    grade           TEXT,
    manufacture_date DATE,
    expiry_date     DATE,
    origin          TEXT,                          -- 원산지
    batch_id        TEXT,                          -- 생산/소분 묶음
    transfer_id     TEXT,                          -- 창고이동 묶음
    repack_doc_no   TEXT,                          -- 소분 문서번호
    event_uid       TEXT,                          -- 외부 이벤트 멱등키 (DR_AUTO:..., COUPANG_API:...)
    memo            TEXT,
    status          TEXT NOT NULL DEFAULT 'active', -- active/cancelled/replaced
    created_by      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_deleted      BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_by      TEXT,
    UNIQUE(biz_id, event_uid)
);
CREATE INDEX idx_stock_ledger_biz_date ON stock_ledger(biz_id, transaction_date) WHERE status='active';
CREATE INDEX idx_stock_ledger_biz_type ON stock_ledger(biz_id, type, transaction_date) WHERE status='active';
CREATE INDEX idx_stock_ledger_biz_product ON stock_ledger(biz_id, product_name) WHERE status='active';
CREATE INDEX idx_stock_ledger_batch ON stock_ledger(biz_id, batch_id) WHERE batch_id IS NOT NULL;
```

### order_transactions
```sql
CREATE TABLE order_transactions (
    id                 BIGSERIAL PRIMARY KEY,
    biz_id             BIGINT NOT NULL REFERENCES businesses(id),
    order_date         DATE NOT NULL,
    channel            TEXT NOT NULL,
    order_no           TEXT NOT NULL,
    line_no            INTEGER NOT NULL DEFAULT 1,
    product_name       TEXT NOT NULL,              -- canonical (option_master 매칭 후)
    original_product   TEXT,                        -- 플랫폼 원문
    option_name        TEXT,
    qty                INTEGER NOT NULL,
    unit_price         INTEGER NOT NULL DEFAULT 0,
    total_amount       INTEGER NOT NULL DEFAULT 0,
    settlement         INTEGER NOT NULL DEFAULT 0,
    commission         INTEGER NOT NULL DEFAULT 0,
    status             TEXT NOT NULL DEFAULT U&'\C815\C0C1',  -- 정상/취소/환불/반품
    is_outbound_done   BOOLEAN NOT NULL DEFAULT FALSE,
    outbound_date      DATE,
    recipient_name     TEXT,
    raw_hash           TEXT,                        -- 중복 INSERT 방지
    raw_data           JSONB,
    import_run_id      BIGINT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(biz_id, channel, order_no, line_no)
);
CREATE INDEX idx_ot_biz_date ON order_transactions(biz_id, order_date DESC);
CREATE INDEX idx_ot_biz_outbound ON order_transactions(biz_id, is_outbound_done, order_date)
    WHERE status = U&'\C815\C0C1';
CREATE INDEX idx_ot_biz_raw_hash ON order_transactions(biz_id, raw_hash) WHERE raw_hash IS NOT NULL;
```

### option_master
```sql
CREATE TABLE option_master (
    id              BIGSERIAL PRIMARY KEY,
    biz_id          BIGINT NOT NULL REFERENCES businesses(id),
    original_name   TEXT NOT NULL,                 -- 플랫폼 원문
    product_name    TEXT NOT NULL,                 -- 표준 (canonical)
    line_code       TEXT,
    sort_order      NUMERIC(10,2) DEFAULT 999,
    barcode         TEXT,
    match_key       TEXT,                          -- normalize_match_key 결과
    last_matched_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_deleted      BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE(biz_id, match_key)
);
CREATE INDEX idx_option_master_biz ON option_master(biz_id) WHERE NOT is_deleted;
CREATE INDEX idx_option_master_biz_product ON option_master(biz_id, product_name);
```

### manual_trades
```sql
CREATE TABLE manual_trades (
    id              BIGSERIAL PRIMARY KEY,
    biz_id          BIGINT NOT NULL REFERENCES businesses(id),
    partner_name    TEXT NOT NULL,
    product_name    TEXT NOT NULL,                 -- canonical
    trade_date      DATE NOT NULL,
    trade_type      TEXT NOT NULL,                 -- 판매/매입
    qty             NUMERIC(12,2) NOT NULL,
    unit            TEXT,
    unit_price      INTEGER DEFAULT 0,
    amount          INTEGER DEFAULT 0,
    memo            TEXT,
    registered_by   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_deleted      BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX idx_mt_biz_date ON manual_trades(biz_id, trade_date DESC) WHERE NOT is_deleted;
```

## RLS 정책 패턴

### 기본 정책 (모든 비즈니스 테이블)
```sql
ALTER TABLE product_costs ENABLE ROW LEVEL SECURITY;

-- service_role은 모든 행 접근 (서버 백엔드용)
CREATE POLICY service_role_all ON product_costs
    FOR ALL TO service_role
    USING (true) WITH CHECK (true);

-- authenticated 사용자는 자신의 biz_id만
CREATE POLICY tenant_isolation ON product_costs
    FOR ALL TO authenticated
    USING (biz_id = current_setting('app.current_biz_id', TRUE)::BIGINT)
    WITH CHECK (biz_id = current_setting('app.current_biz_id', TRUE)::BIGINT);
```

### Flask 측 biz_id 세팅 (before_request)
```python
@app.before_request
def set_tenant_context():
    if current_user.is_authenticated and g.biz_id:
        # Supabase RLS 컨텍스트 설정
        db.client.rpc('set_app_setting', {
            'p_key': 'app.current_biz_id',
            'p_value': str(g.biz_id),
        }).execute()
```

또는 RPC 첫 파라미터로 명시 전달 (RLS 우회 + 명시적 격리):
```python
db.client.rpc('rpc_get_inventory', {
    'p_biz_id': g.biz_id,
    'p_date_from': '...',
}).execute()
```

## 관계도 (텍스트 ER)

```
app_users ─┬─< user_business_map >─┬─ businesses
           │                       │
           │                       ├─< subscriptions ─> plans
           │                       │
           │                       └─< payments
           │
           └─< audit_logs >─ businesses

businesses ─┬─< product_costs
            ├─< stock_ledger >─ product_costs (product_name)
            ├─< order_transactions >─ option_master
            ├─< order_shipping
            ├─< manual_trades
            ├─< daily_revenue
            ├─< import_runs
            ├─< packing_jobs
            └─< business_partners
```

## canonical product_name 규칙

`services/product_name.py`의 `canonical()`:

1. 모든 종류 공백 제거 (일반/전각/NBSP/narrow NBSP/FIGURE SPACE/탭/개행)
2. `strip()`
3. 대소문자 미변환
4. 구분자 통일 미적용

예:
- `canonical("중기이유식 세트")` → `"중기이유식세트"`
- `canonical("  고구마&사과 퓨레 ")` → `"고구마&사과퓨레"`

**모든 INSERT/UPDATE 전에 canonical 강제** — DB-level CHECK constraint 또는 trigger로 강화 가능 (Phase 2).

## 검증 체크리스트

신규 테이블 추가 시:
- [ ] biz_id 컬럼 + FK
- [ ] 비즈니스 인덱스 (biz_id, ...) 시작
- [ ] RLS enable + service_role + tenant_isolation 정책
- [ ] UNIQUE 제약에 biz_id 포함
- [ ] is_deleted, created_at, updated_at 표준 컬럼
- [ ] 한글 SQL 리터럴 0개
