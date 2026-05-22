-- 016_fix_rpc_types.sql — fix TEXT->DATE cast + column name corrections

-- ── 1. get_aggregation_summary ────────────────────────────────────────────
CREATE OR REPLACE FUNCTION get_aggregation_summary(
    p_date_from TEXT,
    p_date_to   TEXT
)
RETURNS JSON
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public
AS $$
DECLARE
    v_out_count     BIGINT; v_out_items BIGINT; v_out_qty BIGINT;
    v_out_locations JSONB;  v_in_count  BIGINT; v_prod_count BIGINT;
    v_rev_count     BIGINT; v_rev_total BIGINT;
    v_rev_by_cat    JSONB;  v_rev_by_ch JSONB;
BEGIN
    SELECT COUNT(*), COUNT(DISTINCT product_name), COALESCE(SUM(ABS(qty)),0)
    INTO v_out_count, v_out_items, v_out_qty
    FROM stock_ledger
    WHERE type = 'SALES_OUT'
      AND transaction_date BETWEEN p_date_from::DATE AND p_date_to::DATE
      AND (status IS NULL OR status = 'active');

    SELECT COALESCE(jsonb_object_agg(location, total_qty), '{}')
    INTO v_out_locations
    FROM (
        SELECT location, SUM(ABS(qty))::BIGINT AS total_qty
        FROM stock_ledger
        WHERE type = 'SALES_OUT'
          AND transaction_date BETWEEN p_date_from::DATE AND p_date_to::DATE
          AND (status IS NULL OR status = 'active')
          AND location IS NOT NULL
        GROUP BY location
    ) t;

    SELECT COUNT(*) INTO v_in_count
    FROM stock_ledger
    WHERE type IN ('INBOUND', 'ETC_IN', 'INIT')
      AND transaction_date BETWEEN p_date_from::DATE AND p_date_to::DATE
      AND (status IS NULL OR status = 'active');

    SELECT COUNT(*) INTO v_prod_count
    FROM stock_ledger
    WHERE type = 'PRODUCTION'
      AND transaction_date BETWEEN p_date_from::DATE AND p_date_to::DATE
      AND (status IS NULL OR status = 'active');

    SELECT COUNT(*), COALESCE(SUM(total_amount),0)::BIGINT
    INTO v_rev_count, v_rev_total
    FROM order_transactions
    WHERE order_date BETWEEN p_date_from::DATE AND p_date_to::DATE
      AND status <> U&'\CDE8\C18C';

    SELECT COALESCE(jsonb_object_agg(channel, ch_total), '{}')
    INTO v_rev_by_cat
    FROM (
        SELECT COALESCE(channel,'기타') channel, SUM(total_amount)::BIGINT ch_total
        FROM order_transactions
        WHERE order_date BETWEEN p_date_from::DATE AND p_date_to::DATE
          AND status <> U&'\CDE8\C18C'
        GROUP BY channel
    ) t;

    v_rev_by_ch := v_rev_by_cat;

    RETURN json_build_object(
        'outbound', json_build_object(
            'count', v_out_count, 'items', v_out_items,
            'qty', v_out_qty, 'locations', v_out_locations
        ),
        'inbound_count', v_in_count,
        'production_count', v_prod_count,
        'revenue', json_build_object(
            'count', v_rev_count, 'total', v_rev_total,
            'by_category', v_rev_by_cat, 'by_channel', v_rev_by_ch
        )
    );
END;
$$;
GRANT EXECUTE ON FUNCTION get_aggregation_summary(TEXT,TEXT) TO authenticated, service_role;


