# AI 아키텍처 리뷰 요청 프롬프트
> ChatGPT / Gemini에 아래 내용을 그대로 붙여넣으세요.

---

## 프롬프트 (복사해서 사용)

```
당신은 시니어 SaaS 아키텍트입니다.
아래는 한국의 식품/축산 유통 스타트업 "주식회사 매실"의 전체 시스템 설계 현황입니다.
아키텍처 관점에서 문제점, 리스크, 개선 방향을 솔직하게 조언해 주세요.

---

## 회사 및 제품 개요

- 업종: 식품/축산 유통 (온라인 판매 + 3PL 창고 운영 + 가공/제조)
- 현재 상태: 레거시 시스템을 신규 SaaS로 이전 중
- 개발 인원: 소규모 (1인 + AI 협업)
- 기술스택: Python/Flask + Supabase (PostgreSQL) + Render 배포

---

## 전체 시스템 목록 (8개)

| 시스템 | 역할 | DB | 상태 |
|---|---|---|---|
| maesil-hub | 신규 SaaS ERP (다테넌트, biz_id 격리) | Supabase C | 개발 중 |
| maesil-total | 레거시 ERP (현재 배마마 실운영) | Supabase A+B | 운영중/freeze |
| maesil-order | 3PL 창고 현장 운영 (패킹/화주사포털) | Supabase A+B 공유 | 운영 중 |
| maesil-insight | 광고분석/경쟁분석/정산SSOT (특허출원) | Supabase B (별도) | 운영 중 |
| maesil-studio | AI 브랜드/콘텐츠 자동생성 SaaS | Supabase (전용) | 개발 중 |
| maesil-agency | AI 멀티에이전트 플랫폼 (운영 AI비서) | agent_work 스키마 | Phase A |
| maesil | 공식 랜딩페이지 | 없음 (정적) | 운영 중 |
| maesil_accounting | 회계 독립 모듈 | - | 미사용 |

---

## 핵심 설계 결정 사항

### 1. 제품 버티컬 (3종)
- **온라인 업체용**: 마켓플레이스 연동, 주문/재고/정산 분석
- **3PL 운영사용**: 화주사 관리, 현장 패킹/출고, 화주사 포털
- **제조기업용**: BOM, 생산계획, 원가계산
→ 단일 플랫폼(hub)에서 플랜/메뉴 on-off로 세 버티컬 지원 예정

### 2. 시스템간 데이터 공유 방식
- **hub ↔ flow(order)**: Supabase DB 직접 공유 (같은 프로젝트, API 없음)
- **hub ↔ insight**: REST API (insight가 엔드포인트 노출, hub가 read-only 호출)
- **total → hub**: DB 브릿지 읽기 (이전 기간 한정 임시)
- **외부 마켓 API**: hub가 직접 수집 (스케줄러)

### 3. SSOT 원칙
- order_transactions: hub write, flow read-only
- stock_ledger: hub write + flow write (현장 입출고)
- api_settlements: insight SSOT, hub는 read-only
- packing_jobs: flow write, hub read

### 4. maesil-order(flow) 역할 정의
- 배마마가 3PL 사업을 운영하는 내부 창고 도구
- 외부 고객(타업체)은 hub만 봄, flow는 보이지 않음
- flow는 비즈니스 로직 없이 현장 UI만 (DB write는 hub Supabase 직접)
- 4개 blueprint만 유지: packing, field, client_portal, operator

### 5. 마이그레이션 로드맵
- Phase 1 (현재): hub SaaS 인프라 완성 (auth/billing/admin)
- Phase 2: ERP 핵심 기능 이식 (재고/주문/출고)
- Phase 3: 배마마 데이터 total→hub 이관
- Phase 4: maesil-total 종료

### 6. 현재 중복 문제
- services/marketplace/, option_matcher, channel_config 등이
  total / hub / order 3곳에 동일하게 존재
→ hub 단일화 후 나머지 제거 예정

---

## 질문 사항

1. **아키텍처 전반**: 이 구조에서 가장 큰 리스크/문제점은 무엇인가요?

2. **DB 공유 방식**: hub ↔ flow를 Supabase DB 직접 공유하는 방식이 올바른 선택인가요?
   REST API로 분리하는 게 나을 수도 있나요? 트레이드오프를 설명해 주세요.

3. **단일 플랫폼 vs 제품 분리**: 온라인/3PL/제조 세 버티컬을 단일 hub로 처리하는 것이
   현실적인가요? 아니면 처음부터 제품을 분리하는 게 나을까요?

4. **마이그레이션 리스크**: 운영 중인 레거시(total)를 신규(hub)로 이전하면서
   배마마가 실제로 사용 중인 상황에서 가장 위험한 구간은 어디인가요?

5. **우선순위**: 1인 개발 팀 기준으로 지금 당장 집중해야 할 것과
   나중으로 미뤄야 할 것을 구분해 주세요.

6. **놓친 것**: 이 설계에서 빠진 중요한 고려사항이 있나요?

솔직하고 날카롭게 조언해 주세요. 좋은 점보다 문제점과 리스크 위주로 답변해 주세요.
```
