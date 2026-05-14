-- 004: seed default plans
-- Korean labels via U&'\XXXX' escape

INSERT INTO plans (code, name, monthly_price, features, is_active, sort_order) VALUES
(
    'starter',
    U&'\C2A4\D0C0\D130',  -- 스타터
    49000,
    '{
        "channels": 1,
        "users": 2,
        "storage_gb": 2,
        "ai_diagnose": false,
        "advanced_reports": false,
        "api_access": false,
        "support": "email",
        "automation_runs_per_day": 5
    }'::jsonb,
    TRUE,
    1
),
(
    'pro',
    U&'\D504\B85C',  -- 프로
    149000,
    '{
        "channels": 5,
        "users": 10,
        "storage_gb": 20,
        "ai_diagnose": true,
        "advanced_reports": true,
        "api_access": true,
        "support": "chat",
        "automation_runs_per_day": 50
    }'::jsonb,
    TRUE,
    2
),
(
    'enterprise',
    U&'\C5D4\D130\D504\B77C\C774\C988',  -- 엔터프라이즈
    699000,
    '{
        "channels": -1,
        "users": -1,
        "storage_gb": 500,
        "ai_diagnose": true,
        "advanced_reports": true,
        "api_access": true,
        "support": "phone",
        "automation_runs_per_day": -1,
        "dedicated_account_manager": true,
        "custom_integration": true
    }'::jsonb,
    TRUE,
    3
)
ON CONFLICT (code) DO UPDATE SET
    name = EXCLUDED.name,
    monthly_price = EXCLUDED.monthly_price,
    features = EXCLUDED.features,
    is_active = EXCLUDED.is_active,
    sort_order = EXCLUDED.sort_order;
