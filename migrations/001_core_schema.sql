-- 001: maesil-hub core schema
-- common tables (no biz_id, system-wide)
-- - businesses, app_users, user_business_map
-- - plans, subscriptions, payments
-- - saas_config, audit_logs

CREATE TABLE IF NOT EXISTS businesses (
    id              BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    biz_reg_no      TEXT,
    representative  TEXT,
    address         TEXT,
    industry        TEXT,
    plan_id         BIGINT,
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_deleted      BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_by      TEXT,
    metadata        JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_businesses_status ON businesses(status) WHERE NOT is_deleted;
CREATE UNIQUE INDEX IF NOT EXISTS uq_businesses_biz_reg_no ON businesses(biz_reg_no)
    WHERE biz_reg_no IS NOT NULL AND NOT is_deleted;


CREATE TABLE IF NOT EXISTS app_users (
    id              BIGSERIAL PRIMARY KEY,
    email           TEXT NOT NULL,
    password_hash   TEXT NOT NULL,
    name            TEXT,
    phone           TEXT,
    is_super_admin  BOOLEAN NOT NULL DEFAULT FALSE,
    email_verified  BOOLEAN NOT NULL DEFAULT FALSE,
    last_login_at   TIMESTAMPTZ,
    failed_login_count INTEGER NOT NULL DEFAULT 0,
    locked_until    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_deleted      BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_app_users_email ON app_users(email) WHERE NOT is_deleted;


CREATE TABLE IF NOT EXISTS user_business_map (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES app_users(id),
    biz_id      BIGINT NOT NULL REFERENCES businesses(id),
    role        TEXT NOT NULL,
    is_primary  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, biz_id)
);
CREATE INDEX IF NOT EXISTS idx_ubm_user ON user_business_map(user_id);
CREATE INDEX IF NOT EXISTS idx_ubm_biz ON user_business_map(biz_id);


CREATE TABLE IF NOT EXISTS plans (
    id              BIGSERIAL PRIMARY KEY,
    code            TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    monthly_price   INTEGER NOT NULL DEFAULT 0,
    features        JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order      INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE IF NOT EXISTS subscriptions (
    id                    BIGSERIAL PRIMARY KEY,
    biz_id                BIGINT NOT NULL UNIQUE REFERENCES businesses(id),
    plan_id               BIGINT NOT NULL REFERENCES plans(id),
    status                TEXT NOT NULL DEFAULT 'trial',
    current_period_start  TIMESTAMPTZ NOT NULL,
    current_period_end    TIMESTAMPTZ NOT NULL,
    cancelled_at          TIMESTAMPTZ,
    portone_billing_key   TEXT,
    metadata              JSONB DEFAULT '{}'::jsonb,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions(status);


CREATE TABLE IF NOT EXISTS payments (
    id                   BIGSERIAL PRIMARY KEY,
    biz_id               BIGINT NOT NULL REFERENCES businesses(id),
    subscription_id      BIGINT REFERENCES subscriptions(id),
    portone_payment_id   TEXT NOT NULL UNIQUE,
    portone_merchant_uid TEXT NOT NULL UNIQUE,
    amount               INTEGER NOT NULL,
    vat_amount           INTEGER NOT NULL DEFAULT 0,
    status               TEXT NOT NULL,
    paid_at              TIMESTAMPTZ,
    raw_response         JSONB,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_payments_biz ON payments(biz_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status, created_at DESC);


CREATE TABLE IF NOT EXISTS saas_config (
    key              TEXT PRIMARY KEY,
    value_encrypted  BYTEA,
    value_plain      TEXT,
    description      TEXT,
    updated_by       BIGINT REFERENCES app_users(id),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE IF NOT EXISTS audit_logs (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT REFERENCES app_users(id),
    biz_id      BIGINT REFERENCES businesses(id),
    operator_id BIGINT REFERENCES app_users(id),
    action      TEXT NOT NULL,
    detail      JSONB,
    ip_address  INET,
    user_agent  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_logs_biz ON audit_logs(biz_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_user ON audit_logs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs(action, created_at DESC);


-- FK 추가 (businesses.plan_id → plans.id) 순환 참조 방지 위해 마지막에
ALTER TABLE businesses
    ADD CONSTRAINT fk_businesses_plan
    FOREIGN KEY (plan_id) REFERENCES plans(id) ON DELETE SET NULL;
