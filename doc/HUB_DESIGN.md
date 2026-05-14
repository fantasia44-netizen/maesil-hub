# maesil-hub 종합 설계

작성일: 2026-05-14
근거: `INSIGHT_SAAS_ANALYSIS.md` (SaaS 인프라) + `TOTAL_ERP_ANALYSIS.md` (ERP 핵심)
목적: SaaS 인프라(insight)와 ERP/WMS(total)을 통합한 멀티테넌트 식품/축산 ERP-SaaS 설계

---

## 1. 디렉터리 구조 (확정)

```
maesil-hub/
├── app.py                  # Flask app factory (insight 패턴)
├── config.py               # DevelopmentConfig / ProductionConfig
├── wsgi.py
├── worker_entrypoint.py    # ROLE=worker용
├── Procfile
├── requirements.txt
├── render.yaml
│
├── auth/                   # 로그인/세션/SSO/2FA
│   ├── __init__.py
│   ├── routes.py           # ← insight/auth.py 를 분할
│   ├── decorators.py       # require_role, require_feature
│   ├── tokens.py           # JWT 핸드오프, pw_reset
│   └── rate_limit.py
│
├── billing/                # 결제·구독
│   ├── __init__.py
│   ├── routes.py           # ← insight/blueprints/main/payment_views.py
│   ├── billing_views.py    # 구독 화면
│   ├── portone.py          # ← insight/services/portone.py 그대로
│   ├── credit.py           # 크레딧/VAT/플랜 변경 계산
│   └── webhook.py          # 웹훅 핸들러
│
├── plans/                  # 요금제
│   ├── __init__.py
│   ├── cache.py            # plan_cache (DB→메모리)
│   └── features.py         # feature flag 정의 (DB SSOT)
│
├── admin/                  # 슈퍼어드민
│   ├── __init__.py
│   ├── decorators.py       # require_superadmin
│   ├── dashboard.py        # KPI, MRR
│   ├── tenants.py          # businesses 관리, impersonation
│   ├── plans.py            # 플랜 편집
│   ├── settings.py         # saas_config CRUD (Fernet)
│   └── revenue.py          # 결제 이력
│
├── onboarding/             # 가입·시드
│   ├── __init__.py
│   ├── seed.py             # 신규 biz 기본 데이터 (창고1, 옵션매핑0, ...)
│   └── tutorial.py         # ← insight/blueprints/guide/
│
├── blueprints/             # 비즈니스 기능 (모두 biz_id 격리)
│   ├── inventory/          # 재고/수불장 (stock_ledger)
│   ├── production/         # 생산 (PROD_OUT + PRODUCTION + fingerprint)
│   ├── orders/             # 주문 수집/매칭
│   ├── outbound/           # 출고 처리 (FIFO)
│   ├── packing/            # 패킹센터
│   ├── shipping/           # 송장 (CJ API 옵션)
│   ├── transfer/           # 창고이동
│   ├── repack/             # 소분/세트
│   ├── partners/           # 거래처
│   ├── trade/              # 수동거래 + 명세서
│   ├── revenue/            # 매출 통합
│   ├── product_master/     # 옵션마스터/품목마스터
│   ├── channel/            # 채널 연동 설정
│   └── dashboard/          # 메인 대시보드
│
├── db/                     # Repository 패턴
│   ├── base.py             # BaseRepo + _safe_execute + _paginate_query
│   ├── client.py           # Supabase 싱글톤 (HTTP/1.1 강제)
│   ├── biz_context.py      # g.biz_id, set_config('app.current_biz_id', ...)
│   ├── auth_repo.py
│   ├── inventory_repo.py
│   ├── orders_repo.py
│   ├── outbound_repo.py
│   ├── trade_repo.py
│   ├── billing_repo.py
│   └── ...
│
├── services/               # 도메인 서비스
│   ├── product_name.py     # canonical (그대로 이식)
│   ├── option_matcher.py   # 옵션 매칭 (그대로 이식)
│   ├── channel_config.py
│   ├── tz_utils.py
│   ├── crypto.py           # Fernet (그대로 이식)
│   ├── rate_limiter.py     # Redis
│   ├── plan_cache.py
│   ├── marketplace/        # 채널 클라이언트
│   ├── order_to_stock_service.py
│   ├── inbound_service.py
│   ├── outbound_service.py
│   ├── scheduler.py        # APScheduler
│   ├── sync_worker.py
│   └── ...
│
├── migrations/
│   ├── STATUS.md           # ← 강제 누적 기록
│   ├── 001_core_schema.sql        # businesses, app_users, user_business_map
│   ├── 002_billing_schema.sql     # plans, subscriptions, payments
│   ├── 003_saas_config.sql
│   ├── 004_audit_logs.sql
│   ├── 005_inventory_schema.sql   # stock_ledger + biz_id
│   ├── 006_production_schema.sql
│   ├── 007_orders_schema.sql
│   ├── 008_marketplace_config.sql # 암호화된 채널 키
│   ├── 009_canonical_trigger.sql
│   ├── 010_stock_snapshot_rpc.sql # biz_id 인지
│   ├── 011_outbound_list_rpc.sql
│   ├── 012_shipment_stats_rpc.sql
│   ├── 013_revenue_unified_view.sql
│   ├── 014_admin_dashboard_rpc.sql
│   └── ...
│
├── templates/              # Jinja2
│   ├── base.html
│   ├── auth/
│   ├── billing/
│   ├── admin/
│   └── blueprints/
│
├── static/
├── tests/
└── doc/
    ├── ARCHITECTURE.md
    ├── PROJECT_PLAN.md
    ├── INSIGHT_SAAS_ANALYSIS.md
    ├── TOTAL_ERP_ANALYSIS.md
    ├── HUB_DESIGN.md       # ← 본 문서
    └── ONBOARDING.md
```

