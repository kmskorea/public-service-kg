"""② 개념 연결: 사용자 표현 → KG 대상 범주 개념.

순서
  1. 규칙   나이·성별·지역에서 바로 나오는 범주 (청년, 노인, 여성, ○○구 거주자 …)
  2. 정확   prefLabel/altLabel 일치 (공백 무시)
  3. 후보   글자 n-gram TF-IDF 유사도 상위 k (+ 선택: 임베딩 유사도 상위 k)
  4. 확인   LLM 이 후보 중 '이 사람이 확실히 속하는' 개념만 고른다 (정확 일치가 있어도 같은 뜻의 개념을 더 찾는다).
           LLM 이 없으면 정확 일치, 없으면 유사도 기준값 이상 1개만 채택.

방향에 주의: 사람이 X 이면 X 의 상위 개념에도 속하지만(판정기가 처리) 하위 개념은 아니다.
'X 가 아니다'이면 반대로 X 와 하위 개념이 아니다. LLM 확인 프롬프트가 이 방향을 지키게 한다.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from .extract import concept_usage, is_target_group
from .kg_index import KGIndex, _nospace
from .llm import LLMClient


@dataclass
class Link:
    phrase: str
    positive: bool                      # True: 해당한다 / False: 해당하지 않는다
    concepts: list[str]                 # 연결된 개념 IRI
    method: str                         # rule | exact | llm | ngram | none
    candidates: list[tuple[str, float]] = field(default_factory=list)
    note: str = ""


# ---------------------------------------------------------------------- n-gram 색인
def _grams(text: str) -> dict[str, float]:
    t = "^" + _nospace(text) + "$"
    out: dict[str, float] = defaultdict(float)
    for n in (2, 3):
        for i in range(len(t) - n + 1):
            out[t[i:i + n]] += 1.0
    return out


class NgramIndex:
    """라벨 글자 2·3-gram TF-IDF 코사인 유사도. 의존성 없이 CPU 에서 수 ms."""

    def __init__(self, labels: list[tuple[str, str]]):        # (라벨, 개념 IRI)
        self.items = labels
        df: dict[str, int] = defaultdict(int)
        grams = [_grams(lab) for lab, _ in labels]
        for g in grams:
            for k in g:
                df[k] += 1
        n = len(labels)
        self.idf = {k: math.log((n + 1) / (v + 1)) + 1 for k, v in df.items()}
        self.post: dict[str, list[tuple[int, float]]] = defaultdict(list)
        self.norm = []
        for i, g in enumerate(grams):
            vec = {k: v * self.idf[k] for k, v in g.items()}
            self.norm.append(math.sqrt(sum(x * x for x in vec.values())) or 1.0)
            for k, w in vec.items():
                self.post[k].append((i, w))

    def search(self, text: str, k: int = 8) -> list[tuple[str, float, str]]:
        """반환: (개념 IRI, 점수, 맞은 라벨). 개념당 가장 높은 라벨 점수."""
        q = {g: v * self.idf.get(g, 0.0) for g, v in _grams(text).items()}
        qn = math.sqrt(sum(x * x for x in q.values())) or 1.0
        acc: dict[int, float] = defaultdict(float)
        for g, w in q.items():
            for i, dw in self.post.get(g, ()):
                acc[i] += w * dw
        best: dict[str, tuple[float, str]] = {}
        for i, dot in acc.items():
            lab, c = self.items[i]
            sc = dot / (qn * self.norm[i])
            if c not in best or sc > best[c][0]:
                best[c] = (sc, lab)
        top = sorted(best.items(), key=lambda x: -x[1][0])[:k]
        return [(c, round(sc, 3), lab) for c, (sc, lab) in top]


class EmbeddingIndex:
    """선택: OpenAI 호환 /embeddings (예: vLLM 으로 띄운 bge-m3). numpy 필요."""

    def __init__(self, labels: list[tuple[str, str]], llm: LLMClient, model: str | None = None, batch: int = 128):
        import numpy as np
        self.np, self.items, self.llm, self.model = np, labels, llm, model
        vecs = []
        for i in range(0, len(labels), batch):
            vecs += llm.embed([lab for lab, _ in labels[i:i + batch]], model=model)
        m = np.asarray(vecs, dtype="float32")
        self.mat = m / (np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)

    def search(self, text: str, k: int = 8) -> list[tuple[str, float, str]]:
        np = self.np
        q = np.asarray(self.llm.embed([text], model=self.model)[0], dtype="float32")
        sims = self.mat @ (q / (np.linalg.norm(q) + 1e-9))
        best: dict[str, tuple[float, str]] = {}
        for i in np.argsort(-sims)[: k * 3]:
            lab, c = self.items[i]
            if c not in best:
                best[c] = (float(sims[i]), lab)
        top = sorted(best.items(), key=lambda x: -x[1][0])[:k]
        return [(c, round(sc, 3), lab) for c, (sc, lab) in top]


# ---------------------------------------------------------------------- LLM 확인
VERIFY_POS = """사용자에 대해 알려진 사실: "{phrase}" 에 해당한다.
아래는 공공서비스 지식그래프의 대상 범주 후보다. 괄호 안은 그 범주의 상위 범주다.
{cands}

