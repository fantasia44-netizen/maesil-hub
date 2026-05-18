# maesil-hub 아키텍처 설계서

> 최종 업데이트: 2026-05-18  
> 작성 기준: `migrations/STATUS.md` 010번 적용 완료 시점

---

## 1. 매실 서비스 전체 지도

```
┌─────────────────────────────────────────────────────────────────┐
│                      매실 서비스 에코시스템                        │
├──────────────┬──────────────┬──────────────┬────────────────────┤
│  maesil-hub  │maesil-insight│ maesil-flow  │  maesil-packing    │
│  (이 레포)   │  (분석 전용) │ (현장 출고)  │  (추후 분리 예정)  │
├──────────────┼──────────────┼──────────────┼────────────────────┤
│ • 주문 관리  │ • 매출 분석  │ • 출고 현장  │ • 영상 녹화 패킹   │
│ • 재고 관리  │ • 광고 ROAS  │ • 모바일 UI  │ • 바코드 스캔      │
│ • 마켓 API   │ • 경쟁사 분석│ • 3PL 연동   │ • 현장 품질 관리   │
│ • 정산/회계  │ • P&L 리포트 │ • 배송 추적  │ (영상 = S3 분리)  │
│ • 팀 관리    │              │              │                    │
│ • 멀티테넌트 │  hub_bridge  │  hub API 연결│                    │
│              │  로 읽기 전용│  (미구현)    │                    │
└──────────────┴──────────────┴──────────────┴────────────────────┘
       ↑                ↑
  Supabase DB      Supabase DB
  (hub 전용)      (insight 전용)
       ↑
  마켓플레이스 API
  (쿠팡/네이버/카카오 등)
```

**역할 분리 원칙**
- **허브**: 데이터 수집·저장·처리의 SSOT (Single Source of Truth)
- **인사이트**: 허브에서 읽기 전용 → 분석/광고 최적화 (쓰기 없음)
- **플로워**: 현장 출고 실행 → 허브 API로 재고 차감 (미래)
- **패킹센터**: 영상+바코드 → 고용량이라 별도 서버/S3 필요 (미래)

---

## 2. maesil-hub 내부 구조

### 2-1. 레이어 구성

```
Request
  │
  ▼
Flask App (app.py)
  │
  ├── before_request
  │     ├── biz_id 세팅 (session → g.biz_id)
  │     ├── biz_name 캐시 조회 (5분 TTL)
  │     └── MarketplaceManager per-request 생성
  │
  ├── Blueprint (45개)
  │     └── blueprints/__init__.py 자동 스캔 (*_bp)
  │
  ├── Service Layer (services/*.py)
  │     └── 비즈니스 로직 (stateless)
  │
  ├── DB Layer
  │     ├── db_supabase.py  (SupabaseDB, 메인 monolithic)
  │     │     └── tenant_guard 패치 (biz_id 자동 주입)
  │     └── db/*.py  (도메인별 Repo, 명시적 biz_id)
  │
  └── Supabase (PostgreSQL + Auth + Storage)
```

### 2-2. 멀티테넌트 격리 메커니즘

```python
# 방법 A: tenant_guard (SupabaseDB 메서드 패치)
# app.py 시작시 1회 install_tenant_guard()
# biz_id 파라미터가 있는 메서드 → g.biz_id 자동 주입

# 방법 B: BaseRepo 명시적 biz_id (db/*.py)
class MarketplaceRepo(BaseRepo):
    def query_marketplace_api_configs(self, channel=None, biz_id=None):
        q = self.client.table("marketplace_api_config").select("*")
        if biz_id is not None:
            q = q.eq("biz_id", biz_id)  # ← 명시적
```

**핵심 원칙**: service_role key 사용 → RLS 우회 → 앱 레벨 WHERE biz_id 강제

---

## 3. Blueprint 구성 (45개)

### 3-1. 주문·출고 도메인
| Blueprint | URL | 주요 기능 |
|-----------|-----|---------|
| `orders` | `/orders` | 주문서 업로드 (엑셀→매칭→DB) |
| `orders_api` | `/orders` | 주문 CRUD REST API |
| `outbound` | `/outbound` | 출고 처리·이력 |
| `etc_outbound` | `/etc-outbound` | 기타출고 (자체소비/샘플) |
| `shipment` | `/shipment` | 송장 관리·CJ API |
| `shipping` | `/shipping` | 배송 조회·상태 |
| `packing` | `/packing` | 패킹 현장 (영상 녹화 포함) |
| `inbound` | `/inbound` | 입고 처리 |

