# maesil-insight SaaS 인프라 전수 분석

작성일: 2026-05-14
대상 레포: `C:\maesil-insight` (Phase 4+ 운영 중인 멀티테넌트 SaaS)
용도: maesil-hub 설계 시 직접 이식할 모듈, 수정할 모듈, 재설계할 모듈을 결정하기 위한 사실 기반 분석

---

## A. 결제 (Payment / Billing) — PortOne v2 (KakaoPay + 카드)

### 핵심 파일
- `services/portone.py` (315 lines) — PortOne v2 API 래퍼 (빌링키 발급/조회/정기결제/환불/웹훅 검증)
- `blueprints/main/payment_views.py` (1030 lines) — 결제 라우트 (저장/변경/취소/환불/웹훅)
- `blueprints/main/billing_views.py` (276 lines) — 구독 화면, 플랜 변경 UI
- `services/scheduler.py` — 정기결제 자동 청구 스케줄러
- `migrations/007_payment_tables.sql` — `payment_transactions`, `subscriptions` 초기 정의
- `migrations/112_payments_table.sql`, `139_operator_billing_key_pg.sql`, `141_plans_policy_update.sql`, `178_payment_renewal_vat_security.sql` — 누적 정책 변경

### 결제 흐름
1. 가입(`/register`) → operators + subscriptions(status='trial', trial_ends_at=+7일) + app_users(owner) 생성 (`auth.py:580-625`)
2. trial 종료 임박 → `/app/billing` 화면에서 PortOne JS SDK로 카드 등록 → 빌링키 프론트 발급
3. `POST /payment/billing-key/save` → `operators.billing_key`, `billing_key_pg` 저장 (`payment_views.py:93-`)
4. `POST /payment/change` → `_calc_credit()`로 잔여일 비례 환산 → `services/portone.charge_subscription()` 호출 → `payments` 행 + `subscriptions` 갱신
5. 매일 스케줄러 → `current_period_end` 임박 구독 → 자동 청구
6. 실패 → status `past_due`/`sleep`로 강등 → `_check_subscription_lock()` (`app.py:495-541`)이 `before_request` 훅에서 모든 페이지 차단 → `/app/billing`로 리다이렉트
7. 환불: `cancel_payment()` → PortOne에 부분/전액 cancel → `payment_transactions.refund_*` 컬럼 업데이트

### 강점
- **PortOne v2 Standard Webhooks 서명 검증** (`portone.py:246-314`) — `webhook-id.timestamp.payload` HMAC-SHA256, `whsec_` base64 디코드, 5분 타임스탬프 신선도 체크. 그대로 복붙 가능.
- **부가세 분리** `_split_vat()` (1/11 분리) — 표시가 = 공급가 + VAT.
- **VAT, 크레딧, 다운그레이드 차단** 로직이 잘 정리됨.
- **카드 미등록 시 need_card 플래그**로 프론트에 안내.
- **빌링 KakaoPay/카드 분리 채널키** (`portone_channel_kakao`, `portone_channel_card`) — PG별 빌링키 분리 보관.

### 약점
- 1030 lines 단일 파일 — `payment_views.py` 가독성 저하. 서비스 레이어로 분리 권장.
- `payment_transactions` (007) vs `payments` (112) 두 테이블이 공존. naming 통일 필요.
- subscription 상태 enum이 코드 곳곳에 흩어짐 (`active`/`trial`/`past_due`/`sleep`/`canceled`/`inactive`/`expired`/`locked`) — Enum 모듈로 통합해야.

### maesil-hub 이식 권고
**그대로 복사 후 다듬기**: `services/portone.py` 전체, `payment_views.py`의 핵심 핸들러 4개(billing-key/save, change, subscription/cancel, webhook). 단 `operator_id` → `biz_id`로 일괄 치환. 빌링 정책(VAT, 크레딧)은 그대로 유효.

---

## B. 슈퍼어드민 (Super Admin)

### 핵심 파일
- `blueprints/admin/` — 8개 view 파일 + decorators
  - `dashboard_views.py` (1353 lines) — KPI, MRR 실수령액 계산, 운영사 목록
  - `settings_views.py` (506 lines) — saas_config 키-값 CRUD (Fernet 암호화)
  - `plans_views.py` (108 lines) — plans 테이블 편집
  - `revenue_views.py` — 매출/결제 관리
  - `agency_views.py` — 광고대행사 관리
  - `partner_views.py` — 파트너 관리
  - `tts_views.py`, `maeyo_views.py` — 부가 기능
- `blueprints/admin/decorators.py` — `@require_superadmin` (단순: `current_user.site_role == 'superadmin'`)
- `migrations/189_admin_health_fast_counts.sql` — `get_admin_dashboard_summary` RPC
- `migrations/103_plans_table.sql` — plans + `change_operator_plan` RPC

