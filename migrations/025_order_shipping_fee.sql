-- 025_order_shipping_fee.sql
-- hub order_transactions에 shipping_fee 컬럼 추가 (total 스키마 정합).
-- 매출관리 정산(revenue_settlement_service) + get_order_revenue_agg(024)가 참조.
-- 기존 행은 0. biz_id 무관 컬럼(order_transactions가 이미 biz_id+RLS 보유).

ALTER TABLE order_transactions
    ADD COLUMN IF NOT EXISTS shipping_fee NUMERIC DEFAULT 0;
