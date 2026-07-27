# total → hub 이식 계획서 (MIGRATION_PLAN)

> 작성 2026-07-27. 근거: total↔hub 전 도메인 6영역 병렬 코드 분석(DB계층/주문·CJ·패킹/정산매출/재고·생산/코어·자동화/SaaS계층).
> 목적: 사내 단일테넌트 `maesil-total`이 6/2 이후 2개월간 쌓은 기능·버그픽스를 멀티테넌트 SaaS `maesil-hub`로 **테넌트 격리를 깨지 않고** 전량 이식.

---

## 0. 결정적 전제 — 두 레포는 "복사본+α"가 아니라 테넌시 모델이 다른 포크

| 축 | total | hub |
|---|---|---|
| 격리 | DB 물리 분리(사업자마다 별도 Supabase, `app.db_pool`) | 행단위 `biz_id`(단일 Supabase) |
| 요청 컨텍스트 | `g.db` = 사업자별 커넥션 | `g.biz_id` + tenant_guard 자동주입 |
| 세션 키 | `session['current_biz']`(str) | `session['current_biz_id']`(int/BIGINT) |
| 실제 격리 엔진 | 커넥션 분리로 자동 | `install_tenant_guard()` monkeypatch + 명시 `.eq('biz_id',...)` + RPC `p_biz_id`. **앱은 service_role로 접속 → RLS는 휴면(정의만 존재)** |
| 인증 | `auth.py` 단일파일, username | `auth/` 패키지, email, 회원가입/비번찾기/초대/회사선택(**hub가 성숙**) |
| 배포 | Docker(gunicorn CMD, workers 1) | Procfile(workers 2) |

**⇒ total 코드를 hub로 통째 복사 절대 금지.** `app.db_pool`/`current_biz`/`g.db` 패턴을 hub의 `get_db()`+`g.biz_id`로 번역하고, 신규 코드는 hub 테넌시 규약을 따라 재작성한다.

**⇒ 최상위 리스크**: service_role은 RLS를 우회하므로, 이식 코드가 `_with_biz`/`_inject_biz_id`/`p_biz_id`를 **한 곳이라도 빠뜨리면 즉시 전 테넌트 데이터 노출**.

---

## 1. 이식 불변식(체크리스트) — 모든 이식이 준수

1. **메서드 시그니처**: 신규/변경 db 메서드는 키워드전용 `*, biz_id=None` 추가(tenant_guard가 kwarg명으로 자동주입). 위치인자 금지.
2. **읽기 3단**: `biz_id=self._resolve_biz_id(biz_id)` → 직접쿼리 `self._with_biz(q, biz_id)` → RPC는 `p_biz_id` 주입.
3. **쓰기 2단**: `_resolve_biz_id` → `self._inject_biz_id(payload, biz_id)`. upsert `on_conflict`/존재확인 키에 **biz_id 포함**.
4. **캐시는 무조건 biz 키잉**: `{biz_id: ...}` dict. total의 전역 캐시(`_filter_cache`,`_trend_cache`,`_option_cache`)를 그대로 옮기면 크로스테넌트 서빙 → hub 방식(`_x_cache_by_biz`)으로 재작성.
5. **신규 테이블**: `biz_id BIGINT NOT NULL REFERENCES businesses(id)` + 인덱스 `(biz_id,...)` 선두 + UNIQUE에 biz_id 포함 + `003` 패턴 RLS 등록(service_role_all + tenant_isolation).
6. **RPC 정의**: `LANGUAGE sql|plpgsql STABLE SECURITY DEFINER SET statement_timeout='15s'` + `p_biz_id BIGINT DEFAULT NULL`(마지막 인자) + `AND (p_biz_id IS NULL OR biz_id=p_biz_id)`. `GRANT ... TO authenticated, service_role, anon`.
7. **한글 SQL 리터럴**: `U&'\XXXX'` 유니코드 이스케이프. (`'정상'`=`U&'\C815\C0C1'` 등)
8. **마이그 번호**: hub 다음은 **021**부터 순차. total 번호(026~034)와 정렬시키지 말 것.
9. **신규 블루프린트**: `xxx_bp = Blueprint(...)` `_bp` 접미사면 `blueprints/__init__.py::register_all()`이 자동등록(app.py 수정 불요). 파일명 `_` 시작은 스킵.
10. **스케줄러/배치**: `_resolve_biz_id`가 `has_app_context()` 지원 → 백그라운드는 **테넌트 루프 돌며 g.biz_id(또는 명시 biz_id) 세팅**. total의 `DEFAULT_BUSINESS` 단일전제 직역 금지.

