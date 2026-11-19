"""KG 기반 자격 판정기.

사람의 프로필(나이·지역·소득·성별·해당 범주)을 받아 서비스마다 조건을 하나씩 따져
적합 / 확인 필요 / 부적합 으로 나누고, 각 판정의 근거(어떤 조건이 어떤 개념 경로로 충족됐는지)를 남긴다.

판정 규칙 (설계서 자격 판정 규칙 1~6, queries/q1 과 같음)
  1. requires 개념은 모두 충족해야 한다.
  2. OR 묶음(hasRequirementGroup)은 묶음마다 anyOf 중 하나 이상 충족해야 한다.
  3. excludes 개념에 하나라도 해당하면 부적합.
  4. prefers 는 자격에 영향 없이 순위만 올린다.
  5. 연령·소득·성별 수치 조건.
  6. 사는 지역이 서비스 지역 안이어야 한다 (kg:partOf*).
  사람이 개념 X 에 해당하면 X 의 상위 개념(skos:broader*)에도 해당한다고 본다.

두 가지 모드
  - open  (기본): 사람이 밝히지 않은 조건은 '모름'으로 두고 '확인 필요'로 분류한다. 대화형 상담용.
  - closed: 기존 SPARQL 질의와 같은 닫힌 세계 판정. 밝히지 않은 범주 조건은 불충족,
            밝히지 않은 수치 조건(나이·소득·성별)은 검사하지 않는다. 회귀 검증용.
"""
from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, field
from enum import Enum

from .kg_index import KGC, KGR, KGIndex, Service


class Status(str, Enum):
    MET = "충족"
    UNMET = "불충족"
    UNKNOWN = "모름"


class Tier(str, Enum):
    ELIGIBLE = "적합"
    NEEDS_INFO = "확인 필요"
    INELIGIBLE = "부적합"


class Apply(str, Enum):
    OPEN = "지금 신청 가능"
    UPCOMING = "신청 기간 전"
    CLOSED = "신청 기간 아님"
    NO_APPLICATION = "신청 불필요"
    UNKNOWN = "기간 확인 필요"


@dataclass
class Profile:
    age: int | None = None
    region: str | None = None             # '중구', '서울 중구', 'region_서울_중구', IRI
    income_pct: float | None = None       # 기준중위소득 %
    gender: str | None = None             # '여성' | '남성'
    groups: list[str] = field(default_factory=list)       # 해당하는 범주 (라벨 또는 개념 IRI)
    not_groups: list[str] = field(default_factory=list)   # 해당하지 않는다고 확인된 범주
    receiving: list[str] = field(default_factory=list)    # 이미 받고 있는 서비스 (ID 또는 제목) → recipientOf
    user_types: list[str] = field(default_factory=lambda: ["개인"])  # 개인 / 가구 / 소상공인 / 법인_시설_단체


@dataclass
class Condition:
    kind: str            # requires | anyOf | excludes | age | income | gender | region | user_type
    status: Status
    text: str            # 사람이 읽을 조건 설명
    evidence: str = ""   # 충족·불충족 근거 (개념 경로 등)
    via: tuple[str, ...] = ()       # 범주 조건을 충족시킨, 사람이 밝힌 개념들
    matched: tuple[str, ...] = ()   # 충족된 서비스 쪽 개념 (requires 개념, OR 묶음에서 충족된 개념) — 일반 범주 판단에 씀
    pending: tuple[str, ...] = ()   # 아직 모르는 개념 라벨 (OR 묶음에서 이미 '아님'인 것은 뺀다) — 되묻기에 씀


@dataclass
class Judgment:
    service: Service
    tier: Tier
    conditions: list[Condition]
    preferred: list[str]         # 충족한 우대(prefers) 범주
    caveats: list[str]           # 해당 여부를 모르는 제외(excludes) 범주
    apply: Apply | None = None

    @property
    def unknowns(self) -> list[Condition]:
        return [c for c in self.conditions if c.status is Status.UNKNOWN]

    @property
    def matched(self) -> int:
        """사람이 밝힌 범주로 충족한 범주 조건(requires·anyOf) 수. 관련도 순위에 쓴다."""
        return sum(1 for c in self.conditions if c.kind in ("requires", "anyOf") and c.status is Status.MET)

    @property
    def unmet(self) -> list[Condition]:
        return [c for c in self.conditions if c.status is Status.UNMET]


