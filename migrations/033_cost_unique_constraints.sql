-- 033_cost_unique_constraints.sql
-- product_costs / channel_costs upsert(ON CONFLICT)용 복합 유니크 제약.
-- 앱 upsert가 on_conflict=(biz_id,product_name)/(biz_id,channel) 사용 → 제약 필요.
-- 멀티테넌트: 테넌트별로 동일 품목명/채널 허용 위해 biz_id 포함 복합.

ALTER TABLE product_costs
    ADD CONSTRAINT uq_product_costs_biz_name UNIQUE (biz_id, product_name);

ALTER TABLE channel_costs
    ADD CONSTRAINT uq_channel_costs_biz_channel UNIQUE (biz_id, channel);
