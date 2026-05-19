-- 012: RPC 함수 biz_id 격리 (멀티테넌트 완전 격리)
--
-- 009/011에서 생성한 RPC 함수들에 p_biz_id 파라미터 추가.
-- DEFAULT NULL → biz_id 없으면 전체 조회 (service_role 호환 유지).
-- Python 호출부는 g.biz_id 값을 명시 전달.
-- ──────────────────────────────────────────────────────────────────────────

-- ── D. get_revenue_summary_agg (p_biz_id 추가) ────────────────────────────

DROP FUNCTION IF EXISTS get_revenue_summary_agg(DATE, DATE, TEXT);
DROP FUNCTION IF EXISTS get_revenue_summary_agg(DATE, DATE, TEXT, BIGINT);
CREATE OR REPLACE FUNCTION get_revenue_summary_agg(
    p_date_from DATE,
    p_date_to   DATE,
    p_category  TEXT    DEFAULT NULL,
    p_biz_id    BIGINT  DEFAULT NULL
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
          AND (p_biz_id IS NULL OR biz_id = p_biz_id)
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
          AND (p_biz_id IS NULL OR biz_id = p_biz_id)
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
              AND (p_biz_id IS NULL OR biz_id = p_biz_id)
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
GRANT EXECUTE ON FUNCTION get_revenue_summary_agg(DATE, DATE, TEXT, BIGINT)
    TO authenticated, service_role, anon;


-- ── E. get_stock_summary (p_biz_id 추가) ──────────────────────────────────

DROP FUNCTION IF EXISTS get_stock_summary();
DROP FUNCTION IF EXISTS get_stock_summary(BIGINT);
CREATE OR REPLACE FUNCTION get_stock_summary(p_biz_id BIGINT DEFAULT NULL)
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
      AND (p_biz_id IS NULL OR sl.biz_id = p_biz_id)
    GROUP BY sl.product_name, pc.category, sl.location
    HAVING SUM(sl.qty) != 0
    ORDER BY sl.product_name;
$$;
GRANT EXECUTE ON FUNCTION get_stock_summary(BIGINT)
    TO authenticated, service_role, anon;


-- ── 011 get_stock_snapshot_agg (p_biz_id 추가) ───────────────────────────

DROP FUNCTION IF EXISTS get_stock_snapshot_agg(DATE, TEXT);
DROP FUNCTION IF EXISTS get_stock_snapshot_agg(BIGINT, DATE, TEXT);
CREATE OR REPLACE FUNCTION get_stock_snapshot_agg(
    p_date_to    DATE,
    p_split_mode TEXT   DEFAULT NULL,
    p_biz_id     BIGINT DEFAULT NULL
)
RETURNS TABLE(
    product_name    TEXT,
    location        TEXT,
    category        TEXT,
    storage_method  TEXT,
    unit            TEXT,
    grade           TEXT,
    origin          TEXT,
    manufacture_date DATE,
    expiry_date      DATE,
    lot_number       TEXT,
    qty              NUMERIC
)
LANGUAGE sql STABLE SECURITY DEFINER SET statement_timeout = '30s'
AS $$
    SELECT
        sl.product_name,
        COALESCE(sl.location, '')       AS location,
        COALESCE(sl.category, '')       AS category,
        COALESCE(sl.storage_method, '') AS storage_method,
        COALESCE(sl.unit, U&'\AC1C')    AS unit,
        COALESCE(sl.grade, '')          AS grade,
        COALESCE(sl.origin, '')         AS origin,
        CASE WHEN p_split_mode = 'manufacture'
             THEN sl.manufacture_date ELSE NULL END AS manufacture_date,
        CASE WHEN p_split_mode = 'expiry'
             THEN sl.expiry_date ELSE NULL END AS expiry_date,
        CASE WHEN p_split_mode = 'lot_number'
             THEN sl.lot_number ELSE NULL END AS lot_number,
        SUM(sl.qty)::NUMERIC            AS qty
    FROM stock_ledger sl
    WHERE sl.status = 'active'
      AND sl.transaction_date <= p_date_to
      AND sl.product_name IS NOT NULL
      AND sl.is_deleted = FALSE
      AND (p_biz_id IS NULL OR sl.biz_id = p_biz_id)
    GROUP BY
        sl.product_name,
        COALESCE(sl.location, ''),
        COALESCE(sl.category, ''),
        COALESCE(sl.storage_method, ''),
        COALESCE(sl.unit, U&'\AC1C'),
        COALESCE(sl.grade, ''),
        COALESCE(sl.origin, ''),
        CASE WHEN p_split_mode = 'manufacture'
             THEN sl.manufacture_date ELSE NULL END,
        CASE WHEN p_split_mode = 'expiry'
             THEN sl.expiry_date ELSE NULL END,
        CASE WHEN p_split_mode = 'lot_number'
             THEN sl.lot_number ELSE NULL END
    HAVING SUM(sl.qty) != 0
    ORDER BY sl.product_name;
$$;
GRANT EXECUTE ON FUNCTION get_stock_snapshot_agg(DATE, TEXT, BIGINT)
    TO authenticated, service_role, anon;


-- ── A-1. rpc_check_event_uid_exists (p_biz_id 추가) ──────────────────────

DROP FUNCTION IF EXISTS rpc_check_event_uid_exists(TEXT[]);
DROP FUNCTION IF EXISTS rpc_check_event_uid_exists(TEXT[], BIGINT);
CREATE OR REPLACE FUNCTION rpc_check_event_uid_exists(
    p_uids   TEXT[],
    p_biz_id BIGINT DEFAULT NULL
)
RETURNS TABLE(event_uid TEXT)
LANGUAGE sql STABLE SECURITY DEFINER SET statement_timeout = '10s'
AS $$
    SELECT DISTINCT sl.event_uid
    FROM stock_ledger sl
    WHERE sl.event_uid = ANY(p_uids)
      AND sl.status = 'active'
      AND (p_biz_id IS NULL OR sl.biz_id = p_biz_id);
$$;
GRANT EXECUTE ON FUNCTION rpc_check_event_uid_exists(TEXT[], BIGINT)
    TO authenticated, service_role, anon;


-- ── A-2. rpc_check_raw_hash_exists (p_biz_id 추가) ───────────────────────

DROP FUNCTION IF EXISTS rpc_check_raw_hash_exists(TEXT[]);
DROP FUNCTION IF EXISTS rpc_check_raw_hash_exists(TEXT[], BIGINT);
CREATE OR REPLACE FUNCTION rpc_check_raw_hash_exists(
    p_hashes TEXT[],
    p_biz_id BIGINT DEFAULT NULL
)
RETURNS TABLE(raw_hash TEXT, channel TEXT)
LANGUAGE sql STABLE SECURITY DEFINER SET statement_timeout = '10s'
AS $$
    SELECT DISTINCT ot.raw_hash, ot.channel
    FROM order_transactions ot
    WHERE ot.raw_hash = ANY(p_hashes)
      AND (p_biz_id IS NULL OR ot.biz_id = p_biz_id);
$$;
GRANT EXECUTE ON FUNCTION rpc_check_raw_hash_exists(TEXT[], BIGINT)
    TO authenticated, service_role, anon;


-- ── A-3. rpc_check_order_no_exists (p_biz_id 추가) ───────────────────────

DROP FUNCTION IF EXISTS rpc_check_order_no_exists(TEXT, TEXT[]);
DROP FUNCTION IF EXISTS rpc_check_order_no_exists(TEXT, TEXT[], BIGINT);
CREATE OR REPLACE FUNCTION rpc_check_order_no_exists(
    p_channel   TEXT,
    p_order_nos TEXT[],
    p_biz_id    BIGINT DEFAULT NULL
)
RETURNS TABLE(channel TEXT, order_no TEXT, line_no INTEGER, raw_hash TEXT, status TEXT, id BIGINT)
LANGUAGE sql STABLE SECURITY DEFINER SET statement_timeout = '10s'
AS $$
    SELECT ot.channel, ot.order_no, ot.line_no, ot.raw_hash, ot.status, ot.id
    FROM order_transactions ot
    WHERE ot.channel  = p_channel
      AND ot.order_no = ANY(p_order_nos)
      AND (p_biz_id IS NULL OR ot.biz_id = p_biz_id);
$$;
GRANT EXECUTE ON FUNCTION rpc_check_order_no_exists(TEXT, TEXT[], BIGINT)
    TO authenticated, service_role, anon;


-- ── C-6. rpc_get_outbound_list (p_biz_id 추가) ───────────────────────────

DROP FUNCTION IF EXISTS rpc_get_outbound_list(TEXT, TEXT, TEXT, TEXT);
DROP FUNCTION IF EXISTS rpc_get_outbound_list(TEXT, TEXT, TEXT, TEXT, BIGINT);
CREATE OR REPLACE FUNCTION rpc_get_outbound_list(
    p_date_from TEXT   DEFAULT NULL,
    p_date_to   TEXT   DEFAULT NULL,
    p_location  TEXT   DEFAULT NULL,
    p_product   TEXT   DEFAULT NULL,
    p_biz_id    BIGINT DEFAULT NULL
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
      AND (p_biz_id IS NULL OR sl.biz_id = p_biz_id)

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
      AND (p_biz_id IS NULL OR mt.biz_id = p_biz_id)

    ORDER BY 2 DESC;
$$;
GRANT EXECUTE ON FUNCTION rpc_get_outbound_list(TEXT, TEXT, TEXT, TEXT, BIGINT)
    TO authenticated, service_role, anon;
