-- 002: maesil-hub business tables (biz_id required, multi-tenant)
-- Korean literal U&'\C815\C0C1' = jeongsang (active/normal status)
-- Korean literal U&'\C81C\D488' = jepum (product / finished goods)

-- ──────────────────────────────────────────────
-- product_costs (product / raw material / sub-material master)
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS product_costs (
    id              BIGSERIAL PRIMARY KEY,
    biz_id          BIGINT NOT NULL REFERENCES businesses(id),
    product_name    TEXT NOT NULL,
    cost_price      NUMERIC(12,2) DEFAULT 0,
    unit            TEXT,
    weight          NUMERIC(10,2) DEFAULT 0,
    weight_unit     TEXT DEFAULT 'g',
    cost_type       TEXT,
    material_type   TEXT,
    category        TEXT,
    purchase_unit   TEXT,
    standard_unit   TEXT,
    conversion_ratio NUMERIC(10,4) DEFAULT 1,
    safety_stock    NUMERIC(10,2) DEFAULT 0,
    lead_time_days  INTEGER DEFAULT 0,
    storage_method  TEXT,
    food_type       TEXT,
    sales_category  TEXT,
    is_stock_managed BOOLEAN DEFAULT TRUE,
    is_production_target BOOLEAN,
    barcode         TEXT,
    sku             TEXT,
    memo            TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_deleted      BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_by      TEXT,
    UNIQUE(biz_id, product_name)
);
CREATE INDEX IF NOT EXISTS idx_pc_biz ON product_costs(biz_id) WHERE NOT is_deleted;
CREATE INDEX IF NOT EXISTS idx_pc_biz_cat ON product_costs(biz_id, category) WHERE NOT is_deleted;
CREATE INDEX IF NOT EXISTS idx_pc_biz_sku ON product_costs(biz_id, sku) WHERE sku IS NOT NULL AND NOT is_deleted;


-- ──────────────────────────────────────────────
-- option_master (channel option -> standard SKU)
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS option_master (
    id              BIGSERIAL PRIMARY KEY,
    biz_id          BIGINT NOT NULL REFERENCES businesses(id),
    original_name   TEXT NOT NULL,
    product_name    TEXT NOT NULL,
    line_code       TEXT,
    sort_order      NUMERIC(10,2) DEFAULT 999,
    barcode         TEXT,
    match_key       TEXT,
    last_matched_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_deleted      BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE(biz_id, match_key)
);
CREATE INDEX IF NOT EXISTS idx_om_biz ON option_master(biz_id) WHERE NOT is_deleted;
CREATE INDEX IF NOT EXISTS idx_om_biz_product ON option_master(biz_id, product_name);


-- ──────────────────────────────────────────────
-- stock_ledger (inventory in/out ledger)
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS stock_ledger (
    id              BIGSERIAL PRIMARY KEY,
    biz_id          BIGINT NOT NULL REFERENCES businesses(id),
    transaction_date DATE NOT NULL,
    type            TEXT NOT NULL,
    product_name    TEXT NOT NULL,
    qty             NUMERIC(12,2) NOT NULL,
    unit            TEXT,
    location        TEXT,
    category        TEXT,
    storage_method  TEXT,
    lot_number      TEXT,
    grade           TEXT,
    manufacture_date DATE,
    expiry_date     DATE,
    origin          TEXT,
    batch_id        TEXT,
    transfer_id     TEXT,
    repack_doc_no   TEXT,
    event_uid       TEXT,
    memo            TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    channel         TEXT,
    created_by      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_deleted      BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_by      TEXT
);
CREATE INDEX IF NOT EXISTS idx_sl_biz_date ON stock_ledger(biz_id, transaction_date) WHERE status='active';
CREATE INDEX IF NOT EXISTS idx_sl_biz_type_date ON stock_ledger(biz_id, type, transaction_date) WHERE status='active';
CREATE INDEX IF NOT EXISTS idx_sl_biz_product ON stock_ledger(biz_id, product_name) WHERE status='active';
CREATE INDEX IF NOT EXISTS idx_sl_biz_batch ON stock_ledger(biz_id, batch_id) WHERE batch_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_sl_biz_transfer ON stock_ledger(biz_id, transfer_id) WHERE transfer_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_sl_biz_event_uid ON stock_ledger(biz_id, event_uid) WHERE event_uid IS NOT NULL;


