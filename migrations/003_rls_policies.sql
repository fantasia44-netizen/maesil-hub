-- 003: RLS policies for all business tables
-- service_role: full access (server backend)
-- authenticated: tenant_isolation by app.current_biz_id session var

-- helper: set_app_setting (server uses to set tenant context per request)
CREATE OR REPLACE FUNCTION set_app_setting(p_key TEXT, p_value TEXT)
RETURNS VOID
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
AS $$
BEGIN
    PERFORM set_config(p_key, p_value, TRUE);  -- TRUE = local (transaction scope)
END;
$$;
GRANT EXECUTE ON FUNCTION set_app_setting(TEXT, TEXT)
    TO authenticated, service_role, anon;


-- enable RLS on all business tables
ALTER TABLE product_costs        ENABLE ROW LEVEL SECURITY;
ALTER TABLE option_master        ENABLE ROW LEVEL SECURITY;
ALTER TABLE stock_ledger         ENABLE ROW LEVEL SECURITY;
ALTER TABLE import_runs          ENABLE ROW LEVEL SECURITY;
ALTER TABLE order_transactions   ENABLE ROW LEVEL SECURITY;
ALTER TABLE order_shipping       ENABLE ROW LEVEL SECURITY;
ALTER TABLE order_change_log     ENABLE ROW LEVEL SECURITY;
ALTER TABLE manual_trades        ENABLE ROW LEVEL SECURITY;
ALTER TABLE daily_revenue        ENABLE ROW LEVEL SECURITY;
ALTER TABLE packing_jobs         ENABLE ROW LEVEL SECURITY;
ALTER TABLE business_partners    ENABLE ROW LEVEL SECURITY;
ALTER TABLE purchase_orders      ENABLE ROW LEVEL SECURITY;
ALTER TABLE my_business          ENABLE ROW LEVEL SECURITY;


-- service_role policies (full access, bypass tenant)
DO $$
DECLARE
    t TEXT;
BEGIN
    FOR t IN SELECT unnest(ARRAY[
        'product_costs','option_master','stock_ledger','import_runs',
        'order_transactions','order_shipping','order_change_log','manual_trades',
        'daily_revenue','packing_jobs','business_partners','purchase_orders','my_business'
    ]) LOOP
        EXECUTE format('CREATE POLICY service_role_all ON %I FOR ALL TO service_role USING (true) WITH CHECK (true);', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I FOR ALL TO authenticated ' ||
            'USING (biz_id = NULLIF(current_setting(''app.current_biz_id'', TRUE), '''')::BIGINT) ' ||
            'WITH CHECK (biz_id = NULLIF(current_setting(''app.current_biz_id'', TRUE), '''')::BIGINT);',
            t
        );
    END LOOP;
END;
$$;


-- common tables: only service_role + authenticated (no tenant filter for businesses/users themselves)
ALTER TABLE businesses          ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_users           ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_business_map   ENABLE ROW LEVEL SECURITY;
ALTER TABLE plans               ENABLE ROW LEVEL SECURITY;
ALTER TABLE subscriptions       ENABLE ROW LEVEL SECURITY;
ALTER TABLE payments            ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas_config         ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs          ENABLE ROW LEVEL SECURITY;

-- service_role full access on common tables
CREATE POLICY service_role_all ON businesses        FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY service_role_all ON app_users         FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY service_role_all ON user_business_map FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY service_role_all ON plans             FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY service_role_all ON subscriptions     FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY service_role_all ON payments          FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY service_role_all ON saas_config       FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY service_role_all ON audit_logs        FOR ALL TO service_role USING (true) WITH CHECK (true);

-- authenticated read of own businesses (via user_business_map)
-- (Phase 1 detailed; Phase 0 is server-side only via service_role)
