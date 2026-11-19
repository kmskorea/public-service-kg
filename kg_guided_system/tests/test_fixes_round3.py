"""2026-11-19 두 번째 실제 실행 보고서(RUN_REPORT2.md)의 문제를 재현하고 고친 동작을 고정한다."""
import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

import pytest

from judge import Judge, Profile, Tier, load_kg
from judge.answer import AnswerGenerator, Fact, clean_answer
from judge.extract import normalize
from judge.pipeline import DEFAULT_PATCHES, Consultant
from tests.conftest import KG_DIR
from tests.fake_llm import FakeLLM, chat_reply, universal_handler

TODAY = dt.date(2026, 11, 18)
F, S = ["주거·자립"], ["현금"]


@pytest.fixture(scope="module")
def kgp():
    return load_kg(KG_DIR / "kg.ttl", DEFAULT_PATCHES)


def raw(**kw):
    base = {"age": None, "birth_year": None, "region": None, "gender": None, "income_pct": None,
            "monthly_income": None, "household_size": None, "groups": [], "not_groups": [], "receiving": [],
            "user_type": "개인", "fields": [], "support_types": [], "keywords": []}
    return {**base, **kw}


# ---------------------------------------------------------------- 1. 추출이 지나치게 작동
def test_multi_person_household_drops_one_person():                  # u02
    ex = normalize(raw(groups=["한부모가족", "1인가구"], household_size=3), TODAY, F, S, "아이 둘 키우는 싱글맘")
    assert ex.groups == ["한부모가족"] and any("1인가구" in n for n in ex.notes)


def test_invented_negation_dropped():                                # u06
    ex = normalize(raw(groups=["대학생"], not_groups=["장애인"]), TODAY, F, S, "아버지가 국가유공자세요. 대학교 3학년")
    assert ex.not_groups == [] and any("근거가 없어" in n for n in ex.notes)
    ok = normalize(raw(not_groups=["장애인", "무주택세대"]), TODAY, F, S, "장애는 없고 집도 있어요")
    assert ok.not_groups == ["장애인", "무주택세대"]


# ---------------------------------------------------------------- 2. 일반 범주 (u04·u05·u14)
def test_condition_met_only_via_generic_concept_is_not_specific(kgp):
    c = Consultant(kgp, None)
    r = c.judge.run(Profile(age=31, region="서대문구", gender="여성",
                            groups=["임산부", "신혼부부", "대한민국 국민", "일반인", "지역주민"]))
    fridge = next(j for j in r.judgments if j.service.title == "고효율 냉동기 지원")
    truck = next(j for j in r.judgments if "푸드트럭" in j.service.title)
    assert fridge.tier is Tier.ELIGIBLE and c.generic_only(fridge)     # 신혼부부 ⊂ 가구 로만 충족
    assert truck.tier is Tier.ELIGIBLE and c.generic_only(truck)       # 대한민국 국민 으로만 충족


def test_generic_only_eligible_ranked_after_relevant_needs_info(kgp):
    ex = raw(age=28, region="서울 영등포구", groups=["북한이탈주민", "구직자/실업자"], keywords=["취업"])
    c = Consultant(kgp, FakeLLM(universal_handler(ex)))
    out = c.consult("북한이탈주민인데 취업 준비 중이에요. 28살이고 영등포구 살아요.", TODAY)
    rel = [j for j in out.ranked if c.relevant(j, out.understanding.extraction)]
    flags = [c.generic_only(j) for j in rel]
    assert flags == sorted(flags)                                       # 일반 범주로만 '적합'인 것은 관련 묶음의 맨 뒤
    titles = [j.service.title for j in rel[:5]]
    assert not any("푸드트럭" in t for t in titles)


