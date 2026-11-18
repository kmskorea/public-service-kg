"""⑥ 답변 생성과 근거 검사."""
import datetime as dt

import pytest

from judge.answer import AnswerGenerator, Fact, answer_consultation, follow_up, kg_facts, template_answer, verify
from judge.pipeline import Consultant
from tests.fake_llm import FakeLLM, chat_reply, universal_handler
from tests.test_pipeline import EXTRACTED

TODAY = dt.date(2026, 11, 18)
FACTS = [
    Fact("S1", "1", "청년월세 지원", "확인 필요", ["지원 내용: 월 최대 20만원, 12개월", "조건 모름: 필수: 무주택세대"]),
    Fact("S2", "2", "K-패스", "적합", ["지원 내용: 교통비 20~53% 환급", "신청기한: 상시"]),
]


def test_verify_clean_answer():
    text = ("「K-패스」는 받을 수 있어요. 교통비를 20~53% 돌려받습니다 [S2].\n"
            "「청년월세 지원」은 월 최대 20만 원을 12개월 받는데, 무주택 여부 확인이 필요해요 [S1].\n"
            "22세이시니 나이 조건은 맞아요.")
    cited, warnings = verify(text, FACTS, question="22살 대학생이에요")
    assert cited == ["2", "1"] and warnings == []


def test_verify_catches_hallucinations():
    text = "「청년 주거급여 분리지급」도 받을 수 있어요 [S3]. 「K-패스」로 월 30만원을 받습니다 [S2]."
    _, w = verify(text, FACTS)
    assert "없는 근거 번호 [S3]" in w
    assert "근거에 없는 사업명 「청년 주거급여 분리지급」" in w
    assert "근거에 없는 숫자 '30만원'" in w


def test_verify_citation_mismatch():
    _, w = verify("「청년월세 지원」은 교통비를 돌려받아요 [S2].\n「K-패스」도 있어요 [S2].", FACTS)
    assert w == ["인용 불일치 「청년월세 지원」 [S2]"]


def test_template_answer_is_self_consistent():
    text = template_answer(FACTS, "혹시 '무주택세대'에 해당하시나요?")
    assert "「K-패스」 [S2]" in text and "무주택세대 확인 필요 [S1]" in text
    cited, warnings = verify(text, FACTS)
    assert set(cited) == {"1", "2"} and warnings == []


def test_generator_strips_think_and_verifies():
    llm = FakeLLM(lambda p, b: chat_reply("<think>고민</think>「K-패스」 받을 수 있어요 [S2]. 100만원 드려요."))
    ans = AnswerGenerator(llm).generate("질문", FACTS)
    assert ans.mode == "llm" and ans.text.startswith("「K-패스」")
    assert ans.cited == ["2"] and ans.warnings == ["근거에 없는 숫자 '100만원'"]
    sent = llm.requests[0][1]["messages"][1]["content"]
    assert "[S1] 「청년월세 지원」 (확인 필요)" in sent and "조건 모름: 필수: 무주택세대" in sent


@pytest.mark.parametrize("missing,expect", [
    ([], ""),
    ([("무주택세대", 12)], "혹시 '무주택세대'에 해당하시나요? (확인되면 12개 사업"),
    ([("소득(기준중위소득 %)", 30)], "가구 월소득과 가구원 수"),
    ([("장애인 | 한부모가족", 3)], "다음 중 해당하는 것이 있나요? 장애인, 한부모가족"),
])
def test_follow_up(missing, expect):
    assert follow_up(missing).startswith(expect) if expect else follow_up(missing) == ""


def test_answer_from_consultation(kg_final):
    log = []
    h = universal_handler(EXTRACTED, {"기초수급자": {"기초생활수급자"}},
                          answer=lambda user: "「청년월세 지원」은 무주택 확인이 필요해요 [S1].", log=log)
    c = Consultant(kg_final, FakeLLM(h))
    out = c.consult("신당동 22살 대학생 기초수급자, 장애 없음. 월세 지원?", TODAY)
    facts = kg_facts(kg_final, out.ranked)
    assert [f.key for f in facts] == [f"S{i}" for i in range(1, len(facts) + 1)]
    assert all(any(x.startswith("지원 내용:") for x in f.lines) for f in facts)
    ans = answer_consultation(c, out, c.llm)
    assert log.count("answer") == 1
    sent = c.llm.requests[-1][1]["messages"][1]["content"]
    assert "[되물을 질문]" in sent and "무주택세대" in sent            # 월세 질문 → 무주택 여부를 되묻는다
    # 답변에 쓰인 근거는 질문과 관련된 사업이 먼저 (관련 7건 + 그 밖의 적합 3건)
    rel_ids = {j.service.id for j in out.ranked if c.relevant(j, out.understanding.extraction)}
    flags = [f.service_id in rel_ids for f in ans.facts]
    assert flags == sorted(flags, reverse=True) and flags.count(True) == 7 and len(ans.facts) == 10
    assert all(f.tier == "적합" for f in ans.facts[7:])
    assert all("org_" not in x for f in ans.facts for x in f.lines)    # 기관은 이름으로
    no_llm = answer_consultation(c, out, None)
    assert no_llm.mode == "template" and no_llm.warnings == []
