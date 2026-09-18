"""ko_morphology.py — KO 어절을 형태소 어간으로 정규화한다 (kiwipiepy).

왜 필요한가
-----------
어절을 공백으로만 자르면 활용형이 전부 다른 용어가 된다.  실제로 물린 결과:

  - install→설치할이 판정에서 "conjugated verb form"으로 거부됐는데,
    trash→폐기한다와 access→액세스한다는 같은 활용형인데 수락됐다.
    그래서 install이 룰 용어집에 없다.
  - 판정 후 남은 룰 충돌 12건이 대부분 어미 변형이다:
    레즈/레즈할, 공개한/공개한다, 육체 피해/육체 피해를.

어간으로 모으면 이 둘이 같이 사라진다.  문장부호 정규화(표면형 2,619→2,407)의
다음 단계이고, 형태소 분석기가 없다는 한계를 실제로 없애는 쪽이다.

보호 대상
---------
Kiwi에 그대로 넣으면 안 되는 것이 둘 있다.

  '2[credit]을'    -> 2 / [ / credit / ] / 을     게임 기호가 부서지고
                                                  credit이 영어 단어와 충돌한다
  '본부(HQ)나'      -> 본부 / ( / HQ / ) / 나      ASCII 약어가 흩어진다
                       ('연구개발부(R&D)'는 R&D가 'R'과 'D'로까지 쪼개진다)

그래서 분석 전에 자리표로 바꿔 넣고 분석 후 되돌린다.  떼어내서 따로 분석하면
안 된다 — 남은 조각이 문맥을 잃어 '을'과 '로'가 조사가 아니라 명사(NNG)로
태깅되고 그대로 살아남는다.  자리표는 문자만 쓴다(숫자를 섞으면 Kiwi가
SL과 SN 두 토큰으로 쪼갠다).

한계 (측정해서 남긴다)
----------------------
사용자 사전에 없는 도메인 복합어는 과분할된다: '연구개발부' -> '연구 개발 부'.
공식 용어집(v2/translations/ko)은 부제·종류·진영·사이클·세트만 담고 룰 어휘를
담지 않아 서버 이름을 덮지 못한다.  사전을 손으로 채우는 것은 도메인 용어를
창작하는 일이므로 하지 않는다.  다만 '연구개발부로'와 '연구개발부의'가 같은
형태로 모이는 본래 목적은 과분할 상태에서도 달성된다.
"""
from __future__ import annotations

import re
from functools import lru_cache

#: 분석 전에 떼어낼 것 — 게임 기호와 ASCII 괄호구.
_PROTECTED = re.compile(r"\[[^\]]+\]|\([A-Za-z0-9&/.\-]+\)")

#: 남길 내용 형태소.  체언·용언·어근·외국어·숫자·부사.
#: 조사(J*), 어미(E*), 파생접사(XS*), 기호(S*), 대명사(NP)는 버린다.
_CONTENT_TAGS = frozenset(
    {"NNG", "NNP", "NNB", "VV", "VA", "VX", "XR", "SL", "SH", "SN", "MAG"}
)

#: 어간만으로는 사전 표제어가 되지 않는 용언.  '뽑' -> '뽑다'.
_VERB_TAGS = ("VV", "VA", "VX")

#: 경동사.  Kiwi는 '설치할'의 '하'를 XSV(파생접사)로, '레즈할'의 '하'를
#: VV(동사)로 태깅한다.  그대로 두면 설치할->설치, 레즈할->레즈 하다가 되어
#: 같은 구성이 다른 용어가 된다.  용언으로 태깅된 '하'/'되'는 버린다.
_LIGHT_VERBS = frozenset({"하", "되"})

#: 자리표 접두사.  문자만 쓴다 — 숫자를 섞으면 SL+SN 두 토큰으로 쪼개진다.
_PLACEHOLDER_PREFIX = "Qzx"


@lru_cache(maxsize=4)
def _kiwi(user_words: tuple[str, ...]):
    """Kiwi 인스턴스를 만들고 *user_words*를 고유명사로 등록한다.

    인스턴스 생성이 수 초 걸리고 배치 전체가 같은 것을 쓰므로 캐시한다.
    """
    from kiwipiepy import Kiwi

    kiwi = Kiwi()
    for word in user_words:
        if word.strip():
            kiwi.add_user_word(word, "NNP")
    return kiwi


def _lemma(form: str, tag: str) -> str:
    return form + "다" if tag.split("-")[0] in _VERB_TAGS else form


def _letters(index: int) -> str:
    """0 -> 'a', 25 -> 'z', 26 -> 'aa'.  자리표를 문자만으로 유일하게 만든다."""
    name = ""
    while True:
        name = chr(ord("a") + index % 26) + name
        index = index // 26 - 1
        if index < 0:
            return name


def normalize_eojeol(eojeol: str, user_words: tuple[str, ...] = ()) -> str:
    """어절 하나를 내용 형태소 어간으로 정규화한다.

    보호 대상은 자리표로 치환해 분석한 뒤 원래 문자열로 되돌린다.  남는 것이
    없으면 (조사·대명사뿐인 어절) 빈 문자열을 돌려준다.

        '설치할'      -> '설치'
        '레즈할'      -> '레즈'
        '서버를'      -> '서버'
        '뽑는다'      -> '뽑다'
        '2[credit]을' -> '2 [credit]'
        '당신의'      -> ''
    """
    mapping: dict[str, str] = {}

    def stash(match: re.Match) -> str:
        placeholder = _PLACEHOLDER_PREFIX + _letters(len(mapping))
        mapping[placeholder] = match.group()
        return placeholder

    masked = _PROTECTED.sub(stash, eojeol)
    if not masked.strip():
        return " ".join(mapping.values())

    kiwi = _kiwi(user_words)
    forms: list[str] = []
    for token in kiwi.tokenize(masked):
        tag = token.tag.split("-")[0]
        if tag not in _CONTENT_TAGS:
            continue
        if tag in _VERB_TAGS and token.form in _LIGHT_VERBS:
            continue
        forms.append(_lemma(token.form, tag))

    return " ".join(p for p in (_restore(f, mapping) for f in forms) if p)


def _restore(form: str, mapping: dict[str, str]) -> str:
    """Put the protected spans back.

    Substring replacement, not exact match: Kiwi can glue a placeholder to an
    adjacent letter, so '[credit]s' comes back as the single token 'Qzxas'.
    Longest placeholder first so 'Qzxa' never eats the prefix of 'Qzxaa'.
    """
    for placeholder in sorted(mapping, key=len, reverse=True):
        form = form.replace(placeholder, mapping[placeholder])
    return form
