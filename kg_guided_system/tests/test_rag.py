"""B1 텍스트 RAG: 검색, 원문 판정 파싱, 답변."""
import pytest

from judge.rag import BM25, TextRAG, _tokens
from tests.fake_llm import FakeLLM, universal_handler


@pytest.fixture(scope="module")
def rag_offline(kg_final):
    return TextRAG(kg_final, None)


def titles(hits):
    return [s.title for s, _ in hits]


def test_bm25_small():
    docs = [_tokens("청년 월세 지원"), _tokens("노인 일자리"), _tokens("청년 일자리 지원")]
    top = BM25(docs).search(_tokens("청년 월세"), 3)
    assert top[0][0] == 0


@pytest.mark.parametrize("q,want", [
    ("청년 월세 지원 받고 싶어요", "청년월세 지원"),
    ("전세사기 피해를 당했는데 이사비 지원", "전세피해"),
    ("국가장학금 신청하고 싶어요", "국가장학금"),
])
def test_retrieval_real_kg(rag_offline, q, want):
    # '청년월세 지원'은 원문이 길어 BM25 길이 보정으로 6위 → RAG 는 상위 20건을 판정하므로 10위 안이면 충분
    assert any(want in t for t in titles(rag_offline.retrieve(q, 10)))


def test_judge_parsing_and_ranking(kg_final):
    # 제목에 '월세' 가 있으면 적합, '전세' 면 부적합, 3번째마다 판정 누락, 나머지 확인 필요
    seen = {"n": 0}

    def verdict(text):
        seen["n"] += 1
        if seen["n"] % 3 == 0:
            return None, None
        if "월세" in text.split("\n")[0]:
            return "적합", "청년이고 월세 사는 중"
        if "전세" in text.split("\n")[0]:
            return "부적합", "전세 대상"
        return "확인 필요", "소득 정보 없음"

    log = []
    llm = FakeLLM(universal_handler({}, rag_verdict=verdict, answer=lambda u: "「청년월세 지원」 [S1]", log=log))
    r = TextRAG(kg_final, llm, k=20, batch=5)
    res = r.run("청년 월세 지원 받고 싶어요")
    assert log.count("rag") == 4 and log.count("answer") == 1         # 20건 / 5건씩 + 답변 1
    assert len(res.retrieved) == 20
    tiers = [j.tier for j in res.retrieved]
    assert tiers.count("판정 없음") == 6
    assert all(j.tier in ("적합", "확인 필요") for j in res.ranked)
    first_need = next(i for i, j in enumerate(res.ranked) if j.tier == "확인 필요")
    assert all(j.tier == "적합" for j in res.ranked[:first_need])
    # 같은 단계 안에서는 검색 순서 유지
    pos = {j.service.id: i for i, j in enumerate(res.retrieved)}
    el = [pos[j.service.id] for j in res.ranked if j.tier == "적합"]
    assert el == sorted(el)
    assert res.answer.mode == "llm" and res.answer.facts[0].lines[0].startswith("지원대상(원문):")
    assert "판정하라" in llm.requests[0][1]["messages"][0]["content"]
    assert "지원대상:" in llm.requests[0][1]["messages"][0]["content"]


def test_rag_needs_llm(rag_offline):
    with pytest.raises(RuntimeError):
        rag_offline.run("질문")
