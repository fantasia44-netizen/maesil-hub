-- 023_app_settings.sql
-- Ported from maesil-total migrations 029/030, redesigned per-tenant.
-- Generic key-value settings store for unmanned automation toggles
-- (auto-collect / auto-CJ-invoice ON·OFF + last-run history).
-- total used a GLOBAL key PK; hub must scope per tenant → composite PK (biz_id, key)
-- so tenant A's toggle never affects tenant B.
-- No global seed rows: get_app_setting returns its `default` when a row is missing,
-- so per-tenant defaults are implicit (auto_collect default ON handled in code).

CREATE TABLE IF NOT EXISTS app_settings (
    biz_id      BIGINT NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    key         TEXT NOT NULL,
    value       JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at  TIMESTAMPTZ DEFAULT now(),
    updated_by  TEXT DEFAULT '',
    PRIMARY KEY (biz_id, key)
);

-- RLS (003 pattern: service_role full + tenant isolation)
ALTER TABLE app_settings ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS app_settings_service_all ON app_settings;
CREATE POLICY app_settings_service_all ON app_settings
    FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS app_settings_tenant_isolation ON app_settings;
CREATE POLICY app_settings_tenant_isolation ON app_settings
    FOR ALL TO authenticated
    USING (biz_id = NULLIF(current_setting('app.current_biz_id', TRUE), '')::BIGINT)
    WITH CHECK (biz_id = NULLIF(current_setting('app.current_biz_id', TRUE), '')::BIGINT);

GRANT ALL ON app_settings TO authenticated, service_role, anon;
