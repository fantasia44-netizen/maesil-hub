# maesil-total ERP/WMS 핵심 분석

작성일: 2026-05-14
대상 레포: `C:\maesil-total` (운영 중인 ERP/WMS, 배마마/쿡대디 2-tenant)
용도: maesil-hub로 이식할 ERP 핵심 모듈 식별 + 멀티테넌시 변환 포인트 도출

---

## 멀티테넌시 현황 (중요)

**maesil-total은 진짜 멀티테넌트가 아님.** 사업장(`baemama`, `cookdaddy`)별로 **별도 Supabase 프로젝트**를 두고, `app.db_pool[biz_id]`로 분기.

- `config.py:21-46` `BUSINESSES` dict — 사업장별 SUPABASE_URL/KEY 분리
- `app.py:36-56` — 시작 시 모든 사업장 DB 풀 초기화
- `db_utils.get_db()` — 현재 세션의 사업장 DB 반환
- `db_supabase._with_biz(query, biz_id)` (`db_supabase.py:176-179`) — biz_id 컬럼 필터 헬퍼는 **존재**하지만, 마이그레이션에 `biz_id` 컬럼이 **없음** (`grep biz_id migrations/` → 0건). 즉 미사용/예비 코드.
- `_test_biz_id_filter.py` — 컬럼 추가 시 동작 확인용 시뮬레이션 테스트만 존재.
- `blueprints/trade.py`의 `my_biz_id`는 **거래 상대방(우리 회사 사업자)** 용 — SaaS 테넌트와 무관.

**hub 이식 시: 이 부분 전면 재설계.** 단일 Supabase + biz_id 컬럼 필수 + RLS로 변환.

---

## F. 재고/수불 (WMS Core)

### 핵심 파일
- `db_supabase.py` — `insert_stock_ledger`, `upsert_stock_ledger_idempotent`, `query_stock_ledger`, `delete_stock_ledger_*` (라인 283~600)
- `services/ledger_service.py` — 원장 조회, 전일이월 계산
- `services/order_to_stock_service.py` — 주문→재고 차감 (FIFO)
- `services/inbound_service.py`, `outbound_service.py`, `etc_outbound_service.py` — 입고/판매출고/기타출고
- `services/adjustment_service.py` — 재고조정
- `services/repack.py`, `set_assembly.py` — 소분/세트 분해조립
- `services/transfer.py` — 창고이동
- `migrations/004_stock_snapshot_rpc.sql` — `get_stock_snapshot_agg` 핵심 RPC
- `migrations/008_enforce_canonical_product_name.sql` — canonical 제약

### Stock Ledger 이벤트 모델
`models.INV_TYPE_LABELS` (`models.py:14-26`):
```
INBOUND        입고
PRODUCTION     생산(산출)
PROD_OUT       생산출고(투입)
SALES_OUT      판매출고
SALES_RETURN   판매반품
MOVE_OUT       이동출고
MOVE_IN        이동입고
INIT           기초재고
REPACK_OUT     소분투입
REPACK_IN      소분산출
SET_OUT        세트투입
SET_IN         세트산출
ETC_OUT        기타출고
ETC_IN         기타입고
ADJUST         재고조정
```
event-sourcing 패턴 — 모든 변동은 `stock_ledger`에 INSERT, 잔고는 SUM(qty) 계산.

### canonical product_name
- `services/product_name.canonical()` — 모든 product_name 저장/조회 직전 호출. 공백 제거 + 정규화.
- `db_supabase._normalize_product_names()`가 insert 직전 강제 적용.
- 마이그 008로 trigger 강제.

### 재고 스냅샷
- 핵심 RPC `get_stock_snapshot_agg(p_date_to, p_split_mode)` (`migrations/004`):
  - 일자 기준 모든 type SUM(qty) 집계
  - `(product_name, location, category, storage_method, unit)` 그룹
  - split_mode = `none|expiry|manufacture|lot_number`로 추가 분리
  - 같은 (product_name, location) 내 빈 category/storage_method는 같은 그룹 내 비어있지 않은 값으로 상속 (window 함수)
  - 15s timeout, LIMIT 5000

