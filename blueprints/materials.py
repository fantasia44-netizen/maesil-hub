"""
materials.py — 생산부 전용 반제품/부재료 관리 Blueprint.

수불장(stock_ledger)의 category 기준으로 분류:
- 반제품수불부: category='반제품'
- 원료수불부:   category in ('원료','원재료')
- 부자재수불부: category='부자재'

완제품은 상품마스터(master.py, 영업부)에서 관리.
"""
import logging
import traceback

from flask import (
    Blueprint, render_template, request, jsonify,
)
from flask_login import current_user

from auth import role_required, _log_action
from db_utils import get_db

logger = logging.getLogger(__name__)

materials_bp = Blueprint('materials', __name__, url_prefix='/materials')

# 생산부가 관리하는 분류 (category 기준)
PRODUCTION_CATEGORIES = ('반제품', '원료', '원재료', '부자재', '부재료')

# UI 표시용 정규화 (원재료 → 원료, 부재료 → 부자재 통합)
CAT_NORMALIZE = {
    '원재료': '원료',
    '부재료': '부자재',
}


def _norm(cat):
    return CAT_NORMALIZE.get((cat or '').strip(), (cat or '').strip())


@materials_bp.route('/')
@role_required('admin', 'manager', 'production')
def index():
    """생산부 자재/반제품 관리 페이지"""
    try:
        return render_template('materials/index.html',
                               material_types=('반제품', '원료', '부자재'))
    except Exception as e:
        logger.error(f'[materials.index] 렌더링 실패: {e}')
        logger.error(traceback.format_exc())
        raise


@materials_bp.route('/api/list')
@role_required('admin', 'manager', 'production')
def api_list():
    """반제품/원료/부자재 품목 조회 (product_costs 기준)"""
    try:
        db = get_db()
    except Exception as e:
        logger.error(f'[materials.api_list] get_db() 실패: {e}')
        logger.error(traceback.format_exc())
        return jsonify({'error': 'DB 연결 실패', 'detail': str(e)[:200]}), 500

    try:
        cost_map = db.query_product_costs() or {}
    except Exception as e:
        logger.error(f'[materials.api_list] product_costs 조회 실패: {e}')
        logger.error(traceback.format_exc())
        # 조회 실패해도 빈 목록 반환 (UI 깨지지 않게)
        return jsonify([])

    rows = []
    product_names = []
    try:
        for pname, cinfo in cost_map.items():
            cat = (cinfo.get('category', '') or '').strip()
            if cat not in PRODUCTION_CATEGORIES:
                continue
            cat_norm = _norm(cat)
            rows.append({
                'product_name': pname,
                'material_type': cat_norm,
                'current_stock': 0,
                'unit': cinfo.get('unit', '') or '',
                'storage_method': cinfo.get('storage_method', '') or '',
                'food_type': cinfo.get('food_type', '') or '',
                'cost_price': cinfo.get('cost_price', 0) or 0,
                'purchase_unit': cinfo.get('purchase_unit', '') or '',
                'standard_unit': cinfo.get('standard_unit', '') or '',
                'conversion_ratio': cinfo.get('conversion_ratio', 1) or 1,
                'weight': cinfo.get('weight', 0) or 0,
                'weight_unit': cinfo.get('weight_unit', 'g') or 'g',
                'memo': cinfo.get('memo', '') or '',
            })
            product_names.append(pname)
    except Exception as e:
        logger.error(f'[materials.api_list] 행 구성 실패: {e}')
        logger.error(traceback.format_exc())
        return jsonify({'error': f'행 구성 실패: {str(e)[:200]}'}), 500

    # 현재 재고 합계 — stock_ledger에서 해당 품목들의 qty 합산
    # ★ Supabase 기본 1000행 limit 회피: chunk 크기 축소 + range() 페이지네이션
    if product_names:
        try:
            client = db.client
            stock_sum = {}
            rpc_ok = False
            # RPC 우선 — 1000행 limit 회피, 단일 호출
            try:
                rpc_res = client.rpc('rpc_get_materials_stock_agg', {
                    'p_categories': list(PRODUCTION_CATEGORIES),
                }).execute()
                for r in (rpc_res.data or []):
                    pn = r.get('product_name', '')
                    qty = float(r.get('total_qty', 0) or 0)
                    stock_sum[pn] = qty
                rpc_ok = True
            except Exception as rpc_err:
                logger.warning(f'[materials.api_list] RPC 재고집계 실패 → fallback: {rpc_err}')

            if not rpc_ok:
                BATCH = 30  # 품목당 평균 행수 고려 — 30품목 * 100행 ≒ 3000 → 페이지네이션 안전
                PAGE = 1000
                for i in range(0, len(product_names), BATCH):
                    names_chunk = product_names[i:i + BATCH]
                    offset = 0
                    while True:
                        try:
                            resp = (client.table('stock_ledger')
                                    .select('product_name,qty')
                                    .eq('status', 'active')
                                    .in_('product_name', names_chunk)
                                    .order('id')
                                    .range(offset, offset + PAGE - 1)
                                    .execute())
                            chunk_rows = resp.data or []
                            for r in chunk_rows:
                                pn = r.get('product_name', '')
                                qty = float(r.get('qty', 0) or 0)
                                stock_sum[pn] = stock_sum.get(pn, 0) + qty
                            if len(chunk_rows) < PAGE:
                                break  # 마지막 페이지
                            offset += PAGE
                        except Exception as chunk_err:
                            logger.warning(f'[materials.api_list] 재고 청크 조회 실패 (offset={offset}): {chunk_err}')
                            break
            for row in rows:
                row['current_stock'] = round(stock_sum.get(row['product_name'], 0), 2)
        except Exception as e:
            logger.warning(f'[materials.api_list] 재고 집계 실패 (목록은 반환): {e}')

    rows.sort(key=lambda r: (r['material_type'], r['product_name']))
    return jsonify(rows)


