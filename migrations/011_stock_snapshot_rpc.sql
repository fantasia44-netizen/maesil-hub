-- 011: get_stock_snapshot_agg RPC
-- stock_ledger 전체 스캔(34K 행) 대신 DB-side 집계로 성능 개선.
-- p_date_to 기준 재고 스냅샷을 manufacture/expiry/lot_number 분할 지원.
-- NOTE: biz_id 파라미터 미포함 (service_role 전용, 추후 멀티테넌트화 예정)

DROP FUNCTION IF EXISTS get_stock_snapshot_agg(DATE, TEXT);
CREATE OR REPLACE FUNCTION get_stock_snapshot_agg(
    p_date_to    DATE,
    p_split_mode TEXT DEFAULT NULL
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

GRANT EXECUTE ON FUNCTION get_stock_snapshot_agg(DATE, TEXT)
    TO authenticated, service_role, anon;
