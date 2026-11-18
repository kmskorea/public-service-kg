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
from .judge import Judge, Judgment, Profile, Result, Tier
from .kg_index import KGC, KGIndex, load_kg
from .linker import ConceptLinker, Link
from .llm import LLMClient

DEFAULT_KG = Path(__file__).resolve().parent.parent.parent / "public_service_kg_final_20261011" / "output" / "kg.ttl"


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

    @classmethod
    def from_paths(cls, kg_path: str | Path = DEFAULT_KG, llm: LLMClient | None = None, **kw) -> "Consultant":
        return cls(load_kg(kg_path), llm or LLMClient(), **kw)

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

    def relevant(self, j: Judgment, ex: Extraction) -> bool:
        """질문과 직접 관련된 사업인가: 키워드가 있으면 키워드가 맞아야, 없으면 분야·지원유형이 맞아야 한다."""
        score = self.interest_score(j, ex)
        if ex.keywords:
            return score >= 2
        return bool(ex.fields or ex.support_types) and score > 0

    def consult_understood(self, u: Understanding, today: dt.date, **run_kw) -> Consultation:
        """순위: 질문과 관련된 사업 묶음 먼저 → 그 안에서 판정 단계 → 관심사 점수 → 판정기 순서.

        판정 단계를 먼저 쓰면 '월세' 를 물었는데 무관한 적합 사업(창업 지원 등)이 위로 올라온다.
        """
        ex = u.extraction
        res = self.judge.run(u.profile, today=today, **run_kw)
        order = {Tier.ELIGIBLE: 0, Tier.NEEDS_INFO: 1, Tier.INELIGIBLE: 2}
        pos = {id(j): i for i, j in enumerate(res.judgments)}
        ranked = sorted(res.judgments, key=lambda j: (
            not self.relevant(j, ex), order[j.tier], -self.interest_score(j, ex), pos[id(j)]))
        return Consultation(u, res, ranked)
