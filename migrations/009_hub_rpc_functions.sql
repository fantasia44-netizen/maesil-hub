-- 009: maesil-hub RPC 함수 모음
--
-- maesil-total migrations/017, 018, 012, 003, 004 를 hub 스키마에 맞게 이식.
-- NOTE: 현재 biz_id 파라미터 미포함 (service_role 전용 호출).
--       멀티테넌트 완전 격리는 추후 010_ 파일에서 biz_id 파라미터 추가 예정.
--
-- ────────────────────────────────────────────────────────────────────────────
-- A. 데이터 무결성 / 중복 체크 RPCs
-- ────────────────────────────────────────────────────────────────────────────

-- A-1. stock_ledger event_uid 중복 체크
DROP FUNCTION IF EXISTS rpc_check_event_uid_exists(TEXT[]);
CREATE OR REPLACE FUNCTION rpc_check_event_uid_exists(p_uids TEXT[])
RETURNS TABLE(event_uid TEXT)
LANGUAGE sql STABLE SECURITY DEFINER SET statement_timeout = '10s'
AS $$
    SELECT DISTINCT sl.event_uid
    FROM stock_ledger sl
    WHERE sl.event_uid = ANY(p_uids)
      AND sl.status = 'active';
$$;
GRANT EXECUTE ON FUNCTION rpc_check_event_uid_exists(TEXT[])
    TO authenticated, service_role, anon;


-- A-2. order_transactions raw_hash 중복 체크
DROP FUNCTION IF EXISTS rpc_check_raw_hash_exists(TEXT[]);
CREATE OR REPLACE FUNCTION rpc_check_raw_hash_exists(p_hashes TEXT[])
RETURNS TABLE(raw_hash TEXT, channel TEXT)
LANGUAGE sql STABLE SECURITY DEFINER SET statement_timeout = '10s'
AS $$
    SELECT DISTINCT ot.raw_hash, ot.channel
    FROM order_transactions ot
    WHERE ot.raw_hash = ANY(p_hashes);
$$;
GRANT EXECUTE ON FUNCTION rpc_check_raw_hash_exists(TEXT[])
    TO authenticated, service_role, anon;


-- A-3. order_transactions 주문번호 존재 여부 체크
DROP FUNCTION IF EXISTS rpc_check_order_no_exists(TEXT, TEXT[]);
CREATE OR REPLACE FUNCTION rpc_check_order_no_exists(
    p_channel   TEXT,
    p_order_nos TEXT[]
)
RETURNS TABLE(channel TEXT, order_no TEXT, line_no INTEGER, raw_hash TEXT, status TEXT, id BIGINT)
LANGUAGE sql STABLE SECURITY DEFINER SET statement_timeout = '10s'
AS $$
    SELECT ot.channel, ot.order_no, ot.line_no, ot.raw_hash, ot.status, ot.id
    FROM order_transactions ot
    WHERE ot.channel  = p_channel
      AND ot.order_no = ANY(p_order_nos);
$$;
GRANT EXECUTE ON FUNCTION rpc_check_order_no_exists(TEXT, TEXT[])
    TO authenticated, service_role, anon;


-- ────────────────────────────────────────────────────────────────────────────
-- B. import_run 집계 RPC
-- ────────────────────────────────────────────────────────────────────────────

DROP FUNCTION IF EXISTS rpc_get_import_run_summary(BIGINT);
CREATE OR REPLACE FUNCTION rpc_get_import_run_summary(p_run_id BIGINT)
RETURNS JSONB
LANGUAGE sql STABLE SECURITY DEFINER SET statement_timeout = '15s'
AS $$
    WITH ot AS (
        SELECT id, status, is_outbound_done, order_no, channel,
               product_name, qty, outbound_date
        FROM order_transactions
        WHERE import_run_id = p_run_id
    )
    SELECT jsonb_build_object(
        'total',               (SELECT COUNT(*) FROM ot),
        'active_count',        (SELECT COUNT(*) FROM ot WHERE status = U&'\C815\C0C1'),
        'outbound_done_count', (SELECT COUNT(*) FROM ot
                                WHERE status = U&'\C815\C0C1' AND is_outbound_done = TRUE),
        'pending', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'id', id, 'order_no', order_no, 'channel', channel,
                'product_name', product_name, 'qty', qty
            ))
            FROM ot WHERE status = U&'\C815\C0C1' AND is_outbound_done = FALSE
        ), '[]'::jsonb),
        'completed', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'id', id, 'order_no', order_no, 'channel', channel,
                'product_name', product_name, 'qty', qty,
                'outbound_date', outbound_date
            ))
            FROM ot WHERE status = U&'\C815\C0C1' AND is_outbound_done = TRUE
        ), '[]'::jsonb)
    );
