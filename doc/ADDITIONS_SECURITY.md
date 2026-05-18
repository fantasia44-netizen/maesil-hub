# maesil-hub — 사업계획서 대비 추가사항 & 보안 보강안

> 기준: 『올해의 K-스타트업 2026 AI리그 사업계획서』 (마감 2026.05.20) vs 현재 hub 설계도/구현
> 작성일: 2026-05-18
> 우선순위: P0(즉시) / P1(이번주) / P2(이번달) / P3(다음달+)

---

## 0. 요약 — 사업계획서가 요구하는데 hub 설계가 누락한 5가지

| # | 사업계획서 명시 | hub 현재 상태 | 갭 | 우선순위 |
|---|---|---|---|---|
| 1 | 자금 20%(2,000만) = **데이터 암호화·서버 이중화** | Fernet은 `saas_config`만, Render Singapore 단일 리전 | PII/거래정보 컬럼 암호화 부재, 멀티리전 0 | **P0** |
| 2 | 자금 10%(1,000만) = **TIPA 기술임치** | 임치 절차 문서 없음 | 소스코드 임치 SOP 부재 | **P1** |
| 3 | 자금 25%(2,500만) = **글로벌 채널 API (아마존·쇼피·라자다)** | 쿠팡/네이버만 (`marketplace/` 4개 클라이언트) | 글로벌 어댑터 0, 다국어/다통화 0 | **P2** (2027 Q3 마감) |
| 4 | 자금 10%(1,000만) = **파트너스 (첫결제 20%, 11개월 10% 레퍼럴)** | 코드 0건, blueprint 0건 (`partners.py`는 거래처용) | 레퍼럴 트래킹/수수료 풀/대시보드 부재 | **P1** (2026.09 런칭) |
| 5 | "**2026.06 PG결제 승인·유료화**" 마감 | `billing.py` blueprint 있음, PortOne secret 환경변수 있음, 실 동작 미검증 | 빌링키·정기결제·webhook 서명검증·환불·VAT 미검증 | **P0** (D-13) |

---

## 1. 보안 갭 (P0) — 코드 즉시 패치 필요

### 1.1 로그인 5회 실패 잠금 미구현 [CRITICAL]
**문제**: `migrations/001_core_schema.sql`에 `failed_login_count`, `locked_until` 컬럼은 있으나, `auth/views.py::login()` 함수가 사용하지 않음.
설계 문서(`doc/AUTH_AND_TENANCY.md` §1-5)에 "5회 실패 시 5분 lockout" 명시되어 있음. **설계-구현 불일치**.

**즉시 패치 (auth/views.py)**:
```python
# 로그인 실패 시
fail_count = (user_row.get('failed_login_count') or 0) + 1
update = {'failed_login_count': fail_count}
if fail_count >= 5:
    update['locked_until'] = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
client.table('app_users').update(update).eq('id', user_row['id']).execute()

# 로그인 시도 전 잠금 체크
locked = user_row.get('locked_until')
if locked and locked > datetime.now(timezone.utc).isoformat():
    flash('5회 실패로 5분간 잠금', 'danger')
    return redirect(url_for('auth.login'))

# 로그인 성공 시 카운터 리셋
client.table('app_users').update({
    'failed_login_count': 0, 'locked_until': None,
    'last_login_at': datetime.now(timezone.utc).isoformat(),
}).eq('id', user.id).execute()
```

### 1.2 RPC `p_biz_id` 파라미터 미적용 [CRITICAL]
**문제**: `docs/ARCHITECTURE.md` §9에 명시 — "**모든 RPC에 `p_biz_id` 파라미터 추가 필요 (현재 service_role 전용이라 임시 작동 중)**". 10개 RPC가 모두 service_role로 멀티테넌트 격리 없이 작동 중.

**위험**: 앱 레벨 `WHERE biz_id` 누락 한 줄 = 전 테넌트 데이터 노출.

**해결책 (P0 — 이번주 마감)**:
1. `migrations/011_rpc_biz_id_required.sql` 생성 — 10개 RPC를 `p_biz_id BIGINT NOT NULL` 첫 파라미터로 재정의
2. `db_supabase.py` / `db/*.py`의 모든 `rpc(...)` 호출에 `g.biz_id` 명시 전달
3. `scripts/check_rpc_biz_id.py` — 정적 검사 스크립트로 누락 RPC 호출 자동 탐지