@materials_bp.route('/api/upsert', methods=['POST'])
@role_required('admin', 'manager', 'production')
def api_upsert():
    """반제품/원료/부자재 1건 등록 또는 수정 (product_costs)"""
    data = request.get_json(silent=True) or {}

    product_name = str(data.get('product_name', '')).strip()
    material_type = str(data.get('material_type', '')).strip()

    if not product_name:
        return jsonify({'error': '품목명을 입력하세요.'}), 400
    if material_type not in ('반제품', '원료', '부자재'):
        return jsonify({
            'error': '분류는 반제품/원료/부자재 중 하나여야 합니다.'
        }), 400

    # 완제품으로 등록된 품목은 여기서 변경 불가 (영업부 상품마스터에서 관리)
    try:
        existing = get_db().query_product_costs().get(product_name) or {}
        existing_cat = (existing.get('category', '') or '').strip()
        if existing_cat in ('완제품', '제품'):
            return jsonify({
                'error': '이 품목은 완제품으로 등록되어 있습니다. '
                         '상품마스터(영업)에서 분류를 변경한 뒤 다시 시도하세요.'
            }), 400
    except Exception:
        pass

    try:
        # cost_type 자동 설정: 반제품은 생산, 원료/부자재는 매입
        cost_type = '생산' if material_type == '반제품' else '매입'

        # 수정 전 기존값 조회 → 감시 롤백용 old_value
        existing_full = {}
        try:
            existing_full = get_db().query_product_costs().get(product_name) or {}
        except Exception:
            pass
        old_val = {
            'material_type': existing_full.get('material_type') or existing_full.get('category', ''),
            'cost_price':       existing_full.get('cost_price', 0),
            'unit':             existing_full.get('unit', ''),
            'memo':             existing_full.get('memo', ''),
            'weight':           existing_full.get('weight', 0),
            'weight_unit':      existing_full.get('weight_unit', 'g'),
            'purchase_unit':    existing_full.get('purchase_unit', ''),
            'standard_unit':    existing_full.get('standard_unit', ''),
            'conversion_ratio': existing_full.get('conversion_ratio', 1),
            'food_type':        existing_full.get('food_type', ''),
            'storage_method':   existing_full.get('storage_method', ''),
            'cost_type':        existing_full.get('cost_type', '매입'),
        } if existing_full else None

        new_val = {
            'material_type':    material_type,
            'cost_price':       float(data.get('cost_price', 0) or 0),
            'unit':             str(data.get('unit', '') or ''),
            'memo':             str(data.get('memo', '') or ''),
            'weight':           float(data.get('weight', 0) or 0),
            'weight_unit':      str(data.get('weight_unit', 'g') or 'g'),
            'purchase_unit':    str(data.get('purchase_unit', '') or ''),
            'standard_unit':    str(data.get('standard_unit', '') or ''),
            'conversion_ratio': float(data.get('conversion_ratio', 1) or 1),
            'food_type':        str(data.get('food_type', '') or ''),
            'storage_method':   str(data.get('storage_method', '') or ''),
            'cost_type':        cost_type,
        }

        get_db().upsert_product_cost(
            product_name=product_name,
            cost_price=new_val['cost_price'],
            unit=new_val['unit'],
            memo=new_val['memo'],
            weight=new_val['weight'],
            weight_unit=new_val['weight_unit'],
            cost_type=cost_type,
            material_type=material_type,
            purchase_unit=new_val['purchase_unit'],
            standard_unit=new_val['standard_unit'],
            conversion_ratio=new_val['conversion_ratio'],
            food_type=new_val['food_type'],
            category=material_type,  # category와 material_type 동일하게 저장
            storage_method=new_val['storage_method'],
        )
        _log_action('materials_upsert', target=product_name,
                    old_value=old_val,
                    new_value=new_val)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@materials_bp.route('/api/delete', methods=['POST'])
