-- 022_set_batch_id.sql
-- Ported from maesil-total migration 031.
-- stock_ledger.set_batch_id — groups all rows of one set-assembly job
-- (1 SET_IN + N SET_OUT) so a whole job can be cancelled atomically
-- (restore components + remove the produced set). Legacy rows are NULL →
-- code falls back to (created_at, location) grouping.
-- biz-independent column; stock_ledger already carries biz_id + RLS.

ALTER TABLE stock_ledger ADD COLUMN IF NOT EXISTS set_batch_id TEXT;

CREATE INDEX IF NOT EXISTS idx_stock_ledger_set_batch
    ON stock_ledger (set_batch_id)
    WHERE set_batch_id IS NOT NULL;