### 1.3 RLS 전면 비활성화 [HIGH]
**문제**: `docs/ARCHITECTURE.md` §10 — "RLS 비활성화 (주요 테이블) — 앱 레벨에서 격리".
하지만 `doc/DATA_MODEL.md`와 `doc/ARCHITECTURE.md`(doc 폴더 쪽)의 원래 설계는 **RLS 100% + biz_id 필터 정책**.
service_role key가 유출되거나 SQL 직접 실행 시 격리 우회 가능.

**해결책**:
- **Phase 1 (P1)**: 모든 비즈니스 테이블에 `tenant_isolation` 정책 enable. service_role은 `USING (true)` 유지.
- **Phase 2 (P2)**: anon/authenticated key 사용 + `current_setting('app.current_biz_id')` 강제. service_role은 시스템 cron 전용으로 격리.

```sql
-- migrations/012_rls_enable_all.sql
ALTER TABLE order_transactions ENABLE ROW LEVEL SECURITY;
CREATE POLICY service_all ON order_transactions FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY tenant_iso  ON order_transactions FOR ALL TO authenticated
    USING (biz_id = current_setting('app.current_biz_id', TRUE)::BIGINT);
-- stock_ledger, product_costs, ... 14개 비즈니스 테이블 동일
```

### 1.4 PII/거래정보 컬럼 평문 저장 [HIGH]
**문제**: `recipient_name`, `recipient_phone`, `address`, `biz_reg_no` 등 PII가 평문. 사업계획서 자금 20%(2,000만)가 **"데이터 암호화"** 명시.

**해결책** (P1):
- 수신자 정보(이름/전화/주소) → `pgcrypto` PGP_SYM_ENCRYPT 또는 애플리케이션 레벨 Fernet
- 사업자등록번호 → 마스킹 컬럼(`biz_reg_no_masked` 추가, 표시용) + 원본 암호화
- 검색 필요 컬럼은 `hash_*` 컬럼(HMAC-SHA256(SALT, value)) 추가로 equality 검색만 허용

### 1.5 PortOne Webhook 서명 검증 미확인 [HIGH]
**문제**: `PORTONE_WEBHOOK_SECRET` 환경변수는 있으나 검증 코드 grep 결과 `services/`에 없음.
검증 누락 시 **위조 결제 webhook으로 구독 활성/환불 조작 가능**.

**해결책 (P0 — 결제 오픈 전 필수)**:
```python
# blueprints/billing.py — webhook 핸들러
@billing_bp.route('/webhook/portone', methods=['POST'])
def portone_webhook():
    sig = request.headers.get('X-Portone-Signature', '')
    body = request.get_data()
    expected = hmac.new(
        os.environ['PORTONE_WEBHOOK_SECRET'].encode(),
        body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return jsonify({'error': 'invalid signature'}), 401
    # ... 이후 처리
```

### 1.6 Audit log IP/UA 누락 [MED]
**문제**: `audit_logs` 스키마에 `ip_address INET`, `user_agent TEXT` 있으나 `helpers.log_audit()` 호출 시 채우는지 검증 필요.
impersonation 추적·법적 증거에 필수.

---

## 2. 사업계획서 직접 명시 항목 — 신규 추가 설계

### 2.1 매실 파트너스 (레퍼럴) — `blueprints/referral.py` 신설 [P1]
**사업계획서**: "첫 결제 20% / 이후 11개월 10% 레퍼럴 수수료 (코드 구현 완료)" ← 사업계획서 진술과 실제 코드 불일치.

