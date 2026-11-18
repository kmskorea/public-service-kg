"""비교 실험용 공통 실행: 같은 질문을 두 시스템에 넣고 같은 형식으로 결과를 받는다.

    KG   : 추출 → 연결 → 판정(KG) → 답변        (pipeline.Consultant + answer)
    RAG  : BM25 검색 → LLM 원문 판정 → 답변      (rag.TextRAG)
    KG-base : KG 시스템에 kg_base.ttl (수업 기본 KG) 을 넣은 것 — KG 개선 효과 비교용
"""
from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, field

from .answer import Answer, answer_consultation
from .judge import Tier
from .llm import LLMClient
from .pipeline import Consultant
from .rag import TextRAG


@dataclass
class SystemOutput:
    system: str
    question: str
    ranked: list[tuple[str, str]]          # (서비스 ID, '적합' | '확인 필요'), 보여줄 순서
    answer: Answer | None
    seconds: float
    llm_calls: int
    detail: dict = field(default_factory=dict)

    def ids(self, tier: str | None = None, top: int | None = None) -> list[str]:
        xs = [i for i, t in self.ranked if tier is None or t == tier]
        return xs[:top] if top else xs


def _calls(llm: LLMClient | None) -> int:
    return (llm.calls + llm.cache_hits) if llm else 0


def run_kg(c: Consultant, question: str, today: dt.date, name: str = "KG", relevant_only: bool = False,
           with_answer: bool = True) -> SystemOutput:
    llm, before, t = c.llm, _calls(c.llm), time.perf_counter()
    out = c.consult(question, today)
    js = out.ranked
    if relevant_only:
        js = [j for j in js if c.relevant(j, out.understanding.extraction)]
    ranked = [(j.service.id, j.tier.value) for j in js if j.tier in (Tier.ELIGIBLE, Tier.NEEDS_INFO)]
    ans = answer_consultation(c, out, llm) if with_answer else None
    u = out.understanding
    detail = {"profile": {k: v for k, v in u.profile.__dict__.items()},
              "links": [[ln.phrase, ln.positive, [c.kg.label(x) for x in ln.concepts], ln.method] for ln in u.links],
              "unresolved": u.unresolved,
              "relevant": [j.service.id for j in out.ranked if c.relevant(j, u.extraction)]}
    return SystemOutput(name, question, ranked, ans, time.perf_counter() - t, _calls(llm) - before, detail)


def run_rag(r: TextRAG, question: str, name: str = "RAG", with_answer: bool = True) -> SystemOutput:
    before, t = _calls(r.llm), time.perf_counter()
    res = r.run(question, with_answer=with_answer)
    ranked = [(j.service.id, j.tier) for j in res.ranked]
    detail = {"retrieved": [[j.service.id, j.tier, j.reason, round(j.score, 2)] for j in res.retrieved]}
    return SystemOutput(name, question, ranked, res.answer, time.perf_counter() - t, _calls(r.llm) - before, detail)
