-- 017_shipment_stats_fix.sql: add days + daily_avg to summary

CREATE OR REPLACE FUNCTION get_shipment_stats_agg(
    p_date_from TEXT,
    p_date_to   TEXT,
    p_location  TEXT DEFAULT NULL
)
RETURNS JSON
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public
AS $$
DECLARE
    v_loc_filter  BOOLEAN;
    v_total_qty   BIGINT;
    v_total_cnt   BIGINT;
    v_total_items BIGINT;
    v_days        INT;
    v_result      JSON;
BEGIN
    v_loc_filter := (p_location IS NOT NULL AND p_location <> '' AND p_location <> U&'\C804\CCB4');
    v_days := GREATEST(1, (p_date_to::DATE - p_date_from::DATE + 1));

    SELECT COUNT(*), COALESCE(SUM(ABS(qty)), 0), COUNT(DISTINCT product_name)
    INTO v_total_cnt, v_total_qty, v_total_items
    FROM stock_ledger
    WHERE type = 'SALES_OUT' AND status = 'active'
      AND transaction_date BETWEEN p_date_from::DATE AND p_date_to::DATE
      AND (NOT v_loc_filter OR location = p_location);

    SELECT json_build_object(
        'summary', json_build_object(
            'total_count', v_total_cnt,
            'total_qty',   v_total_qty,
            'total_items', v_total_items,
            'days',        v_days,
            'daily_avg',   ROUND(v_total_qty::NUMERIC / v_days, 1)
        ),
        'daily_totals', (
            SELECT COALESCE(json_agg(json_build_object('date',d,'count',c,'qty',q) ORDER BY d), '[]')
            FROM (
                SELECT transaction_date::TEXT d, COUNT(*) c, SUM(ABS(qty))::BIGINT q
                FROM stock_ledger
                WHERE type='SALES_OUT' AND status='active'
                  AND transaction_date BETWEEN p_date_from::DATE AND p_date_to::DATE
                  AND (NOT v_loc_filter OR location = p_location)
                GROUP BY transaction_date
            ) dd
        ),
        'monthly_totals', (
            SELECT COALESCE(json_agg(json_build_object('month',m,'count',c,'qty',q) ORDER BY m), '[]')
            FROM (
                SELECT to_char(transaction_date,'YYYY-MM') m, COUNT(*) c, SUM(ABS(qty))::BIGINT q
                FROM stock_ledger
                WHERE type='SALES_OUT' AND status='active'
                  AND transaction_date BETWEEN p_date_from::DATE AND p_date_to::DATE
                  AND (NOT v_loc_filter OR location = p_location)
                GROUP BY 1
            ) mm
        ),
        'location_breakdown', (
            SELECT COALESCE(json_agg(json_build_object('location',l,'count',c,'qty',q) ORDER BY q DESC), '[]')
            FROM (
                SELECT location l, COUNT(*) c, SUM(ABS(qty))::BIGINT q
                FROM stock_ledger
                WHERE type='SALES_OUT' AND status='active'
                  AND transaction_date BETWEEN p_date_from::DATE AND p_date_to::DATE
                  AND (NOT v_loc_filter OR location = p_location)
                GROUP BY location
            ) ll
        ),
        'category_breakdown', (
            SELECT COALESCE(json_agg(json_build_object('category',cat,'count',c,'total',q,'qty',q) ORDER BY q DESC), '[]')
            FROM (
                SELECT category cat, COUNT(*) c, SUM(ABS(qty))::BIGINT q
                FROM stock_ledger
                WHERE type='SALES_OUT' AND status='active'
                  AND transaction_date BETWEEN p_date_from::DATE AND p_date_to::DATE
                  AND (NOT v_loc_filter OR location = p_location)
                GROUP BY category
            ) cc
        ),
        'top_products', (
            SELECT COALESCE(json_agg(json_build_object('product_name',pn,'count',c,'qty',q,'total_qty',q) ORDER BY q DESC), '[]')
            FROM (
                SELECT product_name pn, COUNT(*) c, SUM(ABS(qty))::BIGINT q
                FROM stock_ledger
                WHERE type='SALES_OUT' AND status='active'
                  AND transaction_date BETWEEN p_date_from::DATE AND p_date_to::DATE
                  AND (NOT v_loc_filter OR location = p_location)
                GROUP BY product_name
                ORDER BY 3 DESC LIMIT 15
            ) pp
        ),
        'daily_location_totals',   '[]'::json,
        'monthly_location_totals', '[]'::json
    ) INTO v_result;

    RETURN v_result;
END;
$$;

GRANT EXECUTE ON FUNCTION get_shipment_stats_agg(TEXT,TEXT,TEXT) TO authenticated, service_role;
