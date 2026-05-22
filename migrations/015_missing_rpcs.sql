-- 015_missing_rpcs.sql
-- Missing RPC functions referenced in Python services but not yet defined in DB.
--
-- 1.  get_aggregation_summary          (aggregation.py /api/summary)
-- 2.  get_stock_history_view           (stock history lookup)
-- 3.  get_shipment_stats_agg           (shipment_stats_service.py)
-- 4.  get_channel_orders_agg           (aggregation.py /api/channel-orders)
-- 5.  get_monthly_sales_agg            (sales_analysis_service.py)
-- 6.  get_pnl_monthly_agg             (pnl_service.py _fetch_month_data)
-- 7.  rpc_get_repack_doc_nos           (repack_service.py generate_repack_doc_no)
-- 8.  rpc_validate_outbound_invoices   (outbound_validation_service.py)
-- 9.  get_dashboard_full               (legacy dashboard fallback)
-- 10. get_dashboard_orders_by_channel  (db_supabase.py query_orders_by_channel)
-- 11. get_dashboard_outbound_summary   (db_supabase.py query_outbound_summary)
-- 12. get_dashboard_stock_by_location  (db_supabase.py query_stock_summary_by_location)
-- 13. get_dashboard_top_products       (db_supabase.py query_top_products_by_revenue)
-- 14. get_settlement_summary_by_month  (maesil_bridge)


-- ============================================================
-- 1. get_aggregation_summary
--    aggregation.py: rpc('get_aggregation_summary', {p_date_from, p_date_to})
--    NOTE: no biz_id param (legacy RLS-based)
-- ============================================================
CREATE OR REPLACE FUNCTION get_aggregation_summary(
    p_date_from TEXT,
    p_date_to   TEXT
)
RETURNS JSON
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_out_count     BIGINT;
    v_out_items     BIGINT;
    v_out_qty       BIGINT;
    v_out_locations JSONB;
    v_in_count      BIGINT;
    v_prod_count    BIGINT;
    v_rev_count     BIGINT;
    v_rev_total     BIGINT;
    v_rev_by_cat    JSONB;
    v_rev_by_ch     JSONB;
BEGIN
    -- outbound: SALES_OUT from stock_ledger
    SELECT
        COUNT(*),
        COUNT(DISTINCT product_name),
        COALESCE(SUM(ABS(qty)), 0)
    INTO v_out_count, v_out_items, v_out_qty
    FROM stock_ledger
    WHERE type = 'SALES_OUT'
      AND transaction_date BETWEEN p_date_from AND p_date_to
      AND (status IS NULL OR status = 'active');

    -- outbound by location
    SELECT COALESCE(
        jsonb_object_agg(location, total_qty),
        '{}'::jsonb
    )
    INTO v_out_locations
    FROM (
        SELECT location, SUM(ABS(qty))::BIGINT AS total_qty
        FROM stock_ledger
        WHERE type = 'SALES_OUT'
          AND transaction_date BETWEEN p_date_from AND p_date_to
          AND (status IS NULL OR status = 'active')
          AND location IS NOT NULL
        GROUP BY location
    ) t;

    -- inbound count: type IN ('INBOUND', 'IN')
    SELECT COUNT(*) INTO v_in_count
    FROM stock_ledger
    WHERE type IN ('INBOUND', 'IN')
      AND transaction_date BETWEEN p_date_from AND p_date_to
      AND (status IS NULL OR status = 'active');

    -- production count
    SELECT COUNT(*) INTO v_prod_count
    FROM stock_ledger
    WHERE type = 'PRODUCTION'
      AND transaction_date BETWEEN p_date_from AND p_date_to
      AND (status IS NULL OR status = 'active');

    -- revenue from order_transactions
    SELECT
        COUNT(*),
        COALESCE(SUM(total_amount), 0)
    INTO v_rev_count, v_rev_total
    FROM order_transactions
    WHERE order_date BETWEEN p_date_from AND p_date_to
      AND status != '취소';

    -- revenue by category (channel as category proxy)
    SELECT COALESCE(
        jsonb_object_agg(channel, ch_total),
        '{}'::jsonb
    )
    INTO v_rev_by_cat
    FROM (
        SELECT channel, SUM(total_amount)::BIGINT AS ch_total
        FROM order_transactions
        WHERE order_date BETWEEN p_date_from AND p_date_to
          AND status != '취소'
          AND channel IS NOT NULL
        GROUP BY channel
    ) t;

    v_rev_by_ch := v_rev_by_cat;

    RETURN json_build_object(
        'outbound', json_build_object(
            'count',     v_out_count,
            'items',     v_out_items,
            'qty',       v_out_qty,
            'locations', v_out_locations
        ),
        'inbound_count',  v_in_count,
        'production_count', v_prod_count,
        'revenue', json_build_object(
            'count',       v_rev_count,
            'total',       v_rev_total,
            'by_category', v_rev_by_cat,
            'by_channel',  v_rev_by_ch
        )
    );
