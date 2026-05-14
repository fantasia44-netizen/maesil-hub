"""품목명 정규화 — 전사 단일 규칙 (maesil-total에서 그대로 차용).

모든 product_name 의 저장/매칭/groupby/dict 키 생성은 canonical() 통과 필수.

규칙:
  1. 모든 종류의 공백 제거 (일반/전각/NBSP/narrow NBSP/FIGURE SPACE/탭/개행)
  2. strip
"""

_WHITESPACE_CHARS = (
    ' ',        # 일반 공백
    '　',   # 전각 공백 (IDEOGRAPHIC SPACE)
    ' ',   # NBSP
    ' ',   # NARROW NO-BREAK SPACE
    ' ',   # FIGURE SPACE
    '\t',       # 탭
    '\r',       # CR
    '\n',       # LF
)


def canonical(name) -> str:
    """품목명 정규화 (전사 표준).

    Args:
        name: 입력 문자열 (None/빈값 허용)

    Returns:
        정규화된 문자열. 빈 입력은 '' 반환.
    """
    if not name:
        return ''
    s = str(name)
    for ch in _WHITESPACE_CHARS:
        if ch in s:
            s = s.replace(ch, '')
    return s.strip()


def canonical_or(name, fallback=''):
    """canonical() 이 빈 문자열이면 fallback 반환."""
    result = canonical(name)
    return result if result else fallback


def normalize_match_key(name: str) -> str:
    """option_master 매칭용 정규화 키.
    - canonical 적용
    - 대문자 통일
    - 일부 구분자 통일 (',' '·' '/' '-' → '')
    """
    s = canonical(name).upper()
    for ch in (',', '·', '/', '-', '_'):
        s = s.replace(ch, '')
    return s


# 호환 재수출
normalize_product_name = canonical
_norm = canonical
