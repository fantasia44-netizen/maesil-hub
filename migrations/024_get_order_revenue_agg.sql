-- 024_get_order_revenue_agg.sql
-- Ported from maesil-total migration 027, adapted to hub multi-tenant.
-- Speeds up /revenue query_revenue: SQL GROUP BY instead of row-by-row fetch.
-- hub adds p_biz_id BIGINT (last arg) + tenant filter so cross-tenant totals
-- cannot leak. Korean literal '정상' written as Unicode escape per hub rule.

DROP FUNCTION IF EXISTS get_order_revenue_agg(text, text);
DROP FUNCTION IF EXISTS get_order_revenue_agg(text, text, BIGINT);

CREATE OR REPLACE FUNCTION get_order_revenue_agg(
    p_date_from text,
    p_date_to   text,
    p_biz_id    BIGINT DEFAULT NULL
)
RETURNS json
LANGUAGE sql
STABLE
SECURITY DEFINER
SET statement_timeout = '15s'
AS $$
  SELECT coalesce(json_agg(t), '[]'::json)
  FROM (
    SELECT order_date,
           channel,
           product_name,
           SUM(qty)                          AS qty,
           SUM(total_amount)                 AS total_amount,
           SUM(settlement)                   AS settlement,
           SUM(commission)                   AS commission,
           SUM(COALESCE(discount_amount, 0)) AS discount_amount,
           SUM(COALESCE(shipping_fee, 0))    AS shipping_fee
    FROM order_transactions
    WHERE status = U&'\C815\C0C1'          -- '정상'
      AND order_date >= p_date_from::date
      AND order_date <= p_date_to::date
      AND (p_biz_id IS NULL OR biz_id = p_biz_id)
    GROUP BY order_date, channel, product_name
  ) t;
$$;

-- (biz_id, status, order_date) 복합 인덱스 (테넌트별 집계 스캔 가속)
CREATE INDEX IF NOT EXISTS idx_ot_biz_status_date
  ON order_transactions (biz_id, status, order_date);

GRANT EXECUTE ON FUNCTION get_order_revenue_agg(text, text, BIGINT)
  TO authenticated, service_role, anon;