### FIFO/제조일별 차감
- `services/order_to_stock_service.py:592-746` — SALES_OUT 발생 시 같은 product_name 잔고를 FIFO(오래된 manufacture_date 먼저) 분할 차감.
- `event_uid` 기반 idempotent insert (`db_supabase.py:315-372`) — 같은 주문이 두 번 들어와도 중복 INSERT 방지.

### Idempotency
- `event_uid`: 외부 source(채널 주문번호 등)로 결정적 ID. 이미 있으면 skip.
- 생산 fingerprint cache (`blueprints/production.py:40-83`): SHA256(date+loc+items+user_id) 5분 dedup → 더블클릭 방지. 메모리 캐시(단일 인스턴스 한정).

### maesil-hub 이식 포인트
- stock_ledger 스키마 그대로 + `biz_id BIGINT NOT NULL` 추가, **모든 인덱스 (biz_id, ...)**.
- `get_stock_snapshot_agg` RPC에 `p_biz_id BIGINT` 첫 파라미터 추가, 내부 WHERE에 biz_id 필터.
- canonical 트리거에 biz_id 인지 강제 (다른 biz의 같은 이름 product 충돌 방지).
- production fingerprint cache는 메모리 → Redis로 (멀티 인스턴스 대비).

---

## G. 생산 (MES)

### 핵심 파일
- `blueprints/production.py` — 생산 폼/엑셀업로드/PDF/이력
- `services/actual_cost_service.py` — 생산일지 기반 실제 투입 원가 계산 (PROD_OUT 합계 / PRODUCTION qty)
- `services/bom_cost_service.py` — BOM 기반 표준원가
- `services/yield_mgmt.py` (blueprint) — 수율 관리

### Production Batch 흐름
1. UI에서 산출 제품(들) + 각 제품의 투입 원료(들) 입력
2. `_items_fingerprint()` SHA256 → `_dedup_check()` 5분 캐시 hit 시 차단
3. 같은 transaction_date에 PRODUCTION (제품별 +qty) + PROD_OUT (재료별 -qty) 행 다수 INSERT
4. `manufacture_date`/`expiry_date`/`lot_number` 보존 (이력 추적)

### 약점
- fingerprint cache가 단일 프로세스 메모리. 멀티 인스턴스 시 중복 가능.
- 이력번호(축산물용)는 별도 테이블 미정 — `project_mes_livestock.md` 메모로 설계만 존재.

### maesil-hub 이식
- 그대로 가져오되 fingerprint Redis 이전.
- production batch에 `production_batch_id UUID` 추가, 같은 배치의 PRODUCTION/PROD_OUT 행을 묶어서 조회/취소 가능하도록 보강.

---

## H. 주문/출고 (Order/Outbound)

### 핵심 파일
- `services/order_processor.py` — 채널 엑셀 파싱 → 정규화 (헤더 자동 검출)
- `services/api_order_converter.py` — 채널 API 응답 → order_transactions 변환
- `services/marketplace/` — 채널 클라이언트 (cafe24/coupang/esm/kakao/naver/st11)
- `services/marketplace_sync_service.py` — 채널 수집 오케스트레이션 (graceful shutdown 지원)
- `services/option_matcher.py` — 옵션마스터 매칭 (canonical + NFKC)
- `services/order_to_stock_service.py` — 주문 → SALES_OUT
- `services/outbound_service.py` — 일자/창고/품목 단위 출고 처리
- `blueprints/orders.py`, `orders_api.py`, `outbound.py`, `packing.py`, `shipment.py`, `shipping.py`
- `services/cj_shipping_service.py` — CJ대한통운 송장 API
- `migrations/012_outbound_list_rpc.sql`, `014_shipment_stats_unified.sql`, `016_shipment_stats_volatile.sql`

### 주요 흐름
```
채널 수집 (API or 엑셀) → order_transactions 행 생성 (status=pending)
  → option_matcher로 표준품목명 매칭 (raw 옵션명 → 품목명)
  → 매칭 성공 시 → outbound 처리 가능
  → outbound 확정 → stock_ledger SALES_OUT 차감 (FIFO + event_uid)
  → 송장번호 입력 (수동 or CJ API)
  → status=shipped, is_outbound_done=true
```