@role_required('admin', 'manager')
def api_delete():
    """반제품/원료/부자재 품목 삭제 (admin, manager만)"""
    data = request.get_json(silent=True) or {}
    product_name = str(data.get('product_name', '')).strip()

    if not product_name:
        return jsonify({'error': '품목명이 필요합니다.'}), 400

    try:
        existing = get_db().query_product_costs().get(product_name) or {}
        existing_cat = (existing.get('category', '') or '').strip()
        if existing_cat in ('완제품', '제품'):
            return jsonify({
                'error': '완제품은 여기서 삭제할 수 없습니다. 상품마스터에서 관리하세요.'
            }), 400
    except Exception:
        pass

    try:
        get_db().delete_product_cost(product_name)
        _log_action('materials_delete', target=product_name,
                     old_value={'product_name': product_name})
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@materials_bp.route('/api/rename', methods=['POST'])
@role_required('admin', 'manager', 'production')
def api_rename():
    """품목명 변경 (신규 등록 + 기존 삭제).
    기존 stock_ledger 이력의 product_name은 자동 변경되지 않으므로
    필요 시 원본 페이지(생산관리/입고/재고조정)에서 개별 수정 필요.
    """
    data = request.get_json(silent=True) or {}
    old_name = str(data.get('old_name', '')).strip()
    new_name = str(data.get('new_name', '')).strip()

    if not old_name or not new_name:
        return jsonify({'error': '기존 품목명과 신규 품목명이 필요합니다.'}), 400
    if old_name == new_name:
        return jsonify({'error': '신규 품목명이 기존과 동일합니다.'}), 400

    try:
        cost_map = get_db().query_product_costs()
        existing = cost_map.get(old_name) or {}
        existing_cat = (existing.get('category', '') or '').strip()

        if existing_cat in ('완제품', '제품'):
            return jsonify({
                'error': '완제품 품목명은 여기서 변경할 수 없습니다. 상품마스터에서 수정하세요.'
            }), 400
        if existing_cat not in PRODUCTION_CATEGORIES:
            return jsonify({'error': '등록되지 않은 품목이거나 분류가 올바르지 않습니다.'}), 404

        if new_name in cost_map:
            return jsonify({'error': f'"{new_name}"은(는) 이미 등록된 품목입니다.'}), 400

        # db.rename_product: product_costs + stock_ledger 등 관련 테이블 전체 반영
        result = get_db().rename_product(old_name, new_name)
        if not result.get('success'):
            return jsonify({'error': result.get('error', '이름 변경 실패')}), 500

        _log_action('materials_rename',
                     old_value={'product_name': old_name},
                     new_value={'product_name': new_name})
        changed = result.get('changed', {})
        changed_summary = ', '.join(f'{t}:{c}건' for t, c in changed.items() if c)
        return jsonify({
            'success': True,
            'message': f'"{old_name}" → "{new_name}" 변경 완료. ({changed_summary})',
        })
    except Exception as e:
        logger.error(f'[materials.api_rename] 실패: {e}')
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500
