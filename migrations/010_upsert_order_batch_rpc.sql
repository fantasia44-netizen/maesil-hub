-- 010: rpc_upsert_order_batch — 주문 배치 upsert (멀티테넌트 버전)
--
-- maesil-total create_rpc_functions.sql 기반, hub 스키마에 맞게 수정:
--   - p_biz_id 파라미터 추가 (멀티테넌트)
--   - import_runs: inserted_count/updated_count/failed_count/error_message 사용
--   - order_change_log: channel/order_no 컬럼 없음 (hub 스키마)
--   - UNIQUE: (biz_id, channel, order_no, line_no)
--   - 없는 컬럼 제거: shipping_fee, status_changed_at, order_datetime
--
-- 호출 예:
--   SELECT rpc_upsert_order_batch(1, 123, '[{"transaction":{...},"shipping":{...}}]'::jsonb)

DROP FUNCTION IF EXISTS rpc_upsert_order_batch(BIGINT, BIGINT, JSONB);

CREATE OR REPLACE FUNCTION rpc_upsert_order_batch(
    p_biz_id        BIGINT,
    p_import_run_id BIGINT,
    p_orders        JSONB  -- [{transaction: {...}, shipping: {...}}, ...]
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET statement_timeout = '60s'
AS $$
DECLARE
    v_order        JSONB;
    v_txn          JSONB;
    v_ship         JSONB;
    v_existing     RECORD;
    v_inserted     INT := 0;
    v_updated      INT := 0;
    v_skipped      INT := 0;
    v_failed       INT := 0;
    v_errors       JSONB := '[]'::JSONB;
    v_idx          INT := 0;
    v_txn_id       BIGINT;
    v_field        TEXT;
    v_old_val      TEXT;
    v_new_val      TEXT;
    v_fields       TEXT[] := ARRAY[
        'order_date', 'original_option', 'original_product',
        'product_name', 'barcode', 'line_code', 'sort_order',
        'qty', 'unit_price', 'total_amount', 'discount_amount',
        'settlement', 'commission'
    ];
BEGIN
    FOR v_order IN SELECT * FROM jsonb_array_elements(p_orders)
    LOOP
        v_idx := v_idx + 1;
        v_txn  := v_order->'transaction';
        v_ship := v_order->'shipping';

        BEGIN
            -- 기존 주문 조회 (biz_id 포함)
            SELECT id, raw_hash, status
            INTO v_existing
            FROM order_transactions
            WHERE biz_id   = p_biz_id
              AND channel  = v_txn->>'channel'
              AND order_no = v_txn->>'order_no'
              AND line_no  = COALESCE((v_txn->>'line_no')::INT, 1);

            IF v_existing IS NOT NULL THEN
                -- 취소/환불 주문은 보호 (SKIP)
                IF v_existing.status IN (
                    U&'\CE58\C18C', U&'\D658\BD88'  -- 취소, 환불
                ) THEN
                    v_skipped := v_skipped + 1;
                    CONTINUE;
                END IF;

                -- raw_hash 동일 → 변경 없음 (SKIP)
                IF v_existing.raw_hash IS NOT NULL
                   AND v_existing.raw_hash = v_txn->>'raw_hash' THEN
                    v_skipped := v_skipped + 1;
                    CONTINUE;
                END IF;

                -- raw_hash 다름 → UPDATE + change_log
                v_txn_id := v_existing.id;

                -- 변경된 필드 기록
                FOREACH v_field IN ARRAY v_fields
                LOOP
                    SELECT
                        CASE v_field
                            WHEN 'order_date'        THEN ot.order_date::TEXT
                            WHEN 'original_option'   THEN ot.original_option
                            WHEN 'original_product'  THEN ot.original_product
                            WHEN 'product_name'      THEN ot.product_name
                            WHEN 'barcode'           THEN ot.barcode
                            WHEN 'line_code'         THEN ot.line_code::TEXT
                            WHEN 'sort_order'        THEN ot.sort_order::TEXT
                            WHEN 'qty'               THEN ot.qty::TEXT
                            WHEN 'unit_price'        THEN ot.unit_price::TEXT
                            WHEN 'total_amount'      THEN ot.total_amount::TEXT
                            WHEN 'discount_amount'   THEN ot.discount_amount::TEXT
                            WHEN 'settlement'        THEN ot.settlement::TEXT
                            WHEN 'commission'        THEN ot.commission::TEXT
                        END
                    INTO v_old_val
                    FROM order_transactions ot
                    WHERE id = v_txn_id;

                    v_new_val := (v_txn->>v_field)::TEXT;

                    IF v_old_val IS DISTINCT FROM v_new_val AND v_new_val IS NOT NULL THEN
                        INSERT INTO order_change_log (
                            biz_id, order_transaction_id,
                            field_name, before_value, after_value,
                            change_type, change_reason, changed_by
                        ) VALUES (
                            p_biz_id, v_txn_id,
                            v_field, v_old_val, v_new_val,
                            U&'\C218\C815',  -- 수정
                            'import_update',
                            COALESCE(v_txn->>'uploaded_by', 'system')
                        );
                    END IF;
                END LOOP;

                -- 실제 UPDATE
                UPDATE order_transactions SET
                    order_date        = COALESCE((v_txn->>'order_date')::DATE, order_date),
                    original_option   = COALESCE(v_txn->>'original_option', original_option),
                    original_product  = COALESCE(v_txn->>'original_product', original_product),
                    product_name      = COALESCE(v_txn->>'product_name', product_name),
                    barcode           = COALESCE(v_txn->>'barcode', barcode),
                    line_code         = COALESCE((v_txn->>'line_code')::INT, line_code),
                    sort_order        = COALESCE((v_txn->>'sort_order')::INT, sort_order),
                    qty               = COALESCE((v_txn->>'qty')::INT, qty),
                    unit_price        = COALESCE((v_txn->>'unit_price')::INT, unit_price),
                    total_amount      = COALESCE((v_txn->>'total_amount')::INT, total_amount),
                    discount_amount   = COALESCE((v_txn->>'discount_amount')::INT, discount_amount),
                    settlement        = COALESCE((v_txn->>'settlement')::INT, settlement),
                    commission        = COALESCE((v_txn->>'commission')::INT, commission),
                    raw_hash          = COALESCE(v_txn->>'raw_hash', raw_hash),
                    raw_data          = COALESCE((v_txn->'raw_data'), raw_data),
                    parser_version    = COALESCE(v_txn->>'parser_version', parser_version),
                    import_run_id     = p_import_run_id,
                    updated_at        = now()
                WHERE id = v_txn_id;

                -- shipping update
                IF v_ship IS NOT NULL AND jsonb_typeof(v_ship) = 'object' THEN
                    UPDATE order_shipping SET
                        recipient_name  = COALESCE(v_ship->>'recipient_name', recipient_name),
                        recipient_phone = COALESCE(v_ship->>'recipient_phone', recipient_phone),
                        address         = COALESCE(v_ship->>'address', address),
                        invoice_no      = COALESCE(v_ship->>'invoice_no', invoice_no),
                        courier         = COALESCE(v_ship->>'courier', courier),
                        updated_at      = now()
                    WHERE biz_id   = p_biz_id
                      AND channel  = v_txn->>'channel'
                      AND order_no = v_txn->>'order_no';
                END IF;

                v_updated := v_updated + 1;

            ELSE
                -- 신규 INSERT
                INSERT INTO order_transactions (
                    biz_id, order_date, channel, order_no, line_no,
                    original_option, original_product, product_name,
                    option_name, barcode, line_code, sort_order,
                    qty, unit_price, total_amount, discount_amount,
                    settlement, commission, status, recipient_name,
                    collection_date, raw_hash, raw_data, parser_version,
                    import_run_id
                ) VALUES (
                    p_biz_id,
                    (v_txn->>'order_date')::DATE,
                    v_txn->>'channel',
                    v_txn->>'order_no',
                    COALESCE((v_txn->>'line_no')::INT, 1),
                    v_txn->>'original_option',
                    v_txn->>'original_product',
                    COALESCE(v_txn->>'product_name', ''),
                    v_txn->>'option_name',
                    v_txn->>'barcode',
                    (v_txn->>'line_code')::INT,
                    (v_txn->>'sort_order')::INT,
                    COALESCE((v_txn->>'qty')::INT, 0),
                    COALESCE((v_txn->>'unit_price')::INT, 0),
                    COALESCE((v_txn->>'total_amount')::INT, 0),
                    COALESCE((v_txn->>'discount_amount')::INT, 0),
                    COALESCE((v_txn->>'settlement')::INT, 0),
                    COALESCE((v_txn->>'commission')::INT, 0),
                    U&'\C815\C0C1',  -- 정상
                    v_txn->>'recipient_name',
                    (v_txn->>'collection_date')::DATE,
                    v_txn->>'raw_hash',
                    v_txn->'raw_data',
                    v_txn->>'parser_version',
                    p_import_run_id
                )
                RETURNING id INTO v_txn_id;

                -- shipping insert
                IF v_ship IS NOT NULL AND jsonb_typeof(v_ship) = 'object'
                   AND (v_ship->>'order_no') IS NOT NULL THEN
                    INSERT INTO order_shipping (
                        biz_id, channel, order_no, recipient_name, recipient_phone,
                        address, invoice_no, courier, shipping_status
                    ) VALUES (
                        p_biz_id,
                        COALESCE(v_ship->>'channel', v_txn->>'channel'),
                        v_ship->>'order_no',
                        v_ship->>'recipient_name',
                        v_ship->>'recipient_phone',
                        v_ship->>'address',
                        v_ship->>'invoice_no',
                        v_ship->>'courier',
                        COALESCE(v_ship->>'shipping_status', U&'\C811\C218')  -- 접수
                    )
                    ON CONFLICT (biz_id, channel, order_no) DO UPDATE SET
                        recipient_name  = EXCLUDED.recipient_name,
                        recipient_phone = EXCLUDED.recipient_phone,
                        address         = EXCLUDED.address,
                        invoice_no      = COALESCE(EXCLUDED.invoice_no, order_shipping.invoice_no),
                        courier         = COALESCE(EXCLUDED.courier, order_shipping.courier),
                        updated_at      = now();
                END IF;

                v_inserted := v_inserted + 1;
            END IF;

        EXCEPTION WHEN OTHERS THEN
            v_failed := v_failed + 1;
            v_errors := v_errors || jsonb_build_object(
                'row', v_idx,
                'order_no', v_txn->>'order_no',
                'error', SQLERRM
            );
        END;
    END LOOP;

    -- import_runs 결과 갱신
    UPDATE import_runs SET
        inserted_count = v_inserted,
        updated_count  = v_updated,
        failed_count   = v_failed,
        error_message  = CASE WHEN v_failed > 0 THEN v_errors::TEXT ELSE NULL END,
        status         = CASE
                           WHEN v_failed = 0           THEN 'completed'
                           WHEN v_inserted + v_updated > 0 THEN 'partial'
                           ELSE 'failed'
                         END,
        updated_at     = now()
    WHERE id = p_import_run_id
      AND biz_id = p_biz_id;

    RETURN jsonb_build_object(
        'inserted', v_inserted,
        'updated',  v_updated,
        'skipped',  v_skipped,
        'failed',   v_failed,
        'errors',   v_errors
    );
END;
$$;

GRANT EXECUTE ON FUNCTION rpc_upsert_order_batch(BIGINT, BIGINT, JSONB)
    TO authenticated, service_role, anon;


-- rpc_cancel_or_edit_order — 주문 수정/취소/환불 (멀티테넌트 버전)
DROP FUNCTION IF EXISTS rpc_cancel_or_edit_order(BIGINT, TEXT, JSONB, TEXT, TEXT);
DROP FUNCTION IF EXISTS rpc_cancel_or_edit_order(BIGINT, BIGINT, TEXT, JSONB, TEXT, TEXT);

CREATE OR REPLACE FUNCTION rpc_cancel_or_edit_order(
    p_biz_id      BIGINT,
    p_order_id    BIGINT,
    p_change_type TEXT,   -- 수정/취소/환불
    p_payload     JSONB,
    p_reason      TEXT,
    p_user        TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET statement_timeout = '15s'
AS $$
DECLARE
    v_existing   RECORD;
    v_field      TEXT;
    v_old_val    TEXT;
    v_new_val    TEXT;
BEGIN
    SELECT * INTO v_existing
    FROM order_transactions
    WHERE id = p_order_id AND biz_id = p_biz_id;

    IF v_existing IS NULL THEN
        RETURN jsonb_build_object('success', false, 'error', U&'\C8FC\BB38\C744 \CC3E\C744 \C218 \C5C6\C2B5\B2C8\B2E4');
    END IF;

    IF v_existing.status IN (U&'\CE58\C18C', U&'\D658\BD88')
       AND p_change_type IN (U&'\CE58\C18C', U&'\D658\BD88') THEN
        RETURN jsonb_build_object('success', false, 'error',
            U&'\C774\BBF8 ' || v_existing.status || U&' \CC98\B9AC\B41C \C8FC\BB38\C785\B2C8\B2E4');
    END IF;

    IF p_change_type IN (U&'\CE58\C18C', U&'\D658\BD88') THEN
        INSERT INTO order_change_log (
            biz_id, order_transaction_id,
            field_name, before_value, after_value,
            change_type, change_reason, changed_by
        ) VALUES (
            p_biz_id, p_order_id,
            'status', v_existing.status, p_change_type,
            p_change_type, p_reason, p_user
        );

        UPDATE order_transactions SET
            status       = p_change_type,
            status_reason = p_reason,
            updated_at   = now()
        WHERE id = p_order_id AND biz_id = p_biz_id;

        UPDATE order_shipping SET
            shipping_status = U&'\CE58\C18C'
        WHERE channel  = v_existing.channel
          AND order_no = v_existing.order_no
          AND biz_id   = p_biz_id;

    ELSIF p_change_type = U&'\C218\C815' THEN
        FOR v_field, v_new_val IN
            SELECT key, value#>>'{}'
            FROM jsonb_each(p_payload)
        LOOP
            v_old_val := CASE v_field
                WHEN 'qty'             THEN v_existing.qty::TEXT
                WHEN 'unit_price'      THEN v_existing.unit_price::TEXT
                WHEN 'total_amount'    THEN v_existing.total_amount::TEXT
                WHEN 'discount_amount' THEN v_existing.discount_amount::TEXT
                WHEN 'product_name'    THEN v_existing.product_name
                WHEN 'order_date'      THEN v_existing.order_date::TEXT
                ELSE NULL
            END;

            IF v_old_val IS DISTINCT FROM v_new_val THEN
                INSERT INTO order_change_log (
                    biz_id, order_transaction_id,
                    field_name, before_value, after_value,
                    change_type, change_reason, changed_by
                ) VALUES (
                    p_biz_id, p_order_id,
                    v_field, v_old_val, v_new_val,
                    U&'\C218\C815', p_reason, p_user
                );
            END IF;
        END LOOP;

        UPDATE order_transactions SET
            qty             = COALESCE((p_payload->>'qty')::INT, qty),
            unit_price      = COALESCE((p_payload->>'unit_price')::NUMERIC, unit_price),
            total_amount    = COALESCE((p_payload->>'total_amount')::NUMERIC, total_amount),
            discount_amount = COALESCE((p_payload->>'discount_amount')::NUMERIC, discount_amount),
            product_name    = COALESCE(p_payload->>'product_name', product_name),
            order_date      = COALESCE((p_payload->>'order_date')::DATE, order_date),
            updated_at      = now()
        WHERE id = p_order_id AND biz_id = p_biz_id;

    ELSE
        RETURN jsonb_build_object('success', false, 'error',
            U&'\C62C\BC14\B974\C9C0 \C54A\C740 change_type: ' || p_change_type);
    END IF;

    RETURN jsonb_build_object('success', true, 'change_type', p_change_type, 'order_id', p_order_id);
END;
$$;

GRANT EXECUTE ON FUNCTION rpc_cancel_or_edit_order(BIGINT, BIGINT, TEXT, JSONB, TEXT, TEXT)
    TO authenticated, service_role, anon;
