"""
services/option_matcher.py
옵션마스터 매칭 공통 모듈

OrderProcessor, marketplace_validation_service, marketplace blueprint에서
공통으로 사용하는 옵션 매칭 유틸리티.

Key 규칙: NFKC 정규화 + 공백제거 + 대문자 + 구분자 통일
"""
import unicodedata
from services.product_name import canonical as _canonical_pn


def _canonicalize_match(m):
    """매칭 결과 dict의 '품목명'을 canonical로 통일 (반환 직전 보험)."""
    if m and m.get('품목명'):
        m = {**m, '품목명': _canonical_pn(m['품목명'])}
    return m

# "옵션 없음" 판별용 키워드 — 이 값이 option에 있으면 상품명으로 폴백
_NO_OPT = {'단일상품', '옵션없음', '옵션 없음', '기본', '해당없음',
            '없음', '-', 'noption', 'none', 'n/a', '상품정보참조'}


def _is_no_option(val):
    """option 값이 실질적으로 '없음'인지 판별"""
    if not val:
        return True
    v = val.strip()
    if not v:
        return True
    return v in _NO_OPT or any(nk in v for nk in ('단일상품',))


def build_match_key(mode: str, product_name: str, option_name: str) -> str:
    """채널별 매칭 키 생성.

    쿠팡: 옵션 없으면 상품명만, 있으면 상품명+옵션 결합
    옥션/G마켓: 옵션에서 '/' 앞부분 사용, 없으면 상품명
    스마트스토어/자사몰/오아시스/11번가/카카오 등: 옵션 유효하면 옵션, 아니면 상품명

    Returns:
        정규화 전 원문 키 (공백 포함) — match_option에서 정규화함
    """
    prod = str(product_name or '').strip()
    opt = str(option_name or '').strip()

    if mode == "쿠팡":
        # 쿠팡: 단일상품/빈옵션 → 상품명만, 아니면 상품명+옵션
        return prod if _is_no_option(opt) else prod + opt
    elif mode == "옥션/G마켓":
        # 옥션/G마켓: 옵션에서 '/' 앞부분 사용, 없으면 상품명
        return opt.split('/')[0].strip() if opt and not _is_no_option(opt) else prod
    else:
        # 스마트스토어/자사몰/오아시스/11번가/카카오/해미애찬 등
        # 옵션이 유효하면 옵션 사용, 단일상품/빈값이면 상품명 폴백
        return opt if opt and not _is_no_option(opt) else prod


def _normalize(key: str) -> str:
    """NFKC 정규화 + 공백 제거 + 대문자 + 구분자 통일 (Key 비교 기준).

    1. NFKC: 전각→반각(１００ｇ→100g), 특수공백, 한글 자모분리 통합
    2. 공백 제거 + 대문자
    3. 구분자 통일: , → ;  (Cafe24 API는 ','사용, 마스터는 ';')

    DB match_key 저장(db_supabase.py)에서도 이 함수를 호출하므로
    런타임 매칭과 DB 저장이 항상 동일 결과를 보장.
    """
    s = str(key or '')
    s = unicodedata.normalize('NFKC', s)   # 전각→반각, 자모 통합
    s = s.replace(' ', '').upper()
    s = s.replace(',', ';')                # 구분자 통일
    s = s.replace(';', '')                 # 세미콜론도 제거 (API vs 마스터 불일치 방지)
    return s


def prepare_opt_list(opt_list: list) -> None:
    """옵션 리스트에 정규화 Key를 미리 계산해 주입 (성능 최적화).

    opt_list 각 항목의 Key를 _normalize()로 재정규화.
    DB match_key에 구분자 통일(, → ;)이 반영 안 됐을 수 있으므로 항상 재계산.
    in-place 수정.
    """
    for o in opt_list:
        raw = o.get('Key') or str(o.get('원문명', ''))
        o['Key'] = _normalize(raw)


