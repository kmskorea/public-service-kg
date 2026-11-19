"""2026-11-19 연구실 GPU 첫 실행 보고서(RUN_REPORT.md)의 문제 사례를 재현하고, 고친 동작을 고정한다."""
import datetime as dt
import json

import pytest

from judge import Judge, Profile, Tier, load_kg, missing_info
from judge.answer import ELIGIBLE_WITH_CAVEAT, AnswerGenerator, Fact, kg_facts, template_answer, verify
from judge.linker import ConceptLinker
from judge.pipeline import DEFAULT_PATCHES, Consultant
from tests.conftest import KG_DIR
from tests.fake_llm import FakeLLM, chat_reply, parse_verify_prompt, universal_handler

TODAY = dt.date(2026, 11, 18)


@pytest.fixture(scope="module")
def kg_patched():
    return load_kg(KG_DIR / "kg.ttl", DEFAULT_PATCHES)


def picker(choices):
    """개념 확인 프롬프트에서 choices[(표현, 긍정)] 라벨들을 고르는 가짜 LLM (방향 규칙을 일부러 어긴다)."""
    def handler(path, body):
        phrase, positive, cands = parse_verify_prompt(body["messages"][-1]["content"])
        want = choices.get((phrase, positive), set())
        return chat_reply(json.dumps({"selected": [n for n, lab in cands if lab.strip() in want]}))
    return handler


# ---------------------------------------------------------------- 1. 연결 방향
@pytest.mark.parametrize("phrase,positive,llm_picks,kept,rejected", [
    ("소상공인", True, {"강서구 소상공인"}, set(), {"강서구 소상공인"}),
    ("1인가구", True, {"청년 1인가구"}, set(), {"청년 1인가구"}),
    ("기초생활수급자", True, {"의료급여 수급자"}, set(), {"의료급여 수급자"}),
    ("한부모", True, {"한부모가족", "한부모가정"}, {"한부모가족", "한부모가정"}, set()),
    ("기초생활수급자", False, {"기초연금 수급자", "기초교육급여 수급자"}, {"기초교육급여 수급자"}, {"기초연금 수급자"}),
    ("장애", True, {"장애인", "발달장애인"}, {"장애인"}, {"발달장애인"}),
])
def test_direction_enforced(kg_final, phrase, positive, llm_picks, kept, rejected):
    L = ConceptLinker(kg_final, FakeLLM(picker({(phrase, positive): llm_picks})))
    ln = L.link_one(phrase, positive)
    exact = {kg_final.label(c) for c in kg_final.concepts_for_label(phrase)}
    got = {kg_final.label(c) for c in ln.concepts} - exact
    assert got == kept
    assert {kg_final.label(c) for c, _ in ln.rejected} == rejected


def test_jа_combined_concept_is_broader(kg_final):
    L = ConceptLinker(kg_final, FakeLLM(picker({("대학생", True): {"대학생/대학원생"}})))
    assert "대학생/대학원생" in {kg_final.label(c) for c in L.link_one("대학생").concepts}


# ---------------------------------------------------------------- 2. 제외 조건 (u01 희망두배 청년통장)
def test_patch_makes_exclusion_apply(kg_final, kg_patched):
    p = Profile(age=22, region="중구", groups=["대학생", "기초생활수급자", "청년"])   # 청년: 파이프라인 규칙이 붙임

    def tier(kg):
        r = Judge(kg).run(p, include_ineligible=True)
        return next(j for j in r.judgments if j.service.title == "희망두배 청년통장")

    before, after = tier(kg_final), tier(kg_patched)
    assert before.tier is Tier.ELIGIBLE and "생계·의료·주거·교육급여 수급자" in before.caveats
    assert after.tier is Tier.INELIGIBLE


def test_caveat_eligible_is_conditional_in_answer():
    facts = [Fact("S1", "1", "A 사업", ELIGIBLE_WITH_CAVEAT, ["다음에 해당하면 제외: 군 의무 복무자"]),
             Fact("S2", "2", "B 사업", "확인 필요", ["조건 모름: 필수: 무주택세대"])]
    assert "「A 사업」 (단, 군 의무 복무자에 해당하면 제외) [S1]" in template_answer(facts, "")
    _, w = verify("「A 사업」은 받을 수 있어요 [S1].\n「B 사업」도 받을 수 있어요 [S2].", facts)
    assert w == ["조건부 사업을 받을 수 있다고 단정 [S1]", "조건부 사업을 받을 수 있다고 단정 [S2]"]
    _, ok = verify("군 의무 복무자가 아니라면 「A 사업」을 받을 수 있어요 [S1].", facts)
    assert ok == []


def test_kg_facts_marks_caveat(kg_final):
    r = Judge(kg_final).run(Profile(age=22, region="중구", groups=["대학생", "기초생활수급자", "청년"]))
    j = next(x for x in r.judgments if x.service.title == "희망두배 청년통장")
    assert kg_facts(kg_final, [j])[0].tier == ELIGIBLE_WITH_CAVEAT


# ---------------------------------------------------------------- 4. 일반 범주만으로 충족된 사업 (u03, u05)
def test_generic_only_services_ranked_last(kg_patched):
    ex = {"age": None, "birth_year": 1958, "region": "서울 노원구", "gender": None, "income_pct": None,
          "monthly_income": None, "household_size": None, "groups": ["1인가구"], "not_groups": [],
          "receiving": ["기초연금"], "user_type": "개인", "fields": [], "support_types": [], "keywords": []}
    c = Consultant(kg_patched, FakeLLM(universal_handler(ex)))
    out = c.consult("1958년생 혼자 살아요. 노원구. 기초연금 받아요.", TODAY)
    assert out.understanding.profile.age == 68
    el = [j for j in out.ranked if j.tier is Tier.ELIGIBLE]
    spec = [c.specific(j) > 0 for j in el]
    assert spec == sorted(spec, reverse=True) and spec[0]          # 구체적 범주로 충족된 것이 먼저
    first_generic = spec.index(False)
    # 뒤로 간 사업은 충족된 서비스 조건이 모두 일반 범주(국민·일반인·가구·…거주자 등)
    assert all(m in c.generic for j in el[first_generic:first_generic + 5] for cond in j.conditions for m in cond.matched)


# ---------------------------------------------------------------- 6. 개인에게 법인·기관 범주를 묻지 않음
def test_no_org_follow_up_for_individual(kg_patched):
    ex = {"age": 30, "birth_year": None, "region": "서울 성북구", "gender": None, "income_pct": None,
          "monthly_income": None, "household_size": None, "groups": [], "not_groups": [], "receiving": [],
          "user_type": "개인", "fields": [], "support_types": [], "keywords": ["대출"]}
    c = Consultant(kg_patched, FakeLLM(universal_handler(ex)))
    out = c.consult("대출 지원 있나요?", TODAY)
    skip = c.skip_labels(out.understanding.extraction)
    assert {"기업", "중소기업", "법인/시설/단체"} <= skip
    asked = [n for n, _ in missing_info(out.result, 500, skip=skip)]
    assert not any(n in skip for n in asked)
    assert any(n in skip for n, _ in missing_info(out.result, 500))  # 거르지 않으면 언젠가는 묻게 된다


# ---------------------------------------------------------------- 5. 답변 태그 노출
def test_answer_tags_stripped():
    text = "[되물을 질문]\n요약입니다 [S1].\n[되물 질문] 무주택이신가요?"
    ans = AnswerGenerator(FakeLLM(lambda p, b: chat_reply(text))).generate(
        "q", [Fact("S1", "1", "A", "적합", [])])
    assert "[되물" not in ans.text and ans.text.startswith("요약입니다")
