-- 014_perf_rpc.sql
-- 성능 개선 RPC 2종
-- 1) get_filter_options   : stock_ledger DISTINCT 조회 (페이지네이션 35회 → 1회)
-- 2) get_dashboard_kpi    : 대시보드 KPI 5개 순차 API → 1회 통합

-- ── 1. get_filter_options ──────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION get_filter_options(
    p_biz_id BIGINT DEFAULT NULL
)
RETURNS TABLE (
    location    TEXT,
    category    TEXT
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT DISTINCT location, category
    FROM stock_ledger
    WHERE
        status = 'active'
        AND (p_biz_id IS NULL OR biz_id = p_biz_id)
    ORDER BY location, category;
$$;

GRANT EXECUTE ON FUNCTION get_filter_options(BIGINT) TO authenticated, service_role;


-- ── 2. get_dashboard_kpi ──────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION get_dashboard_kpi(
    p_biz_id     BIGINT,
    p_today      DATE,
    p_month_start DATE
)
RETURNS JSON
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_today_orders  BIGINT;
    v_pending_ship  BIGINT;
    v_stock_items   BIGINT;
    v_month_revenue BIGINT;
BEGIN
    -- 오늘 주문 수
    SELECT COUNT(*) INTO v_today_orders
    FROM order_transactions
    WHERE biz_id = p_biz_id
      AND order_date = p_today;

    -- 미출고
    SELECT COUNT(*) INTO v_pending_ship
    FROM order_transactions
    WHERE biz_id = p_biz_id
      AND is_outbound_done = FALSE
      AND status != '취소';

    -- 재고 품목 종류 (active stock_ledger DISTINCT product_name)
    SELECT COUNT(DISTINCT product_name) INTO v_stock_items
    FROM stock_ledger
    WHERE biz_id = p_biz_id
      AND status = 'active';

    -- 이달 정산 매출
    SELECT COALESCE(SUM(settlement), 0) INTO v_month_revenue
    FROM order_transactions
    WHERE biz_id = p_biz_id
      AND order_date >= p_month_start
      AND order_date <= p_today
      AND status != '취소';

    RETURN json_build_object(
        'today_orders',  v_today_orders,
        'pending_ship',  v_pending_ship,
        'stock_items',   v_stock_items,
        'month_revenue', v_month_revenue
    );
END;
$$;

GRANT EXECUTE ON FUNCTION get_dashboard_kpi(BIGINT, DATE, DATE) TO authenticated, service_role;
