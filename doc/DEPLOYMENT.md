# maesil-hub 배포 가이드

## 인프라 개요

```
┌──────────────────────────────────────────────────────────┐
│  GitHub: maesil-hub                                      │
│  ├─ main 브랜치   → Render prod 자동배포                 │
│  └─ staging 브랜치 → Render staging 자동배포             │
└──────────────────────────────────────────────────────────┘
                           ↓
┌──────────────────────────────────────────────────────────┐
│  Render                                                  │
│  ├─ Web Service (gunicorn) × 2환경                      │
│  └─ Cron Job × N (주문수집/정산/송장)                    │
└──────────────────────────────────────────────────────────┘
                           ↓
┌──────────────────────────────────────────────────────────┐
│  Supabase                                                │
│  ├─ maesil-hub-prod    (운영)                            │
│  └─ maesil-hub-staging (검증)                            │
└──────────────────────────────────────────────────────────┘

외부:
  Cloudflare DNS  → hub.maesil.net, staging.hub.maesil.net
  매실에이전시    → uptime/Sentry/비즈니스 검증
```

## Render — Web Service

### Production
- **Name**: `maesil-hub`
- **Region**: Singapore (Southeast Asia)
- **Branch**: `main`
- **Language**: Python 3
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `gunicorn app:app --workers 2 --bind 0.0.0.0:$PORT --timeout 120`
- **Instance Type**: Starter ($7/mo, always-on)
- **Health Check Path**: `/health`

### Staging
- **Name**: `maesil-hub-staging`
- **Region**: Singapore
- **Branch**: `staging`
- **Build/Start**: prod와 동일
- **Instance Type**: Free (15분 idle 시 sleep, OK)
- **Health Check Path**: `/health`

### 환경변수 (Render Dashboard → Environment)

```
APP_ENV=production              # staging/production
SECRET_KEY=<random-64-byte-hex>
SUPABASE_URL=https://<ref>.supabase.co
SUPABASE_KEY=<anon-key>
SUPABASE_SERVICE_KEY=<service-role-key>
DATABASE_URL=postgresql://postgres.<ref>:<pw>@aws-0-ap-northeast-2.pooler.supabase.com:5432/postgres
SAAS_MODE=multi
SENTRY_DSN=<sentry dsn from agency>
PORTONE_API_KEY=...
PORTONE_API_SECRET=...
PORTONE_STORE_ID=...
KAKAOPAY_CID=...
CJ_CUST_ID=...
NAVER_COMMERCE_CLIENT_ID=...
NAVER_COMMERCE_CLIENT_SECRET=...
COUPANG_VENDOR_ID=...
```

→ 민감 키는 `saas_config` 테이블에 Fernet 암호화 저장 권장 (env에는 FERNET_KEY만 두고 런타임 복호화).

## Render — Cron Job

### 주문 수집 cron 예제 (Coupang 5분)

```yaml
Name: hub-cron-coupang-orders
Schedule: */5 * * * *
Command: python -m scripts.cron_collect_orders --channel coupang
```

### Cron 작업 목록

| Cron 명 | 스케줄 | 명령 | 비고 |
|---|---|---|---|
| `hub-cron-coupang` | `*/5 * * * *` | `python -m scripts.cron_collect_orders --channel coupang` | 5분 |
| `hub-cron-naver` | `*/10 * * * *` | `python -m scripts.cron_collect_orders --channel naver` | 10분 |
| `hub-cron-cj-tracking` | `0 * * * *` | `python -m scripts.cron_cj_tracking` | 1시간 |
| `hub-cron-daily-settle` | `0 2 * * *` | `python -m scripts.cron_daily_settlement` | 매일 02:00 |
| `hub-cron-weekly-backup` | `0 3 * * 0` | `python -m scripts.cron_weekly_backup` | 일요 03:00 |

각 cron은 **테넌트 전수 순회** — biz_id별 실행하되 실패해도 다른 테넌트 영향 없게:

```python
# scripts/cron_collect_orders.py
def main():
    db = get_db()
    bizs = db.client.table('businesses').select('id').eq('status', 'active').execute().data
    for biz in bizs:
        try:
            collect_for_biz(biz['id'])
        except Exception as e:
            sentry.capture_exception(e)
            continue
```

## Supabase — 신규 프로젝트 생성

### 단계
1. https://supabase.com/dashboard → **New Project**
2. **Name**: `maesil-hub-staging` (먼저)
3. **Database Password**: 강력한 패스워드 (1Password 등에 저장)
4. **Region**: `ap-northeast-2 (Seoul)` 또는 `ap-southeast-1 (Singapore)`
5. **Pricing Plan**: Free (개발) / Pro (운영, $25/mo)
6. 생성 완료 (5~10분)

### 생성 후 즉시 작업
1. **Settings → API**: anon key, service_role key 복사 → `.env`
2. **Settings → Database → Connection string (URI)**: pooler URL 복사 → `DATABASE_URL`
3. **Settings → Auth**: 이메일 인증 OFF (자체 인증 사용)
4. **SQL Editor → 마이그 001 실행**: `migrations/001_core_schema.sql`
5. **SQL Editor → 마이그 002 실행**: `migrations/002_rls_policies.sql`