### 다채널 통합
- `services/channel_config.py`로 각 채널의 컬럼명/필수필드/암호화여부/매출카테고리 정의.
- option_matcher가 채널별 매칭 키 규칙 다르게 적용:
  - 쿠팡: 옵션 없으면 상품명만, 있으면 결합
  - 옥션/G마켓: 옵션 `/` 앞부분
  - 스마트스토어/자사몰/오아시스/11번가/카카오: 옵션 유효하면 옵션, 아니면 상품명

### Repos (db/ 분리)
2026-03-23 분리. 도메인별 Repository:
- `auth_repo`, `finance_repo`, `hr_repo`, `inventory_repo`, `marketplace_repo`, `orders_repo`, `outbound_repo`, `packing_repo`, `product_repo`, `settlement_repo`, `shipping_repo`, `trade_repo`
- 각각 `BaseRepo` (`db/base.py`) 상속 — `_safe_execute` 공통 에러 핸들러.
- `db_supabase.py`는 여전히 hub 역할 (3000+ lines). 단계적 분리 진행 중.

### maesil-hub 이식
- 채널별 어댑터(`services/marketplace/`) 그대로 가져옴 — 각 채널 인증/페이징 노하우 살아있음.
- `option_matcher` 그대로.
- `order_transactions`/`stock_ledger`에 biz_id 추가.
- `option_master`(매칭 표) biz_id 격리 — 사업장마다 옵션 매핑 다름.
- 채널 API 키는 `marketplace_api_config(biz_id, channel, credentials_jsonb_encrypted)` 테이블로 격리 + Fernet 암호화.

---

## I. 거래처/매입/매출 (Partners / Trade / Revenue)

### 핵심 파일
- `blueprints/trade.py` — 거래처 + 수동 거래 + 거래명세서 PDF + 발주서
- `db/trade_repo.py` — `query_partners`, `query_manual_trades`, `insert_manual_trade`
- `blueprints/revenue.py` — 매출 조회 (다채널 합산)
- `db_supabase.py:722-`, `db/finance_repo.py` — `query_revenue_in_range` (order_transactions + daily_revenue 합산)
- `services/popbill_service.py` — 세금계산서 자동 발행
- `services/codef_service.py` — CODEF 은행 거래 자동 수집

### manual_trades 흐름
- 거래처 직접 출고: UI에서 거래처 + 품목 + 수량 + 단가 입력 → manual_trades INSERT + stock_ledger SALES_OUT INSERT + daily_revenue INSERT (3중 동기화)
- 매입(purchase_orders): 발주서 → 입고 시 stock_ledger INBOUND
- 거래명세서 PDF: ReportLab + HACCP 템플릿 (`templates/`)

### 매출 계산
`db_supabase:722-799` `query_revenue_in_range`:
- `order_transactions` (온라인 채널) + `daily_revenue` (거래처/로켓 등) 합산
- DB 전환일 이전/이후 분기 처리
- 이중집계 방지를 위해 카테고리 필터 분기

### 약점
- daily_revenue/order_transactions 이중 SSOT — 카테고리 필터 누락 시 이중 집계 사고 위험. 이미 사고 1건 있었음 (`feedback_*` 메모 참조).

### maesil-hub 이식
- manual_trades + daily_revenue + stock_ledger 3중 동기화는 **DB 트랜잭션 또는 RPC**로 강제 (현재는 Python 순차). biz_id 추가.
- 매출 SSOT를 `revenue_unified` 단일 view로 정리 권고.

---

## J. 공통 인프라

### db_utils / repo 패턴
- `db_utils.get_db()`가 현재 사업장 DB 반환. blueprint마다 이 헬퍼 사용.
- `db/base.py:BaseRepo` — `_safe_execute(name, func, ...)` 공통 try/except.
- `_paginate_query(table, builder)` — Supabase 1000행 limit 회피용 OFFSET 페이지네이션.

