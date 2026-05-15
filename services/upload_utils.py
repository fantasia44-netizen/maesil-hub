"""
upload_utils.py — 파일 업로드 검증 유틸.
Magic byte 기반 MIME 검증으로 확장자 위변조 방지.
"""
import io
import logging

logger = logging.getLogger(__name__)

# XLSX / XLSM / XLSB 는 ZIP 포맷 (PK magic bytes)
_XLSX_MAGIC = b'PK\x03\x04'
# XLS (BIFF8) 는 CFB 포맷
_XLS_MAGIC  = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
# CSV 는 텍스트, magic byte 없음 (허용)
_ALLOWED_EXTENSIONS = {'.xlsx', '.xls', '.xlsm', '.csv'}


def validate_excel_upload(file_storage, max_mb: float = 20.0) -> tuple[bool, str]:
    """
    Flask FileStorage 객체 검증.
    Returns: (ok: bool, error_message: str)
    ok=True면 file_storage.stream이 원래 위치(0)로 seek되어 있음.

    검사 항목:
    1. 파일명 존재 여부
    2. 확장자 허용 목록 (.xlsx, .xls, .xlsm, .csv)
    3. 파일 크기 (max_mb MB 초과 거부)
    4. Magic byte 검증 (.xlsx/.xlsm → PK, .xls → CFB) — L-2 보안
    """
    if not file_storage or not file_storage.filename:
        return False, '파일을 선택하세요.'

    fname = file_storage.filename.lower()
    ext = ''
    if '.' in fname:
        ext = '.' + fname.rsplit('.', 1)[-1]

    if ext not in _ALLOWED_EXTENSIONS:
        return False, f'허용되지 않는 파일 형식입니다. ({", ".join(_ALLOWED_EXTENSIONS)}만 허용)'

    # 크기 검사 (stream 읽기)
    try:
        file_storage.stream.seek(0, 2)
        size = file_storage.stream.tell()
        file_storage.stream.seek(0)
        if size > max_mb * 1024 * 1024:
            return False, f'파일 크기가 너무 큽니다. ({max_mb:.0f}MB 이하만 허용)'
    except Exception:
        pass  # stream.seek 불가한 경우 건너뜀

    # Magic byte 검증 (CSV는 생략)
    if ext in ('.xlsx', '.xlsm', '.xls'):
        try:
            header = file_storage.stream.read(8)
            file_storage.stream.seek(0)

            if ext in ('.xlsx', '.xlsm'):
                if not header.startswith(_XLSX_MAGIC):
                    logger.warning(f'[UploadValidation] MIME mismatch: {file_storage.filename} ext={ext} header={header[:4].hex()}')
                    return False, '파일 형식이 올바르지 않습니다. 실제 Excel 파일(.xlsx)을 업로드하세요.'
            elif ext == '.xls':
                if not (header.startswith(_XLSX_MAGIC) or header.startswith(_XLS_MAGIC)):
                    logger.warning(f'[UploadValidation] MIME mismatch: {file_storage.filename} ext={ext} header={header[:4].hex()}')
                    return False, '파일 형식이 올바르지 않습니다. 실제 Excel 파일(.xls)을 업로드하세요.'
        except Exception as e:
            logger.warning(f'[UploadValidation] magic byte read error: {e}')
            # 검증 실패 시 통과 (보수적 접근 — 실제 파싱에서 걸림)

    return True, ''