def match_option(key: str, opt_list: list) -> dict | None:
    """정규화된 Key로 옵션마스터에서 일치 항목 탐색 (하위호환 wrapper).

    ※ 2026-04-24 변경: 100% 정확 매칭만 자동 반영. 부분매칭은 None 반환.
       오매칭 방지 (예: 가자미 주문이 동태로 잘못 매칭되는 사고 방지).
       부분매칭 후보가 필요하면 match_option_detailed() 사용.

    Returns:
        정확 매칭 항목 dict or None (부분매칭은 None)
    """
    m, conf, _ = match_option_detailed(key, opt_list)
    if conf >= 100:
        return _canonicalize_match(m)
    return None


def match_option_detailed(key: str, opt_list: list):
    """정규화된 Key로 옵션마스터에서 매칭 시도 + 신뢰도/후보 반환.

    분류:
      - 100 (정확): normalized Key 완전 일치 → 자동 반영
      - 90  (고신뢰 부분매칭): 부분매칭 후보 1개 + Key가 원문의 50% 이상 차지
              → 팝업으로 사용자 승인 후 반영
      - <90 (저신뢰/애매): 후보 여러 개 또는 매우 짧은 key 매칭
              → 팝업으로 사용자 선택 필요

    Returns:
        tuple (match, confidence, candidates)
            match       : 확정 후보 dict (정확매칭만, 아니면 None)
            confidence  : 100 | 90 | (0~89)
            candidates  : 부분매칭 후보 dict 리스트 (신뢰도 순)
    """
    if not key:
        return None, 0, []
    normalized = _normalize(key)

    # 1차: 정확 매칭 → 100
    for o in opt_list:
        o_key = o.get('Key') or _normalize(str(o.get('원문명', '')))
        if o_key == normalized:
            o2 = _canonicalize_match(o)
            return o2, 100, [o2]

    # 2차: 부분 매칭 후보 수집 (substring, Key 길이 >=4, 품목명 있음)
    partial = [o for o in opt_list
               if len(o.get('Key', '') or '') >= 4
               and o.get('품목명', '').strip()
               and (o.get('Key') or '') in normalized]
    if not partial:
        return None, 0, []

    # 신뢰도 계산 — Key 가 원문의 몇 % 차지하는지
    def _score(o):
        k = o.get('Key', '') or ''
        if not k or not normalized:
            return 0
        ratio = len(k) / max(len(normalized), 1)
        # 매우 짧은 매칭(예: '시금치' 3자가 긴 원문에 포함)에 페널티
        return int(ratio * 100)

    scored = sorted(
        [(o, _score(o)) for o in partial],
        key=lambda x: -x[1]
    )
    top_o, top_score = scored[0]

    # 후보 1개 + 스코어 70 이상이면 "고신뢰 부분매칭" (사용자 확인 필요)
    # ※ 자동 반영하지 않음 — 반드시 팝업 승인 후에만 적용
    if len(scored) == 1 and top_score >= 70:
        return None, 90, [_canonicalize_match(top_o)]

    # 그 외는 저신뢰 — 후보 최대 5개 반환
    candidates = [_canonicalize_match(o) for o, _ in scored[:5]]
    confidence = min(top_score, 89)  # 90 미만으로 클램프
    return None, confidence, candidates


def check_option_registration(orders: list, channel: str, opt_list: list) -> dict:
    """주문 목록에 대해 옵션마스터 등록 여부를 일괄 검사.

    Args:
        orders: [{'product_name': ..., 'option_name': ...}, ...] 형태
        channel: 채널명 (build_match_key mode로 사용)
        opt_list: prepare_opt_list() 처리된 옵션마스터 리스트

    Returns:
        {
            'registered': int,          # 매칭 성공 건수
            'unregistered': int,        # 미매칭 건수
            'unregistered_items': list, # 미매칭 원문 키 목록 (중복 제거)
        }
    """
    registered = 0
    unregistered_set = []  # 순서 유지 중복제거용

    for o in orders:
        prod = str(o.get('product_name') or '').strip()
        opt = str(o.get('option_name') or '').strip()
        key = build_match_key(channel, prod, opt)
        match = match_option(key, opt_list)
        if match:
            registered += 1
        else:
            if key and key not in unregistered_set:
                unregistered_set.append(key)

    return {
        'registered': registered,
        'unregistered': len(orders) - registered,
        'unregistered_items': unregistered_set,
    }