$$;
GRANT EXECUTE ON FUNCTION rpc_get_import_run_summary(BIGINT)
    TO authenticated, service_role, anon;


-- ────────────────────────────────────────────────────────────────────────────
-- C. 쿼리 RPCs (1000행 limit 회피)
-- ────────────────────────────────────────────────────────────────────────────

-- C-1. 재고 이동 상세
DROP FUNCTION IF EXISTS rpc_get_transfer_detail(TEXT);
CREATE OR REPLACE FUNCTION rpc_get_transfer_detail(p_transfer_id TEXT)
RETURNS TABLE(id BIGINT, type TEXT, product_name TEXT, qty NUMERIC, location TEXT, status TEXT)
LANGUAGE sql STABLE SECURITY DEFINER SET statement_timeout = '15s'
AS $$
    SELECT sl.id, sl.type, sl.product_name, sl.qty, sl.location, sl.status
    FROM stock_ledger sl
    WHERE sl.transfer_id = p_transfer_id
      AND (sl.status IS NULL OR sl.status = 'active');
$$;
GRANT EXECUTE ON FUNCTION rpc_get_transfer_detail(TEXT)
    TO authenticated, service_role, anon;


-- C-2. 자재 재고 집계
DROP FUNCTION IF EXISTS rpc_get_materials_stock_agg(TEXT[]);
CREATE OR REPLACE FUNCTION rpc_get_materials_stock_agg(p_categories TEXT[])
RETURNS TABLE(product_name TEXT, total_qty NUMERIC, category TEXT)
LANGUAGE sql STABLE SECURITY DEFINER SET statement_timeout = '20s'
AS $$
    SELECT
        sl.product_name,
        SUM(sl.qty)::NUMERIC AS total_qty,
        pc.category
    FROM stock_ledger sl
    LEFT JOIN product_costs pc ON pc.product_name = sl.product_name
    WHERE sl.status = 'active'
      AND pc.category = ANY(p_categories)
    GROUP BY sl.product_name, pc.category;
$$;
GRANT EXECUTE ON FUNCTION rpc_get_materials_stock_agg(TEXT[])
    TO authenticated, service_role, anon;


-- C-3. 송장번호로 배송 조회
DROP FUNCTION IF EXISTS rpc_search_order_shipping_by_invoice(TEXT[]);
CREATE OR REPLACE FUNCTION rpc_search_order_shipping_by_invoice(p_invoices TEXT[])
RETURNS SETOF order_shipping
LANGUAGE sql STABLE SECURITY DEFINER SET statement_timeout = '20s'
AS $$
    SELECT * FROM order_shipping WHERE invoice_no = ANY(p_invoices);
$$;
GRANT EXECUTE ON FUNCTION rpc_search_order_shipping_by_invoice(TEXT[])
    TO authenticated, service_role, anon;


-- C-4. 패킹 대기 주문 IDs
DROP FUNCTION IF EXISTS rpc_get_packing_pending_orders(TEXT, TEXT[]);
CREATE OR REPLACE FUNCTION rpc_get_packing_pending_orders(
    p_channel   TEXT,
    p_order_nos TEXT[]
)
RETURNS TABLE(id BIGINT)
LANGUAGE sql STABLE SECURITY DEFINER SET statement_timeout = '20s'
AS $$
    SELECT ot.id
    FROM order_transactions ot
    WHERE ot.channel        = p_channel
      AND ot.order_no       = ANY(p_order_nos)
      AND ot.status         = U&'\C815\C0C1'
      AND ot.is_outbound_done = FALSE;
