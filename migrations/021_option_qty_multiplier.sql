-- 021_option_qty_multiplier.sql
-- Ported from maesil-total migration 034.
-- Gift-option quantity multiplier (1+1 / 1+2). One ordered unit ships/deducts
-- qty * qty_multiplier. Revenue stays at ordered qty (gift portion is free).
-- option_master is biz-scoped (UNIQUE(biz_id, match_key)); this column is a
-- per-row attribute so no additional tenant plumbing is required.

ALTER TABLE option_master
    ADD COLUMN IF NOT EXISTS qty_multiplier INT NOT NULL DEFAULT 1;

COMMENT ON COLUMN option_master.qty_multiplier IS
    'Gift-option multiplier (1+1=2, 1+2=3). Outbound/stock deduction = order_qty * multiplier. Revenue stays at order_qty.';

-- Guard against zero/negative
ALTER TABLE option_master
    DROP CONSTRAINT IF EXISTS option_master_qty_multiplier_chk;
ALTER TABLE option_master
    ADD CONSTRAINT option_master_qty_multiplier_chk CHECK (qty_multiplier >= 1);
