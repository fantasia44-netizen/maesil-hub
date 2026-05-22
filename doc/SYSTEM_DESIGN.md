# 매실 시스템 통합 설계 문서

작성일: 2026-05-22  
작성 범위: maesil-hub / maesil-order(flow) / maesil-total / maesil-insight 4개 레포 전체

---

## 목차

0. [전체 생태계 개요](#0-전체-생태계-개요)
1. [시스템 전체 구조](#1-시스템-전체-구조)
2. [데이터 소유권 (SSOT 정의)](#2-데이터-소유권-ssot-정의)
3. [시스템간 데이터 공유 방식](#3-시스템간-데이터-공유-방식)
4. [중복 업무 제거 계획](#4-중복-업무-제거-계획)
5. [통합 포인트 명세](#5-통합-포인트-명세)
6. [마이그레이션 로드맵](#6-마이그레이션-로드맵)
7. [플랜별 기능 매트릭스](#7-플랜별-기능-매트릭스)

---

## 0. 전체 생태계 개요

### 0.1 8개 시스템 전체 목록

| 시스템 | 역할 | DB | 상태 | URL |
|---|---|---|---|---|
| **maesil** | 공식 웹사이트 (정적 랜딩) | 없음 | 운영 중 | maesil.net |
| **maesil-hub** | 신규 SaaS ERP 플랫폼 (다테넌트) | Supabase C (biz_id) | 개발 중 (Phase 1) | hub.maesil.net |
| **maesil-total** | 레거시 ERP (배마마 현재 메인) | Supabase A (배마마) + B (쿡대디) | 운영 중, freeze | Render |
| **maesil-order** | 3PL 창고 현장 운영 (패킹/포털) | Supabase A + B 공유 | 운영 중 | Render |
| **maesil-insight** | 광고분석 / 경쟁분석 / 정산 SSOT | Supabase B (operator_id) | 운영 중 | insight.maesil.net |
| **maesil-studio** | AI 브랜드/콘텐츠 자동생성 SaaS | Supabase (studio 전용) | 개발 중 | Render |
| **maesil-agency** | AI 에이전트 플랫폼 (멀티에이전트) | agent_work 스키마 (total DB) | Phase A 운영 중 | 전용 웹UI |
| **maesil_accounting** | 독립 회계 처리 모듈 | - | 미사용 (참조용) | - |

### 0.2 전체 생태계 구조도

사용자 유형별 접근 경로:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           [사용자 유형별 진입점]                             │
└─────────────────────────────────────────────────────────────────────────────┘

  [잠재고객]          [온라인셀러/3PL/제조 테넌트]    [3PL 현장직원]    [분석 사용자]
      │                          │                        │                │
      ▼                          ▼                        ▼                ▼
  maesil.net              hub.maesil.net          maesil-order       insight.maesil.net
  (정적 랜딩)              (백오피스 ERP)           (현장앱/포털)       (광고분석)
                                 │                        │
                                 └──────────┬─────────────┘
                                            ▼
                                   [Supabase C: maesil-hub]
                                   (단일 DB, biz_id 격리)

  [AI 비서 사용자]           [브랜드 운영자]
        │                         │
        ▼                         ▼
  maesil-agency              maesil-studio
  (멀티에이전트)              (AI 콘텐츠생성)
        │                         │
        ▼                         ▼
  agent_work 스키마         Supabase (studio)
  (total/hub DB 연동)       insight 연동 예정

  ─────────────────────────────────────────
  [레거시 운영 중, hub 이전 완료 전까지]
  maesil-total (배마마 실운영 메인)
    └─ Supabase A (배마마) + Supabase B (쿡대디)
```

### 0.3 시스템간 데이터 흐름

```
외부 마켓API                  외부 광고API
(쿠팡/네이버/ESM 등)           (네이버광고/쿠팡광고)
        │                             │
        ▼                             ▼
   maesil-hub                  maesil-insight
  (주문 자동수집)                (ROAS/정산 수집)
        │                             │
        │  ① 정산집계 읽기(REST API)    │
        │◄─────────────────────────────┤
        │                             │
        ▼                             ▼
  [Supabase C]               [Supabase B insight]
  (hub DB, biz_id)           (operator_id 격리)
        │
        ├──② DB 직접 공유──► maesil-order
        │                    (패킹/출고 write)
        │
        └──③ 브릿지(임시)──► maesil-total
                              (hub 이전 기간 한정)

  ④ insight ──연동 예정──► maesil-studio (실판매 피드백)
  ⑤ total/hub DB ──스키마──► maesil-agency (AI 에이전트 조회)
```

흐름 요약:
- ① hub ↔ insight: REST API (hub P&L에서 광고비/정산 읽기)
- ② hub ↔ flow(order): Supabase DB 직접 공유 (동일 프로젝트)
- ③ total → hub: DB 브릿지 읽기 (이전 기간 한정, 완료 후 제거)
- ④ insight → studio: 실판매 데이터 피드백 (연동 예정)
- ⑤ total/hub → agency: agent_work 스키마 (AI 에이전트 조회용)

### 0.4 개발 우선순위 현황 (2026-05-22)

| 우선순위 | 시스템 | 작업 | 목표 시점 |
|:---:|---|---|---|
| P0 | maesil-hub | Phase 1: SaaS 인프라 이식 (auth/billing/admin/onboarding) | 2026-06-04 |
| P1 | maesil-hub | Phase 2: ERP 핵심 이식 (재고/주문/출고/생산/정산) | 2026-06-30 |
| P1 | maesil-total | 배마마 운영 유지 (긴급 hotfix만, freeze) | hub Phase 2 완료 전까지 |
| P2 | maesil-order | hub Supabase C로 DB 전환 (현장UI만 유지) | hub Phase 2 후 |
| P2 | maesil-insight | hub REST API 엔드포인트 노출 (브릿지 → API 전환) | hub Phase 2 후 |
| P3 | maesil-studio | AI 콘텐츠 SaaS 개발, insight 연동 | 2026 하반기 |
| P3 | maesil-agency | 상용화 준비 (현재 super_admin 1인 운영) | 2026-12 |

특이사항:
- maesil-insight 정산 SSOT 알고리즘: 영업비밀 유지 (특허 출원 2026-04-08, 공개 금지)
- maesil-total: hub 이전 완료 후 레포 archive (read-only) 예정

---

## 1. 시스템 전체 구조

### 1.1 현재 상태 (2026-05-22)

```
┌─────────────────────────────────────────────────────────────────┐
│  maesil-total (C:\maesil-total)  ← 배마마 현재 운영 메인        │
│  · Flask + Supabase 2개 (baemama / cookdaddy)                  │
│  · DB 분리 = 사업장별 별도 Supabase 프로젝트                   │
│  · 상태: 운영 중, freeze (긴급 hotfix만)                       │
└───────────────────────┬─────────────────────────────────────────┘
                        │ 이전 진행 중
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│  maesil-hub (C:\maesil-hub)  ← 신규 SaaS ERP 플랫폼            │
│  · Flask + Supabase 단일 (biz_id 멀티테넌트)                   │
│  · Phase 1(SaaS 인프라) 이식 진행 중                           │
│  · 배포: Render → hub.maesil.net                               │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  maesil-order / 플로워 (C:\maesil-order)  ← 3PL 현장 운영      │
│  · 배마마 Supabase + 쿡대디 Supabase 동시 서비스               │
│  · blueprint: packing, client_portal, operator, outbound 등    │
│  · 목표: hub DB 공유로 전환                                    │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  maesil-insight (C:\maesil-insight\services)  ← 광고분석        │
│  · 네이버/쿠팡 광고 ROAS, 경쟁사 분석                         │
│  · 완전 독립 서비스, 타 시스템과 연결 최소                    │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 목표 상태 (hub 안정 후)

```
                    [사용자 브라우저]
                          │
           ┌──────────────┼──────────────────┐
           │              │                  │
           ▼              ▼                  ▼
   hub.maesil.net  flow.maesil.net   insight.maesil.net
   (백오피스 ERP)   (3PL 현장앱)      (광고분석)
           │              │                  │
           │              │                  │
           └──────┬───────┘                  │
                  │                          │
                  ▼                          ▼
        [Supabase: maesil-hub]      [Supabase: maesil-insight]
        (단일 DB, biz_id 격리)       (operator_id 격리)
```

### 1.3 제품 버티컬

| 버티컬 | 주요 기능 | 주 사용 시스템 |
|---|---|---|
| 온라인 업체 | 마켓플레이스 연동, 주문/재고/정산 분석 | hub |
| 3PL 운영사 | 화주사 관리, 패킹/출고, 화주사 포털 | hub + flow |
| 제조기업 | BOM, 생산계획, 원가계산, 수율관리 | hub |

---

## 2. 데이터 소유권 (SSOT 정의)

### 2.1 원칙

- **SSOT(Single Source of Truth)**: 데이터는 반드시 한 곳에서만 write 발생
- **읽기 복제**: 다른 시스템은 read-only로만 접근하거나, 동기화 후 사용
- **biz_id 격리**: 모든 비즈니스 테이블은 `biz_id BIGINT NOT NULL` 필수

### 2.2 도메인별 SSOT

| 도메인 | 테이블 | SSOT | Write | Read-only |
|---|---|---|---|---|
| 테넌트/사업체 | `businesses` | hub DB | hub | flow |
| 사용자/인증 | `app_users`, `user_business_map` | hub DB | hub | flow |
| 구독/결제 | `subscriptions`, `payments` | hub DB | hub | - |
| 요금제 | `plans` | hub DB | hub | hub, flow |
| **재고원장** | `stock_ledger` | hub DB | hub, flow | insight(미래) |
| **주문** | `order_transactions` | hub DB | hub | flow |
| **옵션매핑** | `option_master` | hub DB | hub | flow |
| **상품마스터** | `product_costs` | hub DB | hub | flow |
| **출고** | packing_jobs, outbound 관련 | hub DB | hub, flow | - |
| **거래처** | `business_partners` | hub DB | hub | flow |
| **광고정산** | `api_settlements` | insight DB | insight | hub(브릿지) |
| **광고주문** | `api_orders` | insight DB | insight | hub(브릿지) |
| 3PL 화주사 | `clients`, `orders` (flow 전용) | flow DB (임시) | flow | - |

### 2.3 현재 total의 DB 소유권 (이전 완료 전까지)

```
maesil-total
  └─ baemama Supabase: 배마마 실운영 데이터 (stock_ledger, order_transactions 등)
  └─ cookdaddy Supabase: 쿡대디 실운영 데이터

→ hub 이전 완료 전까지는 total이 실 SSOT
→ 브릿지(services/maesil_bridge.py)로 hub에서 읽기 전용 접근 가능
```

---

## 3. 시스템간 데이터 공유 방식

### 3.1 선택 기준

| 방식 | 사용 시나리오 | 장점 | 단점 |
|---|---|---|---|
| **DB 직접 공유** | hub ↔ flow (같은 Supabase) | 지연 없음, 트랜잭션 가능 | DB 스키마 결합 |
| **REST API** | hub ↔ insight, 외부 채널 | 느슨한 결합, 독립 배포 | 지연, 에러 처리 필요 |
| **Webhook** | 결제 이벤트, 외부 알림 | 비동기, 실시간 | 재시도 로직 필요 |
| **DB 브릿지(읽기전용)** | total → hub (이전 기간 한정) | 빠른 통합 | 임시 패턴, 결합도 높음 |

### 3.2 hub ↔ flow

**방식: Supabase DB 직접 공유 (같은 프로젝트)**

```
[hub 백오피스]          [flow 현장앱]
      │                       │
      └───── Supabase DB ─────┘
             (hub Supabase)
             biz_id 격리
```

- flow는 hub와 동일 Supabase URL/KEY 사용
- flow에서 발생하는 write (패킹완료, 출고처리) → hub DB에 직접 INSERT
- RLS 정책으로 biz_id 격리 보장
- 선택 이유: 현장 작업의 지연 허용치 낮음(패킹스캔 즉시 반영), REST API 왕복 시간 제거

### 3.3 hub ↔ insight

**방식: REST API (읽기 전용, 최소 연결)**

```
[hub]  ──GET /api/settlements/{biz_id}──>  [insight]
       <──  JSON 채널별 집계  ──────────
```

- insight는 자체 DB(operator_id 격리)에 광고/정산 데이터 보유
- hub는 P&L 화면에서 insight REST API를 read-only 호출
- 현재 임시 구현: `services/maesil_bridge.py` (Supabase 직접 쿼리)
- 목표 구현: insight가 REST 엔드포인트 노출 → hub가 HTTP 호출
- 선택 이유: 두 시스템의 독립 배포 주기가 다름, insight는 별도 DB 스키마 유지 필요

### 3.4 total → hub (이전 기간 한정)

**방식: DB 브릿지 (읽기 전용, 임시)**

- `services/maesil_bridge.py`: total Supabase를 직접 쿼리하여 hub에 데이터 주입
- 이전 완료 후 제거 예정
- 선택 이유: 이전 기간 동안 hub에서 total 데이터도 볼 수 있어야 함

### 3.5 외부 채널 API

**방식: Hub에서 직접 HTTP 호출 (기존 total 패턴 유지)**

- `services/marketplace/`: 쿠팡, 네이버, ESM, 11번가, 카카오, Cafe24
- `services/courier/cj_client.py`: CJ 송장 API
- hub의 APScheduler(cron)에서 주기적으로 수집

---

## 4. 중복 업무 제거 계획

### 4.1 현재 중복 발생 목록

| 중복 모듈 | total | hub | order/flow | 처리 방향 |
|---|---|---|---|---|
| 마켓플레이스 클라이언트 | `services/marketplace/` | `services/marketplace/` | `services/marketplace/` | **hub 단일화** → flow는 hub API 호출 |
| option_matcher | `services/option_matcher.py` | `services/option_matcher.py` | `services/option_matcher.py` | **hub 단일화** |
| inbound/outbound/repack 로직 | `services/inbound_service.py` 등 | 동일 | 동일 | **hub 단일화** (flow는 DB 직접 write) |
| 인증/세션 | total 자체 인증 | hub 인증 | flow 자체 인증 | **hub 인증으로 통합** |
| 대시보드 집계 | `services/dashboard_service.py` | 동일 | 동일 | **hub 단일화** |
| 수불장/원장 | `services/ledger_service.py` | 동일 | 동일 | **hub 단일화** |
| CJ 송장 | `services/courier/cj_client.py` | 동일 | 동일 | **hub 단일화** |
| 채널 설정 | `services/channel_config.py` | 동일 | 동일 | **hub 단일화** |
| BOM/원가 | `blueprints/bom_cost.py` | 동일 | 동일 | **hub 단일화** |

### 4.2 flow(order) 전용 유지 모듈

flow는 현장 작업에 특화된 화면만 유지. 비즈니스 로직은 hub DB write로 처리.

| 모듈 | 이유 |
|---|---|
| `blueprints/packing.py` | 현장 패킹 UI (스캔, 속도모드/안정모드) |
| `blueprints/field.py` | 현장 직원 전용 모바일 화면 |
| `blueprints/client_portal.py` | 화주사 전용 포털 (3PL 고객) |
| `blueprints/operator.py` | 3PL 운영자 대시보드 |

### 4.3 insight 전용 유지 모듈

| 모듈 | 이유 |
|---|---|
| 광고 수집 (naver_ad, coupang_ad) | 브라우저 확장/API 수집 로직 복잡 |
| 경쟁사 분석 엔진 | 완전 독립 도메인 |
| SEO 분석 | 독립 도메인 |
| 정산 SSOT 엔진 | insight의 핵심 알고리즘 (특허 출원) |

---

## 5. 통합 포인트 명세

### 5.1 hub ↔ flow 통합

#### 인증 공유

```
[flow 로그인 요청]
  → hub Supabase app_users 조회
  → user_business_map에서 role 확인
  → flow 전용 role: 'packing_staff', 'warehouse', 'client', 'operator'
  → 세션에 biz_id 세팅 (hub와 동일 방식)
```

flow가 hub와 동일 Supabase를 바라보므로, 동일 `app_users` 테이블 사용.  
flow 전용 UI role은 `user_business_map.role` 컬럼 확장으로 처리.

#### 데이터 이벤트 (flow → hub DB)

| 이벤트 | flow 액션 | hub DB write |
|---|---|---|
| 패킹 완료 | 스캔 완료 버튼 | `packing_jobs` INSERT |
| 출고 처리 | 출고 확정 | `stock_ledger` INSERT (SALES_OUT) |
| 입고 확인 | 입고 검수 | `stock_ledger` INSERT (INBOUND) |
| 창고이동 | 이동 확정 | `stock_ledger` INSERT (MOVE_OUT + MOVE_IN) |
| 재고조정 | 실사 입력 | `stock_ledger` INSERT (ADJUST) |

모두 동일 Supabase → 트랜잭션 보장 가능, RLS로 biz_id 자동 격리.

#### hub → flow 화면 데이터

| 데이터 | 방향 | 방식 |
|---|---|---|
| 출고 대기 주문 목록 | hub DB → flow 화면 | flow에서 hub DB 직접 SELECT |
| 상품마스터 (SKU, 바코드) | hub DB → flow 화면 | flow에서 hub DB 직접 SELECT |
| 화주사(client) 정보 | hub DB → flow 화면 | flow에서 hub DB 직접 SELECT |
| 패킹 현황 대시보드 | hub DB → hub 화면 | hub에서 hub DB SELECT |

### 5.2 hub ↔ insight 통합

#### 현재 (브릿지 방식, 임시)

```python
# services/maesil_bridge.py
# insight Supabase에서 api_settlements / api_orders 직접 조회
# hub P&L 화면에서 total 자체 정산 + insight 정산 병합

maesil_rows = get_maesil_settlements_by_month(maesil_sb, operator_id, year_month)
merged = merge_settlements(own_rows, maesil_rows)
```

#### 목표 (REST API 방식)

```
[hub P&L 화면]
  → GET https://insight.maesil.net/api/v1/settlements?biz_id=X&month=2026-05
      Authorization: Bearer <insight_api_token>
  ← { "channels": [...], "total": {...} }
```

- insight가 `/api/v1/settlements` 엔드포인트 노출
- hub에 발급된 서비스 API 토큰으로 인증
- hub의 biz_id ↔ insight의 operator_id 매핑 테이블 필요 (`biz_insight_map`)

#### 연결 데이터 항목

| 항목 | 방향 | 용도 |
|---|---|---|
| 채널별 광고비 | insight → hub | P&L 광고비 반영 |
| 채널별 정산 집계 | insight → hub | P&L 매출 보완 |
| 상품별 광고 ROAS | insight → hub (미래) | 상품 수익성 분석 |

### 5.3 인증 방식 요약

| 시스템 | 사용자 인증 | 시스템간 인증 |
|---|---|---|
| hub | bcrypt + Flask-Login 세션 (HttpOnly, SameSite=Strict) | - |
| flow | hub Supabase app_users 공유 | hub DB service_role key |
| insight | 자체 인증 (별도 DB) | API 토큰 (Bearer) |
| 외부 채널 API | API Key (Fernet 암호화, saas_config 저장) | - |

---

## 6. 마이그레이션 로드맵

### 6.1 total → hub 이전 단계

```
Phase 0 (완료)  : hub 레포 설계 문서 + 기본 골격
Phase 1 (진행중): SaaS 인프라 이식 (auth, billing, plans, admin, onboarding)
Phase 2 (예정)  : ERP 핵심 이식 (재고, 주문, 출고, 생산, 거래처, 매출)
Phase 3 (예정)  : 첫 외부 고객 온보딩
Phase 4 (예정)  : 배마마 데이터 이관 + total 종료
```

#### Phase 1 — SaaS 인프라 (2026-05-22 ~ 06-04)

- [ ] auth/ 이식: bcrypt + IP잠금 + Redis rate limit
- [ ] billing/ 이식: PortOne 빌링키 + 정기결제 + 웹훅
- [ ] plans/ + DB plans 테이블 + plan_cache
- [ ] admin/ 슈퍼어드민 (테넌트관리, impersonation, 모니터링)
- [ ] onboarding/ 가입→시드 자동화
- [ ] before_request: g.biz_id 세팅 + RLS context + 구독 잠금
- [ ] 마이그레이션 001~006 완료

#### Phase 2 — ERP 핵심 (2026-06-05 ~ 06-30)

total에서 hub로 이식 시 **멀티테넌트 변환** 필수:

| total 패턴 | hub 패턴 |
|---|---|
| 별도 Supabase 프로젝트 per 사업장 | 단일 Supabase + `biz_id` 컬럼 |
| `db_pool[biz_id]`로 DB 분기 | `g.biz_id` + RLS + `.eq('biz_id', g.biz_id)` |
| 한글 SQL 리터럴 | `U&'\XXXX'` Unicode escape 100% |
| 직접 `.select().limit(1000)` | 모든 집계 RPC + 페이지네이션 |
| 단일 인스턴스 메모리 캐시 | Redis (멀티 인스턴스 대응) |

이식 순서:
```
1. stock_ledger + product_costs + option_master (재고 핵심)
2. order_transactions + option_matcher (주문 수집)
3. outbound + packing (출고/패킹)
4. production + repack + transfer (생산/소분/창고이동)
5. trade + business_partners (거래처)
6. revenue + settlement (매출/정산)
7. bom_cost + yield (원가/수율)
8. shipping + CJ API (송장)
```

#### Phase 3 — 배마마 데이터 이관 (hub 안정 후 6개월~1년)

```
[준비] hub Phase 2 완료 + 배마마 운영 검증 3개월
[이관] total baemama Supabase → hub Supabase
  1. 데이터 추출: stock_ledger, order_transactions, option_master 등 전체
  2. biz_id 할당: 배마마 biz_id = N (hub businesses 레코드)
  3. 변환 스크립트: total 스키마 → hub 스키마 (canonical 재적용)
  4. 검증: 재고 스냅샷 비교 (total vs hub)
  5. 컷오버: 배마마 → hub.maesil.net 전환
[종료] maesil-total 레포 archive (read-only)
```

### 6.2 order/flow DB → hub 통합 단계

```
현재: flow(maesil-order)가 자체 Supabase 사용
      (배마마 Supabase + 쿡대디 Supabase 동시)

목표: flow가 hub Supabase를 직접 사용

전환 단계:
  Step 1. hub에 flow 전용 테이블 추가
          - packing_jobs (biz_id 포함)
          - client_companies (3PL 화주사, biz_id = 3PL운영사 biz_id)
          - flow_users role 확장 (user_business_map.role에 'packing_staff' 등 추가)
  Step 2. flow 코드에서 DB 연결을 hub Supabase로 교체
          - SUPABASE_URL/KEY → hub 프로젝트로
          - db_utils.get_db() → hub client
          - biz_id 필터 추가
  Step 3. 기존 flow DB 데이터 이관 (packing 이력 등)
  Step 4. 기존 flow DB 종료
```

---

## 7. 플랜별 기능 매트릭스

### 7.1 버티컬 × 플랜 매트릭스

| 기능 | 온라인 Starter | 온라인 Pro | 3PL Standard | 3PL Pro | 제조 Standard | 제조 Pro |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **주문/재고** | | | | | | |
| 마켓플레이스 연동 채널 수 | 2 | 무제한 | - | - | - | - |
| 주문 자동 수집 | O | O | - | - | - | - |
| 재고 수불장 | O | O | O | O | O | O |
| 다채널 재고 통합 | X | O | - | - | - | - |
| **3PL 기능** | | | | | | |
| 화주사 관리 | - | - | 10개 | 무제한 | - | - |
| 현장 패킹앱(flow) | - | - | O | O | - | - |
| 화주사 포털 | - | - | O | O | - | - |
| 속도모드/안정모드 | - | - | O | O | - | - |
| **제조 기능** | | | | | | |
| BOM 관리 | - | - | - | - | O | O |
| 생산계획 | - | - | - | - | O | O |
| 원가계산 | - | - | - | - | O | O |
| 수율 관리 | - | - | - | - | X | O |
| **분석** | | | | | | |
| 대시보드 | O | O | O | O | O | O |
| P&L 분석 | X | O | - | O | - | O |
| 정산 분석 | X | O | - | - | - | - |
| AI 진단 | X | O | X | O | X | O |
| **공통** | | | | | | |
| 사용자 수 | 3 | 무제한 | 5 | 무제한 | 5 | 무제한 |
| 이력/감사 로그 | 30일 | 1년 | 30일 | 1년 | 30일 | 1년 |
| API 접근 | X | O | X | O | X | O |

비고: `-` = 해당 버티컬 메뉴 미노출 (plan_features off)

### 7.2 플랜 코드 체계 (DB plans 테이블)

```
plan_code 예시:
  online_starter   — 온라인 스타터
  online_pro       — 온라인 프로
  tpl_standard     — 3PL 스탠다드
  tpl_pro          — 3PL 프로
  mfg_standard     — 제조 스탠다드
  mfg_pro          — 제조 프로
  enterprise       — 커스텀 (영업 협의)
```

### 7.3 기능 게이팅 구현 방식

#### DB 구조 (plans.features JSONB)

```json
{
  "channels": 2,
  "users": 3,
  "marketplace_sync": true,
  "multi_channel_stock": false,
  "packing_app": false,
  "client_portal": false,
  "bom": false,
  "production": false,
  "pnl_analysis": false,
  "ai_diagnose": false,
  "api_access": false,
  "log_retention_days": 30
}
```

#### 코드 레벨 게이트

```python
# auth/decorators.py
from functools import wraps
from flask import abort
from plans.features import get_plan_features

def require_feature(feature_name):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            features = get_plan_features(g.biz_id)
            val = features.get(feature_name)
            # 숫자 플랜: 0이면 차단, 양수면 허용
            # 불리언 플랜: False면 차단
            if not val:
                abort(403)
            return f(*args, **kwargs)
        return decorated
    return decorator

# 사용 예:
@require_feature('packing_app')
def packing_view():
    ...
```

주의: `features` 숫자 필드(channels, users 등)를 boolean으로 변환 금지.  
`True == 1` 슬라이싱 사고 방지. 항상 `features.get('channels', 0) > 0` 형태로 비교.

### 7.4 버티컬 선택 시점

```
가입 → 온보딩 Step 1: 버티컬 선택
  [ ] 온라인 판매 (마켓플레이스 중심)
  [ ] 3PL/물류 운영 (창고/패킹 중심)
  [ ] 제조/가공업 (BOM/생산 중심)

→ businesses.industry 컬럼에 저장
→ plan_code에 버티컬 prefix 반영
→ 사이드바 메뉴 on/off 결정 (plan_features 기반)
```

---

## 부록: 운영 규칙

### 레포별 역할 요약

| 레포 | 역할 | 상태 |
|---|---|---|
| maesil-total | 배마마 레거시 운영 | freeze (hotfix only) |
| maesil-hub | 신규 SaaS ERP 플랫폼 | 개발 중 (Phase 1) |
| maesil-order | 3PL 현장 앱 (flow) | 운영 중, hub 통합 예정 |
| maesil-insight | 광고분석 독립 서비스 | 운영 중, 독립 유지 |

### 주요 기술 제약

1. **biz_id 누락 금지**: 모든 비즈니스 테이블 쿼리에 `.eq('biz_id', g.biz_id)` 필수
2. **Supabase 1000행 limit**: 집계 쿼리 전부 RPC 또는 페이지네이션
3. **한글 SQL 리터럴 금지**: `U&'\XXXX'` Unicode escape 100%
4. **멀티 인스턴스 캐시**: 메모리 캐시 대신 Redis (생산 fingerprint 포함)
5. **마이그레이션 순서**: `migrations/STATUS.md` 누적 기록 필수
6. **서비스 키 보호**: `SUPABASE_SERVICE_KEY` 절대 클라이언트 노출 금지
7. **DB 스키마↔코드 동시 배포**: 컬럼 추가 마이그레이션과 코드 배포는 동일 릴리스

### 사고 학습 (total → hub 이식 시 반드시 준수)

| 사고 | 원인 | hub 대책 |
|---|---|---|
| 재고 60kg 부풀림 | materials 1000행 limit + 페이지네이션 누락 | 모든 집계 RPC 강제 |
| 정산 이중집계 | RPC 수정 시 채널 누락 | order_transactions 스키마 변경 시 RPC 동시 수정 |
| 채널명 변경 장애 | DB 마이그레이션과 코드 배포 분리 | 동일 릴리스 강제 |
| 옵션매처 3일 장애 | 리팩토링 시 새 파일 git add 누락 | PR 체크리스트 + CI 파일 변경 감지 |
| production 더블클릭 | 단일 인스턴스 메모리 캐시 | Redis fingerprint 이전 |

---

## 8. GPT + Gemini 아키텍처 리뷰 결과 (2026-05-22)

### 8.1 합의 사항 (두 AI 모두 동의)

| 항목 | 결론 | 우선순위 |
|---|---|:---:|
| flow의 stock_ledger 직접 write | RPC 경유 + idempotency_key 강제 | P0 |
| hub ↔ insight REST API 계약 명확화 | 응답 스키마, 실패 fallback 정의 필수 | P0 |
| migration 방식 | 빅뱅 방식 금지, 스냅샷 방식(재고 최종잔액만 이관) | P0 |
| total → hub 병행 운영 | 최소 2주, 일별 재고/정산 비교 자동화 | P1 |
| agency/studio | hub 안정 전까지 완전 동결 | P3 |
| 감사 로그 표준 강화 | who/when/what/from/to 5종 | P1 |

### 8.2 GPT 추가 지적

1. flow write 필수 컬럼: `ledger_event_id`, `source_system`, `idempotency_key`, `created_by`, `biz_id`
2. 패킹/출고/입고/조정 → DB unique constraint + idempotent insert 필수
3. insight API 실패 시 P&L에 "광고비 미반영" 명시 표시 (정산 오해 방지)
4. 추가 필요 문서: 장애복구 시나리오, migration 검증 체크리스트, API contract, event/idempotency 설계

### 8.3 Gemini 추가 지적 (GPT가 못 짚은 것)

1. **마켓 API Rate Limit 위험** (중요)
   - 멀티테넌트 수십 개가 APScheduler로 동시 수집 시 쿠팡/네이버 IP 차단 위험
   - 대책: 테넌트별 수집 크론을 비동기 큐로 분산, 글로벌 Rate Limiter 레이어 필수

2. **insight 특허 알고리즘 보호 미흡** (중요)
   - `maesil_bridge.py`가 insight DB를 직접 쿼리 = SQL/RPC 구조로 알고리즘 노출
   - 대책: insight를 완전 격리, 추상화된 통계 JSON만 반환하는 REST API 뒤로 숨기기
   - bridge는 즉시 제거 목표로 설정

3. **제조 버티컬 장기 분리 권장**
   - BOM/수율 도메인이 들어오면 DB 스키마 복잡도 기하급수 증가
   - Phase 2까지는 hub 내 모듈, 이후 독립 서비스 분리 준비

### 8.4 두 AI의 최종 평가 공통점

"현재 설계 방향은 맞다. 고도화에서 제일 중요한 보강은 기능 추가가 아니라:
1. 중복 write 방지 (idempotency)
2. 이관 검증 (일별 대조표)
3. 권한 격리 (RLS + RPC 경유)
4. 장애 복구 시나리오
5. 감사 로그"

### 8.5 설계 보강 액션 아이템 (우선순위)

| 우선순위 | 항목 | 담당 | 방식 |
|:---:|---|---|---|
| P0 | flow write → RPC 경유 강제 + idempotency_key | hub DB migration | PostgreSQL RPC + unique constraint |
| P0 | insight bridge → REST API 전환 | maesil-insight | /api/v1/settlements 엔드포인트 |
| P0 | biz_id ↔ operator_id 매핑 테이블 | hub DB | biz_insight_map 테이블 |
| P1 | 마켓 API 수집 비동기 큐 + Rate Limiter | hub APScheduler | Redis Queue / celery |
| P1 | migration 일별 검증 스크립트 | 별도 스크립트 | stock_snapshot 비교 |
| P1 | 감사 로그 표준화 (5종 컬럼) | hub + flow | audit_log 테이블 |
| P2 | 제조 버티컬 분리 아키텍처 설계 | 문서 | 독립 서비스 인터페이스 |
| P3 | agency/studio (hub 안정 후) | - | 동결 |