---

## 2. 데이터 모델 (biz_id 격리)

### 2.1 공통 테이블 (biz_id 없음)

```sql
-- businesses: SaaS 테넌트 마스터
CREATE TABLE businesses (
    id              BIGSERIAL PRIMARY KEY,
    company_name    TEXT NOT NULL,
    company_code    TEXT UNIQUE NOT NULL,
    business_no     TEXT,                       -- 사업자등록번호
    email           TEXT NOT NULL,
    phone           TEXT,
    plan_code       TEXT NOT NULL DEFAULT 'free' REFERENCES plans(code),
    trial_ends_at   TIMESTAMPTZ,
    is_active       BOOLEAN NOT NULL DEFAULT true,
    feature_overrides JSONB DEFAULT '{}'::jsonb,
    settings        JSONB DEFAULT '{}'::jsonb,  -- 사업장별 환경설정
    billing_key     TEXT,
    billing_key_pg  TEXT,                       -- 'card' | 'kakaopay'
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ
);
CREATE INDEX idx_businesses_code ON businesses(company_code);

-- app_users: 모든 사용자 (회사와 분리)
CREATE TABLE app_users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    display_name    TEXT,
    site_role       TEXT NOT NULL DEFAULT 'user',  -- 'user' | 'superadmin'
    is_email_verified BOOLEAN DEFAULT false,
    failed_login_count INT DEFAULT 0,
    locked_until    TIMESTAMPTZ,
    last_seen_at    TIMESTAMPTZ,
    totp_secret     TEXT,                       -- 2FA opt-in
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- user_business_map: 한 user가 여러 biz 소속 가능
CREATE TABLE user_business_map (
    id              BIGSERIAL PRIMARY KEY,
    user_id         UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    biz_id          BIGINT NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    role            TEXT NOT NULL DEFAULT 'viewer',  -- owner | manager | viewer
    is_approved     BOOLEAN DEFAULT false,
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE (user_id, biz_id)
);
CREATE INDEX idx_ubm_user ON user_business_map(user_id);
CREATE INDEX idx_ubm_biz ON user_business_map(biz_id);

-- plans, subscriptions, payments: insight/migrations/007 + 103 + 112 통합
-- saas_config: insight 그대로
-- audit_logs: biz_id nullable (시스템 액션 포함)
```

### 2.2 비즈니스 테이블 (모두 biz_id 필수)

```sql
-- 예: stock_ledger
CREATE TABLE stock_ledger (
    id                  BIGSERIAL PRIMARY KEY,
    biz_id              BIGINT NOT NULL REFERENCES businesses(id),
    transaction_date    DATE NOT NULL,
    type                TEXT NOT NULL,
    location            TEXT,
    product_name        TEXT NOT NULL,
    qty                 NUMERIC NOT NULL,
    unit                TEXT,
    category            TEXT,
    storage_method      TEXT,
    expiry_date         DATE,
    manufacture_date    DATE,
    lot_number          TEXT,
    grade               TEXT,
    event_uid           TEXT,                   -- idempotency
    status              TEXT DEFAULT 'active',
    is_deleted          BOOLEAN DEFAULT false,
    created_by          UUID REFERENCES app_users(id),
    created_at          TIMESTAMPTZ DEFAULT now(),
    UNIQUE (biz_id, event_uid)                  -- biz 단위 idempotency
);
CREATE INDEX idx_sl_biz_date ON stock_ledger(biz_id, transaction_date DESC);
CREATE INDEX idx_sl_biz_product ON stock_ledger(biz_id, product_name, transaction_date DESC);
CREATE INDEX idx_sl_biz_loc ON stock_ledger(biz_id, location, transaction_date DESC);
```