### 마이그레이션 RPC 패턴 (12~18 핵심)
- `LANGUAGE sql STABLE SECURITY DEFINER SET statement_timeout = '15s'` — 일관된 헤더
- `GRANT EXECUTE ON FUNCTION ... TO authenticated, service_role` — 명시적 권한
- 한글 리터럴: 015 마이그에서 일괄 Unicode escape (`U&'\C815\C0C1'`) — 이전 클립보드 깨짐 사고 수정
- `migrations/STATUS.md` — 누적 배포 상태 기록 (insight 없음, total 있음)

### canonical / normalize / validation
- `services/product_name.canonical()` — 단일 진실. 모든 layer에서 호출.
- `services/marketplace_validation_service.py` — 채널 데이터 정합성 검증
- `services/outbound_validation_service.py` — 출고 전 재고 잔량/마감 확인
- DB trigger로 strict 강제 (008).

### 페이지네이션
- `db/base._paginate_query` — Supabase 1000행 limit 회피.
- 일부 직접 query는 LIMIT 1000 누락 사고 이력 (`feedback` 메모).

### biz_id 처리 (현재 상태)
- `_with_biz(query, biz_id)` 헬퍼는 50+ 메서드에 추가됨 (`db_supabase.py`, `db/auth_repo.py`, `db/finance_repo.py` 등).
- **하지만 `migrations/`에 biz_id 컬럼 추가 SQL 없음** → DB 컬럼 부재로 실제 동작 안 함, 모두 `biz_id=None` 호출.
- = 마이그 준비 단계의 미완성 작업.

---

## maesil-hub 이식 우선순위 (Phase 2)

### Tier 1 — 즉시 가져와도 안전 (canonical 모듈)
- `services/product_name.py` (canonical)
- `services/option_matcher.py`
- `services/channel_config.py`
- `services/tz_utils.py`
- `db/base.py:BaseRepo`

### Tier 2 — biz_id 변환 후 가져옴 (코어 비즈니스)
- `db_supabase.py`의 stock_ledger 메서드 → `db/inventory_repo.py`로 분리하면서 biz_id 강제
- `services/order_to_stock_service.py`
- `services/inbound_service.py` / `outbound_service.py` / `etc_outbound_service.py`
- `services/marketplace/` 채널 클라이언트 (인증정보는 Fernet 암호화)
- `migrations/004` 스냅샷 RPC + biz_id

### Tier 3 — 재설계 (구조 개선 후)
- 매출 SSOT 통합 (revenue_unified view)
- production batch_id 도입 (현재는 그룹핑 약함)
- manual_trades 3중 동기화 → RPC로 atomicity

### Tier 4 — 미루기 (당장 불필요)
- `services/codef_service.py` (은행 자동수집) — 옵션
- `services/popbill_service.py` (세금계산서) — 옵션
- `services/cj_shipping_service.py` — 옵션 (수동 송장 우선)
- 회계/저널 (배마마 전용 누적 기능)

---

## 멀티테넌시 변환 체크리스트 (Phase 2 완료 기준)

- [ ] 모든 비즈니스 테이블 `biz_id BIGINT NOT NULL`
- [ ] 모든 인덱스 `(biz_id, ...)` 시작
- [ ] 모든 UNIQUE 제약 `(biz_id, ...)`로 재정의 (예: `option_master.match_key` → `(biz_id, match_key)`)
- [ ] 모든 RPC `p_biz_id BIGINT` 첫 파라미터
- [ ] 모든 RPC 내부 `WHERE biz_id = p_biz_id`
- [ ] RLS 정책 `USING (biz_id = current_setting('app.current_biz_id')::bigint)`
- [ ] 모든 Python repo 메서드 `biz_id` 필수 인자 (default 제거)
- [ ] `before_request` 훅에서 `g.biz_id` + `set_config('app.current_biz_id', g.biz_id)` 세팅
- [ ] canonical 트리거 biz_id 인지
- [ ] event_uid 유니크 제약 → `(biz_id, event_uid)` 복합
- [ ] production fingerprint cache → Redis (멀티 인스턴스 대비)
