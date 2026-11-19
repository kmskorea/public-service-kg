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
    kgl = run_kg(Consultant(kg_final, llm, link_with_llm=True), q, dt.date(2026, 11, 18), "KG-LLM연결")
    rag = run_rag(TextRAG(kg_final, llm), q)
    for o in (kg, rag):
        assert o.ranked and all(t in ("적합", "확인 필요") for _, t in o.ranked)
        assert o.answer is not None and o.llm_calls >= 2
    retry = lambda o: 1 if o.answer.first_warnings else 0     # noqa: E731  근거 검사 경고 → 다시 쓰기 1회
    assert kg.llm_calls == 1 + 1 + retry(kg)                # 추출 1 + 답변 (개념 연결은 기본으로 LLM 을 쓰지 않음)
    # + 개념 확인 2 (대학생·기초수급자). 추출의 '장애인 아님'은 문장에 장애 이야기가 없어 빠진다 (지어낸 '아님' 방지)
    assert kgl.llm_calls == 1 + 2 + 1 + retry(kgl)
    assert rag.llm_calls == 4 + 1 + retry(rag)
    assert kg.detail["relevant"] and kg.ranked[0][0] in kg.detail["relevant"]
    assert len(rag.detail["retrieved"]) == 20