**필요 스키마**:
```sql
-- migrations/013_referrals.sql
CREATE TABLE partner_codes (
    code            TEXT PRIMARY KEY,                -- 'PARTNER-AB12CD'
    owner_user_id   UUID REFERENCES app_users(id),
    biz_id          BIGINT REFERENCES businesses(id),  -- 파트너 본인 회사
    rate_first      NUMERIC(4,3) DEFAULT 0.20,        -- 첫결제 20%
    rate_recurring  NUMERIC(4,3) DEFAULT 0.10,        -- 이후 10%
    recurring_months INT DEFAULT 11,
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE referral_attributions (
    id              BIGSERIAL PRIMARY KEY,
    referee_biz_id  BIGINT NOT NULL REFERENCES businesses(id) UNIQUE,
    partner_code    TEXT NOT NULL REFERENCES partner_codes(code),
    attributed_at   TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE referral_payouts (
    id              BIGSERIAL PRIMARY KEY,
    payment_id      BIGINT NOT NULL REFERENCES payments(id) UNIQUE,
    partner_code    TEXT NOT NULL,
    rate            NUMERIC(4,3) NOT NULL,
    amount          INTEGER NOT NULL,
    period_index    INT NOT NULL,                    -- 0=첫결제, 1..11
    status          TEXT NOT NULL DEFAULT 'pending', -- pending/paid/clawed_back
    paid_at         TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now()
);
```

**대시보드 라우트** (`blueprints/referral.py`):
- `/referral` — 내 코드, 누적 추천수, 이번달 수수료, 누적 페이아웃
- `/referral/payouts` — 정산 내역
- `/admin/referrals` — 슈퍼어드민: 부정 어트리뷰션 감지/차단

**보안**:
- 파트너 코드 = 본인 컨버전 금지 (`partner_codes.biz_id != referee_biz_id` CHECK)
- 환불 시 `referral_payouts.status='clawed_back'` 자동 처리
- 결제 webhook → `referral_payouts` 자동 INSERT (idempotency: `UNIQUE(payment_id)`)

### 2.2 글로벌 채널 어댑터 — `services/marketplace/global/` [P2]
**사업계획서**: 2027 Q3 아마존·쇼피 API 연동, 2028 Q1 라자다 현지화.

**현재**: `services/marketplace/`에 쿠팡/네이버/카카오만.

**설계**:
```
services/marketplace/
├── base_client.py           ← 추상 인터페이스 (fetch_orders, fetch_settlements, push_inventory)
├── coupang_client.py        (있음)
├── naver_client.py          (있음)
└── global/
    ├── amazon_spapi.py      ← SP-API 어댑터 (OAuth + AWS Signature v4)
    ├── shopee_openapi.py    ← Shopee OpenAPI (HMAC-SHA256)
    └── lazada_openapi.py    ← Lazada OpenAPI

services/currency_service.py ← 환율 캐시 (한국은행/네이버 환율 API, 1일 TTL)
services/locale_service.py   ← 언어/통화/타임존 자동 감지
```

**스키마 추가**:
```sql
-- businesses 테이블 확장
ALTER TABLE businesses ADD COLUMN locale TEXT DEFAULT 'ko-KR';
ALTER TABLE businesses ADD COLUMN base_currency TEXT DEFAULT 'KRW';
ALTER TABLE businesses ADD COLUMN tz TEXT DEFAULT 'Asia/Seoul';

-- 환율
CREATE TABLE fx_rates (
    base TEXT NOT NULL, quote TEXT NOT NULL,
    date DATE NOT NULL, rate NUMERIC(12,6) NOT NULL,
    PRIMARY KEY (base, quote, date)
);
```

### 2.3 다국어 UI (i18n) [P2]
**필요**: Flask-Babel 도입, `templates/` Jinja2에 `{{ _('...') }}` 마킹, `translations/` ko/en/zh/th/vi/id.

**Phase 별**:
- P2: ko/en (한국 거주 외국 셀러 + 글로벌 첫 진입)
- P3: zh-CN, ja (동남아 진출 후), th, vi, id (2028 Q1 라자다)

### 2.4 TIPA 기술임치 SOP [P1]
**사업계획서**: 자금 10%(1,000만) "지재권·기술임치 = 오케스트레이션 특허, TIPA 임치".

**문서 신설**: `doc/TIPA_ESCROW.md`
- 임치 주기: 분기 1회 (소스코드 + DB 스키마 + 운영 매뉴얼)
- 임치 패키지 생성 스크립트: `scripts/build_escrow_package.py` (git archive + migrations dump + docs zip)
- TIPA 임치 요청서 양식 / 비용 / 일정 / 담당자 (김대희)

### 2.5 서버 이중화·DR [P2]
**사업계획서**: 자금 20%(2,000만) "데이터 암호화, **서버 이중화**".

**현재**: Render Singapore 단일 리전. Supabase 또한 단일 리전.