### 권한 모델
- 일반 사용자/owner/manager/viewer는 `app_users.role`로 구분 (`models.py:142-158`)
- 슈퍼어드민은 `app_users.site_role = 'superadmin'` 컬럼으로 분리 (보안 P0: 하드코딩 이메일 제거됨)
- 슈퍼어드민의 **대리 보기 모드** (impersonation): `session['superadmin_as_operator_id']` 세팅 시 `before_request`에서 `current_user.operator_id`/`plan_type`을 해당 operator로 스왑 (`app.py:642-665`). 강력한 디버깅 도구.

### 화면/액션
- 대시보드: 신규가입/MRR(실수령+예상)/플랜 분포/결제 이력/AUTH_FAIL 위젯(채널 인증 실패한 운영사 일괄 식별, 자동 sticky 해제)
- 운영사 상세: 강제 정지(`is_active=false`), 플랜 변경 (`change_operator_plan` RPC), feature_overrides JSONB로 기능 단위 토글
- 결제: 환불 처리, 빌링키 강제 삭제
- saas_config: PortOne/Sentry/Render API/Anthropic/Resend 등 API 키 일괄 관리. 민감값은 `services/crypto.py`의 Fernet 암호화로 `value_secret` 컬럼에 저장.

### 강점
- saas_config로 **모든 외부 API 키를 DB 관리** → 환경변수 의존 최소화, 운영 중 재배포 없이 키 회전 가능.
- impersonation은 진짜 유용. 고객 지원 시 필수.
- AUTH_FAIL 위젯 같은 운영 친화 도구가 잘 정착됨.
- 어드민 RPC로 1000행 limit 회피 (`get_admin_operators_list(p_search, p_status_filter, p_limit, p_offset)`).

### 약점
- 인증이 `site_role` 단일 컬럼 — 2단계 인증(2FA) 없음.
- impersonation 감사 로그가 부족 (어떤 슈퍼관리자가 언제 누구를 봤는지 기록 강화 필요).

### maesil-hub 이식 권고
**구조 그대로 + RBAC 강화**: `admin/` 디렉터리 통째로 복사. `decorators.require_superadmin` + `@require_role(level)` 조합 유지. 신규: superadmin 행위 audit_logs에 항상 기록 강제. impersonation은 가져가되 IP/User-Agent + 만료시간(예: 30분) 추가.

---

## C. 회원사 관리 (Tenant / Multi-tenancy)

### 모델 (insight)
- 테넌트 키: `operators.id` (UUID). insight는 시작부터 멀티테넌트 — 모든 비즈니스 테이블에 `operator_id UUID NOT NULL REFERENCES operators(id)`.
- `migrations/001_initial_schema.sql:13-24` — operators 테이블 정의: `company_name`, `company_code`(UNIQUE), `email`, `plan_type`, `trial_ends_at`, `is_active`, `feature_overrides JSONB` (오버라이드용, 추후 추가).
- `app_users` ↔ `operators`: N:1 (한 operator에 여러 user, 한 user는 1 operator만). user 추가 합류 = company_code로 가입(`auth._register_join`).
- 권한: `app_users.role IN ('owner','manager','viewer')` (`models.ROLES`). owner만 결제/사용자관리 가능.

### 가입 흐름
```
신규: /register → operators(plan='free', trial 7d) + subscriptions(trial) + app_users(owner) + consent_logs ×2
합류: /register?type=join → company_code로 operator 검색 → app_users(role='viewer', is_approved=False) → owner 승인 대기
```

### 격리 메커니즘
- **RLS 정책** (`migrations/001`, `007`): `USING (operator_id = current_setting('app.operator_id')::uuid)`. 단 service_role 키로 접근하면 RLS bypass — insight는 service_role 키 사용(`app.py:192`)이라 RLS는 **2차 방어선**, 1차는 코드 레벨 `.eq('operator_id', g.operator_id)`.
- `before_request` 훅 (`app.py:634-665`): `g.operator_id = current_user.operator_id`, 슈퍼어드민 view-as 모드 처리.

### 약점
- 한 user가 여러 회사 소속 못함. 회계법인/대행사 관점에서 막힘. → 광고대행사는 **별도 테이블 `agencies`**로 우회 (`auth._try_agency_login`) — 깔끔하지 않음.
- biz/operator 한 인스턴스에 묶이는 데이터 백업/이관 도구 없음.

### maesil-hub 이식 권고
**구조는 그대로, biz_id 명명만 통일**: `operators` → `businesses`, `operator_id` → `biz_id BIGINT`. `app_users` ↔ `businesses` 사이에 **`user_business_map(user_id, biz_id, role)` join 테이블 추가** — 한 user가 여러 사업장 소속 지원. 첫 출시 때부터 넣어야 나중 마이그 고통 회피.

---

## D. 시스템 관리

### saas_config
- 단순 key-value 테이블, `value_text` + `value_secret`(Fernet 암호화) 분리.
- Sentry DSN, PortOne 키, Render API, Anthropic, Resend, **maesil_total Supabase 브릿지 키**까지 모두 여기.
- RPC `get_saas_config_all()`로 어드민 페이지 일괄 로드.

