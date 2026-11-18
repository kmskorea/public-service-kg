"""KG(.ttl)를 한 번 읽어 판정에 필요한 것만 파이썬 자료구조로 올린다.

- 서비스별 조건(requires, OR 묶음, excludes, prefers, 연령, 소득, 성별, 지역, 사용자 유형, 신청기한)
- 개념 라벨 → 개념, 개념의 상위 개념 폐포(skos:broader*), 하위 개념 폐포
- 지역의 상위 지역 폐포(kg:partOf*)

SPARQL의 `broader*`·`partOf*` 경로 탐색을 미리 계산해 두는 것이 속도 개선의 핵심이다.
(기존 Q0·Q1 SPARQL 약 45초 → 판정 전체 수십 ms)
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pyoxigraph as ox

KG = "http://kg.example.org/ontology#"
KGC = "http://kg.example.org/concept/"
KGR = "http://kg.example.org/resource/"
SKOS = "http://www.w3.org/2004/02/skos/core#"
DCT = "http://purl.org/dc/terms/"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"


@dataclass
class Service:
    iri: str
    id: str = ""
    title: str = ""
    tier: str = ""
    region: str = ""                      # kgr:region_… IRI (서비스당 1개)
    user_types: set[str] = field(default_factory=set)
    support_types: set[str] = field(default_factory=set)
    field_: str = ""
    org: str = ""
    requires: set[str] = field(default_factory=set)
    groups: dict[str, set[str]] = field(default_factory=dict)   # 묶음 IRI → anyOf 개념들
    excludes: set[str] = field(default_factory=set)
    prefers: set[str] = field(default_factory=set)
    age_min: int | None = None
    age_max: int | None = None
    income_max_pct: int | None = None     # 이하
    income_min_pct: int | None = None     # 초과
    gender: str | None = None             # "여성" | "남성"
    budget_limited: bool = False
    deadline_kind: str = "UNKNOWN"
    deadline_text: str = ""
    apply_start: dt.date | None = None
    apply_end: dt.date | None = None
    apply_start_month: int | None = None
    apply_end_month: int | None = None
    target_text: str = ""
    benefit_text: str = ""
    detail_url: str = ""

    @property
    def targeted(self) -> bool:
        """대상 범주 조건(requires 또는 OR 묶음)이 있는가. 설계서 5.3 의 '대상이 정해진 사업'."""
        return bool(self.requires or self.groups)


@dataclass
class KGIndex:
    services: dict[str, Service]
    labels: dict[str, str]                    # 개념·지역 IRI → 대표 라벨
    label_to_concepts: dict[str, set[str]]    # prefLabel/altLabel 문자열(정확히) → 개념 IRI
    label_nospace: dict[str, set[str]]        # 공백을 뺀 라벨 → 개념 IRI (정확 일치가 없을 때만 사용)
    parents: dict[str, set[str]]              # skos:broader
    children: dict[str, set[str]]
    region_parent: dict[str, set[str]]        # kg:partOf
    region_labels: dict[str, str]
    recipient_of: dict[str, set[str]]         # 개념 → 서비스 IRI
    _up: dict[str, frozenset[str]] = field(default_factory=dict, repr=False)
    _down: dict[str, frozenset[str]] = field(default_factory=dict, repr=False)

    # ---- 폐포 -------------------------------------------------------------
    @staticmethod
    def _closure(start: str, edges: dict[str, set[str]]) -> frozenset[str]:
        seen = {start}
        stack = [start]
        while stack:                       # 순환이 있어도 멈춘다
            for nxt in edges.get(stack.pop(), ()):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return frozenset(seen)

    def ancestors(self, concept: str) -> frozenset[str]:
        """자기 자신 포함 상위 개념 전부 (skos:broader*)."""
        if concept not in self._up:
            self._up[concept] = self._closure(concept, self.parents)
        return self._up[concept]

    def descendants(self, concept: str) -> frozenset[str]:
        """자기 자신 포함 하위 개념 전부 (^skos:broader*)."""
        if concept not in self._down:
            self._down[concept] = self._closure(concept, self.children)
        return self._down[concept]

    def region_ancestors(self, region: str) -> frozenset[str]:
        """자기 자신 포함 상위 지역 (kg:partOf*)."""
        return self._closure(region, self.region_parent)

    def broader_path(self, start: str, goal: str) -> list[str] | None:
        """start 에서 goal 까지 skos:broader 경로(양 끝 포함). 설명용."""
        prev: dict[str, str | None] = {start: None}
        queue = [start]
        while queue:
            cur = queue.pop(0)
            if cur == goal:
                path = [cur]
                while prev[path[-1]] is not None:
                    path.append(prev[path[-1]])
                return path[::-1]
            for nxt in self.parents.get(cur, ()):
                if nxt not in prev:
                    prev[nxt] = cur
                    queue.append(nxt)
        return None

    def label(self, iri: str) -> str:
        return self.labels.get(iri) or self.region_labels.get(iri) or iri.rsplit("/", 1)[-1]

    # ---- 이름 찾기 ----------------------------------------------------------
    def concepts_for_label(self, text: str) -> set[str]:
        """라벨(prefLabel/altLabel) 정확 일치. 없으면 공백을 무시하고 한 번 더 찾는다.

        정확 일치를 먼저 쓰는 것은 기존 SPARQL(STR(?l) IN (...))과 결과를 같게 하기 위해서다.
        """
        text = text.strip()
        if text.startswith(KGC):
            return {text} if text in self.labels else set()
        hit = self.label_to_concepts.get(text)
        if hit:
            return set(hit)
        return set(self.label_nospace.get(_nospace(text), ()))

    def resolve_region(self, text: str) -> str | None:
        """'중구', '서울 중구', '서울특별시 중구', 'region_서울_중구', 전체 IRI 모두 받는다."""
        if text.startswith(KGR):
            return text if text in self.region_labels else None
        t = text.strip().replace("특별시", "").replace("광역시", "")
        if t.startswith("region_"):
            iri = KGR + t
            return iri if iri in self.region_labels else None
        parts = t.split()
        cand = [KGR + "region_" + "_".join(parts), KGR + "region_서울_" + parts[-1] if parts else ""]
        for iri in cand:
            if iri in self.region_labels:
                return iri
        hits = [iri for iri, lab in self.region_labels.items() if lab == parts[-1]] if parts else []
        return hits[0] if len(hits) == 1 else None


def _nospace(s: str) -> str:
    return "".join(s.split())


def _lit(term) -> object:
    """pyoxigraph 리터럴 → 파이썬 값."""
    dtype = term.datatype.value if term.datatype else ""
    v = term.value
    if dtype.endswith("#integer") or dtype.endswith("#int"):
        return int(v)
    if dtype.endswith("#date"):
        return dt.date.fromisoformat(v)
    if dtype.endswith("#boolean"):
        return v == "true"
    return v


def load_kg(path: str | Path) -> KGIndex:
    services: dict[str, Service] = {}
    labels: dict[str, str] = {}
    label_to_concepts: dict[str, set[str]] = defaultdict(set)
    label_nospace: dict[str, set[str]] = defaultdict(set)
    parents: dict[str, set[str]] = defaultdict(set)
    children: dict[str, set[str]] = defaultdict(set)
    region_parent: dict[str, set[str]] = defaultdict(set)
    region_labels: dict[str, str] = {}
    recipient_of: dict[str, set[str]] = defaultdict(set)
    groups: dict[str, set[str]] = defaultdict(set)
    svc_groups: dict[str, list[str]] = defaultdict(list)
    regions: set[str] = set()
    triples = []

    for t in ox.parse(path=str(path), format=ox.RdfFormat.TURTLE):
        s, p, o = t.subject.value, t.predicate.value, t.object
        triples.append((s, p, o))
        if p == RDF_TYPE:
            if o.value == KG + "Service":
                services[s] = Service(iri=s)
            elif o.value == KG + "Region":
                regions.add(s)

    def svc(s: str) -> Service:
        return services[s]

    for s, p, o in triples:
        if p == SKOS + "prefLabel":
            labels[s] = o.value
            label_to_concepts[o.value].add(s)
            label_nospace[_nospace(o.value)].add(s)
        elif p == SKOS + "altLabel":
            label_to_concepts[o.value].add(s)
            label_nospace[_nospace(o.value)].add(s)
        elif p == SKOS + "broader":
            parents[s].add(o.value)
            children[o.value].add(s)
        elif p == KG + "partOf":
            region_parent[s].add(o.value)
        elif p == RDFS + "label" and s in regions:
            region_labels[s] = o.value
        elif p == KG + "anyOf":
            groups[s].add(o.value)
        elif p == KG + "recipientOf":
            recipient_of[s].add(o.value)
        elif s in services:
            x = svc(s)
            name = p.rsplit("#", 1)[-1] if p.startswith(KG) else p
            if p == DCT + "title":
                x.title = o.value
            elif p == DCT + "identifier":
                x.id = o.value
            elif name == "tier":
                x.tier = o.value
            elif name == "coversRegion":
                x.region = o.value
            elif name == "forUserType":
                x.user_types.add(o.value)
            elif name == "hasSupportType":
                x.support_types.add(o.value)
            elif name == "inField":
                x.field_ = o.value
            elif name == "providedBy":
                x.org = o.value
            elif name == "requires":
                x.requires.add(o.value)
            elif name == "hasRequirementGroup":
                svc_groups[s].append(o.value)
            elif name == "excludes":
                x.excludes.add(o.value)
            elif name == "prefers":
                x.prefers.add(o.value)
            elif name == "ageMin":
                x.age_min = _lit(o)
            elif name == "ageMax":
                x.age_max = _lit(o)
            elif name == "incomeMaxPct":
                x.income_max_pct = _lit(o)
            elif name == "incomeMinPct":
                x.income_min_pct = _lit(o)
            elif name == "gender":
                x.gender = o.value
            elif name == "budgetLimited":
                x.budget_limited = bool(_lit(o))
            elif name == "deadlineKind":
                x.deadline_kind = o.value
            elif name == "deadlineText":
                x.deadline_text = o.value
            elif name == "applyStart":
                x.apply_start = _lit(o)
            elif name == "applyEnd":
                x.apply_end = _lit(o)
            elif name == "applyStartMonth":
                x.apply_start_month = _lit(o)
            elif name == "applyEndMonth":
                x.apply_end_month = _lit(o)
            elif name == "targetText":
                x.target_text = o.value
            elif name == "benefitText":
                x.benefit_text = o.value
            elif name == "detailUrl":
                x.detail_url = o.value

    for s, gs in svc_groups.items():
        services[s].groups = {g: set(groups.get(g, ())) for g in gs}

    return KGIndex(
        services=services,
        labels=labels,
        label_to_concepts=dict(label_to_concepts),
        label_nospace=dict(label_nospace),
        parents=dict(parents),
        children=dict(children),
        region_parent=dict(region_parent),
        region_labels=region_labels,
        recipient_of=dict(recipient_of),
    )
