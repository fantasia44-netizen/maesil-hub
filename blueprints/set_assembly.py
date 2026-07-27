"""
set_assembly.py — 세트작업 관리 Blueprint.
BOM 기반 세트 조립: 단품 FIFO 차감 → 세트 산출, 부재료 차감, 이력 조회, 엑셀 다운로드.
"""
import io
import json
from datetime import datetime
from services.tz_utils import today_kst

import pandas as pd
from flask import (
    Blueprint, render_template, request, current_app,
    flash, redirect, url_for, send_file, jsonify, g,
)
from flask_login import login_required, current_user


def _resolve_set_batch(db, anchor):
    """세트작업 1건에 속한 모든 active 행(SET_IN + SET_OUT)을 찾는다.

    - set_batch_id 있으면(신규 작업): 그 키로 묶인 전 행.
    - 없으면(레거시): 같은 (created_at, location) 의 SET_IN/SET_OUT 전 행.
    ※ raw 쿼리 → biz_id 명시 필터(service_role이 RLS 우회하므로 테넌트 격리 필수).
    """
    biz_id = g.biz_id
    batch_id = anchor.get('set_batch_id')
    if batch_id:
        rows = db.client.table('stock_ledger').select('*') \
            .eq('biz_id', biz_id).eq('set_batch_id', batch_id) \
            .or_('status.is.null,status.eq.active') \
            .execute().data or []
        return rows
    # 레거시 폴백: created_at + location
    ca = anchor.get('created_at')
    loc = anchor.get('location')
    if not ca:
        return [anchor]
    q = db.client.table('stock_ledger').select('*') \
        .eq('biz_id', biz_id).eq('created_at', ca) \
        .in_('type', ['SET_IN', 'SET_OUT']) \
        .or_('status.is.null,status.eq.active')
    if loc is not None:
        q = q.eq('location', loc)
    return q.execute().data or []

from auth import role_required, _log_action
from models import INV_TYPE_LABELS
from db_utils import get_db

set_assembly_bp = Blueprint('set_assembly', __name__, url_prefix='/set-assembly')


@set_assembly_bp.route('/')
@role_required('admin', 'manager', 'sales', 'logistics', 'production', 'general')
def index():
    """세트작업 폼 + 이력 조회"""
    db = get_db()

    # 위치 목록
    locations = []
    try:
        locations, _ = db.query_filter_options()
    except Exception:
        pass

    # BOM 데이터 로드 (채널별 세트 목록)
    bom_data = {}
    try:
        raw = db.query_master_table('bom_master')
        for row in raw:
            ch = row.get('channel', '')
            sn = row.get('set_name', '')
            if ch and sn:
                if ch not in bom_data:
                    bom_data[ch] = []
                bom_data[ch].append(sn)
    except Exception as e:
        flash(f'BOM 데이터 로드 실패: {e}', 'danger')

    # 이력 조회
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')

    history = []
    if date_from or date_to:
        try:
            raw = db.query_stock_ledger(
                date_to=date_to or '9999-12-31',
                date_from=date_from or None,
                type_list=['SET_OUT', 'SET_IN'],
                order_desc=True,
            )
            history = raw
        except Exception as e:
            flash(f'세트작업 이력 조회 중 오류: {e}', 'danger')

    return render_template('set_assembly/index.html',
                           history=history,
                           locations=locations,
                           bom_data_json=json.dumps(bom_data, ensure_ascii=False),
                           date_from=date_from,
                           date_to=date_to,
                           type_labels=INV_TYPE_LABELS)


@set_assembly_bp.route('/api/products')
@role_required('admin', 'manager', 'sales', 'logistics', 'production', 'general')
def api_products():
    """창고별 재고 품목 목록 JSON 반환 (부재료 자동완성용)"""
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
                products.append({
                    'name': name,
                    'qty': info['total'],
                    'unit': info.get('unit', '개'),
                })
        products.sort(key=lambda x: x['name'])
        return jsonify(products)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@set_assembly_bp.route('/process', methods=['POST'])