### 3-2. 재고·생산 도메인
| Blueprint | URL | 주요 기능 |
|-----------|-----|---------|
| `stock` | `/stock` | 수불장·재고 현황 |
| `transfer` | `/transfer` | 창고간 이동 |
| `repack` | `/repack` | 재포장 |
| `set_assembly` | `/set-assembly` | 세트 조립 |
| `production` | `/production` | 생산 관리 |
| `yield_mgmt` | `/yield` | 수율 관리 |
| `adjustment` | `/adjustment` | 재고 조정 |
| `materials` | `/materials` | 자재 관리 |
| `planning` | `/planning` | 발주 계획 |

### 3-3. 매출·정산 도메인
| Blueprint | URL | 주요 기능 |
|-----------|-----|---------|
| `revenue` | `/revenue` | 매출 일계·분석 |
| `aggregation` | `/aggregation` | 채널별 집계 |
| `closing` | `/closing` | 월마감 |
| `reconciliation` | `/api/reconciliation` | 수익 대사 |
| `history` | `/history` | 거래 이력 |
| `marketplace` | `/marketplace` | 마켓 API 설정·동기화 |

### 3-4. 회계·재무 도메인
| Blueprint | URL | 주요 기능 |
|-----------|-----|---------|
| `accounting` | `/accounting` | 회계 장부 |
| `bank` | `/bank` | 은행 거래내역 |
| `finance` | `/finance` | 재무 리포트 |
| `journal` | `/journal` | 전표 관리 |
| `ledger` | `/ledger` | 원장 |
| `tax_invoice` | `/tax-invoice` | 세금계산서 |
| `trade` | `/trade` | B2B 거래 |

### 3-5. 마스터·설정 도메인
| Blueprint | URL | 주요 기능 |
|-----------|-----|---------|
| `master` | `/master` | 상품·원가 마스터 |
| `base_data` | `/base-data` | 기준 데이터 |
| `price_mgmt` | `/price` | 가격 관리 |
| `bom_cost` | `/bom-cost` | BOM 원가 |
| `promotions` | `/promotions` | 프로모션 |
| `integrity` | `/integrity` | 데이터 무결성 검사 |
| `team` | `/settings/team` | 팀원 초대·권한 |
| `billing` | `/billing` | SaaS 구독 |
| `admin_saas` | `/admin-saas` | 슈퍼어드민 콘솔 |
| `hr` | `/hr` | 인사 관리 |
| `mobile` | `/m` | 현장 모바일 UI |

---

## 4. DB 스키마 구조

### 4-1. 핵심 테이블 관계

```
businesses (사업자)
  ├── app_users (회원)
  │     └── user_business_map (회원-사업자 N:M, role 포함)
  ├── invitations (이메일 초대)
  │
  ├── [주문]
  │     ├── import_runs (주문 업로드 이력)
  │     ├── order_transactions (주문 명세)
  │     │     └── order_change_log (변경 이력)
  │     └── order_shipping (송장·배송)
  │
  ├── [재고]
  │     └── stock_ledger (수불 원장)
  │           UNIQUE(biz_id, event_uid)
  │
  ├── [마스터]
  │     ├── product_costs (원가·옵션 마스터)
  │     ├── option_master (옵션 매핑)
  │     └── business_partners (거래처)
  │
  ├── [매출]
  │     ├── daily_revenue (일별 매출)
  │     └── manual_trades (수동 거래)
  │
  ├── [마켓플레이스 API]
  │     ├── marketplace_api_config (채널별 API 키)
  │     │     UNIQUE(biz_id, channel)
  │     ├── api_orders (마켓 원본 주문)
  │     │     UNIQUE(biz_id, channel, api_order_id, api_line_id)
  │     ├── api_settlements (마켓 정산)
  │     │     UNIQUE(biz_id, channel, settlement_date, settlement_id)
  │     └── api_sync_log (동기화 이력)
  │
  ├── [회계]
  │     ├── bank_accounts / bank_transactions
  │     ├── tax_invoices
  │     ├── journal_entries / journal_lines
  │     └── ...
  │
  └── [SaaS]
        ├── plans / plan_features
        ├── subscriptions
        └── saas_config (암호화 설정 KV)
```

