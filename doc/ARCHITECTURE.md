# 매실 허브 아키텍처

## 멀티테넌시 모델

### 격리 단위: `biz_id`

모든 비즈니스 데이터는 `biz_id`로 격리. 한 가입 = 1 biz_id.

### 데이터 모델 원칙

1. **모든 비즈니스 테이블 `biz_id BIGINT NOT NULL`** 컬럼 필수
2. **모든 인덱스 `(biz_id, ...)`로 시작**
3. **모든 RLS 정책 `biz_id`로 필터**:
   ```sql
   CREATE POLICY tenant_isolation ON <table>
       USING (biz_id = current_setting('app.current_biz_id')::BIGINT);
   ```
4. **모든 RPC 첫 파라미터 `p_biz_id BIGINT`**
5. **app_users 테이블에 `biz_id` 연결**, 로그인 시 `g.biz_id` 세팅

### 공통 테이블 (biz_id 없음)

- `app_users` — 모든 사용자 (각 user는 1+ biz_id 소속 가능)
- `user_business_map` — user_id ↔ biz_id ↔ role
- `businesses` — 사업체 마스터 (= biz_id 원본)
- `plans` — 요금제 정의
- `subscriptions` — biz_id별 구독 상태
- `payment_history` — 결제 이력
- `audit_log` — 시스템 감사 로그

## 디렉터리 구조 (예정)

```
maesil-hub/
├── app.py                  # Flask app factory
├── config.py
├── auth/                   # 로그인/세션/SSO
├── billing/                # 결제·구독
├── plans/                  # 요금제 features
├── admin/                  # 운영자 대시보드
├── onboarding/             # 가입·시드
├── blueprints/             # 비즈니스 블루프린트
│   ├── inventory/          # 재고/수불장
│   ├── production/         # 생산관리
│   ├── shipment/           # 출고관리
│   ├── orders/             # 주문관리
│   ├── packing/            # 패킹센터
│   ├── shipping/           # 송장관리
│   ├── transfer/           # 창고이동
│   ├── partners/           # 거래처
│   ├── revenue/            # 매출
│   └── dashboard/          # 대시보드
├── db/                     # Repo 패턴
├── services/               # 비즈니스 로직
├── migrations/             # SQL 마이그레이션
├── templates/              # Jinja2
├── static/                 # JS/CSS
├── tests/                  # pytest
└── doc/                    # 문서
```

## 마이그레이션 규칙 (강제)

1. **순번**: `001_*.sql`, `002_*.sql`, ... 순차
2. **한글 리터럴**: 모두 `U&'\XXXX'` Unicode escape (예: `'정상'` = `U&'\C815\C0C1'`)
3. **주석**: 영문만 (한글 클립보드 깨짐 방지)
4. **함수**: `LANGUAGE sql STABLE SECURITY DEFINER SET statement_timeout = '...'` 명시
5. **GRANT**: `TO authenticated, service_role, anon` 명시
6. **RLS 정책**: 모든 비즈니스 테이블 필수
7. **DROP**: `DROP FUNCTION IF EXISTS` 항상 동반 (재배포 안전)
8. **STATUS.md**: `migrations/STATUS.md`에 배포 상태 기록

## RPC 설계 원칙

1. **모든 SELECT-and-aggregate는 RPC** (1000행 limit 회피)
2. **첫 파라미터 `p_biz_id`** (테넌트 격리)
3. **명시적 `statement_timeout`** (긴 쿼리 방어)
4. **JSONB 반환** 또는 `RETURNS TABLE(...)` 명시
5. **idempotent**: 재실행해도 결과 동일

## Python 코드 원칙

1. **`get_db()` 헬퍼**로만 client 접근, 직접 import 금지
2. **모든 DB 호출 RPC 우선 + 페이지네이션 fallback**
3. **`canonical()` 강제**: 모든 product_name 저장/매칭 시 공백 제거
4. **idempotency 토큰**: 모든 write API에 fingerprint cache
5. **에러 로깅**: Sentry + 콘솔 둘 다

## 배포 파이프라인

```
feature 브랜치 → PR → staging 자동배포 → 직원 검증 → main 머지 → production 자동배포
```

- main 직접 push 금지 (GitHub branch protection)
- staging에서 최소 24시간 가동 후 prod 머지
- 점검 알림 사전 공지

## 보안

- RLS 정책 100% 적용 (테이블 추가 시 강제 체크 스크립트)
- 비밀번호 bcrypt
- 세션 HttpOnly + Secure + SameSite=Strict
- CSRF 토큰
- API rate limiting (테넌트당)
- Supabase Service Key 절대 클라이언트 노출 금지
