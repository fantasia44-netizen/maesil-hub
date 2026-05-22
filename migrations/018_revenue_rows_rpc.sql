-- 018_revenue_rows_rpc.sql
-- get_revenue_rows_agg: order_transactions 전체를 페이지네이션 없이 GROUP BY로 집계 반환.
-- query_revenue() 의 7일 청크 × _paginate_query 를 단일 RPC 호출로 대체.
-- 반환: JSON array of {order_date, product_name, channel, qty, total_amount, settlement, commission, discount_amount}

CREATE OR REPLACE FUNCTION get_revenue_rows_agg(
    p_date_from TEXT,
    p_date_to   TEXT,
    p_biz_id    BIGINT DEFAULT NULL,
    p_channel   TEXT   DEFAULT NULL
)
RETURNS JSON
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public
AS $$
DECLARE
    v_result JSON;
BEGIN
    SELECT COALESCE(
        json_agg(
            json_build_object(
                'order_date',       order_date::TEXT,
                'product_name',     product_name,
                'channel',          channel,
                'qty',              qty,
                'total_amount',     total_amount,
                'settlement',       settlement,
                'commission',       commission,
                'discount_amount',  discount_amount
            )
            ORDER BY order_date DESC, product_name
        ),
        '[]'::json
    )
    INTO v_result
    FROM (
        SELECT
            order_date,
            COALESCE(NULLIF(TRIM(product_name), ''), '(상품명없음)') AS product_name,
            COALESCE(channel, '')                                    AS channel,
            SUM(qty)::BIGINT             AS qty,
            SUM(total_amount)::BIGINT    AS total_amount,
            SUM(settlement)::BIGINT      AS settlement,
            SUM(commission)::BIGINT      AS commission,
            SUM(discount_amount)::BIGINT AS discount_amount
        FROM order_transactions
        WHERE status = U&'\C815\C0C1'   -- '정상'
          AND order_date BETWEEN p_date_from::DATE AND p_date_to::DATE
          AND (p_biz_id IS NULL OR biz_id = p_biz_id)
          AND (p_channel IS NULL OR p_channel = '' OR channel = p_channel)
        GROUP BY order_date, NULLIF(TRIM(product_name), ''), channel
    ) t;

    RETURN v_result;
END;
$$;

GRANT EXECUTE ON FUNCTION get_revenue_rows_agg(TEXT, TEXT, BIGINT, TEXT) TO authenticated, service_role;
