# maesil-hub 인증 & 멀티테넌시

## 인증 흐름

### 1. 회원가입
```
POST /signup
  → email + password + 사업자정보(name, biz_reg_no, industry)
  → app_users INSERT (이메일 검증 토큰 발송)
  → businesses INSERT (status=pending)
  → user_business_map INSERT (role=owner, is_primary=TRUE)
  → 이메일 인증 링크 클릭 시 status=active
```

### 2. 이메일 인증
```
GET /verify?token=<jwt>
  → app_users.email_verified = TRUE
  → 자동 로그인 → /onboarding 으로 리다이렉트
```

### 3. 온보딩 (첫 가입)
```
/onboarding
  Step 1: 회사 정보 확인/수정
  Step 2: 요금제 선택 (Starter/Pro/Enterprise)
  Step 3: 결제 (PortOne 빌링키 등록 + 첫 결제)
  Step 4: 기본 데이터 시드 (예시 상품, 창고 등) — skip 가능
  완료 → /dashboard
```

### 4. 로그인
```
POST /login
  → bcrypt verify
  → 세션 생성 (Flask-Login)
  → user_business_map 조회 → 회사 1개면 자동 진입, 여러 개면 회사 선택 화면
  → g.biz_id 세팅
```

### 5. 비밀번호 정책
- bcrypt cost=12
- 최소 길이 10자, 영문+숫자+특수문자
- 변경 시 직전 3개 비밀번호 재사용 금지 (옵션)
- 5회 실패 시 5분 lockout

## 세션

```python
app.config.update(
    SECRET_KEY=<random>,
    SESSION_COOKIE_SECURE=True,        # HTTPS only
    SESSION_COOKIE_HTTPONLY=True,      # JS 접근 불가
    SESSION_COOKIE_SAMESITE='Strict',  # CSRF 1차 방어
    PERMANENT_SESSION_LIFETIME=86400,  # 24시간
)
```

Flask-Login의 `remember_me`는 OFF (보안 우선).

## 멀티테넌시 모델

### businesses ↔ app_users 관계
```
한 사용자(app_users) ↔ 여러 회사(businesses)  : 다대다
- user_business_map 테이블이 매핑
- role: owner / manager / staff / viewer
- is_primary: 기본 회사 (로그인 시 자동 진입)
```

### 화면 우상단 회사 전환 UI
```
[현재 회사: 배마마 ▼]
  ├─ 배마마 (owner)         ← 현재
  ├─ 해미예찬 (manager)
  └─ + 새 회사 추가
```

전환 시:
- 세션의 `current_biz_id` 업데이트
- /dashboard 리다이렉트
- audit_log INSERT

### g.biz_id 세팅 (before_request)

```python
from flask import g
from flask_login import current_user

@app.before_request
def set_tenant_context():
    g.biz_id = None
    if current_user.is_authenticated:
        g.biz_id = session.get('current_biz_id')
        if not g.biz_id:
            # 세션에 없으면 primary 회사 자동 설정
            primary = db.client.table('user_business_map') \
                .select('biz_id') \
                .eq('user_id', current_user.id) \
                .eq('is_primary', True) \
                .single().execute().data
            if primary:
                g.biz_id = primary['biz_id']
                session['current_biz_id'] = g.biz_id

        # Supabase RLS 컨텍스트 (선택)
        if g.biz_id:
            try:
                db.client.rpc('set_app_setting', {
                    'p_key': 'app.current_biz_id',
                    'p_value': str(g.biz_id),
                }).execute()
            except Exception:
                pass
```

### 권한 체크 데코레이터

```python
def biz_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not g.biz_id:
            return redirect(url_for('auth.select_business'))
        return f(*args, **kwargs)
    return wrapper

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('auth.login'))
            if current_user.is_super_admin:
                return f(*args, **kwargs)  # super admin bypass
            user_role = get_user_role(current_user.id, g.biz_id)
            if user_role not in roles:
                abort(403)
            return f(*args, **kwargs)
        return wrapper
    return decorator
```

## 슈퍼어드민 (Super Admin)

### 분리 원칙
- 일반 사용자와 동일 테이블 (`app_users.is_super_admin = TRUE`)
- 별도 로그인 페이지 없음 (일반 로그인 후 어드민 화면 자동 표시)
- 어드민 라우트는 `/admin/*` prefix