$$;
GRANT EXECUTE ON FUNCTION rpc_get_packing_pending_orders(TEXT, TEXT[])
    TO authenticated, service_role, anon;


-- C-5. 재고 품목 목록
DROP FUNCTION IF EXISTS rpc_get_stock_distinct_products();
CREATE OR REPLACE FUNCTION rpc_get_stock_distinct_products()
RETURNS TABLE(product_name TEXT)
LANGUAGE sql STABLE SECURITY DEFINER SET statement_timeout = '20s'
AS $$
    SELECT DISTINCT sl.product_name
    FROM stock_ledger sl
    WHERE sl.status = 'active'
      AND sl.product_name IS NOT NULL
      AND sl.product_name <> '';
$$;
GRANT EXECUTE ON FUNCTION rpc_get_stock_distinct_products()
    TO authenticated, service_role, anon;


-- C-6. 출고 목록 (stock_ledger SALES_OUT + manual_trades)
DROP FUNCTION IF EXISTS rpc_get_outbound_list(TEXT, TEXT, TEXT, TEXT);
CREATE OR REPLACE FUNCTION rpc_get_outbound_list(
    p_date_from TEXT DEFAULT NULL,
    p_date_to   TEXT DEFAULT NULL,
    p_location  TEXT DEFAULT NULL,
    p_product   TEXT DEFAULT NULL
)
RETURNS TABLE (
    src          TEXT,
    tx_date      TEXT,
    product_name TEXT,
    qty          INTEGER,
    unit         TEXT,
    location     TEXT,
    category     TEXT,
    channel      TEXT,
    memo         TEXT,
    lot_number   TEXT,
    expiry_date  TEXT,
    outbound_done BOOLEAN
)
LANGUAGE sql STABLE SECURITY DEFINER SET statement_timeout = '20s'
AS $$
    SELECT
        'sales_out'::TEXT,
        sl.transaction_date::TEXT,
        sl.product_name,
        ABS(sl.qty)::INTEGER,
        COALESCE(sl.unit, ''),
        COALESCE(sl.location, ''),
        COALESCE(sl.category, ''),
        ''::TEXT,
        ''::TEXT,
        COALESCE(sl.lot_number, ''),
        COALESCE(sl.expiry_date::TEXT, ''),
        TRUE
    FROM stock_ledger sl
    WHERE sl.type   = 'SALES_OUT'
      AND sl.status = 'active'
      AND (p_date_from IS NULL OR sl.transaction_date::TEXT >= p_date_from)
      AND (p_date_to   IS NULL OR sl.transaction_date::TEXT <= p_date_to)
      AND (p_location  IS NULL OR sl.location = p_location)
      AND (p_product   IS NULL OR sl.product_name ILIKE '%' || p_product || '%')

    UNION ALL

    SELECT
        'manual'::TEXT,
        mt.trade_date::TEXT,
        mt.product_name,
        ABS(mt.qty)::INTEGER,
        COALESCE(mt.unit, ''),
        ''::TEXT,
        ''::TEXT,
        ''::TEXT,
        COALESCE(mt.memo, ''),
        ''::TEXT,
        ''::TEXT,
        TRUE
    FROM manual_trades mt
    WHERE mt.trade_type NOT IN (U&'\C785\ACC0', U&'\AD6C\B9E4')
      AND mt.is_deleted = FALSE
      AND (p_date_from IS NULL OR mt.trade_date >= p_date_from)
      AND (p_date_to   IS NULL OR mt.trade_date <= p_date_to)
      AND (p_product   IS NULL OR mt.product_name ILIKE '%' || p_product || '%')

    ORDER BY 2 DESC;
$$;
GRANT EXECUTE ON FUNCTION rpc_get_outbound_list(TEXT, TEXT, TEXT, TEXT)
    TO authenticated, service_role, anon;


