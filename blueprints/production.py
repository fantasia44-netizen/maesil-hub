"""
production.py — 생산 관리 Blueprint.
시스템 입력(다건 배치) + 엑셀 업로드 + 생산일지 PDF + 생산 이력 조회.
"""
import os
import io
import json
import time
import uuid
import hashlib
import tempfile
import threading
from datetime import datetime
from services.tz_utils import today_kst, now_kst

import pandas as pd
from flask import (
    Blueprint, render_template, request, current_app,
    flash, redirect, url_for, send_file, jsonify, session,
)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from auth import role_required, _log_action
from services.storage_helper import backup_to_storage, backup_bytes_to_storage
from db_utils import get_db

production_bp = Blueprint('production', __name__, url_prefix='/production')

ALLOWED_EXT = {'xlsx', 'xls'}

# ── 중복 제출 방지 ──
# 서버 메모리 캐시: fingerprint -> (timestamp, result)
# 5분 안에 같은 (user, date, location, items_hash) 입력 들어오면 차단
_PROD_DEDUP_CACHE = {}
_PROD_DEDUP_LOCK = threading.Lock()
_DEDUP_TTL_SEC = 300  # 5분


def _items_fingerprint(date_str, location, items, user_id):
    """입력의 결정적 해시 — 같은 입력이면 같은 fingerprint."""
    norm = []
    for it in items:
        norm.append({
            'product_name': str(it.get('product_name', '')).strip(),
            'qty': float(it.get('qty', 0) or 0),
            'unit': str(it.get('unit', '')).strip(),
            'manufacture_date': str(it.get('manufacture_date', '') or ''),
            'expiry_date': str(it.get('expiry_date', '') or ''),
            'materials': sorted([
                (str(m.get('product_name','')).strip(),
                 float(m.get('qty', 0) or 0),
                 str(m.get('manufacture_date','') or ''))
                for m in (it.get('materials', []) or [])
            ]),
        })
    norm.sort(key=lambda x: (x['product_name'], x['qty']))
    payload = json.dumps({
        'user': user_id, 'date': date_str, 'loc': location, 'items': norm,
    }, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _dedup_check(fp):
    """fingerprint 중복 검사. 첫 호출이면 OK 등록 후 None 반환,
    중복이면 캐시된 (timestamp, result)을 반환."""
    now = time.time()
    with _PROD_DEDUP_LOCK:
        # 만료된 항목 정리 (1% 확률)
        if len(_PROD_DEDUP_CACHE) > 100:
            expired = [k for k, (t, _) in _PROD_DEDUP_CACHE.items() if now - t > _DEDUP_TTL_SEC]
            for k in expired:
                _PROD_DEDUP_CACHE.pop(k, None)
        existing = _PROD_DEDUP_CACHE.get(fp)
        if existing and (now - existing[0]) < _DEDUP_TTL_SEC:
            return existing  # 중복
        _PROD_DEDUP_CACHE[fp] = (now, None)
        return None  # 첫 호출


def _dedup_save_result(fp, result):
    with _PROD_DEDUP_LOCK:
        _PROD_DEDUP_CACHE[fp] = (time.time(), result)


def _allowed(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT


# ── 생산 폼 ──

@production_bp.route('/')
@role_required('admin', 'manager', 'logistics', 'production')
def index():
    """생산 관리 페이지"""
    db = get_db()
    locations = []
    try:
        locations, _ = db.query_filter_options()
    except Exception:
        pass
    return render_template('production/index.html', locations=locations)


# ── API: 품목 자동완성 ──

@production_bp.route('/api/products')
@role_required('admin', 'manager', 'logistics', 'production')
def api_products():
    """전체 고유 품목명 목록 JSON (생산품 자동완성)"""
    try:
        products = get_db().query_unique_product_names()
        return jsonify(products)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@production_bp.route('/api/stock')
@role_required('admin', 'manager', 'logistics', 'production')
def api_stock():
    """창고별 재고 품목 목록 JSON (원료/반제품 자동완성)"""
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
                # 재고 그룹에서 제조일 목록 추출 (양수 재고만, 중복 제거, 정렬)
                mfg_dates = sorted(set(
                    str(g.get('manufacture_date', '')).strip()
                    for g in info.get('groups', [])
                    if g.get('qty', 0) > 0 and str(g.get('manufacture_date', '')).strip()
                ))
                products.append({
                    'name': name,
                    'qty': info['total'],
                    'unit': info.get('unit') or '개',
                    'mfg_dates': mfg_dates,
                })
        products.sort(key=lambda x: x['name'])
        return jsonify(products)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── API: 생산 이력 조회 ──

@production_bp.route('/api/history')
@role_required('admin', 'manager', 'logistics', 'production')
def api_history():
    """생산 이력 조회 JSON"""
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    if not date_from or not date_to:
        return jsonify([])
    try:
        data = get_db().query_stock_ledger(
            date_from=date_from, date_to=date_to,
            type_list=['PRODUCTION', 'PROD_OUT'])
        rows = []
        for r in data:
            rows.append({
                'id': r.get('id'),
                'date': r.get('transaction_date', ''),
                'type': r.get('type', ''),
                'product_name': r.get('product_name', ''),
                'qty': r.get('qty', 0),
                'location': r.get('location', ''),
                'category': r.get('category', ''),
                'unit': r.get('unit', '개'),
                'manufacture_date': r.get('manufacture_date', '') or '',
                'expiry_date': r.get('expiry_date', '') or '',
                'storage_method': r.get('storage_method', '') or '',
            })
        rows.sort(key=lambda x: (x['date'], x['type'], x['product_name']))
        return jsonify(rows)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── API: 개별 삭제 (admin 전용) ──

@production_bp.route('/api/delete/<int:record_id>', methods=['POST'])
@role_required('admin')
def api_delete(record_id):
    """개별 생산 이력 블라인드 처리 (admin 전용)"""
    try:
        old_record = get_db().query_stock_ledger_by_id(record_id)
        get_db().blind_stock_ledger(record_id, blinded_by=current_user.username)
        _log_action('blind_production', target=str(record_id),
                     old_value=old_record)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── 시스템 입력 배치 생산 ──

# ── API: 개별 수정 (admin 전용) ──

@production_bp.route('/api/update/<int:record_id>', methods=['POST'])
@role_required('admin', 'manager', 'production')
def api_update(record_id):
    """개별 생산 이력 수정"""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': '수정 데이터가 없습니다.'}), 400
    # 생산 이력 1건의 품목명 수정 허용 (오기입 정정 케이스)
    # — 상품마스터/BOM은 건드리지 않고 stock_ledger의 해당 레코드만 변경
    # — event_uid는 skip_fields에 포함되어 23505 UNIQUE 충돌 방지
    allowed = {'product_name', 'qty', 'location', 'category', 'unit',
               'expiry_date', 'storage_method', 'manufacture_date'}
    update_data = {k: v for k, v in data.items() if k in allowed}
    if 'qty' in update_data:
        try:
            update_data['qty'] = float(update_data['qty'])
            if update_data['qty'] == int(update_data['qty']):
                update_data['qty'] = int(update_data['qty'])
        except (ValueError, TypeError):
            return jsonify({'error': '수량이 올바르지 않습니다.'}), 400
    # 빈 문자열 → None 변환 (PostgreSQL DATE/TEXT 컬럼 호환)
    for key in ('expiry_date', 'manufacture_date', 'storage_method'):
        if key in update_data and update_data[key] == '':
            update_data[key] = None
    if not update_data:
        return jsonify({'error': '수정할 항목이 없습니다.'}), 400

    # 원본 레코드 전체 조회 → 변경하지 않는 필드(transaction_date, type 등) 보존
    original = get_db().query_stock_ledger_by_id(record_id)
    if not original:
        return jsonify({'error': '레코드를 찾을 수 없습니다.'}), 404

    # 원본 기반 new_payload 구성 후 수정값 오버라이드
    # ※ event_uid는 원본의 UNIQUE 값이므로 복사하면 INSERT 시 23505 충돌 →
    #    새 event_uid 생성하거나 제거 필요. 현재는 제거 (새 row는 replaces 링크로 추적 가능).
    skip_fields = {'id', 'status', 'replaced_by', 'replaces',
                   'created_at', 'updated_at', 'updated_by', 'created_by',
                   'is_deleted', 'deleted_at', 'deleted_by',
                   'event_uid'}  # ← 2026-04-23 추가 (23505 UNIQUE 충돌 방지)
    new_payload = {k: v for k, v in original.items() if k not in skip_fields}
    new_payload.update(update_data)

    try:
        new_id = get_db().replace_stock_ledger(
            record_id, new_payload, replaced_by_user=current_user.username)
        _log_action('replace_production', target=str(record_id),
                     old_value={k: original.get(k) for k in update_data},
                     new_value=update_data)
        return jsonify({'success': True, 'new_id': new_id})
    except Exception as e:
        _log_action('replace_production_error', target=str(record_id),
                     detail=f'수량 조정 오류: {str(e)}', new_value=update_data)
        return jsonify({'error': str(e)}), 500


# ── 시스템 입력 배치 생산 ──

@production_bp.route('/batch', methods=['POST'])
@role_required('admin', 'manager', 'logistics', 'production')
def batch():
    """다건 일괄 생산 처리 (JSON, 중첩 materials 포함)"""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': '요청 데이터가 없습니다.'}), 400

    items = data.get('items', [])
    date_str = data.get('date', today_kst())
    location = data.get('location', '')
    force = bool(data.get('force', False))  # 경고 확인 플래그

    if not items:
        return jsonify({'error': '생산 항목이 없습니다.'}), 400

    if not location:
        return jsonify({'error': '생산 위치를 선택하세요.'}), 400

    # ── 중복 제출 방지 (서버 fingerprint 캐시) ──
    # 같은 사용자가 같은 (날짜+위치+품목+재료)로 5분 안에 두 번 누르면 차단
    user_id = getattr(current_user, 'id', None) or getattr(current_user, 'username', '?')
    fp = _items_fingerprint(date_str, location, items, user_id)
    cached = _dedup_check(fp)
    if cached is not None:
        cached_ts, cached_result = cached
        elapsed = int(time.time() - cached_ts)
        current_app.logger.warning(
            f"[PROD-DEDUP] 중복 생산 입력 차단: user={user_id} {elapsed}s 전 동일 입력 "
            f"(date={date_str}, loc={location}, items={len(items)})"
        )
        if cached_result:
            # 이미 처리 완료된 결과 그대로 반환 (idempotent 응답)
            return jsonify(cached_result), 200
        # 처리 중 (응답 전) — 동시 두 번 클릭
        return jsonify({
            'error': (
                f'동일한 생산 입력이 {elapsed}초 전에 이미 제출되었습니다.\n'
                '중복 제출이 의심되어 차단했습니다. '
                '실제로 다시 입력해야 한다면 5분 후 다시 시도하거나 '
                '내용을 수정 후 제출하세요.'
            ),
            'duplicate': True,
            'elapsed_sec': elapsed,
        }), 409

    # 유효성 검증
    for i, item in enumerate(items):
        name = str(item.get('product_name', '')).strip()
        qty = item.get('qty', 0)
        if not name:
            return jsonify({'error': f'{i+1}번째 항목: 품목명을 입력하세요.'}), 400
        try:
            if float(qty) <= 0:
                raise ValueError
        except (ValueError, TypeError):
            return jsonify({'error': f'{i+1}번째 항목 ({name}): 생산수량이 올바르지 않습니다.'}), 400

        for j, mat in enumerate(item.get('materials', [])):
            mat_name = str(mat.get('product_name', '')).strip()
            mat_qty = mat.get('qty', 0)
            if not mat_name:
                return jsonify({'error': f'{i+1}번째 항목 재료{j+1}: 재료명을 입력하세요.'}), 400
            try:
                if float(mat_qty) <= 0:
                    raise ValueError
            except (ValueError, TypeError):
                return jsonify({'error': f'{i+1}번째 항목 재료 ({mat_name}): 수량이 올바르지 않습니다.'}), 400

    # material_type 검증
    # - 완제품: 정상 진행
    # - 반제품: 경고 → 사용자 확인 후 진행 (force=True)
    # - 부재료/원료/포장재: 에러 → 거절
    try:
        cost_map = get_db().query_product_costs()
        blocked = []     # 부재료/원료/포장재 — 생산 불가
        semi_items = []  # 반제품 — 경고 후 확인 필요
        for i, item in enumerate(items):
            pname = str(item.get('product_name', '')).strip()
            info = cost_map.get(pname, {})
            mtype = (info.get('material_type', '') or '').strip()
            if not mtype:
                continue  # 마스터 미등록 품목은 스킵
            if mtype == '완제품':
                continue
            if mtype == '반제품':
                semi_items.append(f'{pname}')
            else:
                # 부재료/원료/포장재 → 생산 불가
                blocked.append(f'{pname} (분류: {mtype})')

        if blocked:
            return jsonify({
                'error': (
                    '생산할 수 없는 품목이 포함되어 있습니다. '
                    '부재료/원료/포장재는 생산관리에서 처리할 수 없습니다.\n\n'
                    + '\n'.join(f'• {x}' for x in blocked)
                    + '\n\n완제품 또는 반제품만 생산 가능합니다. '
                      '품목 분류를 확인하세요.'
                ),
                'blocked_items': blocked,
            }), 400

        if semi_items and not force:
            # 반제품 경고는 사용자가 force=True로 재요청해야 하므로 dedup 캐시에서 제거
            with _PROD_DEDUP_LOCK:
                _PROD_DEDUP_CACHE.pop(fp, None)
            return jsonify({
                'warning': True,
                'message': (
                    '⚠️ 반제품이 포함되어 있습니다.\n'
                    '반제품 생산이 맞는지 다시 확인하세요.\n'
                    '(완제품 오기입 가능성이 있습니다)\n\n'
                    + '\n'.join(f'• {x}' for x in semi_items)
                    + '\n\n계속 진행하시겠습니까?'
                ),
                'items': semi_items,
            }), 200
    except Exception as e:
        # 마스터 조회 실패 시 검증 스킵 (기존 동작 유지)
        print(f'[production.batch] material_type 검증 스킵: {e}')

    try:
        from services.production_service import process_production_batch
        result = process_production_batch(
            get_db(), date_str, location, items,
            created_by=current_user.username)
        _log_action('batch_production',
                     detail=f'{date_str} {location} 생산 — '
                            f'산출 {result.get("produced", 0)}건, '
                            f'원재료 차감 {result.get("materials_used", 0)}건 '
                            f'(항목 {len(items)}건)',
                     new_value={'date': date_str, 'location': location,
                                'batch_ids': result.get('batch_ids', []),
                                'produced': result.get('produced', 0)})
        response_payload = {
            'success': True,
            'produced': result.get('produced', 0),
            'materials_used': result.get('materials_used', 0),
            'warnings': result.get('warnings', []),
        }
        # 결과 캐시 — 같은 입력 재시도 시 idempotent 응답
        _dedup_save_result(fp, response_payload)
        return jsonify(response_payload)
    except ValueError as e:
        # 검증 실패는 dedup 캐시 제거 (사용자가 입력 수정 후 재시도)
        with _PROD_DEDUP_LOCK:
            _PROD_DEDUP_CACHE.pop(fp, None)
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        with _PROD_DEDUP_LOCK:
            _PROD_DEDUP_CACHE.pop(fp, None)
        return jsonify({'error': f'생산 처리 중 오류: {e}'}), 500


# ── 엑셀 생산 업로드 ──

@production_bp.route('/excel', methods=['POST'])
@role_required('admin', 'manager', 'logistics', 'production')
def excel_upload():
    """생산 엑셀 업로드 — 비활성화 (추후 재구현)"""
    return jsonify({'error': '엑셀 업로드 기능은 비활성화되었습니다. 추후 재구현 예정입니다.'}), 410


# ── 생산일지 PDF ──

@production_bp.route('/log_pdf')
@role_required('admin', 'manager', 'logistics', 'production')
def log_pdf():
    """생산일지 PDF 다운로드"""
    date_str = request.args.get('date', '')
    location = request.args.get('location', '')

    if not date_str:
        flash('생산일자를 입력하세요.', 'warning')
        return redirect(url_for('production.index'))

    db = get_db()

    try:
        from services.stock_service import query_all_stock_data
        from models import APPROVAL_LABELS
        from reports.production_daily import generate_production_log_pdf

        df = query_all_stock_data(db, date_str)
        if df.empty:
            flash('해당 일자의 생산 데이터가 없습니다.', 'warning')
            return redirect(url_for('production.index'))

        df = df[df['transaction_date'] == date_str]
        if location:
            df = df[df['location'] == location]

        df_prod = df[df['type'] == 'PRODUCTION'].copy()
        df_out = df[df['type'] == 'PROD_OUT'].copy()

        if df_prod.empty and df_out.empty:
            flash('해당 일자의 생산 데이터가 없습니다.', 'warning')
            return redirect(url_for('production.index'))

        config = {
            'target_date': date_str,
            'approvals': {label: '' for label in APPROVAL_LABELS},
            'title': '생산일지',
            'include_warnings': False,
        }

        tmp = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
        tmp_path = tmp.name
        tmp.close()

        try:
            generate_production_log_pdf(tmp_path, config, df_prod, df_out)
            with open(tmp_path, 'rb') as f:
                pdf_bytes = io.BytesIO(f.read())
            fname = f"생산일지_{date_str}.pdf"
            backup_bytes_to_storage(db, pdf_bytes.getvalue(), fname, 'report', 'production')
            return send_file(pdf_bytes, mimetype='application/pdf',
                             as_attachment=True, download_name=fname)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    except Exception as e:
        flash(f'생산일지 PDF 생성 중 오류: {e}', 'danger')
        return redirect(url_for('production.index'))