### 2.3 RLS 정책 (모든 biz 테이블)

```sql
ALTER TABLE stock_ledger ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON stock_ledger
    USING (biz_id = current_setting('app.current_biz_id', true)::bigint)
    WITH CHECK (biz_id = current_setting('app.current_biz_id', true)::bigint);

-- service_role 키는 RLS bypass — 코드 레벨 .eq('biz_id', g.biz_id) 1차 방어
-- RLS는 2차 방어선 (실수 방지)
```

### 2.4 RPC 표준 시그니처

```sql
CREATE OR REPLACE FUNCTION get_stock_snapshot_agg(
    p_biz_id BIGINT,             -- ★ 첫 파라미터
    p_date_to DATE,
    p_split_mode TEXT DEFAULT 'none'
) RETURNS TABLE (...)
LANGUAGE sql STABLE SECURITY DEFINER SET statement_timeout = '15s'
AS $$
    SELECT ... FROM stock_ledger
    WHERE biz_id = p_biz_id        -- ★ 필수 필터
      AND transaction_date <= p_date_to
      ...
$$;
GRANT EXECUTE ON FUNCTION get_stock_snapshot_agg(BIGINT, DATE, TEXT)
    TO authenticated, service_role;
```

---

## 3. 가입 → 결제 → 시드 → 사용 흐름

```
[랜딩] /
  ↓ "무료 체험 시작" 클릭
[가입] POST /register
  ├─ businesses INSERT (plan='free', trial_ends_at=+7d, is_active=true)
  ├─ app_users INSERT (email, bcrypt password_hash) — 처음이면
  ├─ user_business_map INSERT (user_id, biz_id, role='owner', is_approved=true)
  ├─ subscriptions INSERT (status='trial')
  ├─ consent_logs ×2 (terms, privacy)
  └─ onboarding/seed.create_default_seed(biz_id):
       ├─ warehouses: ('본사', '제1창고')
       ├─ plan_features 적용
       ├─ option_master: 0건 (사용자가 채울 영역)
       └─ tutorial_state: step=1
  → flash 가입 완료, redirect /login

[로그인] POST /login
  ├─ bcrypt verify + IP/계정 rate limit + 잠금 처리
  ├─ user_business_map 조회 → 1건이면 자동 선택, 다건이면 /select-biz
  ├─ session['user_id'], session['biz_id'] 세팅
  └─ before_request에서 g.biz_id + set_config('app.current_biz_id', g.biz_id)
  → /app/dashboard

[Trial 만료 임박] before_request._check_subscription_lock(user)
  ├─ trial_ends_at 지남 + status='trial' → True
  └─ True면 /app/billing 강제 리다이렉트 (BILLING_EXEMPT 경로 외)

[결제 카드 등록] /app/billing → PortOne JS SDK → 빌링키 발급
  ↓
POST /payment/billing-key/save (insight 패턴 그대로)
  ├─ businesses.billing_key/billing_key_pg UPDATE
  └─ flash 등록 완료

POST /payment/change (플랜 선택)
  ├─ 크레딧 계산 (잔여일 비례)
  ├─ portone.charge_subscription(biz_id, billing_key, amount, ...)
  ├─ payments INSERT (status='paid')
  ├─ subscriptions UPDATE (status='active', current_period_start/end +1month)
  └─ businesses.plan_code UPDATE
  → 즉시 사용 가능

[정기 청구] services/scheduler.py 매일 00:00
  ├─ subscriptions WHERE auto_renewal=true AND current_period_end < NOW() + 1d
  └─ portone.charge_subscription() → 성공 시 +1 month, 실패 시 status='past_due', 3일 후 'sleep'

[웹훅] POST /payment/webhook
  ├─ portone.verify_webhook(headers, body) — Standard Webhooks HMAC
  └─ 이벤트별 처리 (paid/cancelled/refunded)
```

---

## 4. before_request 훅 (핵심)