### 4-2. 멀티테넌트 격리 전략

```
모든 테이블: biz_id BIGINT NOT NULL REFERENCES businesses(id)

인덱스 패턴:
  idx_ot_biz_date ON order_transactions(biz_id, order_date DESC)
  idx_sl_biz_date ON stock_ledger(biz_id, transaction_date)

UNIQUE 제약:
  order_transactions: (biz_id, channel, order_no, line_no)
  stock_ledger: (biz_id, event_uid)
  marketplace_api_config: (biz_id, channel)
```

---

## 5. 마켓플레이스 API 연동 구조

### 5-1. 데이터 흐름

```
마켓플레이스 API
 (쿠팡 WING / 네이버커머스 / 카카오 / ...)
        │
        │  REST API (OAuth / HMAC)
        ▼
 MarketplaceManager (services/marketplace/)
        │
        ├── CoupangClient  (coupang.py)
        ├── NaverClient    (naver_commerce.py)
        ├── KakaoClient    (kakao.py)
        └── ...
        │
        │  주문·정산 원본 → api_orders / api_settlements
        ▼
 api_order_converter.py
        │  매핑: api_orders → order_transactions 포맷
        ▼
 db_supabase.upsert_order_batch()
        │  RPC: rpc_upsert_order_batch(biz_id, run_id, orders)
        ▼
 order_transactions + order_shipping (확정 저장)
```

### 5-2. 자동 수집 스케줄러

```python
# services/sync_scheduler.py
# 앱 시작시 start_sync_scheduler(app) 1회 호출
# daemon thread — 1분마다 조건 체크

_ORDER_INTERVAL  = 30분  (SYNC_ORDER_INTERVAL_MIN)
_SETTLE_INTERVAL = 360분 (SYNC_SETTLE_INTERVAL_MIN)
_DAYS_BACK       = 2일   (SYNC_DAYS_BACK)

흐름:
  daemon thread
    └── 30분 경과 시 → run_order_sync(app)
          └── 활성 채널 목록 → sync_orders(db, mgr, channel, ...)
                └── 마켓 API fetch → upsert_api_orders_batch
    └── 6시간 경과 시 → run_settlement_sync(app)
          └── sync_settlements(db, mgr, channel, ...)
```

### 5-3. 채널 등록 흐름

```
/marketplace/settings (GET/POST)
  │  API 키 입력 → marketplace_api_config upsert
  │  is_active = True 설정
  │
  ├── /marketplace/test-connection (POST)
  │     → MarketplaceManager.test_connection(channel)
  │
  └── /marketplace/sync (POST)
        → sync_orders() 즉시 실행
        → api_sync_log에 결과 기록
```

---

## 6. 인증·권한 구조

### 6-1. 사용자 흐름

```
/auth/login  →  app_users 테이블 (bcrypt)
                │
                ├── session['current_biz_id'] 세팅
                └── g.biz_id → 모든 요청에 tenant 격리

/auth/join/<token>  →  invitations 테이블 (7일 만료)
                        │
                        ├── 기존 계정: 로그인 후 자동 합류
                        └── 신규 계정: signup → 합류
```

### 6-2. ROLE 계층

```
owner    → 전체 권한 (삭제 포함)
admin    → 설정·멤버 관리
manager  → 주문·재고·매출 읽기·쓰기
logistics→ 출고·패킹·배송 전용
sales    → 주문·매출 읽기만
viewer   → 읽기 전용
```

```python
# auth/__init__.py
@role_required('manager')
def some_view():
    ...
```

### 6-3. 팀 초대 흐름

```
/settings/team/invite (POST)
  │  email + role 입력
  │  token 생성 (secrets.token_urlsafe(32))
  │  invitations 테이블 저장 (7일 만료)
  │
  └── Resend API → 이메일 발송
        링크: /auth/join/<token>
```

---

## 7. Service Layer 주요 모듈