@role_required('admin', 'manager', 'sales', 'logistics', 'production', 'general')
def process():
    """세트작업 처리"""
    date_str = request.form.get('date', today_kst())
    set_name = request.form.get('set_name', '').strip()
    channel = request.form.get('channel', '').strip()
    location = request.form.get('location', '').strip()
    qty_str = request.form.get('qty', '1').strip()
    storage_method_override = request.form.get('storage_method', '').strip()
    food_type = request.form.get('food_type', '').strip()

    if not set_name or not channel or not location:
        flash('세트종류, 판매처, 창고위치를 모두 선택해주세요.', 'danger')
        return redirect(url_for('set_assembly.index'))

    try:
        qty = int(qty_str)
    except ValueError:
        flash('수량은 숫자로 입력해주세요.', 'danger')
        return redirect(url_for('set_assembly.index'))

    # 부재료 파싱
    sub_names = request.form.getlist('sub_material_name[]')
    sub_qtys = request.form.getlist('sub_material_qty[]')
    sub_materials = []
    for i in range(len(sub_names)):
        s_name = sub_names[i].strip() if i < len(sub_names) else ''
        try:
            s_qty = float(sub_qtys[i]) if i < len(sub_qtys) else 0
            if s_qty == int(s_qty):
                s_qty = int(s_qty)
        except (ValueError, IndexError):
            s_qty = 0
        if s_name and s_qty > 0:
            sub_materials.append({'name': s_name, 'qty': s_qty})

    try:
        from services.set_assembly_service import process_set_assembly
        result = process_set_assembly(
            get_db(), date_str, set_name, channel, location, qty,
            sub_materials=sub_materials,
            storage_method_override=storage_method_override or None,
            food_type=food_type or None,
            created_by=current_user.username,
        )

        if result.get('warnings'):
            for w in result['warnings']:
                flash(w, 'warning')

        if result.get('shortage'):
            for s in result['shortage']:
                flash(f'⚠️ {s}', 'danger')

        if result.get('success'):
            msg = (f"세트작업 완료: {set_name} x{qty} ({channel}) — "
                   f"단품 차감 {result.get('set_out_count', 0)}건, "
                   f"세트 산출 {result.get('set_in_count', 0)}건, "
                   f"구성품 {result.get('component_count', 0)}종 총 {result.get('total_deducted', 0)}개 차감")
            if result.get('sub_out_count', 0) > 0:
                msg += f", 부재료 {result.get('sub_out_count', 0)}건 차감"
            flash(msg, 'success')
    except Exception as e:
        flash(f'세트작업 처리 중 오류: {e}', 'danger')

    return redirect(url_for('set_assembly.index'))


