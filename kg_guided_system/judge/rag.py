"""B1: 텍스트 RAG 비교 시스템.

KG 의 구조(조건 관계·상위 개념·수치 조건·지역 계층)는 쓰지 않고, KG 가 보존한 원문 텍스트만 쓴다
(사업명, 지원대상 targetText, 지원내용 benefitText, 신청기한 deadlineText, 기관·지역 이름).

  검색   BM25 (단어 + 글자 2-gram) 로 상위 k 건
  판정   LLM 이 원문을 읽고 서비스마다 적합 / 확인 필요 / 부적합 (batch 건씩 한 번에)
  답변   KG 시스템과 같은 답변 생성기·근거 검사 (근거는 원문 텍스트)

LLM 호출 수는 판정 k/batch 회 + 답변 1회 (기본 20/5 + 1 = 5회)로 KG 시스템과 비슷하게 맞췄다.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from .answer import Answer, AnswerGenerator, Fact
from .kg_index import KGIndex, Service
from .llm import LLMClient

TIERS = ("적합", "확인 필요", "부적합")


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[가-힣A-Za-z0-9]+", text or "")
    grams = []
    for w in words:
        grams += [w[i:i + 2] for i in range(len(w) - 1)] if len(w) > 1 else [w]
    return [w.lower() for w in words] + grams


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(d) for d in docs]
        self.len = [len(d) for d in docs]
        self.avg = sum(self.len) / max(len(docs), 1)
        df: Counter = Counter()
        for t in self.tf:
            df.update(t.keys())
        n = len(docs)
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}
        self.post: dict[str, list[int]] = defaultdict(list)
        for i, t in enumerate(self.tf):
            for w in t:
                self.post[w].append(i)

    def search(self, query: list[str], k: int) -> list[tuple[int, float]]:
        score: dict[int, float] = defaultdict(float)
        for w in set(query):
            idf = self.idf.get(w)
            if idf is None:
                continue
            for i in self.post[w]:
                f = self.tf[i][w]
                score[i] += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
        return sorted(score.items(), key=lambda x: -x[1])[:k]


def doc_text(kg: KGIndex, s: Service) -> str:
    return (f"사업명: {s.title}\n기관: {kg.label(s.org)} / 지역: {kg.label(s.region)}\n"
            f"지원대상: {s.target_text}\n지원내용: {s.benefit_text}\n신청기한: {s.deadline_text}")


def _clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


JUDGE_PROMPT = """사용자의 상황과 공공서비스 원문을 보고, 서비스마다 사용자가 받을 수 있는지 판정하라.
- 적합: 원문의 지원대상 조건을 사용자가 모두 충족한다고 볼 수 있음
- 확인 필요: 충족 여부를 판단할 정보가 사용자 말에 없음 (무엇이 필요한지 reason 에)
- 부적합: 사용자 말과 맞지 않는 조건이 있음 (지역, 나이, 대상 등)

[사용자]
{question}

[서비스]
{docs}

JSON 으로만 답하라: {{"results": [{{"id": "D1", "verdict": "적합|확인 필요|부적합", "reason": "짧게"}}, ...]}}"""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"results": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "string"}, "verdict": {"enum": list(TIERS)}, "reason": {"type": "string"}},
        "required": ["id", "verdict", "reason"]}}},
    "required": ["results"],
}


def _check_judge(obj: dict) -> dict:
    rs = obj.get("results")
    if not isinstance(rs, list) or not all(isinstance(r, dict) and "id" in r and "verdict" in r for r in rs):
        raise TypeError("results 형식이 아님")
    return obj


@dataclass
class RagJudgment:
    service: Service
    tier: str            # 적합 | 확인 필요 | 부적합 | 판정 없음
    reason: str
    score: float         # BM25


@dataclass
class RagResult:
    retrieved: list[RagJudgment]
    ranked: list[RagJudgment]      # 적합 → 확인 필요 순, 각 안에서는 검색 순
    answer: Answer | None = None


class TextRAG:
    def __init__(self, kg: KGIndex, llm: LLMClient | None, k: int = 20, batch: int = 5):
        self.kg, self.llm, self.k, self.batch = kg, llm, k, batch
        self.services = list(kg.services.values())
        self.docs = [doc_text(kg, s) for s in self.services]
        # 사업명은 doc_text 에도 있으므로 앞에 한 번 더 붙여 가중치를 준다
        self.bm25 = BM25([_tokens(s.title + " " + d) for s, d in zip(self.services, self.docs)])

    def retrieve(self, question: str, k: int | None = None) -> list[tuple[Service, float]]:
        return [(self.services[i], sc) for i, sc in self.bm25.search(_tokens(question), k or self.k)]

    def judge(self, question: str, hits: list[tuple[Service, float]]) -> list[RagJudgment]:
        out = []
        for start in range(0, len(hits), self.batch):
            chunk = hits[start:start + self.batch]
            docs = "\n\n".join(f"[D{i + 1}]\n{_clip(doc_text(self.kg, s), 700)}" for i, (s, _) in enumerate(chunk))
            obj, _ = self.llm.chat_json(
                [{"role": "user", "content": JUDGE_PROMPT.format(question=question, docs=docs)}],
                JUDGE_SCHEMA, validate=_check_judge, max_tokens=150 * len(chunk) + 100)
            got = {}
            for r in obj["results"]:
                m = re.search(r"\d+", str(r.get("id", "")))
                if m and r.get("verdict") in TIERS:
                    got[int(m.group())] = (r["verdict"], str(r.get("reason", "")))
            for i, (s, sc) in enumerate(chunk, 1):
                tier, reason = got.get(i, ("판정 없음", "모델이 이 문서를 판정하지 않음"))
                out.append(RagJudgment(s, tier, reason, sc))
        return out

    def facts(self, ranked: list[RagJudgment], n_eligible: int = 5, n_needs: int = 5) -> list[Fact]:
        el = [j for j in ranked if j.tier == "적합"][:n_eligible]
        ni = [j for j in ranked if j.tier == "확인 필요"][:n_needs]
        facts = []
        for i, j in enumerate(el + ni, 1):
            s = j.service
            lines = [f"지원대상(원문): {_clip(s.target_text, 300)}",
                     f"지원내용(원문): {_clip(s.benefit_text, 220)}",
                     f"신청기한(원문): {_clip(s.deadline_text, 80)}",
                     f"기관: {self.kg.label(s.org)} / 지역: {self.kg.label(s.region)}"]
            if j.tier == "확인 필요" and j.reason:
                lines.append(f"조건 모름: {j.reason}")
            facts.append(Fact(f"S{i}", s.id, s.title, j.tier, lines, s.detail_url))
        return facts

    def run(self, question: str, with_answer: bool = True) -> RagResult:
        if self.llm is None:
            raise RuntimeError("텍스트 RAG 의 판정에는 LLM 이 필요하다")
        hits = self.retrieve(question)
        judged = self.judge(question, hits)
        order = {"적합": 0, "확인 필요": 1}
        ranked = sorted((j for j in judged if j.tier in order), key=lambda j: order[j.tier])   # 안정 정렬: 검색 순 유지
        res = RagResult(judged, ranked)
        if with_answer:
            res.answer = AnswerGenerator(self.llm).generate(question, self.facts(ranked))
        return res


def dumps(res: RagResult) -> str:
    return json.dumps([[j.service.id, j.tier, j.reason, round(j.score, 2)] for j in res.retrieved], ensure_ascii=False)