```
services/
├── order_processor.py      ★ 주문서 파싱·매칭·DB 저장 core
│     ├── 엑셀 파싱 (채널별 포맷)
│     ├── option_matcher → product_name·barcode 매핑
│     └── upsert_order_batch → DB
│
├── marketplace/            ★ 마켓 API 클라이언트 모음
│     ├── __init__.py       MarketplaceManager
│     ├── coupang.py
│     ├── naver_commerce.py
│     └── ...
│
├── marketplace_sync_service.py   주문·정산 수집 로직
├── sync_scheduler.py             자동 수집 스케줄러
│
├── stock_service.py        수불 계산·재고 현황
├── order_to_stock_service.py  주문 → 재고 차감 연결
│
├── revenue_service.py      매출 집계·분석
├── settlement_service.py   정산 처리
├── pnl_service.py          P&L 계산
│
├── saas_config.py          ★ 암호화 설정 KV (Fernet)
│     → API 키, Supabase URL 등 민감정보 저장
│
├── email_service.py        Resend API 래퍼
├── cj_shipping_service.py  CJ 대한통운 API
├── health_monitor.py       서버 모니터링
└── sync_scheduler.py       마켓 자동수집 daemon
```

---

## 8. DB 레이어 구조

```
db/
├── base.py             BaseRepo (client, _safe_execute, _paginate_query)
├── client.py           get_admin_client() — service_role 싱글톤
├── tenant.py           install_tenant_guard() — biz_id 자동 주입 패치
│
├── orders_repo.py      주문·import_run (13개 메서드)
├── marketplace_repo.py 마켓 API 설정·동기화·api_orders (14개 메서드)
├── product_repo.py     원가·옵션 마스터
├── inventory_repo.py   재고 수불장
├── outbound_repo.py    출고 처리
├── packing_repo.py     패킹 현장
├── shipping_repo.py    송장·배송
├── settlement_repo.py  정산
├── finance_repo.py     은행·세금계산서·전표
├── hr_repo.py          인사
├── trade_repo.py       B2B 거래
└── auth_repo.py        인증·팀원
│
db_supabase.py          ★ 메인 monolithic (레거시, 점진적 분리 중)
                          tenant_guard 패치 대상
db_utils.py             get_db() — SupabaseDB 싱글톤 반환
```

**분리 전략**: 신규 기능은 `db/*.py` Repo 패턴으로 작성, `db_supabase.py`는 점진적으로 축소

---

## 9. RPC 함수 목록

### 9-1. 주문 처리 (CRITICAL)

| RPC | 파라미터 | 설명 |
|-----|---------|------|
| `rpc_upsert_order_batch` | biz_id, import_run_id, orders | 주문 배치 저장 (INSERT/UPDATE/SKIP) |
| `rpc_cancel_or_edit_order` | biz_id, order_id, change_type, payload | 주문 수정·취소·환불 |
| `rpc_check_order_no_exists` | channel, order_nos[] | 주문번호 중복 체크 |
| `rpc_check_raw_hash_exists` | hashes[] | raw_hash 중복 체크 |
| `rpc_get_import_run_summary` | run_id | import_run 집계 |

### 9-2. 재고·출고

| RPC | 설명 |
|-----|------|
| `rpc_check_event_uid_exists` | stock_ledger event_uid 중복 체크 |
| `rpc_get_transfer_detail` | 창고이동 상세 |
| `rpc_get_outbound_list` | 출고 목록 (SALES_OUT + manual) |
| `rpc_get_materials_stock_agg` | 자재 재고 집계 |
| `rpc_get_stock_distinct_products` | 재고 품목 목록 |

### 9-3. 집계·분석

| RPC | 설명 |
|-----|------|
| `get_revenue_summary_agg` | 기간 매출 집계 |
| `get_stock_summary` | 재고 현황 집계 |
| `rpc_get_packing_pending_orders` | 패킹 대기 주문 |
| `rpc_search_order_shipping_by_invoice` | 송장번호로 배송 조회 |
| `rpc_get_revenue_by_date` | 날짜별 매출 상세 |

> **⚠️ TODO**: 모든 RPC에 `p_biz_id` 파라미터 추가 필요 (현재 service_role 전용이라 임시 작동 중)

---

## 10. 인프라·배포 구성