@set_assembly_bp.route('/api/delete/<int:record_id>', methods=['POST'])
@role_required('admin', 'manager', 'production')
def api_delete(record_id):
    """세트작업 '단위' 취소 — 클릭한 행이 속한 세트작업 전체를 블라인드.
    배치키(또는 created_at)로 묶인 전 행을 함께 블라인드 → 세트 제거 +
    구성품 재고 복원(분해 복구)."""
    db = get_db()
    try:
        anchor = db.query_stock_ledger_by_id(record_id)
        if not anchor:
            return jsonify({'error': '레코드를 찾을 수 없습니다.'}), 404
        if anchor.get('type') not in ('SET_OUT', 'SET_IN'):
            return jsonify({'error': '세트작업 이력이 아닙니다.'}), 400

        batch_rows = _resolve_set_batch(db, anchor)
        blinded = 0
        for r in batch_rows:
            rid = r.get('id')
            if rid:
                db.blind_stock_ledger(rid, blinded_by=current_user.username)
                blinded += 1

        set_in = next((r for r in batch_rows if r.get('type') == 'SET_IN'), None)
        set_name = (set_in or anchor).get('product_name', '')
        _log_action('cancel_set_assembly', target=str(record_id),
                     old_value=batch_rows,
                     detail=f'세트작업 단위 취소: {set_name} — {blinded}행 블라인드(분해 복구)')
        return jsonify({
            'success': True,
            'blinded': blinded,
            'set_name': set_name,
            'component_count': sum(1 for r in batch_rows if r.get('type') == 'SET_OUT'),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@set_assembly_bp.route('/api/update/<int:record_id>', methods=['POST'])
@role_required('admin', 'manager', 'production')
def api_update(record_id):
    """세트작업 이력 1행 수정. 원본 블라인드 + 새 행 INSERT."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': '수정 데이터가 없습니다.'}), 400

    allowed = {'product_name', 'qty', 'location', 'category', 'unit',
               'storage_method', 'memo'}
    update_data = {k: v for k, v in data.items() if k in allowed}
    if 'qty' in update_data:
        try:
            update_data['qty'] = float(update_data['qty'])
            if update_data['qty'] == int(update_data['qty']):
                update_data['qty'] = int(update_data['qty'])
        except (ValueError, TypeError):
            return jsonify({'error': '수량이 올바르지 않습니다.'}), 400
    if not update_data:
        return jsonify({'error': '수정할 항목이 없습니다.'}), 400

    original = get_db().query_stock_ledger_by_id(record_id)
    if not original:
        return jsonify({'error': '레코드를 찾을 수 없습니다.'}), 404
    if original.get('type') not in ('SET_OUT', 'SET_IN'):
        return jsonify({'error': '세트작업 이력이 아닙니다.'}), 400

    # 부호 보정: SET_OUT(차감)은 음수, SET_IN(산출)은 양수 유지
    if 'qty' in update_data:
        q = abs(update_data['qty'])
        update_data['qty'] = -q if original.get('type') == 'SET_OUT' else q

    skip_fields = {'id', 'status', 'replaced_by', 'replaces',
                   'created_at', 'updated_at', 'updated_by', 'created_by',
                   'is_deleted', 'deleted_at', 'deleted_by'}
    new_payload = {k: v for k, v in original.items() if k not in skip_fields}
    new_payload.update(update_data)

    try:
        new_id = get_db().replace_stock_ledger(
            record_id, new_payload, replaced_by_user=current_user.username)
        _log_action('replace_set_assembly_row', target=str(record_id),
                     old_value={k: original.get(k) for k in update_data},
                     new_value=update_data)
        return jsonify({'success': True, 'new_id': new_id})
    except Exception as e:
        _log_action('replace_set_assembly_error', target=str(record_id),
                     detail=f'세트작업 수정 오류: {str(e)}', new_value=update_data)
        return jsonify({'error': str(e)}), 500


@set_assembly_bp.route('/delete', methods=['POST'])
@role_required('admin')
def delete():
    """세트작업 이력 블라인드 처리 (해당일 SET_OUT + SET_IN 전부 블라인드)"""
    db = get_db()
    date_str = request.form.get('delete_date', '').strip()

    if not date_str:
        flash('삭제할 날짜를 선택해주세요.', 'danger')
        return redirect(url_for('set_assembly.index'))

    try:
        old_records = db.query_stock_ledger(
            date_from=date_str, date_to=date_str,
            type_list=['SET_OUT', 'SET_IN'])
        blinded = 0
        for r in old_records:
            rid = r.get('id')
            if rid:
                db.blind_stock_ledger(rid, blinded_by=current_user.username)
                blinded += 1
        if blinded > 0:
            _log_action('blind_set_assembly', target=date_str,
                         old_value=old_records,
                         detail=f'{blinded}건 블라인드')
            flash(f'{date_str} 세트작업 이력 {blinded}건 삭제 완료', 'success')
        else:
            flash(f'{date_str} 에 처리할 세트작업 이력이 없습니다.', 'warning')
    except Exception as e:
        flash(f'세트작업 이력 처리 중 오류: {e}', 'danger')

    return redirect(url_for('set_assembly.index'))


@set_assembly_bp.route('/export')
@role_required('admin', 'manager', 'sales', 'logistics', 'production', 'general')
def export():
    """세트작업 이력 엑셀 다운로드"""
    db = get_db()

    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')

    try:
        raw = db.query_stock_ledger(
            date_to=date_to or '9999-12-31',
            date_from=date_from or None,
            type_list=['SET_OUT', 'SET_IN'],
            order_desc=True,
        )

        if not raw:
            flash('다운로드할 세트작업 이력이 없습니다.', 'warning')
            return redirect(url_for('set_assembly.index'))

        df = pd.DataFrame(raw)

        col_map = {
            'transaction_date': '일자',
            'type': '유형',
            'product_name': '품목명',
            'qty': '수량',
            'location': '창고',
            'category': '종류',
            'unit': '단위',
            'expiry_date': '소비기한',
            'memo': '비고',
        }
        export_cols = [c for c in col_map.keys() if c in df.columns]
        df = df[export_cols].rename(columns=col_map)

        if '유형' in df.columns:
            df['유형'] = df['유형'].map(lambda x: INV_TYPE_LABELS.get(x, x))

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='세트작업이력')
        output.seek(0)

        fname = f"세트작업이력_{date_from or 'all'}_{date_to or 'all'}.xlsx"
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=fname,
        )
    except Exception as e:
        flash(f'세트작업 이력 다운로드 중 오류: {e}', 'danger')
        return redirect(url_for('set_assembly.index'))
