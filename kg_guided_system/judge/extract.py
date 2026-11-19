"""① 프로필 추출: 사용자의 자연어 → 구조화된 프로필 JSON.

LLM 은 문장에 있는 사실만 옮긴다. 나이 계산·소득 → 기준중위소득 % 환산 같은 계산은 코드가 한다.
프롬프트에 KG 에서 많이 쓰이는 대상 범주 어휘를 넣어, 모델이 KG 의 표현을 그대로 쓰도록 유도한다(KG-guided).
"""
from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from dataclasses import dataclass, field

from .kg_index import KGC, KGIndex
from .llm import LLMClient

# 2026년 기준 중위소득 (원/월, 가구원 수별). 보건복지부 고시(2025.8).
# 1~7인: 보건복지부 페이지 값과 대조함 (2026-11-19, 연구실 실행 보고서).
MEDIAN_INCOME_2026 = {1: 2_564_238, 2: 4_199_292, 3: 5_359_036, 4: 6_494_738, 5: 7_556_719, 6: 8_555_952,
                      7: 9_515_150}


def median_income(size: int) -> int:
    """가구원 수별 기준중위소득. 8인 이상은 1인 늘 때마다 (7인 - 6인) 차이를 더한다.

    8인 이상 산정 방식은 고시문에서 아직 확인하지 못했다 (확인되면 이 주석을 고칠 것).
    """
    size = max(size, 1)
    if size in MEDIAN_INCOME_2026:
        return MEDIAN_INCOME_2026[size]
    step = MEDIAN_INCOME_2026[7] - MEDIAN_INCOME_2026[6]
    return MEDIAN_INCOME_2026[7] + step * (size - 7)

USER_TYPES = ["개인", "가구", "소상공인", "법인/시설/단체"]
NON_TARGET_PREFIX = ("ut_", "st_", "f_", "ot_", "dim_")


def _nullable(t: dict) -> dict:
    return {"anyOf": [t, {"type": "null"}]}


def profile_schema(fields: list[str], support_types: list[str]) -> dict:
    """2차(문법 강제) 생성에 쓰는 JSON 스키마."""
    strs = {"type": "array", "items": {"type": "string"}}
    return {
        "type": "object",
        "properties": {
            "age": _nullable({"type": "integer"}),
            "birth_year": _nullable({"type": "integer"}),
            "region": _nullable({"type": "string"}),
            "gender": _nullable({"enum": ["여성", "남성"]}),
            "income_pct": _nullable({"type": "number"}),
            "monthly_income": _nullable({"type": "integer"}),
            "household_size": _nullable({"type": "integer"}),
            "groups": strs,
            "not_groups": strs,
            "receiving": strs,
            "user_type": {"enum": USER_TYPES},
            "fields": {"type": "array", "items": {"enum": fields}},
            "support_types": {"type": "array", "items": {"enum": support_types}},
            "keywords": strs,
        },
        "required": ["age", "birth_year", "region", "gender", "income_pct", "monthly_income", "household_size",
                     "groups", "not_groups", "receiving", "user_type", "fields", "support_types", "keywords"],
    }


def is_target_group(iri: str) -> bool:
    return iri.startswith(KGC) and not iri[len(KGC):].startswith(NON_TARGET_PREFIX)


def concept_usage(kg: KGIndex) -> Counter:
    """대상 범주 개념이 서비스 조건(requires·anyOf·excludes·prefers)에 쓰인 횟수."""
    use: Counter = Counter()
    for s in kg.services.values():
        for c in s.requires | s.excludes | s.prefers:
            use[c] += 1
        for g in s.groups.values():
            for c in g:
                use[c] += 1
    return use


def vocabulary(kg: KGIndex, n: int = 120) -> list[str]:
    """프롬프트에 넣을 대상 범주 어휘: 많이 쓰이는 순. '…거주자'처럼 지역에서 계산되는 것은 뺀다."""
    out = []
    for c, _ in concept_usage(kg).most_common():
        if not is_target_group(c):
            continue
        lab = kg.label(c)
        if lab.endswith("거주자") or lab in out:
            continue
        out.append(lab)
        if len(out) >= n:
            break
    return out


SYSTEM = """너는 한국 공공서비스 상담 시스템의 '프로필 추출기'다.
사용자의 말에서 자격 판정에 필요한 사실만 JSON 으로 옮긴다. 말하지 않은 것은 추측하지 말고 null 또는 빈 목록으로 둔다."""

