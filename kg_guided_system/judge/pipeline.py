"""문장 → ① 프로필 추출 → ② 개념 연결 → ③ 판정 → 관심사 순위.

    from judge.pipeline import Consultant
    c = Consultant.from_paths()                       # LLM_BASE_URL 의 vLLM 사용
    out = c.consult("신당동 사는 22살 대학생인데 기초수급자예요. 월세 지원 있나요?")
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from .extract import Extraction, ProfileExtractor
from .judge import Judge, Judgment, Profile, Result, Status, Tier
from .kg_index import KGC, KGIndex, load_kg
from .linker import ConceptLinker, Link
from .llm import LLMClient

DEFAULT_KG = Path(__file__).resolve().parent.parent.parent / "public_service_kg_final_20261011" / "output" / "kg.ttl"
DEFAULT_PATCHES = [Path(__file__).resolve().parent.parent / "data" / "kg_patches.ttl"]

# 거의 모든 사람이 해당하는 범주. 이것만으로 충족된 사업은 '받을 수 있다'고 해도 질문과 관련이 적으므로 뒤로 보낸다.
GENERIC_LABELS = ("대한민국 국민", "일반인", "지역주민")
# 개인 사용자에게 되묻지 않을 범주의 뿌리 (이 아래 개념 전부)
ORG_ROOT_LABELS = ("법인/시설/단체", "기관/단체", "기업", "중소기업", "중견기업", "대기업", "사업주")


@dataclass
class Understanding:
    text: str
    extraction: Extraction
    links: list[Link]
    profile: Profile
    unresolved: list[str] = field(default_factory=list)   # 개념으로 잇지 못한 표현

    def summary(self, kg: KGIndex) -> list[str]:
        p, lines = self.profile, []
        facts = [f"{p.age}세" if p.age is not None else None, kg.label(p.region) if p.region else None, p.gender,
                 f"중위소득 {p.income_pct:g}%" if p.income_pct is not None else None]
        lines.append("프로필: " + ", ".join(x for x in facts if x) if any(facts) else "프로필: (없음)")
        for ln in self.links:
            sign = "" if ln.positive else "¬"
            got = ", ".join(kg.label(c) for c in ln.concepts) or "—"
            extra = f"  ({ln.note})" if ln.note else ""
            lines.append(f"  {sign}{ln.phrase} → {got}  [{ln.method}]{extra}")
            if ln.rejected:
                lines.append("      (방향 규칙으로 뺌: " + ", ".join(f"{kg.label(c)}={why}" for c, why in ln.rejected) + ")")
        for n in self.extraction.notes:
            lines.append("  · " + n)
        return lines


@dataclass
class Consultation:
    understanding: Understanding
    result: Result
    ranked: list[Judgment]


class Consultant:
    def __init__(self, kg: KGIndex, llm: LLMClient | None, *, link_with_llm: bool = True):
        self.kg = kg
        self.llm = llm
        self.judge = Judge(kg)
        self.extractor = ProfileExtractor(kg, llm) if llm else None
        self.linker = ConceptLinker(kg, llm if link_with_llm else None)
        self.generic = {c for lab in GENERIC_LABELS for c in kg.concepts_for_label(lab)}
        self.org_labels = {kg.label(d) for lab in ORG_ROOT_LABELS for c in kg.concepts_for_label(lab)
                           for d in kg.descendants(c)}

    @classmethod
    def from_paths(cls, kg_path: str | Path = DEFAULT_KG, llm: LLMClient | None = None,
                   patches: list[str | Path] | None = None, **kw) -> "Consultant":
        """patches 를 주지 않으면 DEFAULT_PATCHES (data/kg_patches.ttl) 를 쓴다. [] 이면 원본 KG 만."""
        return cls(load_kg(kg_path, DEFAULT_PATCHES if patches is None else patches), llm or LLMClient(), **kw)

    # ------------------------------------------------------------ ①②
    def understand(self, text: str, today: dt.date | None = None) -> Understanding:
        if self.extractor is None:
            raise RuntimeError("프로필 추출에는 LLM 이 필요하다 (LLM_CACHE_MODE=only 로 캐시 재생 가능)")
        ex = self.extractor.extract(text, today)
        return self.from_extraction(text, ex)

    def from_extraction(self, text: str, ex: Extraction) -> Understanding:
        region_iri = self.kg.resolve_region(ex.region) if ex.region else None
        links = self.linker.derive(ex.age, ex.gender, region_iri, ex.groups)
        links += self.linker.link(ex.groups, ex.not_groups)
        pos = sorted({c for ln in links if ln.positive for c in ln.concepts})
        neg = sorted({c for ln in links if not ln.positive for c in ln.concepts})
        unresolved = [("" if ln.positive else "¬") + ln.phrase for ln in links if not ln.concepts]
        if ex.region and region_iri is None:
            unresolved.append("지역:" + ex.region)
        # 개인이 묻는 경우 가구 단위 사업(ut_가구)도 신청할 수 있으므로 함께 본다
        user_types = ["개인", "가구"] if ex.user_type == "개인" else [ex.user_type]
        profile = Profile(age=ex.age, region=region_iri, income_pct=ex.income_pct, gender=ex.gender,
                          groups=pos, not_groups=neg, receiving=ex.receiving, user_types=user_types)
        return Understanding(text, ex, links, profile, unresolved)

    # ------------------------------------------------------------ ③ + 순위
    def interest_score(self, j: Judgment, ex: Extraction) -> int:
        s, kg, score = j.service, self.kg, 0
        if ex.fields and kg.label(s.field_) in ex.fields:
            score += 1
        if ex.support_types:
            want = {d for t in ex.support_types for c in kg.concepts_for_label(t)
                    if c.startswith(KGC + "st_") for d in kg.descendants(c)}
            if s.support_types & want:
                score += 1
        hay = s.title + " " + s.benefit_text
        score += 2 * sum(1 for k in ex.keywords if k and k in hay)
        return score

    def consult(self, text: str, today: dt.date | None = None, **run_kw) -> Consultation:
        today = today or dt.date.today()
        u = self.understand(text, today)
        return self.consult_understood(u, today, **run_kw)

    def specific(self, j: Judgment) -> int:
        """일반 범주(국민·일반인·지역주민) 말고 사람이 밝힌 구체적 범주로 충족한 범주 조건 수."""
        return sum(1 for c in j.conditions
                   if c.kind in ("requires", "anyOf") and c.status is Status.MET
                   and any(r not in self.generic for r in c.via))

    def skip_labels(self, ex: Extraction) -> set[str]:
        """되묻지 않을 범주: 개인에게는 법인·기관 범주를 묻지 않는다."""
        return self.org_labels if ex.user_type in ("개인", "가구") else set()

    def relevant(self, j: Judgment, ex: Extraction) -> bool:
        """질문과 직접 관련된 사업인가: 키워드가 있으면 키워드가 사업명·지원내용에 있어야, 없으면 분야·지원유형이 맞아야 한다."""
        if ex.keywords:
            hay = j.service.title + " " + j.service.benefit_text
            return any(k and k in hay for k in ex.keywords)
        return bool(ex.fields or ex.support_types) and self.interest_score(j, ex) > 0

    def consult_understood(self, u: Understanding, today: dt.date, **run_kw) -> Consultation:
        """순위: 질문과 관련된 사업 묶음 먼저 → 판정 단계 → 구체적 범주로 충족했는가 → 관심사 점수 → 판정기 순서.

        판정 단계를 먼저 쓰면 '월세' 를 물었는데 무관한 적합 사업(창업 지원 등)이 위로 올라온다.
        '대한민국 국민'·'일반인' 만으로 충족된 사업(견학 프로그램 등)은 같은 단계 안에서 뒤로 보낸다.
        """
        ex = u.extraction
        res = self.judge.run(u.profile, today=today, **run_kw)
        order = {Tier.ELIGIBLE: 0, Tier.NEEDS_INFO: 1, Tier.INELIGIBLE: 2}
        pos = {id(j): i for i, j in enumerate(res.judgments)}
        ranked = sorted(res.judgments, key=lambda j: (
            not self.relevant(j, ex), order[j.tier], self.specific(j) == 0,
            -self.interest_score(j, ex), pos[id(j)]))
        return Consultation(u, res, ranked)