-- ──────────────────────────────────────────────
-- import_runs (order upload history)
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS import_runs (
    id              BIGSERIAL PRIMARY KEY,
    biz_id          BIGINT NOT NULL REFERENCES businesses(id),
    channel         TEXT NOT NULL,
    filename        TEXT,
    file_hash       TEXT,
    uploaded_by     TEXT,
    total_rows      INTEGER DEFAULT 0,
    inserted_count  INTEGER DEFAULT 0,
    updated_count   INTEGER DEFAULT 0,
    skipped_count   INTEGER DEFAULT 0,
    failed_count    INTEGER DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'processing',
    cancelled_by    TEXT,
    cancelled_at    TIMESTAMPTZ,
    error_message   TEXT,
    metadata        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ir_biz ON import_runs(biz_id, created_at DESC);


-- ──────────────────────────────────────────────
-- order_transactions (orders)
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS order_transactions (
    id                 BIGSERIAL PRIMARY KEY,
    biz_id             BIGINT NOT NULL REFERENCES businesses(id),
    order_date         DATE NOT NULL,
    channel            TEXT NOT NULL,
    order_no           TEXT NOT NULL,
    line_no            INTEGER NOT NULL DEFAULT 1,
    original_option    TEXT,
    original_product   TEXT,
    product_name       TEXT NOT NULL,
    option_name        TEXT,
    barcode            TEXT,
    line_code          INTEGER,
    sort_order         INTEGER,
    qty                INTEGER NOT NULL DEFAULT 0,
    unit_price         INTEGER NOT NULL DEFAULT 0,
    total_amount       INTEGER NOT NULL DEFAULT 0,
    discount_amount    INTEGER NOT NULL DEFAULT 0,
    settlement         INTEGER NOT NULL DEFAULT 0,
    commission         INTEGER NOT NULL DEFAULT 0,
    status             TEXT NOT NULL DEFAULT U&'\C815\C0C1',
    status_reason      TEXT,
    is_outbound_done   BOOLEAN NOT NULL DEFAULT FALSE,
    outbound_date      DATE,
    recipient_name     TEXT,
    collection_date    DATE,
    raw_hash           TEXT,
    raw_data           JSONB,
    parser_version     TEXT,
    import_run_id      BIGINT REFERENCES import_runs(id),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(biz_id, channel, order_no, line_no)
);
CREATE INDEX IF NOT EXISTS idx_ot_biz_date ON order_transactions(biz_id, order_date DESC);
CREATE INDEX IF NOT EXISTS idx_ot_biz_status_outbound ON order_transactions(biz_id, status, is_outbound_done, order_date)
    WHERE status = U&'\C815\C0C1';
CREATE INDEX IF NOT EXISTS idx_ot_biz_pending ON order_transactions(biz_id, is_outbound_done, order_date)
    WHERE status = U&'\C815\C0C1' AND is_outbound_done = FALSE;
CREATE INDEX IF NOT EXISTS idx_ot_biz_raw_hash ON order_transactions(biz_id, raw_hash) WHERE raw_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ot_biz_run ON order_transactions(biz_id, import_run_id) WHERE import_run_id IS NOT NULL;


-- ──────────────────────────────────────────────
-- order_shipping (waybill / shipment)
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS order_shipping (
    id              BIGSERIAL PRIMARY KEY,
    biz_id          BIGINT NOT NULL REFERENCES businesses(id),
    channel         TEXT NOT NULL,
    order_no        TEXT NOT NULL,
    recipient_name  TEXT,
    recipient_phone TEXT,
    address         TEXT,
    invoice_no      TEXT,
    courier         TEXT,
    shipping_status TEXT,
    label_printed   BOOLEAN DEFAULT FALSE,
    label_printed_at TIMESTAMPTZ,
    delivery_status TEXT,
    delivery_date   DATE,
    raw_data        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(biz_id, channel, order_no)
);
CREATE INDEX IF NOT EXISTS idx_os_biz_invoice ON order_shipping(biz_id, invoice_no) WHERE invoice_no IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_os_biz_recipient ON order_shipping(biz_id, recipient_name);


-- ──────────────────────────────────────────────
-- order_change_log
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS order_change_log (
    id                    BIGSERIAL PRIMARY KEY,
    biz_id                BIGINT NOT NULL REFERENCES businesses(id),
    order_transaction_id  BIGINT NOT NULL REFERENCES order_transactions(id),
    change_type           TEXT NOT NULL,
    field_name            TEXT,
    before_value          TEXT,
    after_value           TEXT,
    change_reason         TEXT,
    changed_by            TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ocl_biz_order ON order_change_log(biz_id, order_transaction_id, created_at DESC);


-- ──────────────────────────────────────────────
-- manual_trades (direct B2B partner outbound/purchase)
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS manual_trades (
    id              BIGSERIAL PRIMARY KEY,
    biz_id          BIGINT NOT NULL REFERENCES businesses(id),
    partner_name    TEXT NOT NULL,
    product_name    TEXT NOT NULL,
    trade_date      TEXT NOT NULL,
    trade_type      TEXT NOT NULL,
    qty             NUMERIC(12,2) NOT NULL,
    unit            TEXT,
    unit_price      INTEGER DEFAULT 0,
    amount          INTEGER DEFAULT 0,
    memo            TEXT,
    registered_by   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_deleted      BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_by      TEXT
);
CREATE INDEX IF NOT EXISTS idx_mt_biz_date ON manual_trades(biz_id, trade_date DESC) WHERE NOT is_deleted;
CREATE INDEX IF NOT EXISTS idx_mt_biz_partner ON manual_trades(biz_id, partner_name) WHERE NOT is_deleted;


-- ──────────────────────────────────────────────
-- daily_revenue (daily sales aggregate)
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS daily_revenue (
    id              BIGSERIAL PRIMARY KEY,
    biz_id          BIGINT NOT NULL REFERENCES businesses(id),
    revenue_date    DATE NOT NULL,
    product_name    TEXT NOT NULL,
    category        TEXT,
    channel         TEXT,
    qty             NUMERIC(12,2) DEFAULT 0,
    unit_price      INTEGER DEFAULT 0,
    revenue         INTEGER DEFAULT 0,
    settlement      INTEGER DEFAULT 0,
    commission      INTEGER DEFAULT 0,
    warehouse       TEXT,
    metadata        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_deleted      BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE(biz_id, revenue_date, product_name, category, channel)
);
CREATE INDEX IF NOT EXISTS idx_dr_biz_date ON daily_revenue(biz_id, revenue_date DESC) WHERE NOT is_deleted;


-- ──────────────────────────────────────────────
-- packing_jobs (packing center)
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS packing_jobs (
    id                BIGSERIAL PRIMARY KEY,
    biz_id            BIGINT NOT NULL REFERENCES businesses(id),
    user_id           BIGINT REFERENCES app_users(id),
    username          TEXT,
    channel           TEXT,
    order_no          TEXT,
    order_info        JSONB,
    status            TEXT NOT NULL DEFAULT 'pending',
    started_at        TIMESTAMPTZ,
    completed_at      TIMESTAMPTZ,
    video_path        TEXT,
    video_size_bytes  BIGINT DEFAULT 0,
    video_duration_ms INTEGER,
    metadata          JSONB,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_pj_biz_status ON packing_jobs(biz_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pj_biz_order ON packing_jobs(biz_id, order_no);


-- ──────────────────────────────────────────────
-- business_partners (B2B trading partners)
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS business_partners (
    id              BIGSERIAL PRIMARY KEY,
    biz_id          BIGINT NOT NULL REFERENCES businesses(id),
    partner_name    TEXT NOT NULL,
    biz_reg_no      TEXT,
    representative  TEXT,
    contact_name    TEXT,
    contact_phone   TEXT,
    contact_email   TEXT,
    address         TEXT,
    partner_type    TEXT,
    payment_terms   TEXT,
    memo            TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_deleted      BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE(biz_id, partner_name)
);
CREATE INDEX IF NOT EXISTS idx_bp_biz ON business_partners(biz_id) WHERE NOT is_deleted;


-- ──────────────────────────────────────────────
-- purchase_orders (PO)
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS purchase_orders (
    id              BIGSERIAL PRIMARY KEY,
    biz_id          BIGINT NOT NULL REFERENCES businesses(id),
    partner_name    TEXT NOT NULL,
    order_date      DATE NOT NULL,
    items           JSONB NOT NULL,
    total_amount    INTEGER DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'draft',
    expected_date   DATE,
    received_date   DATE,
    memo            TEXT,
    created_by      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_deleted      BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_by      TEXT
);
CREATE INDEX IF NOT EXISTS idx_po_biz_date ON purchase_orders(biz_id, order_date DESC) WHERE NOT is_deleted;


-- ──────────────────────────────────────────────
-- my_business (own business info — trade statement issuer info etc)
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS my_business (
    id              BIGSERIAL PRIMARY KEY,
    biz_id          BIGINT NOT NULL REFERENCES businesses(id),
    name            TEXT NOT NULL,
    biz_reg_no      TEXT,
    representative  TEXT,
    address         TEXT,
    contact_phone   TEXT,
    contact_email   TEXT,
    bank_account    TEXT,
    seal_image_url  TEXT,
    cj_cust_id      TEXT,
    cj_cust_id_b    TEXT,
    is_default      BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_deleted      BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_mb_biz ON my_business(biz_id) WHERE NOT is_deleted;
