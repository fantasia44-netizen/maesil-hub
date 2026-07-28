-- 030_pnl_agg_with_tax_expenses.sql
-- 028/029로 tax_invoices/expenses가 생겼으므로 get_pnl_monthly_agg의
-- b2b/매입/판관비 섹션을 실데이터로 채움(total 원본 섹션3~5 이식) + p_biz_id 격리.
-- 027은 이 3섹션을 빈 객체로 두던 것을 대체.

DROP FUNCTION IF EXISTS get_pnl_monthly_agg(TEXT, TEXT, TEXT, BIGINT);

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
    v_online JSONB; v_ad JSONB; v_b2b JSONB; v_purchase JSONB; v_expenses JSONB;
BEGIN
    -- 1) 온라인 매출 (api_settlements)
    WITH filt AS (
        SELECT COALESCE(channel,'기타') AS channel,
               COALESCE(gross_sales,0)::BIGINT AS gross,
               COALESCE(total_commission,0)::BIGINT AS comm
        FROM api_settlements
        WHERE settlement_date BETWEEN v_from AND v_to AND settlement_id IS NOT NULL
          AND (p_biz_id IS NULL OR biz_id = p_biz_id)
          AND EXISTS (SELECT 1 FROM unnest(v_settle_prefixes) p WHERE settlement_id LIKE p || '%')
    ),
    totals AS (SELECT COALESCE(SUM(gross),0)::BIGINT online_total, COALESCE(SUM(comm),0)::BIGINT online_commission FROM filt),
    by_ch AS (
        SELECT jsonb_object_agg(channel, sum_gross) by_channel, jsonb_object_agg(channel, sum_comm) comm_by_channel
        FROM (SELECT channel, SUM(gross)::BIGINT sum_gross, SUM(comm)::BIGINT sum_comm FROM filt GROUP BY channel) x
    )
    SELECT jsonb_build_object('online_total', t.online_total, 'online_commission', t.online_commission,
        'by_channel', COALESCE(b.by_channel,'{}'::jsonb), 'commission_by_channel', COALESCE(b.comm_by_channel,'{}'::jsonb))
    INTO v_online FROM totals t CROSS JOIN by_ch b;

    -- 2) 광고비 (api_settlements ad_cost_)
    WITH ad AS (
        SELECT COALESCE(channel,'기타') channel, COALESCE(other_deductions,0)::BIGINT ad_cost
        FROM api_settlements
        WHERE settlement_date BETWEEN v_from AND v_to AND settlement_id LIKE 'ad_cost_%'
          AND (p_biz_id IS NULL OR biz_id = p_biz_id)
    ),
    totals AS (SELECT COALESCE(SUM(ad_cost),0)::BIGINT total_ad FROM ad),
    by_ch AS (SELECT jsonb_object_agg(channel, sum_ad) by_channel FROM (SELECT channel, SUM(ad_cost)::BIGINT sum_ad FROM ad GROUP BY channel) x)
    SELECT jsonb_build_object('total_ad_cost', t.total_ad, 'by_channel', COALESCE(b.by_channel,'{}'::jsonb))
    INTO v_ad FROM totals t CROSS JOIN by_ch b;

    -- 3) 거래처 매출 (tax_invoices sales, 플랫폼 제외)
    WITH filt AS (
        SELECT COALESCE(buyer_corp_name,'기타') vendor, COALESCE(supply_cost_total,0)::BIGINT amt
        FROM tax_invoices
        WHERE direction='sales' AND (is_deleted IS NULL OR is_deleted=FALSE)
          AND COALESCE(status,'')<>'cancelled' AND write_date BETWEEN v_from AND v_to
          AND (p_biz_id IS NULL OR biz_id = p_biz_id)
          AND COALESCE(buyer_corp_name,'기타') NOT IN (
              '쿠팡(주)','쿠팡주식회사','쿠팡 주식회사','네이버파이낸셜 주식회사','네이버파이낸셜주식회사',
              '네이버 주식회사','네이버주식회사','네이버(주)','(주)네이버파이낸셜','주식회사 네이버파이낸셜')
    ),
    totals AS (SELECT COALESCE(SUM(amt),0)::BIGINT b2b_total FROM filt),
    by_v AS (SELECT jsonb_object_agg(vendor, sum_amt) by_vendor FROM (SELECT vendor, SUM(amt)::BIGINT sum_amt FROM filt GROUP BY vendor) x)
    SELECT jsonb_build_object('b2b_total', t.b2b_total, 'by_vendor', COALESCE(v.by_vendor,'{}'::jsonb))
    INTO v_b2b FROM totals t CROSS JOIN by_v v;

    -- 4) 매입 (tax_invoices purchase)
    WITH filt AS (
        SELECT COALESCE(supplier_corp_name,'기타') vendor, COALESCE(supply_cost_total,0)::BIGINT amt
        FROM tax_invoices
        WHERE direction='purchase' AND (is_deleted IS NULL OR is_deleted=FALSE)
          AND COALESCE(status,'')<>'cancelled' AND write_date BETWEEN v_from AND v_to
          AND (p_biz_id IS NULL OR biz_id = p_biz_id)
    ),
    totals AS (SELECT COALESCE(SUM(amt),0)::BIGINT purchase_total FROM filt),
    by_v AS (SELECT jsonb_object_agg(vendor, sum_amt) by_vendor FROM (SELECT vendor, SUM(amt)::BIGINT sum_amt FROM filt GROUP BY vendor) x)
    SELECT jsonb_build_object('purchase_total', t.purchase_total, 'by_vendor', COALESCE(v.by_vendor,'{}'::jsonb))
    INTO v_purchase FROM totals t CROSS JOIN by_v v;

    -- 5) 판관비 (expenses)
    WITH filt AS (
        SELECT COALESCE(category,'기타') category, COALESCE(amount,0)::NUMERIC amt
        FROM expenses
        WHERE (is_deleted IS NULL OR is_deleted=FALSE)
          AND (p_biz_id IS NULL OR biz_id = p_biz_id)
          AND (expense_month = p_year_month OR expense_date BETWEEN v_from AND v_to)
    ),
    by_cat AS (SELECT jsonb_object_agg(category, sum_amt) by_category FROM (SELECT category, SUM(amt)::NUMERIC sum_amt FROM filt GROUP BY category) x)
    SELECT jsonb_build_object('by_category', COALESCE(by_cat.by_category,'{}'::jsonb)) INTO v_expenses FROM by_cat;

    RETURN jsonb_build_object('revenue', v_online, 'ad_cost', v_ad,
        'b2b', v_b2b, 'purchase', v_purchase, 'expenses', v_expenses);
END;
$$;

GRANT EXECUTE ON FUNCTION get_pnl_monthly_agg(TEXT, TEXT, TEXT, BIGINT) TO authenticated, service_role, anon;