GUIDE = """출력 형식 (JSON 객체 하나만, 설명 없이):
{{"age": 정수|null, "birth_year": 정수|null, "region": "시/구"|null, "gender": "여성"|"남성"|null,
 "income_pct": 기준중위소득 %|null, "monthly_income": 가구 월소득(원)|null, "household_size": 가구원 수|null,
 "groups": [해당하는 대상 범주], "not_groups": [해당하지 않는다고 말한 범주], "receiving": [이미 받고 있는 지원 사업 이름],
 "user_type": "개인"|"가구"|"소상공인"|"법인/시설/단체",
 "fields": [관심 분야], "support_types": [원하는 지원 유형], "keywords": [찾는 지원의 핵심 단어]}}

규칙
- age 는 말한 나이 그대로. 출생연도를 말했으면 birth_year 에만 넣고 age 는 null (나이는 시스템이 계산한다).
- region 은 "서울 중구" 처럼 시와 구. 동 이름만 말했으면 그 동이 속한 구를 쓴다. 모르면 null.
- 소득: "중위소득 50%" 처럼 비율을 말했으면 income_pct. 금액을 말했으면 monthly_income 에 원 단위 정수(200만원 → 2000000), 연소득이면 12로 나눈다. household_size 는 본인 포함 가구원 수.
- groups 는 하나의 범주씩 짧은 명사로 나눈다 ("기초수급 대학생" → ["기초생활수급자", "대학생"]). 아래 어휘에 있는 표현이면 그대로 쓰고, 없으면 짧은 명사구로 쓴다. 문장으로 쓰지 않는다.
- 나이·성별·지역에서 바로 나오는 범주(청년, 노인, 여성, ○○구 거주자)는 groups 에 넣지 않는다. 시스템이 계산한다.
- 말에 암시된 본인의 범주도 넣는다:
  "취업 준비 중"·"일자리 구하는 중" → 구직자/실업자 / "혼자 살아요" → 1인가구 / "보육원·시설에서 나왔어요" → 자립준비청년
  "결혼한 지 1년" → 신혼부부 / "귀농했어요" → 귀농인 / "회사원"·"직장 다녀요" → 근로자/직장인 / "임신 중" → 임산부 (gender 는 "여성")
- 가족의 상태를 본인 범주로 넣지 않는다. ("딸이 고등학생이에요" → 본인은 고등학생 아님. 넣는다면 "고등학생 자녀")
- 수급 자격(기초생활수급자, 차상위계층 등)은 사용자가 그 이름을 말했을 때만 groups 에 넣는다.
  이미 받고 있는 개별 사업(기초연금, 근로장려금, 국가장학금 등)은 receiving 에만 넣고, 그것으로 수급 자격을 추측하지 않는다.
  (기초연금 ≠ 기초생활수급자, 실업급여 ≠ 기초생활수급자)
- not_groups 는 사용자가 명시적으로 아니라고 한 것만 ("장애는 없어요" → ["장애인"], "집이 있어요" → not_groups ["무주택세대"], groups ["주택소유자"]).
- user_type 은 보통 "개인". 가게·사업체로 묻는 경우만 "소상공인", 기관·법인이면 "법인/시설/단체".
- fields 는 다음 중에서: {fields}
- support_types 는 다음 중에서: {support_types}
- keywords 는 찾는 지원 자체를 나타내는 단어 (예: "월세", "장학금", "취업"). 범주·지역은 넣지 않는다.

대상 범주 어휘 (KG 에서 많이 쓰이는 순):
{vocab}

예시
사용자: 신당동 사는 22살 대학생이에요. 기초수급자고 장애는 없어요. 월세 지원 받을 수 있을까요?
{{"age": 22, "birth_year": null, "region": "서울 중구", "gender": null, "income_pct": null, "monthly_income": null, "household_size": null,
 "groups": ["대학생", "기초생활수급자"], "not_groups": ["장애인"], "receiving": [], "user_type": "개인",
 "fields": ["주거·자립"], "support_types": ["현금"], "keywords": ["월세"]}}"""


@dataclass
class Extraction:
    """LLM 이 낸 값(raw)과, 코드가 정리·계산한 값."""
    raw: dict
    age: int | None = None
    region: str | None = None
    gender: str | None = None
    income_pct: float | None = None
    groups: list[str] = field(default_factory=list)
    not_groups: list[str] = field(default_factory=list)
    receiving: list[str] = field(default_factory=list)
    user_type: str = "개인"
    fields: list[str] = field(default_factory=list)
    support_types: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)       # 계산 근거·정리 내역
    mode: str = ""                                        # free | guided


def _int(v):
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        return int(float(str(v).replace(",", "").strip()))
    except ValueError:
        return None


def _num(v):
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        return float(str(v).replace("%", "").replace(",", "").strip())
    except ValueError:
        return None


def _strs(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list):
        raise TypeError("목록이 아님")
    out = []
    for x in v:
        x = str(x).strip()
        if x and x not in out and len(x) <= 30:      # 문장처럼 긴 것은 범주가 아니다
            out.append(x)
    return out