@dataclass
class PreparedProfile:
    profile: Profile
    have: frozenset[str]                  # 해당하는 개념 + 그 상위 개념 전부
    have_roots: dict[str, set[str]]       # 사람이 직접 밝힌 개념 → (설명용)
    deny: frozenset[str]                  # 해당하지 않는 개념 + 그 하위 개념 전부
    region: str | None
    region_up: frozenset[str]
    user_types: set[str]
    unresolved: list[str]                 # KG에서 찾지 못한 표현 (개념 연결 단계로 넘길 것)


@dataclass
class Result:
    judgments: list[Judgment]
    prepared: PreparedProfile
    seconds: float

    def by_tier(self, tier: Tier) -> list[Judgment]:
        return [j for j in self.judgments if j.tier is tier]


class Judge:
    def __init__(self, kg: KGIndex):
        self.kg = kg
        self._title_to_svc: dict[str, set[str]] = {}
        for s in kg.services.values():
            self._title_to_svc.setdefault(s.title, set()).add(s.iri)
            self._title_to_svc.setdefault(s.id, set()).add(s.iri)

    # ------------------------------------------------------------------ 준비
    def prepare(self, p: Profile) -> PreparedProfile:
        kg = self.kg
        unresolved: list[str] = []
        roots: set[str] = set()
        for g in p.groups:
            hits = kg.concepts_for_label(g)
            if not hits:
                unresolved.append(g)
            roots |= hits
        # 이미 받는 서비스 → 그 서비스 수혜자 범주 (kg:recipientOf)
        recv = set()
        for r in p.receiving:
            recv |= self._title_to_svc.get(r.strip(), set())
        for concept, svcs in kg.recipient_of.items():
            if svcs & recv:
                roots.add(concept)

        have: set[str] = set()
        have_roots: dict[str, set[str]] = {}
        for c in roots:
            for a in kg.ancestors(c):
                have.add(a)
                have_roots.setdefault(a, set()).add(c)

        deny: set[str] = set()
        for g in p.not_groups:
            hits = kg.concepts_for_label(g)
            if not hits:
                unresolved.append("¬" + g)
            for c in hits:
                deny |= kg.descendants(c)
        deny -= have       # 모순이면 '해당한다'를 따른다

        region = kg.resolve_region(p.region) if p.region else None
        if p.region and region is None:
            unresolved.append("지역:" + p.region)
        user_types = {t if t.startswith(KGC) else KGC + "ut_" + t.replace("/", "_") for t in p.user_types}
        return PreparedProfile(
            profile=p, have=frozenset(have), have_roots=have_roots, deny=frozenset(deny),
            region=region, region_up=kg.region_ancestors(region) if region else frozenset(),
            user_types=user_types, unresolved=unresolved,
        )

    # ------------------------------------------------------------------ 개념 조건
    def _concept_status(self, c: str, pp: PreparedProfile, closed: bool) -> Status:
        if c in pp.have:
            return Status.MET
        if c in pp.deny or closed:
            return Status.UNMET
        return Status.UNKNOWN

    def _why(self, c: str, pp: PreparedProfile) -> str:
        """개념 c 를 충족한 이유: 사람이 밝힌 범주에서 c 까지의 상위 개념 경로."""
        kg = self.kg
        best = None
        for root in sorted(pp.have_roots.get(c, ())):
            path = kg.broader_path(root, c)
            if path and (best is None or len(path) < len(best)):
                best = path
        if not best:
            return ""
        if len(best) == 1:
            return f"'{kg.label(best[0])}'에 해당"
        return " ⊂ ".join(kg.label(x) for x in best) + " (상위 개념)"

    # ------------------------------------------------------------------ 판정
    def judge_service(self, s: Service, pp: PreparedProfile, closed: bool = False,
                      today: dt.date | None = None) -> Judgment:
        kg, p = self.kg, pp.profile
        conds: list[Condition] = []

        # 사용자 유형
        ok = bool(s.user_types & pp.user_types)
        conds.append(Condition("user_type", Status.MET if ok else Status.UNMET,
                               "대상 유형: " + ", ".join(sorted(kg.label(u) for u in s.user_types))))

        # 규칙 6: 지역
        reg_text = f"지역: {kg.label(s.region)}"
        if pp.region is None:
            st = Status.MET if not kg.region_parent.get(s.region) else (Status.UNMET if closed else Status.UNKNOWN)
            conds.append(Condition("region", st, reg_text, "전국 사업" if st is Status.MET else ""))
        elif s.region in pp.region_up:
            conds.append(Condition("region", Status.MET, reg_text, f"{kg.label(pp.region)} 거주"))
        elif pp.region in kg.region_ancestors(s.region) and not closed:
            conds.append(Condition("region", Status.UNKNOWN, reg_text, "세부 거주지(구) 확인 필요"))
        else:
            conds.append(Condition("region", Status.UNMET, reg_text, f"{kg.label(pp.region)} 거주"))

        # 규칙 5: 연령
        if s.age_min is not None or s.age_max is not None:
            lo = f"{s.age_min}세 이상" if s.age_min is not None else ""
            hi = f"{s.age_max}세 이하" if s.age_max is not None else ""
            text = "연령: " + " ".join(x for x in (lo, hi) if x)
            if p.age is None:
                st = Status.MET if closed else Status.UNKNOWN
                ev = ""
            else:
                bad = (s.age_min is not None and p.age < s.age_min) or (s.age_max is not None and p.age > s.age_max)
                st, ev = (Status.UNMET if bad else Status.MET), f"{p.age}세"
            conds.append(Condition("age", st, text, ev))

        # 규칙 5: 소득 (incomeMaxPct 이하, incomeMinPct 초과)
        if s.income_max_pct is not None or s.income_min_pct is not None:
            parts = []
            if s.income_min_pct is not None:
                parts.append(f"{s.income_min_pct}% 초과")
            if s.income_max_pct is not None:
                parts.append(f"{s.income_max_pct}% 이하")
            text = "기준중위소득 " + " ".join(parts)
            if p.income_pct is None:
                st, ev = (Status.MET if closed else Status.UNKNOWN), ""
            else:
                bad = (s.income_max_pct is not None and s.income_max_pct < p.income_pct) or \
                      (s.income_min_pct is not None and s.income_min_pct >= p.income_pct)
                st, ev = (Status.UNMET if bad else Status.MET), f"중위소득 {p.income_pct:g}%"
            conds.append(Condition("income", st, text, ev))

        # 규칙 5: 성별
        if s.gender:
            if p.gender is None:
                st, ev = (Status.MET if closed else Status.UNKNOWN), ""
            else:
                st, ev = (Status.MET if p.gender == s.gender else Status.UNMET), p.gender
            conds.append(Condition("gender", st, f"성별: {s.gender}", ev))

        # 규칙 1: requires 모두
        for c in sorted(s.requires):
            st = self._concept_status(c, pp, closed)
            ev = self._why(c, pp) if st is Status.MET else ("해당하지 않음" if c in pp.deny else "")
            via = tuple(sorted(pp.have_roots.get(c, ()))) if st is Status.MET else ()
            conds.append(Condition("requires", st, f"필수: {kg.label(c)}", ev, via,
                                   matched=(c,) if st is Status.MET else (),
                                   pending=(kg.label(c),) if st is Status.UNKNOWN else ()))

        # 규칙 2: 묶음마다 하나 이상
        for g in sorted(s.groups):
            members = sorted(s.groups[g])
            sts = {c: self._concept_status(c, pp, closed) for c in members}
            text = "하나 이상: " + " | ".join(kg.label(c) for c in members)
            met = [c for c in members if sts[c] is Status.MET]
            if met:
                via = tuple(sorted({r for c in met for r in pp.have_roots.get(c, ())}))
                conds.append(Condition("anyOf", Status.MET, text, self._why(met[0], pp), via, matched=tuple(met)))
            elif all(v is Status.UNMET for v in sts.values()):
                conds.append(Condition("anyOf", Status.UNMET, text, "모두 해당하지 않음"))
            else:
                pend = tuple(kg.label(c) for c in members if sts[c] is Status.UNKNOWN)
                conds.append(Condition("anyOf", Status.UNKNOWN, text, pending=pend))

        # 규칙 3: excludes 하나라도 해당하면 부적합. 모르는 것은 주의 사항으로만 남긴다.
        caveats = []
        for c in sorted(s.excludes):
            if c in pp.have:
                conds.append(Condition("excludes", Status.UNMET, f"제외: {kg.label(c)}", self._why(c, pp)))
            elif not closed and c not in pp.deny:
                caveats.append(kg.label(c))

        # 규칙 4: 우대
        preferred = sorted(kg.label(c) for c in s.prefers if c in pp.have)

        sts = {c.status for c in conds}
        tier = Tier.INELIGIBLE if Status.UNMET in sts else Tier.NEEDS_INFO if Status.UNKNOWN in sts else Tier.ELIGIBLE
        return Judgment(s, tier, conds, preferred, caveats, apply_status(s, today) if today else None)

    def run(self, profile: Profile, *, mode: str = "open", targeted_only: bool = False,
            include_ineligible: bool = False, today: dt.date | None = None) -> Result:
        t = time.perf_counter()
        closed = mode == "closed"
        pp = self.prepare(profile)
        out = []
        for s in self.kg.services.values():
            if targeted_only and not s.targeted:
                continue
            j = self.judge_service(s, pp, closed=closed, today=today)
            if j.tier is Tier.INELIGIBLE and not include_ineligible:
                continue
            out.append(j)
        order = {Tier.ELIGIBLE: 0, Tier.NEEDS_INFO: 1, Tier.INELIGIBLE: 2}
        out.sort(key=lambda j: (
            order[j.tier],
            len(j.unknowns),
            -j.matched,                                             # 밝힌 범주로 충족한 조건이 많은 사업 먼저
            -len(j.preferred),
            not j.service.targeted,                                 # 대상이 정해진 사업 먼저
            -len(self.kg.region_ancestors(j.service.region)),     # 가까운 지역 사업 먼저
            j.service.title,
            j.service.id,                                           # 제목이 같은 사업끼리도 결정적으로
        ))
        return Result(out, pp, time.perf_counter() - t)