### ⛔ 덮어쓰기 금지 파일 (SaaS 계층 파괴)
`app.py`, `config.py`, `models.py`의 인증부(hub는 `auth/models.HubUser` 사용, `models.User`는 dead), `db_supabase.py`(메서드 `biz_id` 파라미터 제거 금지), `auth/`·`db/` 패키지 전체, `blueprints/{billing,onboarding,admin_saas,team,unmatched}.py`, `services/{saas_config,portone}.py`, **`cj_shipping_service.py`**(상태머신 상이), `services/product_name.py`(hub가 NFKC로 앞섬).

### ✅ 이식하면 안 되는 것(hub가 이미 앞섬 → 회귀 금지)
- hub 인증/사이드바/DB풀 구조(total보다 성숙) — total auth·PAGE_REGISTRY 동적사이드바 이식 금지
- `adjustment.py` `validate_excel_upload()` 보안헬퍼(total 인라인 체크보다 강함)
- `stock.py` `today_kst()` 기본일자
- `product_name.py` canonical(NFKC), `option_matcher.py`(양쪽 동일)
- 필터옵션 RPC(hub 014가 p_biz_id판 보유)

---

## 2. 선결 아키텍처 결정 (이식 전 사장님 확정 필요) ★

이식 코드량과 무관하게, 아래 4개는 **먼저 결정**해야 진행 가능:

| # | 결정 사항 | 배경 | 권고 |
|---|---|---|---|
| D1 | **워커 중복 가드** | hub Procfile `--workers 2` + 스케줄러 모듈최상위 → 스케줄러 2중 실행(주문 이중수집·CJ 이중채번 위험). total 자동채번 이식의 **선행 필수** | Procfile `--workers 1` 또는 워커0 전용 가드(env/파일락) 먼저 도입 |
| D2 | **insight 연동 per-tenant** | maesil_bridge가 글로벌 env `MAESIL_OPERATOR_ID` 1개 → 전 테넌트가 하나의 insight 공유. 정산매출·광고비 화면이 테넌트 격리 불가 | 테넌트별 operator_id/자격증명을 `saas_config`(암호화)에 배선. 배마마 단일운영 동안은 유예 가능하나 외부고객 전 필수 |
| D3 | **CJ A/B 계정 방식** | hub=A/B를 인증계정 분기, total=단일인증+협력사코드(dlcm_cd). CJ 담당 확인결과라 **total 방식이 정답 유력** | total dlcm 방식으로 통일(hub `CJ_CUST_ID_B` 인증분기 폐기). ※사장님 확인 |
| D4 | **CJ 계정/발송인 멀티테넌트화** | CJ 인증·발송인이 양쪽 다 env 전역 → 테넌트별 CJ 계약 불가 | 상용화(외부고객) 전 과제. 배마마 단일 동안 유예 |

> D2·D4는 "배마마 단독 운영" 동안은 현행(글로벌)로 두고, **외부 신규고객 온보딩 직전**에 배선해도 됨. D1·D3은 이식 착수 전 결정 필요.

---

## 3. 도메인별 격차 요약

