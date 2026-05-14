"""
blueprints/integrity.py — 데이터 무결성 검사 API + 관리 화면.
관리자(admin)만 접근 가능.
"""
from flask import Blueprint, render_template, request, current_app, jsonify
from flask_login import current_user
from auth import role_required, _log_action
from db_utils import get_db

integrity_bp = Blueprint('integrity', __name__, url_prefix='/integrity')


@integrity_bp.route('/')
@role_required('admin', 'manager')
def index():
    """정합성 검사 대시보드."""
    return render_template('integrity/index.html')


@integrity_bp.route('/api/run', methods=['POST'])
@role_required('admin', 'manager')
def api_run_check():
    """정합성 검사 실행 API."""
    try:
        from core.integrity_monitor import IntegrityMonitor

        data = request.get_json(silent=True) or {}
        date_from = data.get('date_from', '')
        date_to = data.get('date_to', '')

        monitor = IntegrityMonitor(get_db())
        report = monitor.run_all_checks(
            date_from=date_from or None,
            date_to=date_to or None,
            save=True,
        )

        _log_action('integrity_check',
                     detail=report.get('summary', ''),
                     target=f"{report.get('critical_count', 0)} critical")

        return jsonify(report)
    except Exception as e:
        return jsonify({'error': f'정합성 검사 실행 오류: {e}'}), 500


@integrity_bp.route('/api/reports')
@role_required('admin', 'manager')
def api_reports():
    """최근 정합성 보고서 목록."""
    try:
        from core.integrity_monitor import IntegrityMonitor
        monitor = IntegrityMonitor(get_db())
        reports = monitor.get_recent_reports(limit=20)
        return jsonify(reports)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@integrity_bp.route('/api/outbound-validate')
@role_required('admin', 'manager')
def api_outbound_validate():
    """출고 검증: 송장 역산 vs stock_ledger SALES_OUT 비교."""
    try:
        from services.outbound_validation_service import validate_outbound
        from services.tz_utils import days_ago_kst, today_kst

        date_from = request.args.get('date_from') or days_ago_kst(30)
        date_to = request.args.get('date_to') or today_kst()

        result = validate_outbound(get_db(), date_from, date_to)
        return jsonify(result)
    except Exception as e:
        import traceback
        return jsonify({
            'error': f'출고 검증 오류: {e}',
            'trace': traceback.format_exc(),
        }), 500


@integrity_bp.route('/api/invoice-vs-order')
@role_required('admin', 'manager')
def api_invoice_vs_order():
    """송장출고 기준 vs 주문수집 통합집계 일자별 비교."""
    try:
        from services.invoice_aggregation_service import compare_invoice_vs_order
        from services.tz_utils import days_ago_kst, today_kst

        date_from = request.args.get('date_from') or days_ago_kst(14)
        date_to = request.args.get('date_to') or today_kst()

        result = compare_invoice_vs_order(get_db(), date_from, date_to)
        return jsonify(result)
    except Exception as e:
        import traceback
        return jsonify({
            'error': f'비교 실패: {e}',
            'trace': traceback.format_exc(),
        }), 500


@integrity_bp.route('/api/quick-check')
@role_required('admin', 'manager')
def api_quick_check():
    """빠른 음수 재고 + 이동 불일치 확인 (대시보드용)."""
    try:
        from core.integrity_monitor import IntegrityMonitor
        monitor = IntegrityMonitor(get_db())
        monitor.check_negative_stock()
        monitor.check_transfer_balance()

        critical = sum(1 for i in monitor.issues
                       if i.severity == 'critical')
        warning = sum(1 for i in monitor.issues
                      if i.severity == 'warning')
        return jsonify({
            'critical': critical,
            'warning': warning,
            'issues': [i.to_dict() for i in monitor.issues],
        })
    except Exception as e:
        return jsonify({'critical': 0, 'warning': 0, 'error': str(e)})