# ---------------------------------------------------------------- 9. KG 서비스 조건 보정
def test_individual_extension_benefit_patched(kgp):
    p = Profile(age=22, region="중구", groups=["대학생", "기초생활수급자", "청년"])
    j = next(x for x in Judge(kgp).run(p, include_ineligible=True).judgments if x.service.title == "개별연장급여")
    assert j.tier is not Tier.ELIGIBLE and "필수: 구직급여 수급자" in [c.text for c in j.conditions]


# ---------------------------------------------------------------- 4. 되묻기
def test_follow_up_downweights_sensitive_and_hides_denied_members(kgp):
    ex = raw(age=26, region="서울 송파구", groups=["1인가구"], not_groups=["장애인"], keywords=["일자리"])
    c = Consultant(kgp, FakeLLM(universal_handler(ex)))
    out = c.consult("송파구 26살 혼자 살아요. 장애는 없어요. 일자리 구해요.", TODAY)
    items = c.follow_up_items(out, 10)
    names = [n for n, _ in items]
    assert all("장애인" not in n.split(" | ") for n in names)          # 이미 '아님'인 범주는 묻지 않음
    raw_counts = dict(items)
    for n, k in items:                                                  # 민감한 범주는 같은 빈도의 다른 범주보다 뒤
        if any(w in n for w in ("질병", "피해", "폭력")):
            assert all(names.index(n) > names.index(m) for m, kk in items if kk >= k and m != n
                       and not any(w in m for w in ("장애", "질병", "질환", "환자", "피해", "폭력")))
    assert raw_counts


# ---------------------------------------------------------------- 5. 답변 다시 쓰기·중복 제거
def test_answer_retry_when_warnings():
    facts = [Fact("S1", "1", "A 사업", "확인 필요", ["조건 모름: 필수: 무주택세대"])]
    replies = iter(["「A 사업」 받을 수 있어요 [S1].", "무주택이시면 「A 사업」을 받을 수 있어요 [S1]."])
    ans = AnswerGenerator(FakeLLM(lambda p, b: chat_reply(next(replies)))).generate("q", facts)
    assert ans.mode == "llm-retry" and ans.warnings == [] and ans.first_warnings
    worse = iter(["「A 사업」 받을 수 있어요 [S1].", "「B 사업」 받을 수 있어요 [S9]. 100만원"])
    keep = AnswerGenerator(FakeLLM(lambda p, b: chat_reply(next(worse)))).generate("q", facts)
    assert keep.mode == "llm" and keep.text.startswith("「A 사업」")       # 다시 쓴 것이 더 나쁘면 첫 답변 유지


def test_clean_answer_removes_cross_section_duplicates():
    text = "받을 수 있는 사업\n- 「A」 [S1]\n- 「B」 [S2]\n확인이 필요한 사업\n- 「A」 소득 확인 [S1]\n- 「C」 [S3]"
    assert clean_answer(text).splitlines() == ["받을 수 있는 사업", "- 「A」 [S1]", "- 「B」 [S2]",
                                               "확인이 필요한 사업", "- 「C」 [S3]"]


# ---------------------------------------------------------------- 8. 결정성
def test_requests_identical_across_hash_seeds():
    probe = Path(__file__).with_name("determinism_probe.py")
    outs = set()
    for seed in ("0", "1", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        r = subprocess.run([sys.executable, str(probe)], capture_output=True, text=True, env=env, timeout=300)
        assert r.returncode == 0, r.stderr[-2000:]
        outs.add(r.stdout.strip())
    assert len(outs) == 1, outs


# ---------------------------------------------------------------- 3. LLM 연결 없이도 흔한 줄임말 연결 (별칭)
def test_aliases_link_without_llm(kgp):
    from judge.linker import ConceptLinker
    L = ConceptLinker(kgp)                       # LLM 없음 = 시스템 기본값
    for phrase, want in [("기초수급자", "기초생활수급자"), ("탈북민", "북한이탈주민"), ("싱글맘", "한부모가족"),
                         ("취준생", "취업준비생")]:
        ln = L.link_one(phrase)
        assert ln.method == "exact" and want in {kgp.label(c) for c in ln.concepts}, phrase
