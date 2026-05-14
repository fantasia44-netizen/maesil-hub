# 매실 허브 (Maesil Hub)

식품·축산업 전용 ERP/WMS SaaS.

## 포지셔닝

- **타겟**: 식품 제조·축산·수산업체 (영세~중소)
- **첫 고객**: 배마마, 해미예찬 (자사 운영)
- **확장**: 동일 업종 외부 고객 SaaS 제공

## 핵심 기능

- **WMS**: 입출고·재고관리·창고이동·소분
- **MES**: 생산관리·이력번호·소비기한
- **주문**: 다채널 통합 (쿠팡/스마트스토어/자사몰)·송장·패킹센터
- **거래처**: 매입/매출/거래명세서
- **회계**: 일일정산·매출 분석·세금계산서 연동
- **법규**: 축산물 이력번호·HACCP·식약처 신고 자동화

## 기술 스택

- **Backend**: Python 3.11, Flask, Supabase (PostgreSQL + RLS)
- **Frontend**: HTML/JS + Tailwind/Bootstrap (기존 유지)
- **인프라**: Render (서비스), Cloudflare (DNS/CDN), Supabase Storage
- **결제**: KakaoPay
- **모니터링**: Sentry

## SaaS 구조

- **멀티테넌트**: `biz_id` 기반 RLS 격리
- **플랜**: Starter / Pro / Enterprise (요금제별 features)
- **온보딩**: 가입 → 결제 → 자동 biz_id 생성 → 기본 데이터 시드

## 환경

| 환경 | 도메인 | Render | Supabase |
|---|---|---|---|
| Production | hub.maesil.net | (예정) | maesil-hub-prod |
| Staging | staging.hub.maesil.net | (예정) | maesil-hub-staging |

## 관련 레포

- `maesil-insight` — 광고분석 SaaS (가동 중). SaaS 인프라 코드 모태.
- `maesil-total` — 배마마/해미예찬 사내 운영 인스턴스 (freeze 예정).
- `maesil-flow` — 3PL 전용 (별도 SaaS).

## 로드맵

`doc/PROJECT_PLAN.md` 참고.

## 운영 원칙

- `main` 브랜치 직접 push 금지. PR + staging 검증 후 머지.
- DB 마이그레이션은 `migrations/` 순번대로, 모든 한글 SQL 리터럴 `U&'\XXXX'` Unicode escape.
- 모든 RPC는 `LANGUAGE sql/plpgsql STABLE SECURITY DEFINER` + 명시적 `statement_timeout`.
- 페이지네이션 누락 절대 금지 — 모든 SELECT는 RPC 또는 `_paginate_query`.