-- ── 2. get_channel_orders_agg ─────────────────────────────────────────────
CREATE OR REPLACE FUNCTION get_channel_orders_agg(
    p_date_from TEXT,
    p_date_to   TEXT
)
RETURNS JSON
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public
AS $$
BEGIN
    RETURN (
        SELECT json_build_object(
            'rows',
            COALESCE(json_agg(row_data ORDER BY row_date), '[]'::json)
        )
        FROM (
            SELECT
                order_date::TEXT AS row_date,
                json_build_object(
                    'date', order_date::TEXT,
                    'groups', jsonb_object_agg(channel, cnt)
                ) AS row_data
            FROM (
                SELECT order_date, COALESCE(channel, U&'\AD6C\B9E4') AS channel, COUNT(*) AS cnt
                FROM order_transactions
                WHERE order_date BETWEEN p_date_from::DATE AND p_date_to::DATE
                  AND status <> U&'\CDE8\C18C'
                GROUP BY order_date, channel
            ) sub
            GROUP BY order_date
        ) outer_q
    );
END;
$$;
GRANT EXECUTE ON FUNCTION get_channel_orders_agg(TEXT,TEXT) TO authenticated, service_role;


-- ── 3. get_monthly_sales_agg ──────────────────────────────────────────────
CREATE OR REPLACE FUNCTION get_monthly_sales_agg(
    p_year   INT,
    p_month  INT    DEFAULT NULL,
    p_biz_id BIGINT DEFAULT NULL
)
RETURNS JSON
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public
AS $$
    SELECT COALESCE(
        jsonb_object_agg(
            product_name,
            jsonb_build_object('total_qty', total_qty, 'total_amount', total_amount)
        ),
        '{}'::jsonb
    )::JSON
    FROM (
        SELECT product_name,
               SUM(qty)::BIGINT          AS total_qty,
               SUM(total_amount)::BIGINT AS total_amount
        FROM order_transactions
        WHERE EXTRACT(YEAR FROM order_date) = p_year
          AND (p_month IS NULL OR EXTRACT(MONTH FROM order_date) = p_month)
          AND (p_biz_id IS NULL OR biz_id = p_biz_id)
          AND status <> U&'\CDE8\C18C'
        GROUP BY product_name
    ) t;
$$;
GRANT EXECUTE ON FUNCTION get_monthly_sales_agg(INT,INT,BIGINT) TO authenticated, service_role;


-- ── 4. get_shipment_stats_agg ─────────────────────────────────────────────
CREATE OR REPLACE FUNCTION get_shipment_stats_agg(
    p_date_from TEXT,
    p_date_to   TEXT,
    p_location  TEXT DEFAULT NULL
)
RETURNS JSON
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public
AS $$
DECLARE
    v_loc_filter BOOLEAN;
    v_result     JSON;