이 사실만으로 이 사용자가 **확실히 속한다**고 할 수 있는 범주의 번호를 모두 고르라.
- 같은 뜻이거나 더 넓은 범주는 고른다. (예: "생계급여 받음" → 기초생활수급자 O)
- 더 좁거나 추가 조건이 붙은 범주는 고르지 않는다. (예: "장애인" → 중증장애인 X, 장애인 예비창업자 X)
- 관련만 있고 같은 대상이 아니면 고르지 않는다. 해당하는 것이 없으면 빈 목록.
JSON 으로만 답하라: {{"selected": [번호, ...]}}"""

VERIFY_NEG = """사용자에 대해 알려진 사실: "{phrase}" 에 해당하지 **않는다**.
아래는 공공서비스 지식그래프의 대상 범주 후보다. 괄호 안은 그 범주의 상위 범주다.
{cands}

이 사실만으로 이 사용자가 **확실히 속하지 않는다**고 할 수 있는 범주의 번호를 모두 고르라.
- 같은 뜻이거나 더 좁은 범주는 고른다. (예: "장애 없음" → 장애인 O, 중증장애인 O)
- 더 넓은 범주는 고르지 않는다. (예: "중증장애 아님" → 장애인 X)
해당하는 것이 없으면 빈 목록. JSON 으로만 답하라: {{"selected": [번호, ...]}}"""

VERIFY_SCHEMA = {"type": "object", "properties": {"selected": {"type": "array", "items": {"type": "integer"}}},
                 "required": ["selected"]}


def _check_selected(obj: dict) -> dict:
    sel = obj.get("selected")
    if not isinstance(sel, list) or not all(isinstance(x, int) or str(x).isdigit() for x in sel):
        raise TypeError("selected 가 정수 목록이 아님")
    return {"selected": [int(x) for x in sel]}


# ---------------------------------------------------------------------- 규칙
# (라벨, 조건, 근거). 서비스마다 정의가 다르지만, 연령 수치 조건은 판정기가 따로 검사하므로 일반 정의를 쓴다.
AGE_RULES = [
    ("영유아", lambda a: a < 7, "영유아보육법: 7세 미만"),
    ("아동", lambda a: a < 18, "아동복지법: 18세 미만"),
    # 청소년(청소년기본법 9~24세)은 넣지 않는다: 서비스의 '청소년'은 대개 10대라 22세를 넣으면 오판이 커진다.
    ("청년", lambda a: 19 <= a <= 34, "청년기본법: 19~34세"),
    ("노인", lambda a: a >= 65, "노인복지법: 65세 이상"),
    ("고령자", lambda a: a >= 65, "65세 이상"),
    ("경로우대자", lambda a: a >= 65, "65세 이상"),
]
ASSUMED_FOR_CITIZEN = ["대한민국 국민", "일반인"]


class ConceptLinker:
    def __init__(self, kg: KGIndex, llm: LLMClient | None = None, embed: EmbeddingIndex | None = None,
                 k: int = 8, accept: float = 0.8):
        self.kg, self.llm, self.embed, self.k, self.accept = kg, llm, embed, k, accept
        labels = [(lab, c) for lab, cs in kg.label_to_concepts.items() for c in cs if is_target_group(c)]
        self.labels = sorted(set(labels))
        self.ngram = NgramIndex(self.labels)
        self.usage = concept_usage(kg)

    # ---------------- 1. 규칙
    def derive(self, age: int | None, gender: str | None, region_iri: str | None,
               groups_text: list[str]) -> list[Link]:
        kg, out = self.kg, []

        def add(label: str, note: str):
            cs = {c for c in kg.concepts_for_label(label) if is_target_group(c)}
            if cs:
                out.append(Link(label, True, sorted(cs), "rule", note=note))

        if age is not None:
            for lab, cond, why in AGE_RULES:
                if cond(age):
                    add(lab, f"{age}세 → {why}")
        if gender:
            add(gender, "성별")
        if region_iri:
            for r in sorted(kg.region_ancestors(region_iri)):
                name = kg.label(r)
                if name not in ("전국", "서울특별시"):
                    add(f"{name} 거주자", "거주지")
            add("지역주민", "거주지")
        if not any("외국" in g for g in groups_text):
            for lab in ASSUMED_FOR_CITIZEN:
                add(lab, "외국인이라고 하지 않으면 국민으로 봄")
        return out

    # ---------------- 2~4
    def candidates(self, phrase: str) -> list[tuple[str, float, str]]:
        cands = self.ngram.search(phrase, self.k)
        if self.embed is not None:
            seen = {c for c, _, _ in cands}
            cands += [x for x in self.embed.search(phrase, self.k) if x[0] not in seen]
        return cands

    def _describe(self, c: str) -> str:
        ups = [self.kg.label(p) for p in sorted(self.kg.parents.get(c, ()))]
        return self.kg.label(c) + (f" ({', '.join(ups)})" if ups else "")

    def link_one(self, phrase: str, positive: bool = True) -> Link:
        exact = sorted(c for c in self.kg.concepts_for_label(phrase) if is_target_group(c))
        cands = [x for x in self.candidates(phrase) if x[0] not in exact]
        pairs = [(c, sc) for c, sc, _ in cands]
        if self.llm is None:
            if exact:
                return Link(phrase, positive, exact, "exact")
            if cands and cands[0][1] >= self.accept:
                c, sc, lab = cands[0]
                return Link(phrase, positive, [c], "ngram", pairs, f"'{lab}' 유사도 {sc}")
            note = f"최고 유사도 {cands[0][1]} < {self.accept}" if cands else ""
            return Link(phrase, positive, [], "none", pairs, note)
        # KG 에는 같은 뜻의 개념이 따로 있는 경우가 많다 (한부모 / 한부모가족 / 한부모가정).
        # 정확 일치가 있어도 유사 후보 중 같은 대상을 LLM 이 더 고르게 한다.
        chosen: list[str] = []
        if cands:
            listing = "\n".join(f"{i + 1}. {self._describe(c)}" for i, (c, _, _) in enumerate(cands))
            if exact:
                listing = f"(이미 연결됨: {', '.join(self._describe(c) for c in exact)})\n" + listing
            prompt = (VERIFY_POS if positive else VERIFY_NEG).format(phrase=phrase, cands=listing)
            obj, _ = self.llm.chat_json([{"role": "user", "content": prompt}], VERIFY_SCHEMA,
                                        validate=_check_selected, max_tokens=128)
            chosen = [cands[i - 1][0] for i in obj["selected"] if 1 <= i <= len(cands)]
        concepts = sorted(set(exact) | set(chosen))
        method = ("exact+llm" if chosen else "exact") if exact else ("llm" if chosen else "none")
        return Link(phrase, positive, concepts, method, pairs)

    def link(self, groups: list[str], not_groups: list[str]) -> list[Link]:
        return [self.link_one(g, True) for g in groups] + [self.link_one(g, False) for g in not_groups]