-- ────────────────────────────────────────────────────────────────────────────
-- D. 매출 집계 RPC
-- ────────────────────────────────────────────────────────────────────────────

DROP FUNCTION IF EXISTS get_revenue_summary_agg(DATE, DATE, TEXT);
CREATE OR REPLACE FUNCTION get_revenue_summary_agg(
    p_date_from DATE,
    p_date_to   DATE,
    p_category  TEXT DEFAULT NULL
)
RETURNS JSONB
LANGUAGE sql STABLE SECURITY DEFINER SET statement_timeout = '15s'
AS $$
    WITH ot_agg AS (
        SELECT
            COALESCE(SUM(total_amount), 0)::BIGINT  AS revenue,
            COALESCE(SUM(settlement), 0)::BIGINT    AS settlement,
            COALESCE(SUM(commission), 0)::BIGINT    AS commission,
            COUNT(*)::BIGINT                         AS cnt
        FROM order_transactions
        WHERE status    = U&'\C815\C0C1'
          AND order_date BETWEEN p_date_from AND p_date_to
          AND (p_category IS NULL OR p_category = U&'\C804\CCB4')
    ),
    dr_agg AS (
        SELECT
            COALESCE(SUM(revenue), 0)::BIGINT AS revenue,
            0::BIGINT                          AS settlement,
            0::BIGINT                          AS commission,
            COUNT(*)::BIGINT                   AS cnt
        FROM daily_revenue
        WHERE (is_deleted IS NULL OR is_deleted = FALSE)
          AND revenue_date BETWEEN p_date_from AND p_date_to
          AND (p_category IS NULL OR p_category = U&'\C804\CCB4' OR category = p_category)
    ),
    by_channel AS (
        SELECT jsonb_object_agg(channel, total) AS data
        FROM (
            SELECT COALESCE(channel, U&'\AE30\D0C0') AS channel,
                   SUM(total_amount)::BIGINT  AS total
            FROM order_transactions
            WHERE status    = U&'\C815\C0C1'
              AND order_date BETWEEN p_date_from AND p_date_to
              AND (p_category IS NULL OR p_category = U&'\C804\CCB4')
            GROUP BY COALESCE(channel, U&'\AE30\D0C0')
        ) x
    )
    SELECT jsonb_build_object(
        'total_revenue',     ot_agg.revenue + dr_agg.revenue,
        'total_settlement',  ot_agg.settlement,
        'total_commission',  ot_agg.commission,
        'ot_count',          ot_agg.cnt,
        'dr_count',          dr_agg.cnt,
        'by_channel',        COALESCE(by_channel.data, '{}'::jsonb)
    )
    FROM ot_agg, dr_agg, by_channel;
$$;
GRANT EXECUTE ON FUNCTION get_revenue_summary_agg(DATE, DATE, TEXT)
    TO authenticated, service_role, anon;


-- ────────────────────────────────────────────────────────────────────────────
-- E. 재고 현황 집계 RPC
-- ────────────────────────────────────────────────────────────────────────────

DROP FUNCTION IF EXISTS get_stock_summary();
CREATE OR REPLACE FUNCTION get_stock_summary()
RETURNS TABLE(product_name TEXT, total_qty NUMERIC, category TEXT, location TEXT)
LANGUAGE sql STABLE SECURITY DEFINER SET statement_timeout = '20s'
AS $$
    SELECT
        sl.product_name,
        SUM(sl.qty)::NUMERIC AS total_qty,
        COALESCE(pc.category, '') AS category,
        COALESCE(sl.location, '') AS location
    FROM stock_ledger sl
    LEFT JOIN product_costs pc ON pc.product_name = sl.product_name
    WHERE sl.status = 'active'
      AND sl.product_name IS NOT NULL
    GROUP BY sl.product_name, pc.category, sl.location
    HAVING SUM(sl.qty) != 0
    ORDER BY sl.product_name;
$$;
GRANT EXECUTE ON FUNCTION get_stock_summary()
    TO authenticated, service_role, anon;
