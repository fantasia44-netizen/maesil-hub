-- 032_order_shipping_packing_cols.sql
-- 패킹센터 바코드조회(api_lookup_barcode)가 참조하는 컬럼 추가.
-- invoice_no_clean: 하이픈 제거 송장번호(생성컬럼, 자동유지) — 바코드 exact match용.
-- is_anonymized: PII 익명화 플래그(기본 false).

ALTER TABLE order_shipping
    ADD COLUMN IF NOT EXISTS is_anonymized BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE order_shipping
    ADD COLUMN IF NOT EXISTS invoice_no_clean TEXT
    GENERATED ALWAYS AS (replace(COALESCE(invoice_no, ''), '-', '')) STORED;

CREATE INDEX IF NOT EXISTS idx_order_shipping_invoice_clean
    ON order_shipping (biz_id, invoice_no_clean);