def apply_status(s: Service, today: dt.date) -> Apply:
    """신청기한 판정 (queries/q5 와 같은 규칙)."""
    k = s.deadline_kind
    if k == "ALWAYS":
        return Apply.OPEN
    if k == "NO_APPLICATION":
        return Apply.NO_APPLICATION
    if k in ("RANGE", "FROM", "UNTIL"):
        if s.apply_start and s.apply_start > today:
            return Apply.UPCOMING
        if s.apply_end and s.apply_end < today:
            return Apply.CLOSED
        return Apply.OPEN
    if k == "ANNUAL" and s.apply_end_month is not None:
        m, sm, em = today.month, s.apply_start_month, s.apply_end_month
        if sm is None:
            ok = m <= em
        elif sm <= em:
            ok = sm <= m <= em
        else:
            ok = m >= sm or m <= em
        return Apply.OPEN if ok else Apply.CLOSED
    return Apply.UNKNOWN


def missing_info(result: Result, top: int = 10, judgments: list[Judgment] | None = None,
                 skip: set[str] | None = None) -> list[tuple[str, int]]:
    """'확인 필요' 서비스들이 어떤 정보를 몰라서 판정을 못 했는지 센다.

    많이 나온 것부터 물어보면 후보가 가장 많이 정리된다 (되묻기 단계의 기초).
    범주 조건은 개념 라벨, 수치 조건은 '나이'·'소득'·'성별'·'거주 구'로 센다.
    judgments 를 주면 그 안에서만 센다 (예: 질문과 관련된 사업만).
    skip 에 있는 범주 라벨은 묻지 않는다 (예: 개인에게 법인·기관 범주). OR 묶음은 모두 skip 일 때만 뺀다.
    """
    from collections import Counter

    names = {"age": "나이", "income": "소득(기준중위소득 %)", "gender": "성별", "region": "거주 구"}
    cnt: Counter[str] = Counter()
    pool = result.judgments if judgments is None else judgments
    for j in (x for x in pool if x.tier is Tier.NEEDS_INFO):
        seen = set()
        for c in j.unknowns:
            if c.kind in names:
                seen.add(names[c.kind])
            elif c.kind in ("requires", "anyOf"):
                parts = list(c.pending) or c.text.split(": ", 1)[1].split(" | ")
                if skip and all(x in skip for x in parts):
                    continue
                seen.add(" | ".join(parts))
        cnt.update(sorted(seen))
    # 동점은 이름순: Counter 의 삽입 순서(=set 순회 순서)는 해시 시드에 따라 바뀌므로 쓰지 않는다
    return sorted(cnt.items(), key=lambda x: (-x[1], x[0]))[:top]