def check_raw(obj: dict) -> dict:
    """1차 자유 생성 결과 검사. 틀이 맞지 않으면 TypeError → 2차 문법 강제 생성."""
    if not isinstance(obj, dict) or "groups" not in obj:
        raise TypeError("groups 키가 없음")
    for k in ("groups", "not_groups", "receiving", "fields", "support_types", "keywords"):
        _strs(obj.get(k))
    return obj


def normalize(raw: dict, today: dt.date, fields: list[str], support_types: list[str]) -> Extraction:
    ex = Extraction(raw=raw)
    ex.age = _int(raw.get("age"))
    by = _int(raw.get("birth_year"))
    if by and 1900 < by <= today.year:          # 출생연도가 있으면 모델의 나이 계산을 믿지 않는다
        age = today.year - by
        if ex.age is not None and ex.age != age:
            ex.notes.append(f"모델이 낸 나이 {ex.age} 대신 출생연도로 계산")
        ex.age = age
        ex.notes.append(f"나이 = {today.year} - {by} = {ex.age} (생일 전이면 1살 적음)")
    if ex.age is not None and not 0 <= ex.age <= 120:
        ex.notes.append(f"나이 {ex.age} 무시")
        ex.age = None

    region = raw.get("region")
    ex.region = str(region).strip() if region else None
    g = raw.get("gender")
    ex.gender = g if g in ("여성", "남성") else None

    ex.income_pct = _num(raw.get("income_pct"))
    if ex.income_pct is None:
        inc, size = _int(raw.get("monthly_income")), _int(raw.get("household_size"))
        if inc:
            if inc < 10_000:                      # '200'(만원)처럼 단위를 빠뜨린 경우
                inc *= 10_000
                ex.notes.append("월소득을 만원 단위로 보고 환산")
            if size is None:
                size = 1
                ex.notes.append("가구원 수를 말하지 않아 1인 가구로 계산")
            base = median_income(size)
            ex.income_pct = round(inc / base * 100, 1)
            ex.notes.append(f"소득 {inc:,}원 / {size}인 가구 기준중위소득 {base:,}원 = {ex.income_pct}%")
    if ex.income_pct is not None and not 0 <= ex.income_pct <= 1000:
        ex.notes.append(f"소득 비율 {ex.income_pct} 무시")
        ex.income_pct = None

    ex.groups = _strs(raw.get("groups"))
    if ex.gender is None and any(g in ("임산부", "임신 중", "임신부") for g in ex.groups):
        ex.gender = "여성"
        ex.notes.append("임산부 → 성별 여성")
    ex.not_groups = [x for x in _strs(raw.get("not_groups")) if x not in ex.groups]
    ex.receiving = _strs(raw.get("receiving"))
    ut = raw.get("user_type")
    ex.user_type = ut if ut in USER_TYPES else "개인"
    ex.fields = [x for x in _strs(raw.get("fields")) if x in fields]
    ex.support_types = [x for x in _strs(raw.get("support_types")) if x in support_types]
    ex.keywords = _strs(raw.get("keywords"))
    return ex


class ProfileExtractor:
    def __init__(self, kg: KGIndex, llm: LLMClient, vocab_size: int = 120):
        self.kg, self.llm = kg, llm
        self.fields = sorted({kg.label(s.field_) for s in kg.services.values() if s.field_})
        # 지원유형은 최상위(현금, 현물, 서비스 …)만 고르게 하고, 하위(현금(장학금) 등)는 판정 쪽에서 펼친다
        self.support_types = sorted({kg.label(c) for c in kg.labels if c.startswith(KGC + "st_")
                                     and not any(p.startswith(KGC + "st_") for p in kg.parents.get(c, ()))})
        self.vocab = vocabulary(kg, vocab_size)
        self.schema = profile_schema(self.fields, self.support_types)

    def messages(self, text: str) -> list[dict]:
        guide = GUIDE.format(fields=", ".join(self.fields), support_types=", ".join(self.support_types),
                             vocab=", ".join(self.vocab))
        return [{"role": "system", "content": SYSTEM + "\n\n" + guide},
                {"role": "user", "content": text}]

    def extract(self, text: str, today: dt.date | None = None) -> Extraction:
        today = today or dt.date.today()
        raw, mode = self.llm.chat_json(self.messages(text), self.schema, validate=check_raw)
        ex = normalize(raw, today, self.fields, self.support_types)
        ex.mode = mode
        return ex


def dumps(ex: Extraction) -> str:
    d = {k: v for k, v in ex.__dict__.items() if k != "raw"}
    return json.dumps(d, ensure_ascii=False)
