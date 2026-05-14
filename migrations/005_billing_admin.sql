-- 005: billing/admin extensions
-- ports SaaS billing fields from maesil-insight

-- subscriptions: failed retry tracking + auto-renew flag + cancel_reason
ALTER TABLE subscriptions
    ADD COLUMN IF NOT EXISTS auto_renewal           BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS billing_key_pg         TEXT,
    ADD COLUMN IF NOT EXISTS card_info              JSONB,
    ADD COLUMN IF NOT EXISTS next_billing_at        TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS failed_attempt_count   INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_retry_at          TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS cancel_reason          TEXT;

-- payments: VAT split + order_name + receipt + refund tracking
ALTER TABLE payments
    ADD COLUMN IF NOT EXISTS supply_amount          INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS order_name             TEXT,
    ADD COLUMN IF NOT EXISTS payment_type           TEXT NOT NULL DEFAULT 'subscription',
    ADD COLUMN IF NOT EXISTS pg_provider            TEXT,
    ADD COLUMN IF NOT EXISTS receipt_url            TEXT,
    ADD COLUMN IF NOT EXISTS method                 TEXT,
    ADD COLUMN IF NOT EXISTS refund_status          TEXT,
    ADD COLUMN IF NOT EXISTS refund_amount          INTEGER,
    ADD COLUMN IF NOT EXISTS refund_reason          TEXT,
    ADD COLUMN IF NOT EXISTS refund_payment_id      TEXT,
    ADD COLUMN IF NOT EXISTS refund_requested_at    TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS refunded_at            TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS failed_at              TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS updated_at             TIMESTAMPTZ NOT NULL DEFAULT now();

-- businesses: trial / onboarding / subscription_status mirror
ALTER TABLE businesses
    ADD COLUMN IF NOT EXISTS trial_ends_at          TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS subscription_status    TEXT NOT NULL DEFAULT 'trial',
    ADD COLUMN IF NOT EXISTS onboarding_completed   BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS onboarding_step        INTEGER NOT NULL DEFAULT 0;

-- saas_config: category for grouping in admin UI
ALTER TABLE saas_config
    ADD COLUMN IF NOT EXISTS category               TEXT NOT NULL DEFAULT 'general';

CREATE INDEX IF NOT EXISTS idx_saas_config_category ON saas_config(category);
