# Phase 0 체크리스트

목표: **maesil-hub 인프라 가동 + 기본 데이터 모델 + Render 첫 deploy 성공**

소요 예상: 1~2일 (집중 시 4~6시간)

## 진행 상태 추적

```
[ ] 미완료    [/] 진행중    [x] 완료
```

## A. 레포 골격 (완료)

- [x] (5분) GitHub maesil-hub 레포 생성
- [x] (10분) 로컬 clone + 첫 commit
- [x] (15분) README.md
- [x] (20분) doc/PROJECT_PLAN.md
- [x] (15분) doc/ARCHITECTURE.md
- [x] (60분) doc/INSIGHT_SAAS_ANALYSIS.md
- [x] (60분) doc/TOTAL_ERP_ANALYSIS.md
- [x] (90분) doc/HUB_DESIGN.md
- [x] (60분) doc/DATA_MODEL.md
- [x] (45분) doc/DEPLOYMENT.md
- [x] (45분) doc/AUTH_AND_TENANCY.md
- [x] (15분) doc/PHASE0_CHECKLIST.md (이 문서)
- [x] (10분) requirements.txt + Procfile + runtime.txt
- [x] (10분) app.py 최소 골격 (/ 랜딩 + /health)
- [x] (5분) .env.example, .gitignore

## B. Render 셋업

- [/] (10분) Render Web Service 신규 생성 (사용자 진행 중)
  - Name: maesil-hub-staging (먼저)
  - Branch: main (또는 staging 별도 브랜치 생성 후 staging)
  - Region: Singapore
  - Instance: Free
- [ ] (5분) /health 200 OK 확인 (배포 후)
- [ ] (10분) 환경변수 설정 (Render Dashboard)
  - SECRET_KEY, APP_ENV
  - SUPABASE_URL, SUPABASE_KEY (Supabase 셋업 후)
- [ ] (10분) Production Web Service 생성 (Starter $7/mo)
- [ ] (5분) staging 브랜치 생성 → staging 자동배포 분리

## C. Supabase 셋업

- [ ] (10분) Supabase **maesil-hub-staging** 프로젝트 생성
  - Region: ap-northeast-2 (Seoul)
  - DB Password 강력하게 + 1Password 저장
- [ ] (5분) anon/service_role key 복사 → Render env
- [ ] (5분) DATABASE_URL (pooler) 복사 → Render env
- [ ] (10분) Supabase **maesil-hub-prod** 프로젝트 생성
- [ ] (5분) Storage 버킷 생성 (attachments, imports, backups)

## D. 마이그레이션 — 코어 스키마

- [ ] (60분) `migrations/001_core_schema.sql` 작성
  - businesses, app_users, user_business_map
  - plans, subscriptions, payments
  - saas_config, audit_logs
  - 한글 0개, U&'\XXXX' escape
- [ ] (10분) staging Supabase에 001 실행 (`run_sql.py`)
- [ ] (10분) `migrations/002_rls_policies.sql` 작성
  - service_role 모든 권한
  - authenticated tenant_isolation
- [ ] (5분) staging에 002 실행
- [ ] (15분) `migrations/005_seed_plans.sql` — Starter/Pro/Enterprise 시드
- [ ] (10분) `migrations/006_seed_admin.sql` — 슈퍼어드민 계정 1개

## E. 인증 골격

- [ ] (30분) `auth/__init__.py` — Flask-Login User 모델
- [ ] (30분) `auth/views.py` — /login, /logout, /signup
- [ ] (20분) `templates/auth/login.html`
- [ ] (20분) `templates/auth/signup.html`
- [ ] (30분) bcrypt 비밀번호 해싱 + 검증
- [ ] (15분) before_request 훅 (g.biz_id 세팅)
- [ ] (15분) `@login_required`, `@biz_required` 데코레이터
- [ ] (10분) /dashboard 임시 페이지 (로그인 후 진입)

## F. /health 엔드포인트 강화

- [ ] (10분) DB 연결 체크 (Supabase ping)
- [ ] (5분) 마이그레이션 버전 표시 (migrations/STATUS.md 읽기)
- [ ] (5분) 응답 예시:
  ```json
  {
    "status": "ok",
    "service": "maesil-hub",
    "env": "staging",
    "db": "ok",
    "migrations": "001,002,005,006",
    "time": "..."
  }
  ```

## G. CI/CD 기본

- [ ] (20분) `.github/workflows/ci.yml`
  - pytest 자동 실행
  - 한글 SQL 리터럴 검출
  - biz_id 누락 검출 (신규 CREATE TABLE)
- [ ] (10분) GitHub branch protection — main 직접 push 금지, PR + 1 review

## H. 도메인 연결

- [ ] (10분) Cloudflare DNS — staging.hub.maesil.net → Render staging
- [ ] (10분) Render Custom Domain 등록 + SSL 자동 발급
- [ ] (5분) 브라우저에서 https://staging.hub.maesil.net/health 200 확인
- [ ] (deferred) prod 도메인 hub.maesil.net 은 Phase 1 끝나고

## I. 매실에이전시 연동

- [ ] (15분) 에이전시에 hub 헬스체크 등록 (1분 ping)
- [ ] (10분) Sentry 프로젝트 생성 + DSN 발급 → Render env
- [ ] (5분) Sentry 알람 룰 (5분간 5개+ 에러 → 슬랙)

## Phase 0 완료 기준 (Definition of Done)

다음 모두 만족 시 Phase 0 종료:

- [x] GitHub repo 존재 + 초기 commit + 푸시
- [ ] Render web service 가동 중 (staging) + /health 200
- [ ] Supabase staging 프로젝트 생성 + 코어 스키마 8개 테이블 + RLS 적용
- [ ] 슈퍼어드민 계정으로 로그인 → /admin 진입 가능
- [ ] 일반 사용자 회원가입 → 회사 생성 → 로그인 → /dashboard 진입 가능
- [ ] 매실에이전시 모니터링 등록 + 5분 안정 확인
- [ ] PROJECT_PLAN.md Phase 0 모든 항목 [x]

## Phase 1 진입 트리거

Phase 0 DoD 모두 만족 → 다음 작업으로:

- 결제 (PortOne) 이식
- 슈퍼어드민 화면 (회원사 관리)
- 온보딩 플로우
- saas_config Fernet 암호화

## 위험 관리

| 위험 | 영향 | 완화책 |
|---|---|---|
| Supabase 신규 프로젝트 한글 데이터 깨짐 | 높음 | 모든 SQL ASCII only + run_sql.py 검증 |
| Render free plan cold start | 중간 | staging만 free, prod는 Starter |
| 매실에이전시 미연결 시 사고 인지 늦음 | 중간 | Phase 0 DoD에 포함 강제 |
| maesil-total과 코드 분기 후 sync 부담 | 낮음 | hub는 새로 작성, total은 freeze |

## 작업 명령 모음 (Phase 0)

```bash
# 1. 코어 마이그레이션
cd C:\maesil-hub
python scripts/run_sql.py migrations/001_core_schema.sql --env staging
python scripts/run_sql.py migrations/002_rls_policies.sql --env staging
python scripts/run_sql.py migrations/005_seed_plans.sql --env staging
python scripts/run_sql.py migrations/006_seed_admin.sql --env staging

# 2. 로컬 실행 테스트
python app.py  # http://localhost:5000

# 3. 헬스체크
curl https://maesil-hub-staging.onrender.com/health

# 4. 슈퍼어드민 첫 로그인
# 시드 계정: admin@maesil.net / 임시비번 (마이그 006 참조)
```