### Storage 버킷
- `attachments` — 거래명세서 PDF, 송장 라벨 등
- `imports` — 업로드한 엑셀 파일 백업
- `backups` — 주별 DB 덤프

각 버킷 RLS 정책으로 biz_id 폴더 격리:
```sql
CREATE POLICY "biz isolation" ON storage.objects
    USING (biz_id::TEXT = (storage.foldername(name))[1]);
```

## Cloudflare DNS

### 레코드
| 호스트 | Type | Target | Proxy |
|---|---|---|---|
| hub | CNAME | `maesil-hub.onrender.com` | ON (orange cloud) |
| staging.hub | CNAME | `maesil-hub-staging.onrender.com` | ON |

### SSL/TLS
- **Encryption mode**: Full (strict)
- Render이 자체 SSL 발급, Cloudflare가 edge에서 한 번 더

### Render 측 Custom Domain 등록
1. Render Dashboard → Service → Settings → Custom Domain
2. `hub.maesil.net` 추가 → CNAME 검증 자동 통과 (Cloudflare에서 등록했으면)

## 매실에이전시 연동

### /health 엔드포인트 명세
```
GET /health
Response 200:
{
  "status": "ok",
  "service": "maesil-hub",
  "env": "production",
  "time": "2026-05-14T15:30:00+00:00"
}
```

### 매실에이전시에 등록할 모니터
1. **Uptime Monitor**: GET https://hub.maesil.net/health, 1분 주기
2. **Sentry Project**: maesil-hub (별도 DSN 발급, env에 SENTRY_DSN 설정)
3. **DB 검증 SQL** (에이전시가 read-only 키로 호출):
   - 음수 재고 검출
   - status='정상' 미출고 24시간+ 적체
   - 결제 실패 후 24시간+ retry 안 된 건
4. **알람 채널**: 슬랙 #maesil-ops

## 배포 절차

### 신규 기능 (PR → staging → main)

1. 로컬에서 feature 브랜치 작업
2. PR 생성 → CI 자동 실행 (pytest + biz_id grep + 한글 SQL 검출)
3. staging 브랜치에 머지 → Render staging 자동배포
4. staging.hub.maesil.net 에서 직원 검증 (최소 24시간)
5. main에 PR 머지 → Render prod 자동배포
6. 매실에이전시 모니터 5분간 주시

### Hotfix
1. main에서 hotfix 브랜치
2. 수정 + PR + 1 review
3. main 머지 → 즉시 prod 배포
4. 직후 staging 브랜치도 머지 (sync)

### DB 마이그레이션
1. `migrations/0XX_*.sql` 작성 (한글 0개)
2. **staging Supabase에 먼저 실행** (`python scripts/run_sql.py migrations/0XX_*.sql --env staging`)
3. staging 검증
4. **prod Supabase에 실행** (사용자 사용 시간 피해서, 점검 공지 후)
5. `migrations/STATUS.md`에 배포 기록

## 롤백 절차

### Render 롤백
1. Render Dashboard → Service → **Deploys 탭**
2. 이전 정상 deploy 선택 → **Rollback to this deploy**
3. 1~2분 내 이전 코드로 복귀

### DB 롤백
- 마이그레이션은 가능한 **idempotent + reversible**하게 작성
- `DROP FUNCTION IF EXISTS` 패턴
- 데이터 변경 마이그는 백업 후 진행

### 긴급 정지
1. Render → Service → **Suspend** (즉시 중단)
2. 또는 Cloudflare에서 페이지 룰로 maintenance 모드

## 비용 추정 (월)

| 항목 | Free Tier | Production |
|---|---|---|
| Render Web (staging) | $0 | - |
| Render Web (prod) | - | $7 |
| Render Cron × 5 | $1~5 | $1~5 |
| Supabase staging | $0 | - |
| Supabase prod | - | $25 (Pro) |
| Cloudflare | $0 | $0 |
| Sentry | $0 (5k events) | $26+ (필요 시) |
| **합계** | $1~5 | **$33~63** |

매실에이전시 자체 운영비는 별도.

## CI/CD (GitHub Actions, Phase 1)

`.github/workflows/ci.yml`:
```yaml
name: CI
on: [pull_request]
jobs:
  lint-and-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -r requirements.txt
      - run: pip install pytest
      - name: 한글 SQL 리터럴 검출
        run: |
          for f in migrations/*.sql; do
            kor=$(python -c "import sys; s=open('$f',encoding='utf-8').read(); print(sum(1 for c in s if '가'<=c<='힣'))")
            if [ "$kor" -gt 0 ]; then
              echo "FAIL: $f has $kor Korean chars"; exit 1
            fi
          done
      - name: biz_id 누락 검출
        run: |
          # 새 CREATE TABLE에 biz_id 없으면 fail (공통 테이블 화이트리스트 제외)
          ...
      - run: pytest tests/
```

## 보안 점검

- [ ] SECRET_KEY는 환경마다 다름
- [ ] SUPABASE_SERVICE_KEY는 서버 환경변수에만, 클라이언트 노출 절대 금지
- [ ] DATABASE_URL 비밀번호 정기 회전 (분기별)
- [ ] PortOne webhook은 HMAC 서명 검증 필수
- [ ] CSRF 토큰 모든 POST에
- [ ] Session cookie Secure + HttpOnly + SameSite
