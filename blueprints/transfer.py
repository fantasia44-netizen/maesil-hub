"""
transfer.py — 창고 이동 관리 Blueprint.
수동 이동 입력 + 엑셀 일괄 이동.
"""
import os
from datetime import datetime
from services.tz_utils import today_kst

import pandas as pd
from flask import (
    Blueprint, render_template, request, current_app,
    flash, redirect, url_for, jsonify, g,
)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from auth import role_required, _log_action
from services.storage_helper import backup_to_storage
from db_utils import get_db

transfer_bp = Blueprint('transfer', __name__, url_prefix='/transfer')

ALLOWED_EXT = {'xlsx', 'xls'}


def _allowed(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT


@transfer_bp.route('/')
@role_required('admin', 'manager', 'logistics', 'general')
def index():
    """창고 이동 폼 (수동 + 엑셀)"""
    db = get_db()
    locations = []
    try:
        locations, _ = db.query_filter_options()
    except Exception:
        pass
    return render_template('transfer/index.html', locations=locations)


@transfer_bp.route('/api/history')
@role_required('admin', 'manager', 'logistics', 'general')
def api_history():
    """창고이동 이력 조회 (transfer_id 기준 그룹).

    Query: date_from, date_to, location, product, limit (기본 100그룹)
    """
    from collections import defaultdict
    from datetime import datetime, timedelta
    db = get_db()
    date_from = (request.args.get('date_from') or '').strip()
    date_to = (request.args.get('date_to') or '').strip()
    if not date_from:
        date_from = (datetime.now().date() - timedelta(days=14)).isoformat()
    if not date_to:
        date_to = datetime.now().date().isoformat()
    loc_filter = (request.args.get('location') or '').strip()
    prod_filter = (request.args.get('product') or '').strip()
    limit = int(request.args.get('limit', 200))

    biz_id = g.biz_id
    try:
        # 범위 내 전체 MOVE 행을 페이지네이션으로 조회.
        # ※ 단일 .limit(2000)은 Supabase max_rows(=1000)에 잘려 최신 1000행만
        #   반환됨 → 범위 내 이동이 1000건 넘으면 오래된 이동이 이력에서 통째로
        #   누락되는 버그(total에서 규명). range() 루프로 전량 확보.
        # + biz_id 필터: 앱이 service_role로 접속해 RLS가 우회되므로 테넌트 격리를
        #   명시적으로 걸어야 함(누락 시 전 테넌트 이동이력 노출).
        rows = []
        _off = 0
        while True:
            _b = db.client.table('stock_ledger').select(
                'id,transaction_date,type,product_name,qty,location,'
                'transfer_id,manufacture_date,lot_number,grade,'
                'created_by,created_at,status,unit'
            ).eq('biz_id', biz_id) \
             .in_('type', ['MOVE_OUT', 'MOVE_IN']) \
             .gte('transaction_date', date_from) \
             .lte('transaction_date', date_to) \
             .or_('status.is.null,status.eq.active') \
             .order('id', desc=True).range(_off, _off + 999).execute().data or []
            rows.extend(_b)
            if len(_b) < 1000:
                break
            _off += 1000

        # transfer_id 그룹핑
        groups = defaultdict(lambda: {'out_rows': [], 'in_rows': []})
        for r in rows:
            tid = r.get('transfer_id') or f'_lone_{r["id"]}'
            if r['type'] == 'MOVE_OUT':
                groups[tid]['out_rows'].append(r)
            else:
                groups[tid]['in_rows'].append(r)

        result = []
        for tid, grp in groups.items():
            out_rows = grp['out_rows']
            in_rows = grp['in_rows']
            ref = (out_rows or in_rows)[0]
            from_loc = out_rows[0]['location'] if out_rows else ''
            to_loc = in_rows[0]['location'] if in_rows else ''
            qty = sum(abs(int(x['qty'] or 0)) for x in out_rows) or sum(int(x['qty'] or 0) for x in in_rows)
            # 필터
            if loc_filter and loc_filter not in (from_loc, to_loc):
                continue
            if prod_filter and prod_filter.lower() not in (ref.get('product_name') or '').lower():
                continue
            result.append({
                'transfer_id': tid if not tid.startswith('_lone_') else None,
                'transaction_date': ref.get('transaction_date'),
                'product_name': ref.get('product_name'),
                'qty': qty,
                'unit': ref.get('unit', '개'),
                'from_location': from_loc,
                'to_location': to_loc,
                'manufacture_date': ref.get('manufacture_date'),
                'lot_number': ref.get('lot_number'),
                'grade': ref.get('grade'),
                'created_by': ref.get('created_by'),
                'created_at': ref.get('created_at'),
                'row_ids': sorted(
                    [x['id'] for x in (out_rows + in_rows)]
                ),
                'out_count': len(out_rows),
                'in_count': len(in_rows),
            })

        # 최신순 정렬
        result.sort(key=lambda x: (x['created_at'] or '', x['transaction_date'] or ''), reverse=True)
        return jsonify({
            'rows': result[:limit],
            'total': len(result),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@transfer_bp.route('/api/cancel/<transfer_id>', methods=['POST'])
@role_required('admin', 'manager')
def api_cancel(transfer_id):
    """창고이동 취소 (해당 transfer_id의 모든 MOVE_OUT/IN 행을 status='cancelled' 처리).

    재고 영향:
      - status='cancelled' 인 행은 활성 재고 집계에서 제외
      - 결과적으로 해당 이동은 발생하지 않은 것으로 간주
    """
    db = get_db()
    try:
        # 대상 행 확인 — RPC 우선 (1000행 limit 회피)
        rows = []
        try:
            rpc_res = db.client.rpc('rpc_get_transfer_detail',
                                    {'p_transfer_id': str(transfer_id)}).execute()
            rows = rpc_res.data or []
        except Exception as rpc_err:
            # Fallback: 직접 조회 + range 페이지네이션
            print(f'[transfer.cancel] rpc_get_transfer_detail fallback: {rpc_err}')
            rows = db.client.table('stock_ledger').select('id,type,product_name,qty,location,status') \
                .eq('transfer_id', transfer_id) \
                .or_('status.is.null,status.eq.active') \
                .range(0, 9999) \
                .execute().data or []
        if not rows:
            return jsonify({'error': '해당 이동을 찾을 수 없거나 이미 취소됨'}), 404

        old_value = [{'id': r['id'], 'type': r['type'], 'qty': r['qty'],
                      'location': r['location'], 'product_name': r['product_name']}
                     for r in rows]

        # 일괄 cancel
        db.client.table('stock_ledger').update({
            'status': 'cancelled',
        }).eq('transfer_id', transfer_id).execute()

        _log_action('cancel_transfer',
                     target=str(transfer_id),
                     detail=f"{len(rows)}건 status=cancelled",
                     old_value=old_value,
                     new_value={'status': 'cancelled'})

        return jsonify({'success': True, 'cancelled_rows': len(rows)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@transfer_bp.route('/api/update/<transfer_id>', methods=['POST'])
@role_required('admin', 'manager')
def api_update(transfer_id):
    """창고이동 수정 — 기존 status='cancelled' 처리 + 새 이동 등록 (재실행).

    Body: {product_name, qty, from_location, to_location, date_str, lot_number?, grade?}
    """
    data = request.get_json(silent=True) or {}
    name = str(data.get('product_name', '')).strip()
    qty = data.get('qty')
    from_loc = str(data.get('from_location', '')).strip()
    to_loc = str(data.get('to_location', '')).strip()
    date_str = str(data.get('date', today_kst())).strip() or today_kst()
    lot = (str(data.get('lot_number', '')).strip() or None)
    grade = (str(data.get('grade', '')).strip() or None)

    if not name or not from_loc or not to_loc:
        return jsonify({'error': '품목/출발/도착 창고 필수'}), 400
    try:
        qty_val = float(qty)
        if qty_val <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({'error': '수량이 올바르지 않습니다'}), 400
    if from_loc == to_loc:
        return jsonify({'error': '출발/도착 창고가 같습니다'}), 400

    db = get_db()
    try:
        # 1) 기존 transfer 취소
        db.client.table('stock_ledger').update({
            'status': 'cancelled',
        }).eq('transfer_id', transfer_id).execute()

        # 2) 새 이동 실행
        from services.transfer_service import process_manual_transfer
        result = process_manual_transfer(
            db, name, qty_val, from_loc, to_loc, date_str,
            lot_number=lot, grade=grade,
            created_by=current_user.username,
        )
        _log_action('update_transfer',
                     target=str(transfer_id),
                     detail=f'{name} {qty_val} {from_loc}→{to_loc} 재이동 처리',
                     new_value=data)
        return jsonify({
            'success': True,
            'cancelled_old_transfer_id': transfer_id,
            'new_moved_count': result.get('moved_count', 0),
            'warnings': result.get('warnings', []),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@transfer_bp.route('/api/products')
@role_required('admin', 'manager', 'logistics', 'general')
def api_products():
    """출발 창고 기준 재고 품목 목록 JSON (자동완성용)"""
    location = request.args.get('location', '')
    if not location:
        return jsonify([])
    try:
        products = None
        # ── RPC 우선: 서버 GROUP BY 1회 왕복 (행 풀스캔은 큰 창고 20초+, total 22.8s 사고).
        #    hub RPC는 (DATE,TEXT,BIGINT) — p_biz_id로 테넌트 격리 ──
        rpc_params = {'p_date_to': today_kst(), 'p_split_mode': 'manufacture'}
        if getattr(g, 'biz_id', None):
            rpc_params['p_biz_id'] = g.biz_id
        try:
            res = get_db().client.rpc('get_stock_snapshot_agg', rpc_params).execute()
            rpc_rows = res.data if isinstance(res.data, list) else None
        except Exception as _rpc_err:
            print(f"[transfer.api_products] RPC 폴백: {_rpc_err}")
            rpc_rows = None
        if rpc_rows is not None:
            agg = {}
            for r in rpc_rows:
                if (r.get('location') or '') != location:
                    continue
                name = r.get('product_name') or ''
                if not name:
                    continue
                q = float(r.get('qty') or 0)
                a = agg.setdefault(name, {'qty': 0.0, 'unit': r.get('unit') or '개',
                                          'mfg': set()})
                a['qty'] += q
                mfg = (r.get('manufacture_date') or '').strip()
                if q > 0 and mfg:
                    a['mfg'].add(mfg)
            products = [
                {'name': n, 'qty': a['qty'], 'unit': a['unit'],
                 'mfg_dates': sorted(a['mfg'])}
                for n, a in agg.items() if a['qty'] > 0
            ]
        # ── 폴백: 행 스캔 (RPC 실패 시) ──
        if products is None:
            from services.excel_io import build_stock_snapshot
            all_data = get_db().query_stock_by_location(location)
            snapshot = build_stock_snapshot(all_data)
            products = []
            for name, info in snapshot.items():
                if info['total'] > 0:
                    mfg_dates = sorted(set(
                        str(grp.get('manufacture_date', '')).strip()
                        for grp in info.get('groups', [])
                        if grp.get('qty', 0) > 0 and str(grp.get('manufacture_date', '')).strip()
                    ))
                    products.append({
                        'name': name,
                        'qty': info['total'],
                        'unit': info.get('unit', '개'),
                        'mfg_dates': mfg_dates,
                    })
        products.sort(key=lambda x: x['name'])
        return jsonify(products)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@transfer_bp.route('/manual', methods=['POST'])
@role_required('admin', 'manager', 'logistics', 'general')
def manual():
    """수동 창고 이동 (단건 — 하위 호환)"""
    product_name = request.form.get('product_name', '').strip()
    qty = request.form.get('qty', 0, type=int)
    from_location = request.form.get('from_location', '').strip()
    to_location = request.form.get('to_location', '').strip()
    date_str = request.form.get('date', today_kst())

    if not product_name or qty <= 0 or not from_location or not to_location:
        flash('품목명, 수량, 출발/도착 창고를 모두 입력하세요.', 'danger')
        return redirect(url_for('transfer.index'))

    if from_location == to_location:
        flash('출발 창고와 도착 창고가 같습니다.', 'danger')
        return redirect(url_for('transfer.index'))

    lot_number = request.form.get('lot_number', '').strip() or None
    grade = request.form.get('grade', '').strip() or None

    try:
        from services.transfer_service import process_manual_transfer
        result = process_manual_transfer(
            get_db(), product_name, qty,
            from_location, to_location, date_str,
            lot_number=lot_number, grade=grade,
            created_by=current_user.username,
        )

        if result.get('warnings'):
            for w in result['warnings']:
                flash(w, 'warning')

        _log_action('manual_transfer',
                     detail=f'{date_str} {product_name} x{qty} '
                            f'{from_location}→{to_location} '
                            f'({result.get("moved_count", 0)}건 처리)')
        flash(f"창고 이동 완료: {result.get('moved_count', 0)}건 처리", 'success')
    except ValueError as e:
        flash(str(e), 'danger')
    except Exception as e:
        flash(f'창고 이동 중 오류: {e}', 'danger')

    return redirect(url_for('transfer.index'))


@transfer_bp.route('/batch', methods=['POST'])
@role_required('admin', 'manager', 'logistics', 'general')
def batch():
    """다건 일괄 창고 이동 (JSON)"""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': '요청 데이터가 없습니다.'}), 400

    items = data.get('items', [])
    date_str = data.get('date', today_kst())

    if not items:
        return jsonify({'error': '이동 항목이 없습니다.'}), 400

    # 유효성 검증
    for i, item in enumerate(items):
        name = str(item.get('product_name', '')).strip()
        qty = item.get('qty', 0)
        from_loc = str(item.get('from_location', '')).strip()
        to_loc = str(item.get('to_location', '')).strip()
        if not name or not from_loc or not to_loc:
            return jsonify({'error': f'{i+1}번째 항목: 품목명/출발/도착 창고를 모두 입력하세요.'}), 400
        try:
            qty_val = float(qty)
            if qty_val <= 0:
                raise ValueError
        except (ValueError, TypeError):
            return jsonify({'error': f'{i+1}번째 항목 ({name}): 수량이 올바르지 않습니다.'}), 400
        if from_loc == to_loc:
            return jsonify({'error': f'{i+1}번째 항목 ({name}): 출발/도착 창고가 같습니다.'}), 400

    try:
        db = get_db()
        total_count = 0
        all_warnings = []

        # Python 경로로 일관 처리 — 제조일·배치속성이 MOVE_OUT/MOVE_IN에 정확히 반영됨
        # (RPC는 manufacture_date 상속이 불확실하므로 사용 중단)
        from services.transfer_service import process_manual_transfer
        import gc
        shared_stock = {}
        for i, item in enumerate(items):
            result = process_manual_transfer(
                db,
                str(item['product_name']).strip(),
                float(item['qty']),
                str(item['from_location']).strip(),
                str(item['to_location']).strip(),
                date_str,
                lot_number=str(item.get('lot_number', '')).strip() or None,
                grade=str(item.get('grade', '')).strip() or None,
                manufacture_date=str(item.get('manufacture_date', '')).strip() or None,
                created_by=current_user.username,
                shared_stock=shared_stock,
            )
            total_count += result.get('moved_count', 0)
            all_warnings.extend(result.get('warnings', []))
            if (i + 1) % 50 == 0:
                gc.collect()

        _log_action('batch_transfer',
                     detail=f'{date_str} 일괄 창고이동 {total_count}건 처리 '
                            f'(항목 {len(items)}건)')
        return jsonify({
            'success': True,
            'count': total_count,
            'warnings': all_warnings,
        })
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': f'창고 이동 중 오류: {e}'}), 500


@transfer_bp.route('/excel', methods=['POST'])
@role_required('admin', 'manager', 'logistics', 'general')
def excel():
    """엑셀 일괄 창고 이동 — 비활성화 (추후 재구현)"""
    return jsonify({'error': '엑셀 업로드 기능은 비활성화되었습니다. 추후 재구현 예정입니다.'}), 410