### 3-1. DB 계층 (최중량)
- hub는 85개 메서드에 biz_id 배선 완료 → **total 로직 델타만 병합**.
- 신규 필요: `get/set_app_setting`(→ `(biz_id,key)` PK 재설계), `upsert/delete_bom_master`(→ biz_id 매칭), `query_option_master_as_list`에 `수량배수` 1줄.
- 마이그 이식: 034(옵션배수)·031(세트배치)·033(CJ발송인)·029-030(app_settings 재설계)·028/032(버그픽스 병합)·027(shipping_fee 컬럼→hub 018 병합).
- ⚠️ total 전역 캐시 → biz 키잉 재작성 필수.

### 3-2. 주문/옵션/CJ/패킹
- **없음(신규)**: 옵션 수량배수, 로켓상품관리 탭, 자동옵션등록 배수감지, 패킹 카메라선택, HTML테이블 폴백, 무인 자동화 토글.
- **구버전**: 품목명 다수결통일(hub는 하드블록), CJ 라벨/PRT_ST/상세주소분리.
- ⚠️ **order_shipping 스키마 상이**(name/phone/memo vs recipient_name/recipient_phone + shipping_status). CJ 이식 전반에 파급.
- ⚠️ **hub 미매칭 파이프라인**(unmatched.py 상태머신 미매칭→접수→대기). **cj_shipping_service 통째 이식 금지, CJ payload 개선분만 발췌**.

### 3-3. 정산매출/회계
- accounting/finance/journal/pnl/tax_invoice = **100% 동일**(이식 불요).
- 실작업: 정산매출 탭(신규 service+template+RPC 027) + maesil_bridge 4함수 + revenue_service BOM 매출배분 + 월간분석 로직.
- ⚠️ D2(insight per-tenant) 블로커 — tenant-safe 부분 먼저, 정산탭은 D2 후.
- finance_repo 이미 biz-aware(저위험).

### 3-4. 재고/생산/창고/출고
- **저위험·고효과**: transfer 1000행캡 버그(3곳), get_stock_snapshot_agg 성능(22.8s→0.7s), 재고조정 off-by-one, 단건출고 라인병합·이중차감방지·비재고품목 스킵.
- **중위험**: outbound_alert_service(신규, 의존성 전부 hub 존재=클린이식) + 배너/'지금처리' 라우트, shipment_stats `_span_days`(+ **get_shipment_stats_agg에 p_biz_id 추가 신규마이그 필수**).
- **고위험**: 세트작업 개별수정/삭제/단위취소 + `set_batch_id` 컬럼(마이그 + `_resolve_set_batch` 교차테넌트 방어).
- 부수: 생산/자재/입고 엑셀 일괄등록.

### 3-5. 코어앱/인증/자동화/인프라
- 이식 대상 좁힘(인프라·자동화만): ProxyFix, `/healthz`, 보안헤더, 세션타임아웃, PERF로깅, IP rate-limit.
- auto_pipeline → hub sync_scheduler 패턴(전 채널 순회+채널 biz_id)으로 **재작성**. app_settings biz화 동반.
- Procfile gunicorn 튜닝(keep-alive 65/gthread/max-requests) — 저위험 고효용.
- ⚠️ requirements: supabase SDK 2.11(total) vs 2.30(hub) — DB 이식 시 API 호환 확인.

---

## 4. 이식 로드맵 (파도별)

리스크·의존도 순. 각 파도 끝에 배마마 데이터로 회귀 검증.

### Wave 0 — 인프라 저위험 승리 (테넌시 무관, 즉시)
- Procfile gunicorn 튜닝(keep-alive 65, gthread threads 4, max-requests 1000) **단 워커수는 D1 결정 후**
- `/healthz`, ProxyFix, 보안응답헤더, 세션 비활동 타임아웃, 느린요청 PERF 로깅
- 로그인 IP rate-limit
- **위험 낮음, 배포 효과 즉시(502 해결 등)**

### Wave 1 — 재고/출고 버그픽스 (저위험·고효과)
- transfer 1000행캡 3곳 → range 페이지네이션
- transfer 스냅샷 `get_stock_snapshot_agg`(+p_biz_id) 전환 (성능)
- 재고조정 off-by-one(+timedelta) 수정
- 단건출고 라인병합(이중통과) + 이중차감 방지(`skip_auto_stock_categories` 전달) + `is_non_stock_item`
- **DB 함수 무변경, 대부분 래퍼경유 → biz 적응 최소**

