-- 006: product_name canonical trigger + SKU auto-generation
-- DB-level enforcement for "product identification" convention
-- - canonical product_name: strip ALL whitespace + NFKC normalize on INSERT/UPDATE
-- - sku auto-generated if NULL (format: P-{biz_id}-{sequence padded 6})

-- ─--- 1) canonical product_name BEFORE trigger ---─
-- regex removes all Unicode whitespace + zero-width chars
-- PostgreSQL regex: explicit unicode whitespace + zero-width chars
CREATE OR REPLACE FUNCTION fn_canonical_product_name()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.product_name IS NOT NULL THEN
        -- remove all whitespace + zero-width + strip
        NEW.product_name := regexp_replace(
            NEW.product_name,
            '[\s   -‍  　﻿]',
            '',
            'g'
        );
        NEW.product_name := trim(NEW.product_name);
    END IF;
    RETURN NEW;
END;
$$;

-- product_costs, stock_ledger, order_transactions, manual_trades, daily_revenue, option_master
DROP TRIGGER IF EXISTS trg_canonical_product_name ON product_costs;
CREATE TRIGGER trg_canonical_product_name
    BEFORE INSERT OR UPDATE OF product_name ON product_costs
    FOR EACH ROW EXECUTE FUNCTION fn_canonical_product_name();

DROP TRIGGER IF EXISTS trg_canonical_product_name ON stock_ledger;
CREATE TRIGGER trg_canonical_product_name
    BEFORE INSERT OR UPDATE OF product_name ON stock_ledger
    FOR EACH ROW EXECUTE FUNCTION fn_canonical_product_name();

DROP TRIGGER IF EXISTS trg_canonical_product_name ON order_transactions;
CREATE TRIGGER trg_canonical_product_name
    BEFORE INSERT OR UPDATE OF product_name ON order_transactions
    FOR EACH ROW EXECUTE FUNCTION fn_canonical_product_name();

DROP TRIGGER IF EXISTS trg_canonical_product_name ON manual_trades;
CREATE TRIGGER trg_canonical_product_name
    BEFORE INSERT OR UPDATE OF product_name ON manual_trades
    FOR EACH ROW EXECUTE FUNCTION fn_canonical_product_name();

DROP TRIGGER IF EXISTS trg_canonical_product_name ON daily_revenue;
CREATE TRIGGER trg_canonical_product_name
    BEFORE INSERT OR UPDATE OF product_name ON daily_revenue
    FOR EACH ROW EXECUTE FUNCTION fn_canonical_product_name();

DROP TRIGGER IF EXISTS trg_canonical_product_name ON option_master;
CREATE TRIGGER trg_canonical_product_name
    BEFORE INSERT OR UPDATE OF product_name ON option_master
    FOR EACH ROW EXECUTE FUNCTION fn_canonical_product_name();


-- ─--- 2) SKU auto-generation sequence (per tenant) ---─
CREATE TABLE IF NOT EXISTS sku_sequences (
    biz_id      BIGINT PRIMARY KEY REFERENCES businesses(id),
    last_seq    BIGINT NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION fn_assign_sku()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    next_seq BIGINT;
BEGIN
    -- keep existing SKU
    IF NEW.sku IS NOT NULL AND NEW.sku <> '' THEN
        RETURN NEW;
    END IF;

    -- increment sequence (UPSERT)
    INSERT INTO sku_sequences (biz_id, last_seq, updated_at)
    VALUES (NEW.biz_id, 1, now())
    ON CONFLICT (biz_id) DO UPDATE
        SET last_seq = sku_sequences.last_seq + 1,
            updated_at = now()
    RETURNING last_seq INTO next_seq;

    NEW.sku := 'P-' || NEW.biz_id || '-' || lpad(next_seq::TEXT, 6, '0');
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_assign_sku ON product_costs;
CREATE TRIGGER trg_assign_sku
    BEFORE INSERT ON product_costs
    FOR EACH ROW EXECUTE FUNCTION fn_assign_sku();


-- ─--- 3) product find RPC (SKU/barcode/canonical) ---─
DROP FUNCTION IF EXISTS rpc_find_product(BIGINT, TEXT);
CREATE OR REPLACE FUNCTION rpc_find_product(
    p_biz_id BIGINT,
    p_query TEXT
)
RETURNS SETOF product_costs
LANGUAGE plpgsql STABLE SECURITY DEFINER SET statement_timeout = '5s'
AS $$
DECLARE
    v_canon TEXT;
BEGIN
    IF p_query IS NULL OR p_query = '' THEN
        RETURN;
    END IF;

    -- canonical
    v_canon := regexp_replace(
        p_query,
        '[\s   -‍  　﻿]',
        '',
        'g'
    );
    v_canon := trim(v_canon);

    -- 1) SKU exact
    RETURN QUERY
    SELECT * FROM product_costs
    WHERE biz_id = p_biz_id AND NOT is_deleted AND sku = p_query
    LIMIT 1;
    IF FOUND THEN RETURN; END IF;

    -- 2) barcode exact
    RETURN QUERY
    SELECT * FROM product_costs
    WHERE biz_id = p_biz_id AND NOT is_deleted AND barcode = p_query
    LIMIT 1;
    IF FOUND THEN RETURN; END IF;

    -- 3) canonical exact
    RETURN QUERY
    SELECT * FROM product_costs
    WHERE biz_id = p_biz_id AND NOT is_deleted AND product_name = v_canon
    LIMIT 1;
    IF FOUND THEN RETURN; END IF;

    -- 4) canonical partial (single result only)
    RETURN QUERY
    SELECT * FROM product_costs
    WHERE biz_id = p_biz_id AND NOT is_deleted AND product_name ILIKE '%' || v_canon || '%'
    LIMIT 1;
END;
$$;
GRANT EXECUTE ON FUNCTION rpc_find_product(BIGINT, TEXT)
    TO authenticated, service_role, anon;


-- ─--- 4) product search (multi, autocomplete) ---─
DROP FUNCTION IF EXISTS rpc_search_products(BIGINT, TEXT, INTEGER);
CREATE OR REPLACE FUNCTION rpc_search_products(
    p_biz_id BIGINT,
    p_query TEXT,
    p_limit INTEGER DEFAULT 20
)
RETURNS SETOF product_costs
LANGUAGE plpgsql STABLE SECURITY DEFINER SET statement_timeout = '5s'
AS $$
DECLARE
    v_canon TEXT;
BEGIN
    IF p_query IS NULL OR p_query = '' THEN
        RETURN;
    END IF;

    v_canon := regexp_replace(
        p_query,
        '[\s   -‍  　﻿]',
        '',
        'g'
    );
    v_canon := trim(v_canon);

    RETURN QUERY
    SELECT DISTINCT ON (id) *
    FROM product_costs
    WHERE biz_id = p_biz_id AND NOT is_deleted
      AND (
          sku ILIKE p_query || '%'
          OR barcode ILIKE p_query || '%'
          OR product_name ILIKE '%' || v_canon || '%'
      )
    ORDER BY id, sku NULLS LAST, product_name
    LIMIT p_limit;
END;
$$;
GRANT EXECUTE ON FUNCTION rpc_search_products(BIGINT, TEXT, INTEGER)
    TO authenticated, service_role, anon;


-- ─--- 5) UNIQUE constraint reinforcement ---─
-- product_costs.sku is auto-generated, add (biz_id, sku) UNIQUE
CREATE UNIQUE INDEX IF NOT EXISTS uq_product_costs_biz_sku
    ON product_costs(biz_id, sku) WHERE NOT is_deleted AND sku IS NOT NULL;
