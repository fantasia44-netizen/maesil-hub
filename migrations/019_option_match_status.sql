-- 019_option_match_status.sql
-- 미매칭 주문 관리를 위한 option_match_status 컬럼 추가
-- 자동수집 중 option_master에 없는 옵션값을 가진 주문을 별도 보관,
-- 사용자가 '미매칭 관리' 메뉴에서 수동매칭 후 CJ 송장 생성.
--
-- option_match_status 값:
--   'auto'      — 자동 매칭 성공 (기본값)
--   'unmatched' — 옵션마스터에 없음, 수동매칭 대기
--   'manual'    — 사용자가 수동으로 매칭 완료
--   'ignored'   — 취소/반품 등으로 무시 처리

-- 1) 컬럼 추가
ALTER TABLE order_transactions
  ADD COLUMN IF NOT EXISTS option_match_status TEXT DEFAULT 'auto';

-- 2) 기존 데이터: 빈 product_name & barcode 이면 unmatched 후보로 표시 (선택)
-- UPDATE order_transactions
--   SET option_match_status = 'unmatched'
--   WHERE option_match_status = 'auto'
--     AND (product_name IS NULL OR product_name = '')
--     AND (barcode IS NULL OR barcode = '')
--     AND is_outbound_done = false;

-- 3) 미매칭 조회 성능 인덱스
CREATE INDEX IF NOT EXISTS idx_ot_option_match_status
  ON order_transactions(biz_id, option_match_status)
  WHERE option_match_status != 'auto';

-- 4) order_shipping: 미매칭 상태값 허용 확인 (CHECK 제약이 있는 경우)
-- shipping_status 값에 '미매칭' 추가 (제약 없으면 그냥 insert 됨)