### Impersonation (회원사 화면 들어가기)
```python
@admin_bp.route('/impersonate/<int:biz_id>')
@super_admin_required
def impersonate(biz_id):
    # audit_log
    db.client.table('audit_logs').insert({
        'user_id': current_user.id,
        'biz_id': biz_id,
        'operator_id': current_user.id,  # 본인이 본인을 위장 (구분 불필요)
        'action': 'impersonate_start',
        'detail': {'target_biz_id': biz_id},
    }).execute()

    # 세션에 impersonate 플래그
    session['impersonating_biz_id'] = biz_id
    session['original_user_id'] = current_user.id  # 복귀용

    return redirect(url_for('main.dashboard'))


@app.before_request
def apply_impersonation():
    if session.get('impersonating_biz_id'):
        g.biz_id = session['impersonating_biz_id']
        g.is_impersonating = True
```

### 어드민 화면

| 라우트 | 기능 |
|---|---|
| `/admin` | 대시보드 (테넌트 수, 결제 현황, 시스템 메트릭) |
| `/admin/businesses` | 회원사 목록 / 검색 / 정지 / 플랜 변경 |
| `/admin/businesses/<id>` | 상세 (사용자, 구독, 결제, 사용량) |
| `/admin/payments` | 결제 이력 / 환불 처리 |
| `/admin/audit-logs` | 감사 로그 (impersonate 포함) |
| `/admin/saas-config` | 시스템 설정 (API 키 등) |
| `/admin/health` | 시스템 메트릭 (실제 모니터링은 매실에이전시) |

## Plan 게이팅

### plans.features JSONB 구조
```json
{
  "channels": 3,           // 사용 가능 채널 수
  "users": 5,              // 사용자 수
  "ai_diagnose": false,    // AI 진단 기능
  "advanced_reports": true,
  "api_access": false,
  "storage_gb": 5,
  "support_priority": "email"  // email/chat/phone
}
```

### 데코레이터
```python
def plan_required(feature):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            sub = get_subscription(g.biz_id)
            if not sub:
                abort(402, '구독이 필요합니다')
            features = sub.plan.features
            if not features.get(feature):
                return jsonify({
                    'error': f'{feature} 기능은 현재 플랜에서 사용 불가',
                    'current_plan': sub.plan.code,
                    'upgrade_url': url_for('billing.upgrade'),
                }), 402
            return f(*args, **kwargs)
        return wrapper
    return decorator

# 사용 예
@bp.route('/diagnose')
@plan_required('ai_diagnose')
def diagnose():
    ...
```

### 사용량 제한 (예: 채널 수)
```python
def check_channel_limit():
    sub = get_subscription(g.biz_id)
    max_channels = sub.plan.features.get('channels', 1)
    current = db.client.table('order_transactions') \
        .select('channel', count='exact') \
        .eq('biz_id', g.biz_id) \
        .execute()
    distinct_channels = len(set(r['channel'] for r in current.data))
    if distinct_channels >= max_channels:
        raise PlanLimitExceeded(f'최대 {max_channels}개 채널까지 가능')
```

## 권한 매트릭스

| 액션 | owner | manager | staff | viewer |
|---|:-:|:-:|:-:|:-:|
| 대시보드 보기 | ✅ | ✅ | ✅ | ✅ |
| 재고 조회 | ✅ | ✅ | ✅ | ✅ |
| 입출고 등록 | ✅ | ✅ | ✅ | ❌ |
| 생산 등록 | ✅ | ✅ | ✅ | ❌ |
| 주문 처리 | ✅ | ✅ | ✅ | ❌ |
| 거래처 관리 | ✅ | ✅ | ❌ | ❌ |
| 매출 분석 | ✅ | ✅ | ❌ | ❌ |
| 사용자 관리 | ✅ | ✅ | ❌ | ❌ |
| 구독/결제 변경 | ✅ | ❌ | ❌ | ❌ |
| 회사 정보 변경 | ✅ | ❌ | ❌ | ❌ |
| 회사 삭제 | ✅ | ❌ | ❌ | ❌ |

## 보안 체크리스트

- [ ] 모든 라우트에 `@login_required` 또는 명시적 public 표시
- [ ] 모든 비즈니스 라우트에 `@biz_required`
- [ ] 모든 쓰기 라우트에 `@role_required(...)`
- [ ] 모든 RPC 호출에 g.biz_id를 p_biz_id로 명시 전달
- [ ] CSRF 토큰 모든 POST/PUT/DELETE 폼
- [ ] SQL Injection: parameterized query / Supabase client 사용 (raw SQL 금지)
- [ ] XSS: Jinja2 자동 이스케이프 유지 (|safe 사용 시 검증)
- [ ] 비밀번호 평문 로그 금지
- [ ] PortOne secret 클라이언트 노출 금지
- [ ] Impersonate 시 모든 액션 audit_log
