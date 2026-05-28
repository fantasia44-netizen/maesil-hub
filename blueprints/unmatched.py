"""
unmatched.py — 미매칭 주문 관리 Blueprint.

자동수집 중 option_master 에 없는 옵션값을 가진 주문을
사용자가 직접 매칭 후 CJ 송장 생성.

Routes:
  GET  /unmatched/              미매칭 주문 목록
  POST /unmatched/<id>/match    수동 매칭 저장 + (선택) CJ 송장 생성
  POST /unmatched/<id>/ignore   해당 주문 무시 처리 (상태=무시)
  POST /unmatched/batch_match   일괄 매칭 (같은 키 전부)
"""
import logging

from flask import (
    Blueprint, render_template, request, jsonify, g,
)
from flask_login import login_required

from auth import role_required
from db_utils import get_db

logger = logging.getLogger(__name__)

unmatched_bp = Blueprint('unmatched', __name__, url_prefix='/unmatched')


# ────────────────────────────────────────────────────
# 목록 조회
# ────────────────────────────────────────────────────

@unmatched_bp.route('/')
@login_required
@role_required('admin', 'ceo', 'manager', 'general')
def index():
    """미매칭 주문 목록 페이지."""
    db = get_db()
    biz_id = g.biz_id

    channel_filter = request.args.get('channel', '')
    page = max(1, int(request.args.get('page', 1)))
    per_page = 50
    offset = (page - 1) * per_page

    # 미매칭 주문 조회 (order_transactions.option_match_status = 'unmatched')
    orders = []
    total = 0
    try:
        q = db.client.table('order_transactions') \
            .select(
                'id, channel, order_no, order_date, collection_date, '
                'original_option, original_product, qty, created_at, '
                'option_match_status'
            ) \
            .eq('option_match_status', 'unmatched')
        if biz_id:
            q = q.eq('biz_id', biz_id)
        if channel_filter:
            q = q.eq('channel', channel_filter)

        count_res = q.execute()
        total = len(count_res.data or [])

        res = q.order('created_at', desc=True) \
               .range(offset, offset + per_page - 1) \
               .execute()
        orders = res.data or []
    except Exception as e:
        logger.error(f'[Unmatched] 목록 조회 오류: {e}')

    # 활성 채널 목록 (필터용)
    channels = []
    try:
        ch_res = db.client.table('order_transactions') \
            .select('channel') \
            .eq('option_match_status', 'unmatched') \
            .execute()
        channels = sorted(set(r['channel'] for r in (ch_res.data or []) if r.get('channel')))
    except Exception:
        pass

    # option_master (매칭 드롭다운용)
    opt_list = []
    try:
        opt_list = db.query_option_master_as_list()
    except Exception:
        pass

    total_pages = max(1, (total + per_page - 1) // per_page)

    return render_template(
        'unmatched/index.html',
        orders=orders,
        channels=channels,
        channel_filter=channel_filter,
        opt_list=opt_list,
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages,
    )


# ────────────────────────────────────────────────────
# 수동 매칭 + (선택) 송장 생성
# ────────────────────────────────────────────────────

@unmatched_bp.route('/<int:order_id>/match', methods=['POST'])
@login_required
@role_required('admin', 'ceo', 'manager', 'general')
def match_order(order_id):
    """미매칭 주문 수동 매칭.

    Body (JSON or form):
        match_key: str          — option_master의 Key
        generate_invoice: bool  — True 면 CJ 송장도 생성
    """
    db = get_db()
    biz_id = g.biz_id

    data = request.get_json(silent=True) or request.form
    match_key = (data.get('match_key') or '').strip()
    generate_invoice = str(data.get('generate_invoice', 'false')).lower() in ('true', '1', 'yes')

    if not match_key:
        return jsonify({'ok': False, 'error': 'match_key 필수'}), 400

    # 1) option_master에서 선택된 키 조회
    opt_list = []
    try:
        opt_list = db.query_option_master_as_list()
    except Exception as e:
        return jsonify({'ok': False, 'error': f'옵션마스터 조회 실패: {e}'}), 500

    matched_opt = next(
        (o for o in opt_list if str(o.get('Key', '')).strip() == match_key),
        None
    )
    if not matched_opt:
        # Key 필드가 없을 수 있음 — 원문명으로도 시도
        matched_opt = next(
            (o for o in opt_list if str(o.get('원문명', '')).strip() == match_key),
            None
        )
    if not matched_opt:
        return jsonify({'ok': False, 'error': f'옵션마스터에서 key={match_key} 를 찾을 수 없음'}), 404

    product_name = str(matched_opt.get('품목명', '')).strip()
    barcode      = str(matched_opt.get('바코드', '')).strip()
    line_code    = int(matched_opt.get('라인코드', 0) or 0)
    sort_order   = int(matched_opt.get('출력순서', 999) or 999)

    if not product_name:
        return jsonify({'ok': False, 'error': '옵션마스터의 품목명이 비어있음'}), 400

    # 2) order_transactions 업데이트
    try:
        q = db.client.table('order_transactions').update({
            'product_name':        product_name,
            'barcode':             barcode,
            'line_code':           line_code,
            'sort_order':          sort_order,
            'option_match_status': 'manual',
            'is_outbound_done':    False,  # 재고차감 대기
        }).eq('id', order_id)
        if biz_id:
            q = q.eq('biz_id', biz_id)
        q.execute()
    except Exception as e:
        logger.error(f'[Unmatched] 매칭 업데이트 오류: {e}')
        return jsonify({'ok': False, 'error': str(e)}), 500

    # 3) order_shipping 상태 복원 (미매칭 → 접수)
    try:
        order_res = db.client.table('order_transactions') \
            .select('order_no, channel') \
            .eq('id', order_id).limit(1).execute()
        if order_res.data:
            order_no = order_res.data[0]['order_no']
            channel  = order_res.data[0]['channel']
            db.client.table('order_shipping').update({
                'shipping_status': '접수'
            }).eq('channel', channel).eq('order_no', order_no).execute()
    except Exception as e:
        logger.warning(f'[Unmatched] order_shipping 상태 복원 실패: {e}')

    invoice_result = None
    if generate_invoice:
        # 4) CJ 송장 즉시 생성
        try:
            from services.cj_shipping_service import query_orders_without_invoice, generate_cj_invoices
            order_res = db.client.table('order_transactions') \
                .select('order_no, channel') \
                .eq('id', order_id).limit(1).execute()
            if order_res.data:
                order_no = order_res.data[0]['order_no']
                channel  = order_res.data[0]['channel']
                orders = query_orders_without_invoice(db, channel=channel)
                target_orders = [o for o in orders if o['order_no'] == order_no]
                if target_orders:
                    invoice_result = generate_cj_invoices(db, target_orders)
                else:
                    invoice_result = {'ok': False, 'error': '송장 생성 대상 주문 없음 (이미 처리됐거나 조건 불일치)'}
        except Exception as e:
            logger.error(f'[Unmatched] CJ 송장 생성 오류: {e}')
            invoice_result = {'ok': False, 'error': str(e)}

    return jsonify({
        'ok': True,
        'order_id': order_id,
        'product_name': product_name,
        'barcode': barcode,
        'invoice': invoice_result,
    })


# ────────────────────────────────────────────────────
# 무시 처리
# ────────────────────────────────────────────────────

@unmatched_bp.route('/<int:order_id>/ignore', methods=['POST'])
@login_required
@role_required('admin', 'ceo', 'manager')
def ignore_order(order_id):
    """미매칭 주문 무시 처리 (취소/반품 등으로 처리 불필요한 경우)."""
    db = get_db()
    biz_id = g.biz_id
    try:
        q = db.client.table('order_transactions').update({
            'option_match_status': 'ignored',
            'status':              '취소',
        }).eq('id', order_id)
        if biz_id:
            q = q.eq('biz_id', biz_id)
        q.execute()

        # order_shipping 도 취소 처리
        order_res = db.client.table('order_transactions') \
            .select('order_no, channel').eq('id', order_id).limit(1).execute()
        if order_res.data:
            order_no = order_res.data[0]['order_no']
            channel  = order_res.data[0]['channel']
            db.client.table('order_shipping').update({
                'shipping_status': '취소'
            }).eq('channel', channel).eq('order_no', order_no).execute()

        return jsonify({'ok': True, 'order_id': order_id})
    except Exception as e:
        logger.error(f'[Unmatched] 무시 처리 오류: {e}')
        return jsonify({'ok': False, 'error': str(e)}), 500


# ────────────────────────────────────────────────────
# 일괄 매칭 (동일 원문명 전부)
# ────────────────────────────────────────────────────

@unmatched_bp.route('/batch_match', methods=['POST'])
@login_required
@role_required('admin', 'ceo', 'manager')
def batch_match():
    """동일 original_option 을 가진 미매칭 주문 일괄 매칭.

    Body (JSON):
        original_option: str  — 같은 원문명 전부 매칭
        match_key:        str  — option_master Key
        generate_invoice: bool
    """
    db = get_db()
    biz_id = g.biz_id
    data = request.get_json(silent=True) or {}

    original_option = (data.get('original_option') or '').strip()
    match_key       = (data.get('match_key') or '').strip()
    generate_invoice = bool(data.get('generate_invoice', False))

    if not original_option or not match_key:
        return jsonify({'ok': False, 'error': 'original_option, match_key 필수'}), 400

    # option_master 조회
    try:
        opt_list = db.query_option_master_as_list()
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

    matched_opt = next(
        (o for o in opt_list if str(o.get('Key', '')).strip() == match_key
         or str(o.get('원문명', '')).strip() == match_key),
        None
    )
    if not matched_opt:
        return jsonify({'ok': False, 'error': f'key={match_key} 찾을 수 없음'}), 404

    product_name = str(matched_opt.get('품목명', '')).strip()
    barcode      = str(matched_opt.get('바코드', '')).strip()
    line_code    = int(matched_opt.get('라인코드', 0) or 0)
    sort_order   = int(matched_opt.get('출력순서', 999) or 999)

    # 해당 original_option 을 가진 미매칭 주문 전부 업데이트
    try:
        q = db.client.table('order_transactions').update({
            'product_name':        product_name,
            'barcode':             barcode,
            'line_code':           line_code,
            'sort_order':          sort_order,
            'option_match_status': 'manual',
            'is_outbound_done':    False,
        }).eq('option_match_status', 'unmatched') \
          .eq('original_option', original_option)
        if biz_id:
            q = q.eq('biz_id', biz_id)
        update_res = q.execute()
        updated = len(update_res.data or [])
    except Exception as e:
        logger.error(f'[Unmatched] 일괄매칭 업데이트 오류: {e}')
        return jsonify({'ok': False, 'error': str(e)}), 500

    # order_shipping 상태 복원
    try:
        order_nos_res = db.client.table('order_transactions') \
            .select('order_no, channel') \
            .eq('original_option', original_option) \
            .eq('option_match_status', 'manual') \
            .execute()
        for row in (order_nos_res.data or []):
            db.client.table('order_shipping').update({
                'shipping_status': '접수'
            }).eq('channel', row['channel']).eq('order_no', row['order_no']).execute()
    except Exception as e:
        logger.warning(f'[Unmatched] 일괄매칭 order_shipping 복원 실패: {e}')

    invoice_result = None
    if generate_invoice and updated > 0:
        try:
            from services.cj_shipping_service import query_orders_without_invoice, generate_cj_invoices
            orders = query_orders_without_invoice(db)
            if orders:
                invoice_result = generate_cj_invoices(db, orders)
        except Exception as e:
            invoice_result = {'ok': False, 'error': str(e)}

    return jsonify({
        'ok': True,
        'updated': updated,
        'product_name': product_name,
        'invoice': invoice_result,
    })
