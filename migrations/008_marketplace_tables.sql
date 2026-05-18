-- 008: marketplace API 연동 테이블 (멀티테넌트 biz_id 기반)
--   marketplace_api_config  — 채널별 API 키/OAuth 설정
--   api_orders              — 마켓플레이스 주문 원본
--   api_settlements         — 마켓플레이스 정산 원본
--   api_sync_log            — 동기화 이력

-- ── marketplace_api_config ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS marketplace_api_config (
    id               BIGSERIAL PRIMARY KEY,
    biz_id           BIGINT      NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    channel          TEXT        NOT NULL,
    client_id        TEXT        NOT NULL DEFAULT '',
    client_secret    TEXT        NOT NULL DEFAULT '',
    is_active        BOOLEAN     NOT NULL DEFAULT FALSE,
    integration_type TEXT        NOT NULL DEFAULT 'api',   -- 'api' | 'bridge' | 'oauth'
    extra_config     JSONB       NOT NULL DEFAULT '{}'::jsonb,
    last_synced_at   TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (biz_id, channel)
);
CREATE INDEX IF NOT EXISTS idx_mac_biz ON marketplace_api_config(biz_id);
CREATE INDEX IF NOT EXISTS idx_mac_active ON marketplace_api_config(biz_id, is_active) WHERE is_active;


-- ── api_orders ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS api_orders (
    id               BIGSERIAL PRIMARY KEY,
    biz_id           BIGINT      NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    channel          TEXT        NOT NULL,
    api_order_id     TEXT        NOT NULL DEFAULT '',
    api_line_id      TEXT        NOT NULL DEFAULT '',
    order_date       DATE,
    product_name     TEXT,
    option_name      TEXT,
    qty              NUMERIC     DEFAULT 0,
    unit_price       NUMERIC     DEFAULT 0,
    total_amount     NUMERIC     DEFAULT 0,
    discount_amount  NUMERIC     DEFAULT 0,
    settlement_amount NUMERIC    DEFAULT 0,
    commission       NUMERIC     DEFAULT 0,
    shipping_fee     NUMERIC     DEFAULT 0,
    fee_detail       JSONB       DEFAULT '{}'::jsonb,
    order_status     TEXT,
    match_status     TEXT,
    seller_product_id TEXT,
    raw_data         JSONB       DEFAULT '{}'::jsonb,
    raw_hash         TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (biz_id, channel, api_order_id, api_line_id)
);
CREATE INDEX IF NOT EXISTS idx_ao_biz_date    ON api_orders(biz_id, order_date DESC);
CREATE INDEX IF NOT EXISTS idx_ao_biz_channel ON api_orders(biz_id, channel, order_date DESC);
CREATE INDEX IF NOT EXISTS idx_ao_raw_hash    ON api_orders(biz_id, raw_hash) WHERE raw_hash IS NOT NULL;


-- ── api_settlements ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS api_settlements (
    id                  BIGSERIAL PRIMARY KEY,
    biz_id              BIGINT    NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    channel             TEXT      NOT NULL,
    settlement_date     DATE      NOT NULL,
    settlement_id       TEXT      NOT NULL DEFAULT '',
    gross_sales         NUMERIC   DEFAULT 0,
    total_commission    NUMERIC   DEFAULT 0,
    shipping_fee_income NUMERIC   DEFAULT 0,
    shipping_fee_cost   NUMERIC   DEFAULT 0,
    coupon_discount     NUMERIC   DEFAULT 0,
    point_discount      NUMERIC   DEFAULT 0,
    other_deductions    NUMERIC   DEFAULT 0,
    net_settlement      NUMERIC   DEFAULT 0,
    fee_breakdown       JSONB     DEFAULT '{}'::jsonb,
    raw_data            JSONB     DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (biz_id, channel, settlement_date, settlement_id)
);
CREATE INDEX IF NOT EXISTS idx_as_biz_date    ON api_settlements(biz_id, settlement_date DESC);
CREATE INDEX IF NOT EXISTS idx_as_biz_channel ON api_settlements(biz_id, channel, settlement_date DESC);


-- ── api_sync_log ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS api_sync_log (
    id            BIGSERIAL PRIMARY KEY,
    biz_id        BIGINT    REFERENCES businesses(id) ON DELETE SET NULL,
    channel       TEXT      NOT NULL,
    sync_type     TEXT      NOT NULL DEFAULT 'orders',  -- 'orders' | 'settlements' | 'ad_costs'
    status        TEXT      NOT NULL DEFAULT 'running', -- 'running' | 'success' | 'error'
    date_from     DATE,
    date_to       DATE,
    triggered_by  TEXT,
    fetched       INT       DEFAULT 0,
    new           INT       DEFAULT 0,
    updated       INT       DEFAULT 0,
    error_message TEXT,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_asl_biz     ON api_sync_log(biz_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_asl_channel ON api_sync_log(channel, started_at DESC);