### plan_features 매핑
- 두 곳에 정의: 코드 `models.PLAN_FEATURES` (5개 플랜, 모든 feature 플래그) + DB `plans.features JSONB`.
- 런타임은 `services/plan_cache.get_plan(plan_type)` 우선, 실패 시 코드 fallback. 코드/DB 동기화 책임은 운영자.
- `feature_overrides`는 operator 단위 오버라이드 (특별 협상 시 사용).

### 도메인/서브도메인 라우팅
- 단일 도메인 `maesil-insight.com`. 게이트웨이 모드(`services/routing.py`)로 플랜별 다른 Render 서비스로 핸드오프 가능 — JWT 기반 SSO 토큰 (`services/auth_token.py:generate_handoff_token`). 현재는 단일 서버 폴백 사용.

### 백그라운드/스케줄러
- `services/scheduler.py` — APScheduler. 가입 후 `init_scheduler(app)` 호출 (`app.py:78`).
- `ROLE=worker` 환경변수로 web/worker 분리 가능.
- `naver_ad/worker.py`, `agency_ad/worker.py` — 광고 데이터 수집 별도 워커.
- `scheduler_leader` 키로 멀티 인스턴스 leader election.

### 모니터링
- Sentry: `_init_sentry(app)`가 saas_config에서 DSN 조회 후 init.
- 구조화 로깅: `services/logging_config.setup_logging(app)`.
- `audit_logs` 테이블에 LOGIN, change_plan 등 핵심 행위 기록.
- `api_sync_log`: 채널별 수집 성공/실패 + AUTH_FAIL 태그.

### 마이그레이션
- `migrations/001~212` 순번제. 213개 누적.
- `STATUS.md` 없음 (insight) — 운영자 기억 의존. **maesil-hub에서는 STATUS.md 강제**.
- 한글 SQL 리터럴이 인용 부호로 그대로 들어감 → 이전 maesil-total에서 클립보드 깨짐 사고 있었음. **insight도 동일 위험 존재**.

### maesil-hub 이식 권고
- saas_config 패턴 그대로 가져오기. 첫날부터 PortOne, Sentry, Anthropic 키 DB로.
- plan_features는 **DB만** SSOT, 코드 fallback 제거.
- 도메인은 우선 `hub.maesil.net` 단일 + `<biz>.hub.maesil.net` 옵션 유지.
- migrations에 `STATUS.md` 강제, **한글 리터럴 100% Unicode escape** (maesil-total 015 마이그 참조).

---

## E. 인증/세션

### 핵심 파일
- `auth.py` (926 lines) — 로그인/가입/합류/비번재설정/핸드오프
- `services/auth_token.py` — JWT 핸드오프 토큰 (게이트웨이 모드)
- `services/rate_limiter.py` — Redis 기반 IP rate limit (Redis 미설정 시 인메모리 폴백)
- `services/crypto.py` — Fernet 암호화 (saas_config secret 값)

### 메커니즘
- Flask-Login 사용. `User`(`models.py:197-279`)는 UserMixin.
- 세션 저장소: Redis 우선 (`flask-session`), 미설정 시 Flask 기본 cookie 세션 (`app.py:152-183`).
- 비밀번호: **bcrypt** (`bcrypt.checkpw/hashpw`, `auth.py:50-63`).
- IP rate limit: 15분/20회 → 차단 30분.
- 계정 잠금: 5회 실패 → 15분 (`LOGIN_MAX_ATTEMPTS`, `LOGIN_LOCKOUT_MINUTES`).
- 세션 비활동 타임아웃: 60분(insight) / 120분(total).
- user_loader 캐시: 60초 세션 캐시로 매 요청 DB 조회 방지 (`app.py:320-426`). Supabase 일시 장애 시 stale 캐시 5분 연장 — 강제 로그아웃 방지.

### SSO/OAuth
- **소셜 로그인 없음**. 이메일+패스워드만.
- 게이트웨이 핸드오프는 자체 JWT (HS256, SECRET_KEY 서명).
- 비번 재설정도 JWT 기반 1시간 만료 토큰 → Resend 이메일 발송.

### 강점
- bcrypt + IP/계정 이중 제한 + Redis rate limit + 감사 로그까지 완비. **PackFlow 패턴 잘 적용됨**.
- user_loader 세션 캐싱이 DB 부하 큰 폭 감소.
- find-account/reset-password 보안 — 이메일 존재 여부 노출 안 함.

### 약점
- 2FA 없음.
- session fixation 방어 weak (login 직후 session.regenerate 호출 안 함). 추가 권장.

### maesil-hub 이식 권고
**거의 그대로 복사**: `auth.py` + `models.User` + `services/rate_limiter.py` + `services/crypto.py`. 첫날부터 Redis 의존 (Render Redis 추가). 추가 작업: (1) 로그인 직후 session 재생성, (2) TOTP 2FA 옵트인 옵션, (3) `app_users` ↔ `user_business_map` 분리.