```python
@app.before_request
def before_request():
    # 1. 봇/스캐너 차단
    if any(p in request.path.lower() for p in BOT_PATHS):
        abort(404)
    if request.path.startswith('/static'):
        return

    # 2. 세션 타임아웃
    if current_user.is_authenticated:
        # ... (insight 패턴)

        # 3. biz 컨텍스트 — ★ 핵심
        g.biz_id = session.get('biz_id') or current_user.default_biz_id
        if not g.biz_id and request.endpoint not in ('auth.select_biz', ...):
            return redirect(url_for('auth.select_biz'))

        g.user_role = get_role_in_biz(current_user.id, g.biz_id)

        # 4. RLS context — Supabase에 세션 변수 설정
        if g.biz_id:
            try:
                app.supabase.rpc('set_biz_context', {'p_biz_id': g.biz_id}).execute()
            except Exception:
                pass  # service_role 사용 시 RLS bypass라 fail-open

        # 5. 슈퍼어드민 impersonation
        if current_user.is_superadmin and session.get('impersonate_biz_id'):
            g.biz_id = session['impersonate_biz_id']
            g.impersonating = True

        # 6. 구독 만료 체크
        if not current_user.is_superadmin:
            if _check_subscription_lock(g.biz_id):
                if not request.path.startswith(('/app/billing', '/auth')):
                    return redirect(url_for('billing.index'))
```

---

## 5. Phase별 마일스톤 (PROJECT_PLAN.md 보완)

### Phase 0 — 셋업 (2026-05-14 ~ 05-21, 1주)
- [x] 레포 + 로컬 폴더 + ARCHITECTURE.md/PROJECT_PLAN.md
- [x] **doc/INSIGHT_SAAS_ANALYSIS.md, TOTAL_ERP_ANALYSIS.md, HUB_DESIGN.md** ← 본 작업
- [ ] Supabase: `maesil-hub-staging` 프로젝트 생성, anon/service 키 확보
- [ ] Render: 빈 web 서비스 (Hello World) 배포 → `staging.hub.maesil.net` 매핑
- [ ] 도메인 구매/매핑 (`hub.maesil.net`)
- [ ] GitHub branch protection (main: PR 필수 + 리뷰 1)
- [ ] 기본 Flask 골격: `app.py`, `config.py`, `wsgi.py`, `Procfile`, `render.yaml`, `.env.example`
- [ ] 첫 마이그 `migrations/001_core_schema.sql` (businesses, app_users, user_business_map, plans, subscriptions, payments, saas_config, audit_logs)
- [ ] `migrations/STATUS.md` 시작
- [ ] 마이그 도구: `scripts/run_sql.py` (한글 리터럴 검증 포함)