**Phase 1 (P1)**:
- Supabase Pro 플랜 → PITR(Point-In-Time Recovery) 활성화
- 주간 pg_dump → S3/R2 백업 (`scripts/weekly_dump.py`, cron)

**Phase 2 (P2)**:
- Render Production 1대 (Singapore) + Render Standby 1대 (Frankfurt or Oregon)
- DNS Failover (Cloudflare Load Balancer) 또는 active-passive

**Phase 3 (P3)**:
- Supabase Read Replica (별도 리전) — 글로벌 셀러 응답 속도 + DR

### 2.6 결제 핵심 누락 항목 [P0]
**사업계획서 마감**: 2026.06 PG결제 승인·유료화.

체크리스트:
- [ ] PortOne 빌링키 등록·삭제 플로우 (`/billing/payment-methods`)
- [ ] 정기결제 자동 청구 cron (월별, 실패시 3회 재시도)
- [ ] webhook 서명 검증 (1.5 참조)
- [ ] 환불 정책 (D+7 100%, D+30 부분, D+90 거절) UI + 슈퍼어드민 강제환불
- [ ] 세금계산서 자동 발급 (`blueprints/tax_invoice.py` 있음 — 트리거 자동화 확인)
- [ ] **이중 결제 멱등성** (`payments.portone_imp_uid UNIQUE`는 있음 — webhook 재시도 시 검증)
- [ ] 카드 만료 알림 (만료 7일 전 이메일)
- [ ] 결제 실패 → `subscriptions.status='past_due'` → 14일 grace → `suspended`

---

## 3. 옵저버빌리티·신뢰성 보강 [P1]

### 3.1 비용/API 호출 추적 부재
**현재**: Sentry로 에러는 추적, but **마켓플레이스 API 호출 비용/횟수/Rate Limit 추적 0**.
Coupang/네이버 API Rate Limit 초과 시 한순간에 전 테넌트 동기화 중단.

**해결책**:
```sql
CREATE TABLE api_call_log (
    id BIGSERIAL PRIMARY KEY,
    biz_id BIGINT NOT NULL,
    channel TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    called_at TIMESTAMPTZ DEFAULT now(),
    status_code INT, latency_ms INT, rate_limit_remaining INT,
    error TEXT
);
CREATE INDEX idx_api_call_biz_time ON api_call_log(biz_id, called_at DESC);
```

`services/marketplace/base_client.py`에 데코레이터 `@log_api_call` 의무화.

### 3.2 멱등성·재시도 표준 부재
**문제**: 결제 webhook, 마켓 동기화, 송장 등록 등 외부 호출에 멱등키 규칙이 각자 다름.

**해결책**: `services/idempotency.py` — `event_uid` 표준
- 형식: `{source}:{biz_id}:{external_id}:{event_type}`
- 예: `portone:7:imp_123abc:paid`, `coupang:7:O1234567890:order_received`
- 모든 INSERT에 `event_uid` UNIQUE 강제 (stock_ledger 이미 적용 — 표준 확대)

### 3.3 매실에이전시 → hub 연동 표준 [P1]
**현재**: agency는 hub를 어떻게 모니터링? `program_registry`에 hub 등록 + Render 로그만 폴링.
hub 비즈니스 메트릭(주문/매출/이상치)을 agency가 분석하려면 **읽기 전용 API 또는 read-only DB 뷰** 필요.

**해결책**:
- `app_users` 신규 role: `agency_readonly` (read-only API token 인증)
- `/api/v1/metrics/*` 엔드포인트 신설 (agency 전용):
  - `GET /api/v1/metrics/daily-revenue?biz_id=&date=`
  - `GET /api/v1/metrics/anomalies?biz_id=&type=` (재고 이상치, 환불률 등)
- Bearer 토큰 + biz_id 화이트리스트
- `services/maesil_bridge.py`가 모태 (현재 hub→insight 방향) — agency 양방향으로 확장

---

## 4. 매실 에이전시와의 통합 (사업계획서 2026.12 상용화)

### 4.1 에이전시 인-허브 위젯 [P2]
**사업계획서**: "매실 에이전시 = AI 의사결정 타워. 에이전트 매일 보고 → 대표 판단만".
허브 운영자(셀러)에게도 동일 가치 제공해야 함.

