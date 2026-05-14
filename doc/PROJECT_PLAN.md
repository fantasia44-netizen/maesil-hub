# 매실 허브 프로젝트 플랜

## 비전
식품·축산업체가 ERP/WMS를 SaaS로 즉시 사용. 가입 → 결제 → 10분 안에 첫 입고 등록 가능.

## Phase 0 — 셋업 (목표: 이번주)

- [ ] GitHub `maesil-hub` 레포 생성 (완료)
- [ ] 로컬 폴더 + README + 플랜 (완료)
- [ ] Supabase 프로젝트 신규 (`maesil-hub-prod`, `maesil-hub-staging`)
- [ ] Render 서비스 신규 (hub, hub-staging)
- [ ] 도메인 매핑 (`hub.maesil.net`, `staging.hub.maesil.net`)
- [ ] GitHub branch protection: main → PR 필수, staging 자동배포
- [ ] 기본 Flask 골격 (app.py, config.py, .env.example)
- [ ] biz_id 데이터 모델 설계 문서

## Phase 1 — SaaS 인프라 이식 (1주차)

`maesil-insight`에서 가져올 모듈:

- [ ] **auth**: 회원가입/로그인/SSO/세션
- [ ] **billing**: KakaoPay 결제, 구독 상태 관리
- [ ] **plans**: 요금제 features 매핑 (Starter/Pro/Enterprise)
- [ ] **admin**: 운영자 대시보드 (테넌트 관리, 사용량 모니터링)
- [ ] **onboarding**: 가입 → 자동 biz_id 생성 → 기본 시드 데이터
- [ ] **RLS 정책**: 모든 테이블 biz_id 기반 격리
- [ ] **plan_features 게이트**: `@plan_required('feature_name')` 데코레이터

**전략**: 우선 직접 복사 → 안정화 후 `maesil-saas-core` 공용 라이브러리로 분리 검토.

## Phase 2 — ERP 핵심 이식 (2-3주차)

`maesil-total`에서 가져올 모듈 (멀티테넌트 변환하면서):

- [ ] **재고**: stock_ledger, materials (반제품/원료/부자재)
- [ ] **생산**: production batch (idempotency 포함), 이력번호
- [ ] **출고**: shipment (012/014/016 RPC 통합 조회)
- [ ] **주문**: orders, packing, 다채널 통합
- [ ] **송장**: shipping (CJ API 연동)
- [ ] **거래처**: 거래처 관리, 매입/매출
- [ ] **수불장**: ledger
- [ ] **통계**: 대시보드, 매출 분석
- [ ] **모든 RPC**: 한글 Unicode escape, biz_id 필수 파라미터

**리팩토링 원칙**:
- 모든 테이블 `biz_id` 컬럼 필수
- 모든 쿼리 RLS 또는 `.eq('biz_id', g.biz_id)` 강제
- 모든 RPC 첫 파라미터 `p_biz_id BIGINT NOT NULL`
- 한글 SQL 리터럴 `U&'\XXXX'` 100%
- 페이지네이션 또는 RPC 강제 (직접 select limit 1000 금지)

## Phase 3 — 첫 외부 고객 온보딩 (1개월)

- [ ] 가입 플로우: 사업자 정보 → 결제 → biz_id 생성 → 시드
- [ ] 플랜 기능 게이팅 (Starter는 채널 1개, Pro는 5개 등)
- [ ] 도메인: `<biz>.hub.maesil.net` 또는 `hub.maesil.net/<biz>`
- [ ] 고객 지원 채널 (카카오 채널톡 또는 채널.io)
- [ ] 결제 실패 시 자동 정지/복구
- [ ] 사용량 통계 (API 호출, 저장 용량)

## Phase 4 — 운영 안정화 (2개월+)

- [ ] 백업·복원 자동화 (Supabase Point-in-Time Recovery)
- [ ] 알람 시스템 (Sentry + 이메일 + 슬랙)
- [ ] 보안 점검 (CSRF, XSS, SQL Injection 자동 스캔)
- [ ] 부하 테스트 (테넌트 10/50/100명 시뮬레이션)
- [ ] 가격 정책 fine-tuning
- [ ] 마케팅 페이지 (maesil.net 연계)

## 기술 부채 / 보류 항목

- `maesil-total` → 배마마 freeze 유지, 긴급 버그만 hotfix
- 배마마 데이터 hub 이관: hub 안정 후 6개월~1년
- `maesil-saas-core` 라이브러리화: Phase 4 이후 검토
- monorepo 전환: 미루기

## 운영 원칙 (강제)

1. **main 브랜치 직접 push 금지** — PR + 1 review + staging 검증 필수
2. **모든 마이그레이션** — 한글 리터럴 0개, `run_sql.py`로 검증
3. **데이터 손실 방지** — 모든 destructive SQL은 dry-run 먼저
4. **테스트 우선** — 신규 기능은 하네스 테스트 동반
5. **사용자 통지** — push 전 직원 사용 중인지 확인, 점검 시간 사전 공지

## 참고 문서

- `maesil-total` 운영 사고 학습 (2026-05-13~14):
  - Supabase 1000행 limit 회피 패턴 (RPC + 페이지네이션)
  - 한글 SQL 리터럴 클립보드 깨짐 → Unicode escape
  - 생산 batch 더블클릭 → idempotency fingerprint cache
  - 페이지네이션 누락 → materials 잔량 부풀림 (60kg 사고)
- `maesil-insight` SaaS 인프라 구조
