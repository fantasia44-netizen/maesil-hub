"""
etc_outbound.py — 기타출고 관리 Blueprint.
무상출고, 실험사용, 샘플, 폐기 등 재고 차감 처리, 이력 조회, 엑셀 다운로드.
"""
import io
from datetime import datetime
from services.tz_utils import today_kst

import pandas as pd
from flask import (
    Blueprint, render_template, request, current_app,
    flash, redirect, url_for, send_file, jsonify,
)
from flask_login import login_required, current_user

from auth import role_required, _log_action
from models import INV_TYPE_LABELS, ETC_OUT_REASONS
from db_utils import get_db

etc_outbound_bp = Blueprint('etc_outbound', __name__, url_prefix='/etc-outbound')


@etc_outbound_bp.route('/')
@role_required('admin', 'manager', 'sales', 'logistics', 'production', 'general')
def index():
    """기타출고 폼 + 이력 조회"""
    db = get_db()

    # 위치 목록
    locations = []
    try:
        locations, _ = db.query_filter_options()
    except Exception:
        pass

    # 이력 조회 — 날짜 미입력 시 최근 30일 기본 표시
    from services.tz_utils import today_kst
    from datetime import datetime, timedelta
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')

    if not date_from and not date_to:
        date_to = today_kst()
        date_from = (datetime.strptime(date_to, '%Y-%m-%d') - timedelta(days=30)).strftime('%Y-%m-%d')

    history = []
    try:
        raw = db.query_stock_ledger(
            date_to=date_to or '9999-12-31',
            date_from=date_from or None,
            type_list=['ETC_OUT', 'ETC_IN'],
            order_desc=True,
        )
        history = raw
    except Exception as e:
        flash(f'기타출고 이력 조회 중 오류: {e}', 'danger')

    return render_template('etc_outbound/index.html',
                           history=history,
                           locations=locations,
                           reasons=ETC_OUT_REASONS,
                           date_from=date_from,
                           date_to=date_to,
                           type_labels=INV_TYPE_LABELS)


@etc_outbound_bp.route('/api/products')
@role_required('admin', 'manager', 'sales', 'logistics', 'production', 'general')
def api_products():
    """창고별 재고 품목 목록 JSON 반환 (자동완성용)"""
    location = request.args.get('location', '')
    if not location:
        return jsonify([])
    try:
        from services.excel_io import build_stock_snapshot
        all_data = get_db().query_stock_by_location(location)
        snapshot = build_stock_snapshot(all_data)
        products = []
        for name, info in snapshot.items():
            if info['total'] > 0:
                # 배치별 그룹 (제조일자 선택용)
                batches = []
                for g in info.get('groups', []):
                    if g.get('qty', 0) > 0:
                        batches.append({
                            'manufacture_date': g.get('manufacture_date', '') or '',
                            'qty': g.get('qty', 0),
                            'expiry_date': g.get('expiry_date', '') or '',
                        })
                products.append({
                    'name': name,
                    'qty': info['total'],
                    'unit': info.get('unit', '개'),
                    'batches': batches,
                })
        products.sort(key=lambda x: x['name'])
        return jsonify(products)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@etc_outbound_bp.route('/process', methods=['POST'])
@role_required('admin', 'manager', 'sales', 'logistics', 'production', 'general')
def process():
    """기타출고 처리"""
    date_str = request.form.get('date', today_kst())
    location = request.form.get('location', '').strip()

    if not location:
        flash('창고위치를 선택해주세요.', 'danger')
        return redirect(url_for('etc_outbound.index'))

    # 다건 품목 파싱
    product_names = request.form.getlist('product_name[]')
    qtys = request.form.getlist('qty[]')
    reasons = request.form.getlist('reason[]')
    memos = request.form.getlist('memo[]')
    mfg_dates = request.form.getlist('manufacture_date[]')

    items = []
    for i in range(len(product_names)):
        name = product_names[i].strip() if i < len(product_names) else ''
        try:
            qty = float(qtys[i]) if i < len(qtys) else 0
        except (ValueError, IndexError):
            qty = 0
        reason = reasons[i] if i < len(reasons) else '기타'
        memo = memos[i].strip() if i < len(memos) else ''
        mfg = (mfg_dates[i].strip() if i < len(mfg_dates) else '') or ''

        if name and qty != 0:
            items.append({
                'product_name': name,
                'qty': qty,       # 양수=차감, 음수=증량
                'reason': reason,
                'memo': memo,
                'manufacture_date': mfg,
            })

    if not items:
        flash('출고할 품목을 입력해주세요.', 'danger')
        return redirect(url_for('etc_outbound.index'))

    try:
        from services.etc_outbound_service import process_etc_outbound
        result = process_etc_outbound(get_db(), date_str, location, items)

        if result.get('warnings'):
            for w in result['warnings']:
                flash(w, 'warning')

        if result.get('shortage'):
            for s in result['shortage']:
                flash(f'⚠️ {s}', 'danger')

        if result.get('success'):
            parts = []
            if result.get('out_count', 0):
                parts.append(f"차감 {result['out_count']}건")
            if result.get('in_count', 0):
                parts.append(f"증량 {result['in_count']}건")
            flash(
                f"기타출고 완료: {result.get('item_count', 0)}개 품목, "
                f"{', '.join(parts) if parts else '0건 처리'}",
                'success'
            )
    except Exception as e:
        flash(f'기타출고 처리 중 오류: {e}', 'danger')

    return redirect(url_for('etc_outbound.index'))


