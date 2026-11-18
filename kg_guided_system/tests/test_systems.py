"""비교 실행: 두 시스템이 같은 형식으로 결과를 내는지."""
import datetime as dt

from judge.pipeline import Consultant
from judge.rag import TextRAG
from judge.systems import run_kg, run_rag
from tests.fake_llm import FakeLLM, universal_handler
from tests.test_pipeline import EXTRACTED


def test_both_systems_same_shape(kg_final):
    h = universal_handler(EXTRACTED, {"기초수급자": {"기초생활수급자"}},
                          rag_verdict=lambda t: ("적합", "") if "월세" in t.split("\n")[0] else ("확인 필요", "모름"),
                          answer=lambda u: "답변 [S1]")
    llm = FakeLLM(h)
    q = "신당동 22살 대학생 기초수급자예요. 월세 지원 있나요?"
    kg = run_kg(Consultant(kg_final, llm), q, dt.date(2026, 11, 18))
    rag = run_rag(TextRAG(kg_final, llm), q)
    for o in (kg, rag):
        assert o.ranked and all(t in ("적합", "확인 필요") for _, t in o.ranked)
        assert o.answer is not None and o.llm_calls >= 2
    assert kg.llm_calls == 1 + 3 + 1          # 추출 1 + 개념 확인(대학생·기초수급자·장애인 아님 → 후보 있는 것만) + 답변 1
    assert rag.llm_calls == 4 + 1
    assert kg.detail["relevant"] and kg.ranked[0][0] in kg.detail["relevant"]
    assert len(rag.detail["retrieved"]) == 20
