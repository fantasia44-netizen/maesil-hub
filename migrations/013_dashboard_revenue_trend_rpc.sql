-- 013_dashboard_revenue_trend_rpc.sql
-- 대시보드 최근 N일 매출 추이 RPC
-- order_transactions 기준 일별 집계 (biz_id 격리 포함)

CREATE OR REPLACE FUNCTION get_dashboard_revenue_trend(
    p_days   INT     DEFAULT 7,
    p_biz_id BIGINT  DEFAULT NULL
)
RETURNS TABLE (
    date        TEXT,
    total       BIGINT,
    settlement  BIGINT
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT
        order_date::TEXT                    AS date,
        COALESCE(SUM(total_amount), 0)::BIGINT  AS total,
        COALESCE(SUM(settlement),  0)::BIGINT   AS settlement
    FROM order_transactions
    WHERE
        order_date >= (CURRENT_DATE - (p_days - 1) * INTERVAL '1 day')::DATE
        AND order_date <= CURRENT_DATE
        AND status != '취소'
        AND (p_biz_id IS NULL OR biz_id = p_biz_id)
    GROUP BY order_date
    ORDER BY order_date;
$$;

GRANT EXECUTE ON FUNCTION get_dashboard_revenue_trend(INT, UUID) TO authenticated, service_role;
