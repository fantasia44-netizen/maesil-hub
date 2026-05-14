"""품목명 정규화 — 전사 단일 규칙 (maesil-total에서 차용 + 강화).

모든 product_name 의 저장/매칭/groupby/dict 키 생성은 canonical() 통과 필수.

규칙:
  1. 모든 종류의 공백 제거 (보이는 공백 + 보이지 않는 zero-width 공백 모두)
  2. NFKC 정규화 (전각->반각, 합성문자 통일)
  3. strip
"""
import unicodedata

# 제거 대상 공백/제어 문자 (보이는 것 + zero-width 모두)
_WHITESPACE_CODEPOINTS = (
    0x0009,  # tab
    0x000A,  # LF
    0x000B,  # vertical tab
    0x000C,  # form feed
    0x000D,  # CR
    0x0020,  # 일반 공백
    0x00A0,  # NBSP
    0x1680,  # OGHAM SPACE
    0x2000,  # EN QUAD
    0x2001,  # EM QUAD
    0x2002,  # EN SPACE
    0x2003,  # EM SPACE
    0x2004,  # THREE-PER-EM SPACE
    0x2005,  # FOUR-PER-EM SPACE
    0x2006,  # SIX-PER-EM SPACE
    0x2007,  # FIGURE SPACE
    0x2008,  # PUNCTUATION SPACE
    0x2009,  # THIN SPACE
    0x200A,  # HAIR SPACE
    0x200B,  # ZERO WIDTH SPACE
    0x200C,  # ZERO WIDTH NON-JOINER
    0x200D,  # ZERO WIDTH JOINER
    0x202F,  # NARROW NO-BREAK SPACE
    0x205F,  # MEDIUM MATHEMATICAL SPACE
    0x3000,  # 전각 공백 IDEOGRAPHIC SPACE
    0xFEFF,  # ZERO WIDTH NO-BREAK SPACE (BOM)
)
_WHITESPACE_TRANS = {cp: None for cp in _WHITESPACE_CODEPOINTS}


def canonical(name) -> str:
    """품목명 정규화 (전사 표준).

    띄어쓰기·전각·zero-width 모두 무관하게 같은 결과.
    예:
      canonical('전복 벌집 200g')        == '전복벌집200g'
      canonical('전복　벌집200g')         == '전복벌집200g'  (전각 공백)
      canonical('전복​벌집200g')    == '전복벌집200g'  (zero-width)
      canonical(' 전복벌집200g ')         == '전복벌집200g'

    Args:
        name: 입력 문자열 (None/빈값 허용)

    Returns:
        정규화된 문자열. 빈 입력은 '' 반환.
    """
    if not name:
        return ''
    s = str(name)
    # NFKC: 전각->반각, 합성문자 통일
    s = unicodedata.normalize('NFKC', s)
    # 공백/zero-width 모두 제거
    s = s.translate(_WHITESPACE_TRANS)
    return s.strip()


def canonical_or(name, fallback=''):
    """canonical() 이 빈 문자열이면 fallback 반환."""
    result = canonical(name)
    return result if result else fallback


def normalize_match_key(name: str) -> str:
    """option_master 매칭용 정규화 키.
    - canonical 적용 (NFKC + 모든 공백 제거)
    - 대문자 통일
    - 일부 구분자 제거 (',', '·', '/', '-', '_', '.')
    """
    s = canonical(name).upper()
    for ch in (',', '·', '/', '-', '_', '.'):
        s = s.replace(ch, '')
    return s


def equals_canonical(a, b) -> bool:
    """띄어쓰기 무관 동등 비교."""
    return canonical(a) == canonical(b)


# 호환 재수출
normalize_product_name = canonical
_norm = canonical