### Phase 1 — SaaS 인프라 이식 (2026-05-22 ~ 06-04, 2주)
- [ ] **auth/** 이식 (`insight/auth.py` → `auth/routes.py` 분할). bcrypt + IP/계정 잠금 + Redis rate limit
- [ ] **billing/** 이식 (`insight/services/portone.py` 그대로 + `payment_views.py` 분할)
- [ ] **plans/** + DB plans 테이블 + plan_cache
- [ ] **admin/** 슈퍼어드민 (`require_superadmin`, dashboard, settings, plans, tenants)
- [ ] **onboarding/seed.py** 신규 biz 기본 시드
- [ ] before_request 훅 (`g.biz_id`, RLS context, subscription lock)
- [ ] `services/crypto.py` Fernet (saas_config 암호화)
- [ ] templates: auth/login, register, billing/index, admin/dashboard 최소 화면
- [ ] **테스트 시나리오**: 가입 → 7일 trial → 카드 등록 → 결제 → 만료 → 재결제 → 환불 (staging)

### Phase 2 — ERP 핵심 이식 (2026-06-05 ~ 06-30, 4주)
- [ ] **services/product_name.canonical, option_matcher** 그대로
- [ ] **services/marketplace/** 채널 클라이언트 (cafe24, coupang, naver, smartstore, st11, esm, kakao)
- [ ] **db/inventory_repo, orders_repo, outbound_repo** (biz_id 강제)
- [ ] **migrations 005~014** (inventory, production, orders, marketplace_config, RPC들)
- [ ] **blueprints/inventory, production, orders, outbound, packing, shipping, transfer, repack, partners, trade, revenue, dashboard**
- [ ] event_uid 유니크: `(biz_id, event_uid)`
- [ ] production fingerprint Redis 이전
- [ ] 매출 통합 view (`revenue_unified`)
- [ ] **테스트**: 채널 1개 연결 → 주문 수집 → 옵션매칭 → 출고 → 재고 차감 → 송장 → 매출 집계 (staging)

### Phase 3 — 첫 외부 고객 온보딩 (2026-07-01 ~ 07-31, 1개월)
- [ ] 도메인 옵션: `hub.maesil.net/<biz_code>` 또는 `<biz>.hub.maesil.net`
- [ ] 가입 후 3일/7일 자동 안내 메일 (Resend)
- [ ] 카카오 채널톡 / 채널.io 연동
- [ ] 결제 실패 자동 정지/복구 (paid 후 즉시 잠금 해제)
- [ ] 사용량 통계 (DAU, API 호출, 저장 용량)
- [ ] 가이드/튜토리얼 (`onboarding/tutorial.py`)
- [ ] 베타 고객 5명 모집 → 6개월 무료 + 피드백

### Phase 4 — 운영 안정화 (2026-08-01 ~)
- [ ] Supabase PITR 백업
- [ ] Sentry + 텔레그램/슬랙 알람
- [ ] 부하 테스트 (테넌트 50/100명 동시)
- [ ] 가격 fine-tune
- [ ] **maesil-saas-core 라이브러리화 검토** — auth/billing/admin을 별도 PyPI 패키지로

---

## 6. 위험 요소와 완화책

| 위험 | 영향 | 완화 |
|---|---|---|
| **biz_id 누락 쿼리** (사고 시 다른 회사 데이터 노출) | Critical | (1) RLS 정책 모든 테이블 강제 (2) 코드 리뷰 PR 체크리스트 (3) `.eq('biz_id', g.biz_id)` 누락 grep 자동 검출 CI |
| **canonical product_name 충돌** (biz A의 "사과" ↔ biz B의 "사과") | Medium | UNIQUE 제약을 `(biz_id, canonical_name)`으로 정의 |
| **event_uid 충돌** (채널 주문번호 중복) | Medium | UNIQUE `(biz_id, event_uid)` |
| **PortOne 빌링키 노출** | Critical | DB에 평문 저장 (PortOne 정책: 빌링키 자체는 평문 OK), 단 channel_key는 saas_config Fernet |
| **superadmin impersonation 오용** | High | (1) 모든 impersonation 행위 audit_logs (2) 30분 자동 만료 (3) 본인 IP/UA 기록 |
| **결제 실패 후 데이터 손실** | High | (1) 결제 실패 시 즉시 잠금 X, 3일 grace period (2) `status='past_due'` 후 메일/카톡 알림 3회 |
| **마이그 한글 깨짐** (insight 015 사고 재발) | Medium | `scripts/run_sql.py`가 한글 리터럴 자동 검출 → Unicode escape 강제 |
| **Supabase 1000행 limit** | High | 모든 list 쿼리 RPC 또는 `_paginate_query`. 직접 `.select().limit(N)`만 쓰는 코드 grep 검출 CI |
| **단일 슈퍼관리자 계정 탈취** | Critical | 슈퍼관리자 2FA 강제 (TOTP) |
| **첫 외부 고객 데이터 사고 시 신뢰 상실** | Critical | 베타 6개월 무료 + 명시적 SLA 없음 + 일일 백업 검증 |
| **maesil-total 운영 사고 (배마마)** | Medium | total은 freeze, 긴급 hotfix만. hub 안정 후 12개월 내 데이터 이관 별도 Phase |
| **광고대행사/파트너 모델 부재** | Low | Phase 4+ 추가. user_business_map 모델이 이미 N:N 지원 → 확장 용이 |

---

## 7. 즉시 시작 가능한 Phase 0 작업 (이번주)

1. **Supabase 프로젝트 생성** + `.env.example` 정리 + `db/client.py` (HTTP/1.1 강제 패턴은 maesil-total `db_supabase.py:9-93` 그대로)
2. **migrations/001_core_schema.sql** 작성 — businesses, app_users, user_business_map, plans, subscriptions, payments, saas_config, audit_logs (한글 리터럴 0개)
3. **scripts/run_sql.py** — 마이그 실행 + 한글 리터럴 검증 + STATUS.md 자동 갱신
4. **app.py 골격** — insight `app.py` 복사 후 supabase init + Flask-Login + CSRF + 빈 hello 라우트
5. **GitHub Actions CI** — pytest + flake8 + `grep -rE "\.eq\(.\"|'\)"` (biz_id 누락 검출)
