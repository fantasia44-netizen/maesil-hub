-- 027_pnl_monthly_agg_nested.sql
-- get_pnl_monthly_agg를 total 중첩구조로 재작성 + 테넌트 격리(p_biz_id).
--
-- 배경: hub 기존 RPC는 revenue를 스칼라로 반환 → pnl_service._calc_revenue_v2가
--   중첩 dict(revenue.online_total 등)를 기대해 'int' object has no attribute 'get' 크래시.
--   또한 호출부가 p_biz_id 미전달 → 전 테넌트 집계(잠재 누수).
--
-- hub 현실: api_settlements만 존재(biz_id 有). tax_invoices/expenses 테이블 없음
--   → b2b/purchase/expenses는 빈 중첩객체로 반환(코드가 키를 읽어도 크래시 안 함).

DROP FUNCTION IF EXISTS get_pnl_monthly_agg(TEXT, TEXT, TEXT, BIGINT);
DROP FUNCTION IF EXISTS get_pnl_monthly_agg(TEXT, TEXT, TEXT);

CREATE OR REPLACE FUNCTION get_pnl_monthly_agg(
    p_date_from  TEXT,
    p_date_to    TEXT,
    p_year_month TEXT DEFAULT NULL,
    p_biz_id     BIGINT DEFAULT NULL
) RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER SET statement_timeout = '10s'
AS $$
DECLARE
    v_settle_prefixes TEXT[] := ARRAY[
        'nsettle_', 'wsettle_', 'rocket_', '11settle_',
        'tsettle_', 'osettle_', 'auction_', 'gmarket_'
    ];
    v_from DATE := p_date_from::DATE;
    v_to   DATE := p_date_to::DATE;
    v_online JSONB;
    v_ad     JSONB;
BEGIN
    -- 1) 온라인 매출 (api_settlements, 정산서 prefix) — biz_id 격리
    WITH filt AS (
        SELECT COALESCE(channel,'기타') AS channel,
               COALESCE(gross_sales,0)::BIGINT       AS gross,
               COALESCE(total_commission,0)::BIGINT  AS comm
        FROM api_settlements
        WHERE settlement_date BETWEEN v_from AND v_to
          AND settlement_id IS NOT NULL
          AND (p_biz_id IS NULL OR biz_id = p_biz_id)
          AND EXISTS (
              SELECT 1 FROM unnest(v_settle_prefixes) p
              WHERE settlement_id LIKE p || '%'
          )
    ),
    totals AS (
        SELECT COALESCE(SUM(gross),0)::BIGINT AS online_total,
               COALESCE(SUM(comm),0)::BIGINT  AS online_commission
        FROM filt
    ),
    by_ch AS (
        SELECT jsonb_object_agg(channel, sum_gross) AS by_channel,
               jsonb_object_agg(channel, sum_comm)  AS comm_by_channel
        FROM (
            SELECT channel, SUM(gross)::BIGINT AS sum_gross, SUM(comm)::BIGINT AS sum_comm
            FROM filt GROUP BY channel
        ) x
    )
    SELECT jsonb_build_object(
        'online_total',          t.online_total,
        'online_commission',     t.online_commission,
        'by_channel',            COALESCE(b.by_channel,      '{}'::jsonb),
        'commission_by_channel', COALESCE(b.comm_by_channel, '{}'::jsonb)
    ) INTO v_online
    FROM totals t CROSS JOIN by_ch b;

    -- 2) 광고비 (api_settlements, ad_cost_ prefix) — biz_id 격리
    WITH ad AS (
        SELECT COALESCE(channel,'기타') AS channel,
               COALESCE(other_deductions,0)::BIGINT AS ad_cost
        FROM api_settlements
        WHERE settlement_date BETWEEN v_from AND v_to
          AND settlement_id LIKE 'ad_cost_%'
          AND (p_biz_id IS NULL OR biz_id = p_biz_id)
    ),
    totals AS (
        SELECT COALESCE(SUM(ad_cost),0)::BIGINT AS total_ad FROM ad
    ),
    by_ch AS (
        SELECT jsonb_object_agg(channel, sum_ad) AS by_channel
        FROM (
            SELECT channel, SUM(ad_cost)::BIGINT AS sum_ad FROM ad GROUP BY channel
        ) x
    )
    SELECT jsonb_build_object(
        'total_ad_cost', t.total_ad,
        'by_channel',    COALESCE(b.by_channel, '{}'::jsonb)
    ) INTO v_ad
    FROM totals t CROSS JOIN by_ch b;

    -- 3~5) b2b/매입/판관비: hub에 tax_invoices/expenses 테이블 없음 → 빈 중첩객체
    RETURN jsonb_build_object(
        'revenue',  v_online,
        'ad_cost',  v_ad,
        'b2b',      jsonb_build_object('b2b_total', 0, 'by_vendor', '{}'::jsonb),
        'purchase', jsonb_build_object('purchase_total', 0, 'by_vendor', '{}'::jsonb),
        'expenses', jsonb_build_object('by_category', '{}'::jsonb)
    );
END;
$$;

GRANT EXECUTE ON FUNCTION get_pnl_monthly_agg(TEXT, TEXT, TEXT, BIGINT)
    TO authenticated, service_role, anon;