@etc_outbound_bp.route('/api/cancel/<int:row_id>', methods=['POST'])
@role_required('admin', 'manager')
def api_cancel(row_id):
    """기타출고 1건 취소 (status='cancelled') — 재고 원복."""
    db = get_db()
    try:
        cur = db.client.table('stock_ledger').select('*').eq('id', row_id).limit(1).execute()
        if not cur.data:
            return jsonify({'error': '레코드 없음'}), 404
        old = cur.data[0]
        if old.get('type') not in ('ETC_OUT', 'ETC_IN'):
            return jsonify({'error': 'ETC_OUT/ETC_IN 만 취소 가능'}), 400
        if old.get('status') == 'cancelled':
            return jsonify({'error': '이미 취소된 항목'}), 400
        db.client.table('stock_ledger').update({'status': 'cancelled'}).eq('id', row_id).execute()
        _log_action('cancel_etc_outbound', target=str(row_id),
                     detail=f"{old.get('product_name')} qty={old.get('qty')} loc={old.get('location')}",
                     old_value=old)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@etc_outbound_bp.route('/api/update/<int:row_id>', methods=['POST'])
@role_required('admin', 'manager')
def api_update(row_id):
    """기타출고 1건 수정 — 기존 cancel + 신규 INSERT (재고 다시 처리)."""
    data = request.get_json(silent=True) or {}
    qty = data.get('qty')
    memo = (data.get('memo') or '').strip()

    db = get_db()
    try:
        cur = db.client.table('stock_ledger').select('*').eq('id', row_id).limit(1).execute()
        if not cur.data:
            return jsonify({'error': '레코드 없음'}), 404
        old = cur.data[0]
        if old.get('type') not in ('ETC_OUT', 'ETC_IN'):
            return jsonify({'error': 'ETC_OUT/ETC_IN 만 수정 가능'}), 400

        try:
            new_qty = float(qty)
        except (TypeError, ValueError):
            return jsonify({'error': '수량 형식 오류'}), 400

        # 기존 취소
        db.client.table('stock_ledger').update({'status': 'cancelled'}).eq('id', row_id).execute()

        # 부호 보존 (ETC_OUT은 음수, ETC_IN은 양수)
        if old.get('type') == 'ETC_OUT':
            new_qty = -abs(new_qty)
        else:
            new_qty = abs(new_qty)

        new_payload = {k: v for k, v in old.items()
                       if k not in ('id', 'created_at', 'updated_at',
                                    'status', 'event_uid')}
        new_payload['qty'] = new_qty
        if memo:
            new_payload['memo'] = memo
        new_payload['status'] = 'active'

        ins = db.client.table('stock_ledger').insert(new_payload).execute()
        new_id = ins.data[0]['id'] if ins.data else None

        _log_action('update_etc_outbound', target=str(row_id),
                     detail=f'qty {old.get("qty")} → {new_qty}, memo={memo!r}',
                     old_value={'qty': old.get('qty'), 'memo': old.get('memo')},
                     new_value={'qty': new_qty, 'memo': memo, 'new_id': new_id})
        return jsonify({'success': True, 'new_id': new_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@etc_outbound_bp.route('/export')
@role_required('admin', 'manager', 'sales', 'logistics', 'production', 'general')
def export():
    """기타출고 이력 엑셀 다운로드"""
    db = get_db()

    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')

    try:
        raw = db.query_stock_ledger(
            date_to=date_to or '9999-12-31',
            date_from=date_from or None,
            type_list=['ETC_OUT', 'ETC_IN'],
            order_desc=True,
        )

        if not raw:
            flash('다운로드할 기타출고 이력이 없습니다.', 'warning')
            return redirect(url_for('etc_outbound.index'))

        df = pd.DataFrame(raw)

        col_map = {
            'transaction_date': '일자',
            'type': '유형',
            'product_name': '품목명',
            'qty': '수량',
            'location': '창고',
            'category': '종류',
            'unit': '단위',
            'memo': '사유/비고',
        }
        export_cols = [c for c in col_map.keys() if c in df.columns]
        df = df[export_cols].rename(columns=col_map)

        if '유형' in df.columns:
            df['유형'] = df['유형'].map(lambda x: INV_TYPE_LABELS.get(x, x))

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='기타출고이력')
        output.seek(0)

        fname = f"기타출고이력_{date_from or 'all'}_{date_to or 'all'}.xlsx"
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=fname,
        )
    except Exception as e:
        flash(f'기타출고 이력 다운로드 중 오류: {e}', 'danger')
        return redirect(url_for('etc_outbound.index'))