END;
$$;

GRANT EXECUTE ON FUNCTION get_aggregation_summary(TEXT, TEXT) TO authenticated, service_role;


-- ============================================================
-- 2. get_stock_history_view
--    stock_ledger history lookup by date (biz_id optional)
-- ============================================================
CREATE OR REPLACE FUNCTION get_stock_history_view(
    p_date    TEXT,
    p_biz_id  BIGINT DEFAULT NULL
)
RETURNS TABLE (
    transaction_date TEXT,
    type             TEXT,
    product_name     TEXT,
    qty              NUMERIC,
    unit             TEXT,
    location         TEXT,
    category         TEXT,
    storage_method   TEXT,
    lot_number       TEXT,
    grade            TEXT,
    manufacture_date TEXT,
    expiry_date      TEXT,
    memo             TEXT,
    batch_id         TEXT,
    transfer_id      TEXT,
    id               BIGINT
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
SET statement_timeout = '30s'
AS $$
    SELECT
        sl.transaction_date::TEXT,
        sl.type,
        sl.product_name,
        sl.qty,
        sl.unit,
        sl.location,
        sl.category,
        sl.storage_method,
        sl.lot_number,
        sl.grade,
        sl.manufacture_date::TEXT,
        sl.expiry_date::TEXT,
        sl.memo,
        sl.batch_id::TEXT,
        sl.transfer_id::TEXT,
        sl.id
    FROM stock_ledger sl
    WHERE sl.transaction_date::TEXT <= p_date
      AND (sl.status IS NULL OR sl.status = 'active')
      AND (p_biz_id IS NULL OR sl.biz_id = p_biz_id)
    ORDER BY sl.transaction_date DESC, sl.id DESC
    LIMIT 2000;
$$;

GRANT EXECUTE ON FUNCTION get_stock_history_view(TEXT, BIGINT) TO authenticated, service_role;


-- ============================================================
-- 3. get_shipment_stats_agg
--    shipment_stats_service.py: rpc('get_shipment_stats_agg', {p_date_from, p_date_to, p_location})
--    Returns JSON matching _calc_summary/_calc_daily_totals etc.
-- ============================================================
CREATE OR REPLACE FUNCTION get_shipment_stats_agg(
    p_date_from TEXT,
    p_date_to   TEXT,
    p_location  TEXT DEFAULT NULL
)
RETURNS JSON
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public
SET statement_timeout = '60s'
AS $$
DECLARE
    v_summary          JSON;
    v_daily_totals     JSON;
    v_monthly_totals   JSON;
    v_location_brkdown JSON;
    v_category_brkdown JSON;
    v_top_products     JSON;
BEGIN
    -- summary
    SELECT json_build_object(
        'total_qty',   COALESCE(SUM(ABS(qty)), 0)::BIGINT,
        'total_items', COUNT(DISTINCT product_name),
        'total_count', COUNT(*),
        'days',        COUNT(DISTINCT transaction_date),
        'daily_avg',   ROUND(
            COALESCE(SUM(ABS(qty)), 0) /
            NULLIF(COUNT(DISTINCT transaction_date), 0)
        , 1)
    )
    INTO v_summary
    FROM stock_ledger
    WHERE type = 'SALES_OUT'
      AND transaction_date BETWEEN p_date_from AND p_date_to
      AND (status IS NULL OR status = 'active')
      AND (p_location IS NULL OR p_location = '' OR location = p_location);

    -- daily_totals
    SELECT COALESCE(json_agg(t ORDER BY t.date), '[]'::json)
    INTO v_daily_totals
    FROM (
        SELECT transaction_date::TEXT AS date, COUNT(*) AS count,
               SUM(ABS(qty))::BIGINT AS qty
        FROM stock_ledger
        WHERE type = 'SALES_OUT'
          AND transaction_date BETWEEN p_date_from AND p_date_to
          AND (status IS NULL OR status = 'active')
          AND (p_location IS NULL OR p_location = '' OR location = p_location)
        GROUP BY transaction_date
    ) t;

    -- monthly_totals
    SELECT COALESCE(json_agg(t ORDER BY t.month), '[]'::json)
    INTO v_monthly_totals
    FROM (
        SELECT to_char(transaction_date, 'YYYY-MM') AS month,
               COUNT(*) AS count,
               SUM(ABS(qty))::BIGINT AS qty
        FROM stock_ledger
        WHERE type = 'SALES_OUT'
          AND transaction_date BETWEEN p_date_from AND p_date_to
          AND (status IS NULL OR status = 'active')
          AND (p_location IS NULL OR p_location = '' OR location = p_location)
        GROUP BY to_char(transaction_date, 'YYYY-MM')
    ) t;

    -- location_breakdown
    SELECT COALESCE(json_agg(t ORDER BY t.qty DESC), '[]'::json)
    INTO v_location_brkdown
    FROM (
        SELECT COALESCE(location, '기타') AS location,
               COUNT(*) AS count,
               SUM(ABS(qty))::BIGINT AS qty
        FROM stock_ledger
        WHERE type = 'SALES_OUT'
          AND transaction_date BETWEEN p_date_from AND p_date_to
          AND (status IS NULL OR status = 'active')
          AND (p_location IS NULL OR p_location = '' OR location = p_location)
        GROUP BY location
    ) t;

    -- category_breakdown
    SELECT COALESCE(json_agg(t ORDER BY t.qty DESC), '[]'::json)
    INTO v_category_brkdown
    FROM (
        SELECT COALESCE(category, '기타') AS category,
               COUNT(*) AS count,
               SUM(ABS(qty))::BIGINT AS qty
        FROM stock_ledger
        WHERE type = 'SALES_OUT'
          AND transaction_date BETWEEN p_date_from AND p_date_to
          AND (status IS NULL OR status = 'active')
          AND (p_location IS NULL OR p_location = '' OR location = p_location)
        GROUP BY category
    ) t;

    -- top_products (limit 15)
    SELECT COALESCE(json_agg(t ORDER BY t.total_qty DESC), '[]'::json)
    INTO v_top_products
    FROM (
        SELECT product_name,
               COUNT(*) AS count,
               SUM(ABS(qty))::BIGINT AS total_qty
        FROM stock_ledger
        WHERE type = 'SALES_OUT'
          AND transaction_date BETWEEN p_date_from AND p_date_to
          AND (status IS NULL OR status = 'active')
          AND (p_location IS NULL OR p_location = '' OR location = p_location)
          AND product_name IS NOT NULL
        GROUP BY product_name
        ORDER BY SUM(ABS(qty)) DESC
        LIMIT 15
    ) t;

    RETURN json_build_object(
        'summary',            v_summary,
        'daily_totals',       v_daily_totals,
        'monthly_totals',     v_monthly_totals,
        'location_breakdown', v_location_brkdown,
        'category_breakdown', v_category_brkdown,
        'top_products',       v_top_products
    );
END;
$$;

GRANT EXECUTE ON FUNCTION get_shipment_stats_agg(TEXT, TEXT, TEXT) TO authenticated, service_role;


-- ============================================================
-- 4. get_channel_orders_agg
--    aggregation.py /api/channel-orders
--    Returns JSON: {rows: [{date, groups: {channel: count}}]}
--    Python side: agg_json.get('rows') or []
-- ============================================================
CREATE OR REPLACE FUNCTION get_channel_orders_agg(
    p_date_from TEXT,
    p_date_to   TEXT
)
RETURNS JSON
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public
SET statement_timeout = '60s'
AS $$
DECLARE
    v_rows JSON;
BEGIN
    SELECT COALESCE(json_agg(day_row ORDER BY day_row.date), '[]'::json)
    INTO v_rows
    FROM (
        SELECT
            order_date::TEXT AS date,
            jsonb_object_agg(
                COALESCE(channel, '기타'),
                cnt
            ) AS groups
        FROM (
            SELECT order_date, channel, COUNT(*) AS cnt
            FROM order_transactions
            WHERE order_date BETWEEN p_date_from AND p_date_to
              AND status != '취소'
            GROUP BY order_date, channel
        ) base
        GROUP BY order_date
    ) day_row;

    RETURN json_build_object('rows', v_rows);
END;
$$;

GRANT EXECUTE ON FUNCTION get_channel_orders_agg(TEXT, TEXT) TO authenticated, service_role;


-- ============================================================
-- 5. get_monthly_sales_agg
--    sales_analysis_service.py _fetch_month_sales
--    Called: rpc('get_monthly_sales_agg', {p_year, p_month})
--    Returns JSON: {product_name: {total_qty, total_amount}}
-- ============================================================
CREATE OR REPLACE FUNCTION get_monthly_sales_agg(
    p_year   INT,
    p_month  INT     DEFAULT NULL,
    p_biz_id BIGINT  DEFAULT NULL
)
RETURNS JSON
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public
SET statement_timeout = '60s'
AS $$
DECLARE
    v_date_from TEXT;
    v_date_to   TEXT;
    v_result    JSON;
BEGIN
    IF p_month IS NOT NULL THEN
        v_date_from := to_char(make_date(p_year, p_month, 1), 'YYYY-MM-DD');
        v_date_to   := to_char(
            (make_date(p_year, p_month, 1) + INTERVAL '1 month - 1 day')::DATE,
            'YYYY-MM-DD'
        );
    ELSE
        v_date_from := to_char(make_date(p_year, 1, 1), 'YYYY-MM-DD');
        v_date_to   := to_char(make_date(p_year, 12, 31), 'YYYY-MM-DD');
    END IF;

    SELECT COALESCE(
        json_object_agg(
            product_name,
            json_build_object(
                'total_qty',    total_qty,
                'total_amount', total_amount
            )
        ),
        '{}'::json
    )
    INTO v_result
    FROM (
        SELECT
            product_name,
            SUM(qty)::BIGINT           AS total_qty,
            SUM(total_amount)::BIGINT  AS total_amount
        FROM order_transactions
        WHERE order_date BETWEEN v_date_from AND v_date_to
          AND status = '정상'
          AND product_name IS NOT NULL
          AND (p_biz_id IS NULL OR biz_id = p_biz_id)
        GROUP BY product_name
    ) t;

    RETURN v_result;
END;
$$;

GRANT EXECUTE ON FUNCTION get_monthly_sales_agg(INT, INT, BIGINT) TO authenticated, service_role;


-- ============================================================
-- 6. get_pnl_monthly_agg
--    pnl_service.py _fetch_month_data
--    Called: rpc('get_pnl_monthly_agg', {p_date_from, p_date_to, p_year_month})
--    Returns JSON structure consumed by _calc_revenue_v2, _calc_cogs_v2, _calc_sga_v2
-- ============================================================
CREATE OR REPLACE FUNCTION get_pnl_monthly_agg(
    p_date_from  TEXT,
    p_date_to    TEXT,
    p_year_month TEXT DEFAULT NULL,
    p_biz_id     BIGINT DEFAULT NULL
)
RETURNS JSON
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public
SET statement_timeout = '60s'
AS $$
DECLARE
    v_online_total       BIGINT := 0;
    v_online_commission  BIGINT := 0;
    v_by_channel         JSONB  := '{}'::jsonb;
    v_b2b_total          BIGINT := 0;
    v_purchase_total     BIGINT := 0;
    v_purchase_by_vendor JSONB  := '{}'::jsonb;
    v_exp_by_category    JSONB  := '{}'::jsonb;
    v_ad_total           BIGINT := 0;
    v_ad_by_channel      JSONB  := '{}'::jsonb;
BEGIN
    -- online revenue from api_settlements (nsettle_/wsettle_/rocket_/etc prefixes)
    SELECT
        COALESCE(SUM(gross_sales), 0)::BIGINT,
        COALESCE(SUM(total_commission), 0)::BIGINT
    INTO v_online_total, v_online_commission
    FROM api_settlements
    WHERE (
        settlement_id LIKE 'nsettle_%' OR
        settlement_id LIKE 'wsettle_%' OR
        settlement_id LIKE 'rocket_%'  OR
        settlement_id LIKE '11settle_%' OR
        settlement_id LIKE 'tsettle_%' OR
        settlement_id LIKE 'osettle_%' OR
        settlement_id LIKE 'auction_%' OR
        settlement_id LIKE 'gmarket_%'
    )
    AND settled_date BETWEEN p_date_from AND p_date_to
    AND (p_biz_id IS NULL OR biz_id = p_biz_id);

    -- by_channel from api_settlements
    SELECT COALESCE(jsonb_object_agg(channel, ch_total), '{}'::jsonb)
    INTO v_by_channel
    FROM (
        SELECT channel, SUM(gross_sales)::BIGINT AS ch_total
        FROM api_settlements
        WHERE (
            settlement_id LIKE 'nsettle_%' OR
            settlement_id LIKE 'wsettle_%' OR
            settlement_id LIKE 'rocket_%'  OR
            settlement_id LIKE '11settle_%' OR
            settlement_id LIKE 'tsettle_%' OR
            settlement_id LIKE 'osettle_%' OR
            settlement_id LIKE 'auction_%' OR
            settlement_id LIKE 'gmarket_%'
        )
        AND settled_date BETWEEN p_date_from AND p_date_to
        AND (p_biz_id IS NULL OR biz_id = p_biz_id)
        AND channel IS NOT NULL
        GROUP BY channel
    ) t;

    -- b2b revenue from tax_invoices (sales direction)
    SELECT COALESCE(SUM(COALESCE(supply_cost_total, supply_amount, 0)), 0)::BIGINT
    INTO v_b2b_total
    FROM tax_invoices
    WHERE direction = 'sales'
      AND status != 'cancelled'
      AND issue_date BETWEEN p_date_from AND p_date_to
      AND (p_biz_id IS NULL OR biz_id = p_biz_id)
      AND buyer_corp_name NOT IN (
          '쿠팡(주)', '쿠팡주식회사', '쿠팡 주식회사',
          '네이버파이낸셜 주식회사', '네이버파이낸셜주식회사',
          '네이버 주식회사', '네이버주식회사', '네이버(주)',
          '(주)네이버파이낸셜', '주식회사 네이버파이낸셜'
      );

    -- purchase (COGS + SGA) total from tax_invoices
    SELECT COALESCE(SUM(COALESCE(supply_cost_total, 0)), 0)::BIGINT
    INTO v_purchase_total
    FROM tax_invoices
    WHERE direction = 'purchase'
      AND status != 'cancelled'
      AND issue_date BETWEEN p_date_from AND p_date_to
      AND (p_biz_id IS NULL OR biz_id = p_biz_id);

    -- purchase by vendor from tax_invoices
    SELECT COALESCE(jsonb_object_agg(vendor, vendor_total), '{}'::jsonb)
    INTO v_purchase_by_vendor
    FROM (
        SELECT COALESCE(supplier_corp_name, '기타') AS vendor,
               SUM(COALESCE(supply_cost_total, 0))::BIGINT AS vendor_total
        FROM tax_invoices
        WHERE direction = 'purchase'
          AND status != 'cancelled'
          AND issue_date BETWEEN p_date_from AND p_date_to
          AND (p_biz_id IS NULL OR biz_id = p_biz_id)
        GROUP BY supplier_corp_name
    ) t;

    -- expenses by category
    SELECT COALESCE(jsonb_object_agg(category, cat_total), '{}'::jsonb)
    INTO v_exp_by_category
    FROM (
        SELECT category, SUM(amount)::BIGINT AS cat_total
        FROM expenses
        WHERE month = COALESCE(p_year_month, to_char(p_date_from::DATE, 'YYYY-MM'))
          AND (p_biz_id IS NULL OR biz_id = p_biz_id)
        GROUP BY category
    ) t;

    -- ad costs from api_settlements (ad_cost_ prefix)
    SELECT COALESCE(SUM(ABS(other_deductions)), 0)::BIGINT
    INTO v_ad_total
    FROM api_settlements
    WHERE settlement_id LIKE 'ad_cost_%'
      AND settled_date BETWEEN p_date_from AND p_date_to
      AND (p_biz_id IS NULL OR biz_id = p_biz_id);

    SELECT COALESCE(jsonb_object_agg(channel, ad_ch_total), '{}'::jsonb)
    INTO v_ad_by_channel
    FROM (
        SELECT channel, SUM(ABS(other_deductions))::BIGINT AS ad_ch_total
        FROM api_settlements
        WHERE settlement_id LIKE 'ad_cost_%'
          AND settled_date BETWEEN p_date_from AND p_date_to
          AND (p_biz_id IS NULL OR biz_id = p_biz_id)
          AND channel IS NOT NULL
        GROUP BY channel
    ) t;

    RETURN json_build_object(
        'revenue', json_build_object(
            'online_total',       v_online_total,
            'online_commission',  v_online_commission,
            'by_channel',         v_by_channel
        ),
        'b2b', json_build_object(
            'b2b_total', v_b2b_total
        ),
        'purchase', json_build_object(
            'purchase_total', v_purchase_total,
            'by_vendor',      v_purchase_by_vendor
        ),
        'expenses', json_build_object(
            'by_category', v_exp_by_category
        ),
        'ad_cost', json_build_object(
            'total_ad_cost', v_ad_total,
            'by_channel',    v_ad_by_channel
        )
    );
END;
$$;

GRANT EXECUTE ON FUNCTION get_pnl_monthly_agg(TEXT, TEXT, TEXT, BIGINT) TO authenticated, service_role;


-- ============================================================
-- 7. rpc_get_repack_doc_nos
--    repack_service.py generate_repack_doc_no
--    Called: rpc('rpc_get_repack_doc_nos', {p_date_str: 'YYYY-MM-DD'})
--    Returns [{repack_doc_no TEXT}]
--    Finds DISTINCT repack_doc_no where date matches
-- ============================================================
CREATE OR REPLACE FUNCTION rpc_get_repack_doc_nos(
    p_date_str TEXT
)
RETURNS TABLE (repack_doc_no TEXT)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
SET statement_timeout = '10s'
AS $$
    SELECT DISTINCT sl.repack_doc_no
    FROM stock_ledger sl
    WHERE sl.repack_doc_no IS NOT NULL
      AND sl.repack_doc_no != ''
      AND sl.transaction_date = p_date_str::DATE
      AND sl.type IN ('REPACK_OUT', 'REPACK_IN')
      AND (sl.status IS NULL OR sl.status = 'active');
$$;

GRANT EXECUTE ON FUNCTION rpc_get_repack_doc_nos(TEXT) TO authenticated, service_role;


-- ============================================================
-- 8. rpc_validate_outbound_invoices
--    outbound_validation_service.py _fetch_invoice_reverse
--    Called: rpc('rpc_validate_outbound_invoices', {p_order_nos: [...]})
--    Returns [{channel, order_no, invoice_no}]
-- ============================================================
CREATE OR REPLACE FUNCTION rpc_validate_outbound_invoices(
    p_order_nos TEXT[]
)
RETURNS TABLE (
    channel    TEXT,
    order_no   TEXT,
    invoice_no TEXT
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
SET statement_timeout = '10s'
AS $$
    SELECT
        os.channel,
        os.order_no,
        os.invoice_no
    FROM order_shipping os
    WHERE os.order_no = ANY(p_order_nos)
      AND os.invoice_no IS NOT NULL
      AND os.invoice_no != '';
$$;

GRANT EXECUTE ON FUNCTION rpc_validate_outbound_invoices(TEXT[]) TO authenticated, service_role;


-- ============================================================
-- 9. get_dashboard_full  (legacy dashboard)
--    Returns combined daily snapshot: outbound + revenue + stock
-- ============================================================
CREATE OR REPLACE FUNCTION get_dashboard_full(
    p_date       TEXT,
    p_month_start TEXT
)
RETURNS JSON
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public
SET statement_timeout = '30s'
AS $$
DECLARE
    v_today_orders  BIGINT := 0;
    v_month_revenue BIGINT := 0;
    v_pending_ship  BIGINT := 0;
    v_outbound_qty  BIGINT := 0;
BEGIN
    SELECT COUNT(*) INTO v_today_orders
    FROM order_transactions
    WHERE order_date = p_date::DATE;

    SELECT COALESCE(SUM(total_amount), 0) INTO v_month_revenue
    FROM order_transactions
    WHERE order_date BETWEEN p_month_start AND p_date
      AND status != '취소';

    SELECT COUNT(*) INTO v_pending_ship
    FROM order_transactions
    WHERE is_outbound_done = FALSE
      AND status = '정상';

    SELECT COALESCE(SUM(ABS(qty)), 0) INTO v_outbound_qty
    FROM stock_ledger
    WHERE type = 'SALES_OUT'
      AND transaction_date = p_date::DATE
      AND (status IS NULL OR status = 'active');

    RETURN json_build_object(
        'today_orders',  v_today_orders,
        'month_revenue', v_month_revenue,
        'pending_ship',  v_pending_ship,
        'outbound_qty',  v_outbound_qty
    );
END;
$$;

GRANT EXECUTE ON FUNCTION get_dashboard_full(TEXT, TEXT) TO authenticated, service_role;


-- ============================================================
-- 10. get_dashboard_orders_by_channel
--     db_supabase.py query_orders_by_channel
--     Returns [{channel, count, qty, amount}]
-- ============================================================
CREATE OR REPLACE FUNCTION get_dashboard_orders_by_channel(
    p_date_from TEXT DEFAULT NULL,
    p_date_to   TEXT DEFAULT NULL
)
RETURNS TABLE (
    channel TEXT,
    count   BIGINT,
    qty     BIGINT,
    amount  BIGINT
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
SET statement_timeout = '30s'
AS $$
    SELECT
        COALESCE(channel, '기타') AS channel,
        COUNT(*)::BIGINT                       AS count,
        COALESCE(SUM(qty), 0)::BIGINT          AS qty,
        COALESCE(SUM(total_amount), 0)::BIGINT AS amount
    FROM order_transactions
    WHERE status = '정상'
      AND (p_date_from IS NULL OR order_date >= p_date_from::DATE)
      AND (p_date_to   IS NULL OR order_date <= p_date_to::DATE)
    GROUP BY channel
    ORDER BY count DESC;
$$;

GRANT EXECUTE ON FUNCTION get_dashboard_orders_by_channel(TEXT, TEXT) TO authenticated, service_role;


-- ============================================================
-- 11. get_dashboard_outbound_summary
--     db_supabase.py query_outbound_summary
--     Returns JSON: {pending, done}
-- ============================================================
CREATE OR REPLACE FUNCTION get_dashboard_outbound_summary(
    p_date_from TEXT DEFAULT NULL,
    p_date_to   TEXT DEFAULT NULL
)
RETURNS JSON
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public
SET statement_timeout = '15s'
AS $$
DECLARE
    v_pending BIGINT := 0;
    v_done    BIGINT := 0;
BEGIN
    SELECT COUNT(*) INTO v_pending
    FROM order_transactions
    WHERE is_outbound_done = FALSE
      AND status = '정상'
      AND (p_date_from IS NULL OR order_date >= p_date_from::DATE)
      AND (p_date_to   IS NULL OR order_date <= p_date_to::DATE);

    SELECT COUNT(*) INTO v_done
    FROM order_transactions
    WHERE is_outbound_done = TRUE
      AND (p_date_from IS NULL OR order_date >= p_date_from::DATE)
      AND (p_date_to   IS NULL OR order_date <= p_date_to::DATE);

    RETURN json_build_object(
        'pending', v_pending,
        'done',    v_done
    );
END;
$$;

GRANT EXECUTE ON FUNCTION get_dashboard_outbound_summary(TEXT, TEXT) TO authenticated, service_role;


-- ============================================================
-- 12. get_dashboard_stock_by_location
--     db_supabase.py query_stock_summary_by_location
--     Returns [{location, product_count, total_qty}]
-- ============================================================
CREATE OR REPLACE FUNCTION get_dashboard_stock_by_location(
    p_days INT DEFAULT 90
)
RETURNS TABLE (
    location      TEXT,
    product_count BIGINT,
    total_qty     BIGINT
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
SET statement_timeout = '30s'
AS $$
    WITH stock_agg AS (
        SELECT
            product_name,
            location,
            SUM(qty) AS net_qty
        FROM stock_ledger
        WHERE transaction_date >= (CURRENT_DATE - p_days * INTERVAL '1 day')::DATE
        GROUP BY product_name, location
        HAVING SUM(qty) > 0
    )
    SELECT
        COALESCE(location, '기타') AS location,
        COUNT(DISTINCT product_name)::BIGINT AS product_count,
        SUM(net_qty)::BIGINT AS total_qty
    FROM stock_agg
    GROUP BY location
    ORDER BY product_count DESC;
$$;

GRANT EXECUTE ON FUNCTION get_dashboard_stock_by_location(INT) TO authenticated, service_role;


-- ============================================================
-- 13. get_dashboard_top_products
--     db_supabase.py query_top_products_by_revenue
--     Returns [{product_name, qty, revenue, settlement}]
-- ============================================================
CREATE OR REPLACE FUNCTION get_dashboard_top_products(
    p_days    INT    DEFAULT 30,
    p_limit   INT    DEFAULT 10,
    p_biz_id  BIGINT DEFAULT NULL
)
RETURNS TABLE (
    product_name TEXT,
    qty          BIGINT,
    revenue      BIGINT,
    settlement   BIGINT
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
SET statement_timeout = '30s'
AS $$
    SELECT
        product_name,
        SUM(qty)::BIGINT           AS qty,
        SUM(total_amount)::BIGINT  AS revenue,
        SUM(settlement)::BIGINT    AS settlement
    FROM order_transactions
    WHERE order_date >= (CURRENT_DATE - p_days * INTERVAL '1 day')::DATE
      AND status = '정상'
      AND product_name IS NOT NULL
      AND product_name != ''
      AND (p_biz_id IS NULL OR biz_id = p_biz_id)
    GROUP BY product_name
    ORDER BY SUM(total_amount) DESC
    LIMIT p_limit;
$$;

GRANT EXECUTE ON FUNCTION get_dashboard_top_products(INT, INT, BIGINT) TO authenticated, service_role;


-- ============================================================
-- 14. get_settlement_summary_by_month  (maesil_bridge)
--     Returns summary of settlements for a given operator and month
-- ============================================================
CREATE OR REPLACE FUNCTION get_settlement_summary_by_month(
    p_operator_id INT,
    p_month       TEXT
)
RETURNS JSON
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public
SET statement_timeout = '30s'
AS $$
DECLARE
    v_date_from TEXT;
    v_date_to   TEXT;
    v_total     BIGINT := 0;
    v_count     BIGINT := 0;
    v_by_ch     JSONB  := '{}'::jsonb;
BEGIN
    v_date_from := p_month || '-01';
    v_date_to   := to_char(
        (to_date(p_month, 'YYYY-MM') + INTERVAL '1 month - 1 day')::DATE,
        'YYYY-MM-DD'
    );

    SELECT
        COUNT(*),
        COALESCE(SUM(net_settlement), 0)::BIGINT
    INTO v_count, v_total
    FROM api_settlements
    WHERE operator_id = p_operator_id
      AND settled_date BETWEEN v_date_from AND v_date_to;

    SELECT COALESCE(jsonb_object_agg(channel, ch_total), '{}'::jsonb)
    INTO v_by_ch
    FROM (
        SELECT channel, SUM(net_settlement)::BIGINT AS ch_total
        FROM api_settlements
        WHERE operator_id = p_operator_id
          AND settled_date BETWEEN v_date_from AND v_date_to
          AND channel IS NOT NULL
        GROUP BY channel
    ) t;

    RETURN json_build_object(
        'month',      p_month,
        'count',      v_count,
        'total',      v_total,
        'by_channel', v_by_ch
    );
END;
$$;

GRANT EXECUTE ON FUNCTION get_settlement_summary_by_month(INT, TEXT) TO authenticated, service_role;