BEGIN
    -- NULL or empty or '전체' means no location filter
    v_loc_filter := (p_location IS NOT NULL AND p_location <> '' AND p_location <> U&'\C804\CCB4');

    SELECT json_build_object(
        'summary', json_build_object(
            'total_count', COUNT(*),
            'total_qty',   COALESCE(SUM(ABS(qty)),0)::BIGINT,
            'total_items', COUNT(DISTINCT product_name)
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
            SELECT COALESCE(json_agg(json_build_object('category',cat,'count',c,'qty',q) ORDER BY q DESC), '[]')
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
            SELECT COALESCE(json_agg(json_build_object('product_name',pn,'count',c,'total_qty',q) ORDER BY q DESC), '[]')
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
    ) INTO v_result
    FROM stock_ledger
    WHERE type='SALES_OUT' AND status='active'
      AND transaction_date BETWEEN p_date_from::DATE AND p_date_to::DATE
      AND (NOT v_loc_filter OR location = p_location);

    RETURN v_result;
END;
$$;
GRANT EXECUTE ON FUNCTION get_shipment_stats_agg(TEXT,TEXT,TEXT) TO authenticated, service_role;


-- ── 5. get_pnl_monthly_agg  (api_settlements only — no tax_invoices/expenses) ──
CREATE OR REPLACE FUNCTION get_pnl_monthly_agg(
    p_date_from  TEXT,
    p_date_to    TEXT,
    p_year_month TEXT   DEFAULT NULL,
    p_biz_id     BIGINT DEFAULT NULL
)
RETURNS JSON
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public
AS $$
DECLARE
    v_revenue        BIGINT; v_settlement   BIGINT;
    v_commission     BIGINT; v_shipping_cost BIGINT;
    v_coupon         BIGINT;
BEGIN
    SELECT
        COALESCE(SUM(gross_sales),0)::BIGINT,
        COALESCE(SUM(net_settlement),0)::BIGINT,
        COALESCE(SUM(total_commission),0)::BIGINT,
        COALESCE(SUM(shipping_fee_cost),0)::BIGINT,
        COALESCE(SUM(coupon_discount + point_discount),0)::BIGINT
    INTO v_revenue, v_settlement, v_commission, v_shipping_cost, v_coupon
    FROM api_settlements
    WHERE settlement_date BETWEEN p_date_from::DATE AND p_date_to::DATE
      AND (p_biz_id IS NULL OR biz_id = p_biz_id);

    RETURN json_build_object(
        'revenue',          v_revenue,
        'settlement',       v_settlement,
        'commission',       v_commission,
        'shipping_cost',    v_shipping_cost,
        'coupon_discount',  v_coupon,
        'b2b',      0,
        'purchase', 0,
        'expenses', 0,
        'ad_cost',  0
    );
END;
$$;
GRANT EXECUTE ON FUNCTION get_pnl_monthly_agg(TEXT,TEXT,TEXT,BIGINT) TO authenticated, service_role;


-- ── 6. get_settlement_summary_by_month  (maesil_bridge: biz_id=p_operator_id) ──
CREATE OR REPLACE FUNCTION get_settlement_summary_by_month(
    p_operator_id INT,
    p_month       TEXT
)
RETURNS TABLE (
    channel        TEXT,
    gross_sales    BIGINT,
    net_settlement BIGINT,
    commission     BIGINT
)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public
AS $$
    SELECT
        channel,
        SUM(gross_sales)::BIGINT      AS gross_sales,
        SUM(net_settlement)::BIGINT   AS net_settlement,
        SUM(total_commission)::BIGINT AS commission
    FROM api_settlements
    WHERE biz_id = p_operator_id
      AND to_char(settlement_date, 'YYYY-MM') = p_month
    GROUP BY channel
    ORDER BY gross_sales DESC;
$$;
GRANT EXECUTE ON FUNCTION get_settlement_summary_by_month(INT,TEXT) TO authenticated, service_role;


-- ── 7. get_dashboard_full  (date cast fix) ────────────────────────────────
CREATE OR REPLACE FUNCTION get_dashboard_full(
    p_date        TEXT,
    p_month_start TEXT
)
RETURNS JSON
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public
AS $$
DECLARE
    v_today_orders  BIGINT; v_pending_ship  BIGINT;
    v_month_revenue BIGINT; v_month_orders  BIGINT;
BEGIN
    SELECT COUNT(*) INTO v_today_orders
    FROM order_transactions WHERE order_date = p_date::DATE;

    SELECT COUNT(*) INTO v_pending_ship
    FROM order_transactions
    WHERE is_outbound_done = FALSE AND status <> U&'\CDE8\C18C';

    SELECT COUNT(*), COALESCE(SUM(settlement),0)::BIGINT
    INTO v_month_orders, v_month_revenue
    FROM order_transactions
    WHERE order_date BETWEEN p_month_start::DATE AND p_date::DATE
      AND status <> U&'\CDE8\C18C';

    RETURN json_build_object(
        'today_orders',  v_today_orders,
        'pending_ship',  v_pending_ship,
        'month_orders',  v_month_orders,
        'month_revenue', v_month_revenue
    );
END;
$$;
GRANT EXECUTE ON FUNCTION get_dashboard_full(TEXT,TEXT) TO authenticated, service_role;