### Wave 2 — 옵션/주문 코어 (중위험)
- 마이그 034 qty_multiplier(+RLS 유지, `option_master.match_key` UNIQUE에 biz_id 포함 여부 **선검증**)
- `query_option_master_as_list`에 `수량배수` 추가
- 품목명 다수결 자동통일 + 자동옵션등록 배수감지(in-memory, 안전)
- 로켓상품관리 탭(master_prices/products 조회에 `_with_biz` 필수)
- HTML테이블 폴백, 거래처주문 품목명변경
- ※ hub `allow_unmatched` 경로를 정본으로 두고 다수결/배수 로직을 그 안에 병합

### Wave 3 — CJ 송장 (고위험, D3 선결)
- cj_client payload 발췌 이식: PRT_ST='02', FARE 선불, `dlcm_cd`, ORA-00001 중복처리, `_split_address` 번지분리
- `cj_shipping_service`는 **hub 상태머신 유지 + register_shipment 인자(fare_type/dlcm_cd)만 total로 교체**
- CJ 자체출력 라벨 오버레이(cj_label_generator) — **실물 감열지 프린터 캘리브레이션 필요, 현장 검증 없이 배포 불가**
- 마이그 033 cj_sender_* 컬럼(my_business)
- 패킹 카메라 선택(순수 프런트, 무관 — Wave 1으로 앞당겨도 무방)

### Wave 4 — 자동화 (D1 선결 필수)
- app_settings 테이블 `(biz_id,key)` PK + get/set 메서드 biz화 + RLS
- auto_pipeline → 전 테넌트 채널순회 재작성(sync_scheduler 템플릿) + 무인 토글 UI(shipping.py)
- shipment_stats `_span_days` + **get_shipment_stats_agg p_biz_id 신규마이그**
- memory_utils catchup 스케줄러

### Wave 5 — 세트작업 (고위험)
- 마이그 031 set_batch_id 컬럼(+partial index biz 동반) — `insert_stock_ledger` 미지컬럼 필터 확인
- 세트작업 개별수정/삭제/단위취소(분해복구) + 권한, `_resolve_set_batch` 교차테넌트 방어

### Wave 6 — 정산매출 (D2 선결)
- tenant-safe 먼저: revenue_service BOM 매출배분, 월간분석 로직, finance_repo 3줄, index/stats 템플릿
- D2 확정 후: RPC 027(+p_biz_id)·bridge 4함수(biz operator_id)·정산탭 화면·revenue.py 라우트

### Wave 7 — 부수
- 생산/자재/입고 엑셀 일괄등록 미리보기

---

## 5. 권장 착수 (파일럿)

**Wave 0(인프라) + Wave 1(재고버그픽스)를 첫 파일럿**으로 권고:
- 테넌시 적응이 최소(래퍼경유/무관)라 hub 멀티테넌트 패턴을 익히는 안전한 출발점
- 즉시 효과(502 해결, 창고이동 성능 22.8s→0.7s, 재고 오집계 버그 해소)
- 이 과정에서 "total→hub 번역 패턴"을 확립해 Wave 2+에 반복 적용

**착수 전 확정 필요**: D1(워커가드), D3(CJ 계정방식). D2·D4는 외부고객 온보딩 전까지 유예 가능.

---

## 6. 규모 감각
- hub 분기(5/14) 이후 total 변경: services 62 / blueprints 42 / templates 68 / db_supabase 23회 / migrations 22 (조사아티팩트 제외 ~170 코드파일)
- 실제 이식 surface는 그보다 좁음(회계 100% 동일, option_matcher/product_name 이식불요, 다수는 버그픽스 델타)
- 현실적 일정: Wave 0~1은 1~2일, 전체는 파도별 검증 포함 다일(多日). 순차 진행 + 파도마다 배마마 회귀검증 권장.