**구현**:
- hub `/dashboard`에 "오늘의 AI 인사이트" 위젯 (iframe or 직접 SDK)
- agency `sales_insights` 테이블에서 해당 biz_id의 최근 인사이트 fetch
- "처방" 버튼 클릭 → agency `/api/cs/dev-escalate` 호출 → 자동 처리

### 4.2 매요AI 멀티테넌트화 [P2]
**현재 agency**: 매요AI(CS)는 maesil-insight 전용 (DESIGN.md §18 Future Work 명시).
**사업계획서**: hub 셀러도 CS 자동화 필요 (자금 항목엔 없으나 "월 1만+ 고객사 관리 구조" 명시).

**해결책**:
- agency `maeyo_conversations`에 `biz_id BIGINT NULL` (NULL=insight, NOT NULL=hub)
- hub `templates/base.html`에 매요AI 챗 위젯 SDK 임베드 (X-Maeyo-Token + biz_id 헤더)
- L2 스크립트는 program별이 아니라 (program, biz_id) 별로 분리

---

## 5. 마이그레이션 신규 발행 목록 (우선순위)

```
011_rpc_biz_id_required.sql      [P0] 10개 RPC `p_biz_id` 첫 파라미터화
012_rls_enable_all.sql           [P0/P1] 비즈니스 테이블 RLS 100% enable
013_referrals.sql                [P1] 파트너스 레퍼럴 3테이블
014_pii_encrypt_cols.sql         [P1] PII 컬럼 암호화 (pgcrypto)
015_api_call_log.sql             [P1] 외부 API 호출 추적
016_locale_currency.sql          [P2] businesses.locale/base_currency + fx_rates
017_login_lockout_index.sql      [P0] failed_login_count 인덱스 + 잠금 RPC
```

각 마이그 파일 작성 시 `doc/CONVENTIONS.md` §2 — 한글 SQL 리터럴 `U&'\XXXX'` Unicode escape 강제.

---

## 6. 코드 변경 즉시 적용 권장 순서 (D-13까지)

| Day | 작업 | 파일 |
|---|---|---|
| D-13 | 로그인 잠금 패치 + 테스트 | `auth/views.py`, `tests/test_auth_lockout.py` |
| D-13 | PortOne webhook 서명 검증 | `blueprints/billing.py` |
| D-12 | RPC `p_biz_id` 강제 적용 (10개) | `migrations/011_*.sql`, `db/*.py` |
| D-11 | RLS 정책 enable (P1 비즈니스 5개) | `migrations/012_*.sql` |
| D-10 | 파트너스 스키마 + UI 골격 | `migrations/013_*.sql`, `blueprints/referral.py` |
| D-9 | PII 컬럼 암호화 (recipient_*, address) | `migrations/014_*.sql`, `services/pii.py` |
| D-8 | API call log + Rate Limit 가드 | `migrations/015_*.sql`, `services/marketplace/base_client.py` |
| D-7 | E2E: 가입→결제→첫주문→환불→레퍼럴 정산 | `tests/e2e/` |
| D-5 | TIPA 임치 패키지 1회차 빌드 | `scripts/build_escrow_package.py` |
| D-3 | 점검·취약점 스캔 (bandit, pip-audit) | CI workflow |
| D-1 | 최종 staging→prod 머지 | branch protection 확인 |

---

## 7. 사업계획서 체크리스트 보강 (제출 직전)

사업계획서 ⚠️ 체크리스트에 추가 권장:
- [ ] hub `migrations/STATUS.md` 016까지 production 배포 완료
- [ ] PortOne webhook 검증 통합 테스트 PASS
- [ ] 로그인 5회 잠금·CSRF·세션쿠키(Secure/HttpOnly/SameSite) production 검증
- [ ] TIPA 기술임치 1회차 영수증 사본 첨부 (지재권 증빙)
- [ ] 매실에이전시-허브 인증 토큰·메트릭 API 동작 캡처
- [ ] (글로벌) 아마존 SP-API Sandbox 키 등록 증빙 (2027 Q3 로드맵 가시성)

---

*이 문서는 사업계획서 vs maesil-hub 현 시점(2026-05-18, migrations 010까지) 대비표.*
*변경 시 `doc/PROJECT_PLAN.md`의 Phase 우선순위와 정합성 유지 필수.*
