"""
shipment.py — 출고관리 Blueprint.
RPC rpc_get_outbound_list 로 DB에서 3소스 UNION 처리.
Python은 렌더링·엑셀 변환만 담당.
"""
import io
import json
from flask import (
    Blueprint, render_template, request, current_app,
    flash, send_file,
)
from flask_login import login_required

from auth import role_required
from db_utils import get_db

shipment_bp = Blueprint('shipment', __name__, url_prefix='/shipment')


def _call_rpc(db, date_from, date_to, location, product_filter):
    """rpc_get_outbound_list 호출 → rows 반환."""
    try:
        params = {
            'p_date_from': date_from or None,
            'p_date_to':   date_to   or None,
            'p_location':  location  if location != '전체' else None,
            'p_product':   product_filter.replace(' ', '') if product_filter else None,
        }
        res = db.client.rpc('rpc_get_outbound_list', params).execute()
        data = res.data or []
        rows = []
        for r in data:
            rows.append({
                'transaction_date': r.get('tx_date', ''),
                'product_name':     r.get('product_name', ''),
                'qty':              abs(int(r.get('qty', 0) or 0)),
                'unit':             r.get('unit', '개') or '개',
                'location':         r.get('location', '') or '',
                'category':         r.get('category', '') or '',
                'channel':          r.get('channel', '') or '',
                'memo':             r.get('memo', '') or '',
                'lot_number':       r.get('lot_number', '') or '',
                'expiry_date':      r.get('expiry_date', '') or '',
                '_source':          r.get('src', ''),
                '_outbound_done':   bool(r.get('outbound_done', True)),
            })
        return rows, None
    except Exception as e:
        return None, str(e)


@shipment_bp.route('/')
@role_required('admin', 'ceo', 'manager', 'sales', 'logistics', 'general')
def index():
    """출고 내역 조회 — RPC 통합 조회 (3소스)"""
    db = get_db()

    date_from      = request.args.get('date_from', '')
    date_to        = request.args.get('date_to', '')
    location       = request.args.get('location', '전체')
    product_filter = request.args.get('product', '').strip()

    locations = []
    try:
        locs, _ = db.query_filter_options()
        locations = locs
    except Exception:
        pass

    rows  = []
    stats = {'total_items': 0, 'total_qty': 0, 'total_count': 0}

    if date_from or date_to:
        effective_from = date_from or date_to
        effective_to   = date_to   or date_from

        rows, err = _call_rpc(db, effective_from, effective_to, location, product_filter)
        if err:
            flash(f'출고 조회 중 오류: {err}', 'danger')
            rows = []

        stats = {
            'total_items': len(set(r['product_name'] for r in rows)),
            'total_qty':   sum(r['qty'] for r in rows),
            'total_count': len(rows),
        }

    return render_template('shipment/index.html',
                           date_from=date_from, date_to=date_to,
                           location=location,
                           product_filter=product_filter,
                           locations=locations,
                           rows=rows, stats=stats)


@shipment_bp.route('/export')
@role_required('admin', 'ceo', 'manager', 'sales', 'logistics', 'general')
def export():
    """출고 데이터 엑셀 다운로드 — RPC 동일 데이터 사용"""
    import pandas as pd

    db = get_db()
    date_from      = request.args.get('date_from', '')
    date_to        = request.args.get('date_to', '')
    location       = request.args.get('location', '전체')
    product_filter = request.args.get('product', '').strip()

    try:
        effective_from = date_from or date_to
        effective_to   = date_to   or date_from

        if not effective_from:
            flash('기간을 선택하세요.', 'warning')
            from flask import redirect, url_for
            return redirect(url_for('shipment.index'))

        rows, err = _call_rpc(db, effective_from, effective_to, location, product_filter)
        if err or not rows:
            flash('다운로드할 데이터가 없습니다.', 'warning')
            from flask import redirect, url_for
            return redirect(url_for('shipment.index'))

        src_label = {'sales_out': '주문출고', 'manual': '거래처직출', 'order': '온라인주문'}
        export_rows = [{
            '출고일자':    r['transaction_date'],
            '품목명':      r['product_name'],
            '수량':        r['qty'],
            '단위':        r['unit'],
            '창고':        r['location'],
            '종류':        r['category'],
            '채널':        r['channel'],
            '비고':        r['memo'],
            '구분':        src_label.get(r['_source'], r['_source']),
            '출고완료':    '완료' if r['_outbound_done'] else '미출고',
        } for r in rows]

        df = pd.DataFrame(export_rows)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='출고내역')
        output.seek(0)

        fname = f"출고내역_{date_from or 'all'}_{date_to or 'all'}.xlsx"
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=fname,
        )
    except Exception as e:
        flash(f'출고 다운로드 중 오류: {e}', 'danger')
        from flask import redirect, url_for
        return redirect(url_for('shipment.index'))


@shipment_bp.route('/stats')
@role_required('admin', 'ceo', 'manager', 'sales', 'logistics', 'general')
def stats():
    """출고 통계 + 그래프"""
    date_from = request.args.get('date_from', '')
    date_to   = request.args.get('date_to', '')
    location  = request.args.get('location', '전체')

    locations = []
    try:
        locs, _ = get_db().query_filter_options()
        locations = locs
    except Exception:
        pass

    stats_data = None
    if date_from or date_to:
        try:
            from services.shipment_stats_service import get_shipment_stats
            stats_data = get_shipment_stats(
                get_db(),
                date_from=date_from or None,
                date_to=date_to   or None,
                location=location if location != '전체' else None,
            )
        except Exception as e:
            flash(f'출고 통계 조회 중 오류: {e}', 'danger')

    return render_template('shipment/stats.html',
                           date_from=date_from, date_to=date_to,
                           location=location,
                           locations=locations,
                           stats=stats_data,
                           stats_json=json.dumps(stats_data, ensure_ascii=False) if stats_data else '{}')