```
Render (Web Service)
  └── maesil-hub
        ├── Flask + Gunicorn
        ├── RENDER_API_KEY → 서버 모니터링 (admin_saas)
        └── 환경변수 (.env)
              ├── SUPABASE_URL / SUPABASE_SERVICE_KEY
              ├── SECRET_KEY (Flask session)
              ├── FERNET_KEY (saas_config 암호화)
              ├── SYNC_ORDER_INTERVAL_MIN=30
              ├── SYNC_SETTLE_INTERVAL_MIN=360
              └── SYNC_DAYS_BACK=2

Supabase (PostgreSQL)
  ├── service_role key → hub 앱 (RLS 우회, biz_id 앱 레벨 격리)
  ├── RLS 비활성화 (주요 테이블) — 앱 레벨에서 격리
  └── 인덱스 최적화 (biz_id + 날짜 복합 인덱스)
```

---

## 11. 현재 상태 및 로드맵

### 11-1. 완료 (2026-05-18 기준)

| 항목 | 상태 |
|------|------|
| 멀티테넌트 기반 구조 | ✅ |
| 팀원 초대·권한 관리 | ✅ |
| 마켓플레이스 API 설정 UI | ✅ |
| 자동 수집 스케줄러 (30분) | ✅ |
| RPC 함수 10개 이식 | ✅ |
| total → hub 데이터 이관 스크립트 | ✅ |
| order_shipping hub 스키마 호환 | ✅ |
| Resend 이메일 발송 | ✅ |

### 11-2. 진행 중

| 항목 | 상태 |
|------|------|
| total 데이터 이관 (배마마 biz_id=1) | 🔄 67% |
| 실서비스 버그 헌팅 | 🔄 예정 |

### 11-3. 우선순위별 TODO

#### P0 — 즉시
- [ ] order_transactions 이관 완성 (73K → 현재 49K)
- [ ] 로컬 서버 실행 → 기본 기능 E2E 테스트
- [ ] 마켓플레이스 API 키 등록 → 자동수집 실 동작 확인

#### P1 — 이번 주
- [ ] RPC에 `p_biz_id` 파라미터 추가 (멀티테넌트 완전 격리)
- [ ] 대시보드 RPC 이식 (`get_dashboard_full` 등)
- [ ] `rpc_upsert_order_batch` 실 동작 테스트 (주문 업로드 E2E)
- [ ] maesil-total 사용자 계정 → hub 계정 생성 가이드

#### P2 — 이번 달
- [ ] maesil-flow 연동 설계 (현장 출고 API)
- [ ] 결제 PG 연동 (KakaoPay EASY_PAY) → SaaS 과금 실동작
- [ ] 2번째 테넌트 온보딩 테스트
- [ ] E2E 테스트 스위트 구축

#### P3 — 다음 달
- [ ] 패킹센터 영상녹화 → S3 분리
- [ ] maesil-packing 서비스 분리
- [ ] 멀티PC 환경 동기화 가이드 정비

---

## 12. 개발 가이드

### 신규 기능 추가 체크리스트

```
□ Blueprint: blueprints/<name>.py → <name>_bp 변수로 자동 등록
□ Service: services/<name>_service.py (stateless, biz_id 파라미터 명시)
□ Repo: db/<name>_repo.py → BaseRepo 상속 (명시적 biz_id)
□ Migration: migrations/<num>_<desc>.sql → python scripts/run_sql.py 실행
□ Template: templates/<name>/ → base.html 상속
□ biz_id 격리 확인: 모든 쿼리에 WHERE biz_id = ? 포함
□ RLS 아닌 앱 레벨 격리 확인
```

### 환경 세팅

```bash
# 로컬 개발
cp .env.example .env
# SUPABASE_URL, SUPABASE_SERVICE_KEY 등 설정

python -m flask run --debug

# 마이그레이션 실행
python scripts/run_sql.py migrations/XXX.sql

# 데이터 이관 (total → hub)
python scripts/migrate_total_to_hub.py --dry-run --all
python scripts/migrate_total_to_hub.py --table order_transactions
```

### 채널 추가 방법

```python
# services/marketplace/<channel>.py 생성
class NewChannelClient:
    def fetch_orders(self, date_from, date_to) -> list[dict]: ...
    def fetch_settlements(self, date_from, date_to) -> list[dict]: ...

# services/channel_config.py에 채널명 등록
# api_order_converter.py에 필드 매핑 추가
```
