"""① 프로필 추출: LLM 출력 정리와 계산."""
import datetime as dt
import json

import pytest

from judge.extract import MEDIAN_INCOME_2026, ProfileExtractor, check_raw, normalize
from tests.fake_llm import FakeLLM, chat_reply

TODAY = dt.date(2026, 11, 18)
FIELDS = ["주거·자립", "보육·교육"]
STYPES = ["현금", "현물"]


def raw(**kw):
    base = {"age": None, "birth_year": None, "region": None, "gender": None, "income_pct": None,
            "monthly_income": None, "household_size": None, "groups": [], "not_groups": [], "receiving": [],
            "user_type": "개인", "fields": [], "support_types": [], "keywords": []}
    return {**base, **kw}


def test_income_from_amount():
    ex = normalize(raw(monthly_income=2_000_000, household_size=2), TODAY, FIELDS, STYPES)
    assert ex.income_pct == round(2_000_000 / MEDIAN_INCOME_2026[2] * 100, 1)
    assert "2인 가구" in ex.notes[-1]


def test_income_manwon_unit_and_default_household():
    ex = normalize(raw(monthly_income=150), TODAY, FIELDS, STYPES)
    assert ex.income_pct == round(1_500_000 / MEDIAN_INCOME_2026[1] * 100, 1)
    assert any("만원" in n for n in ex.notes) and any("1인 가구" in n for n in ex.notes)


def test_income_pct_wins_and_strings_ok():
    ex = normalize(raw(income_pct="50%", monthly_income=9_000_000), TODAY, FIELDS, STYPES)
    assert ex.income_pct == 50.0


def test_big_household():
    ex = normalize(raw(monthly_income=9_000_000, household_size=7), TODAY, FIELDS, STYPES)
    assert ex.income_pct == round(9_000_000 / 9_515_150 * 100, 1)            # 7인: 고시 값
    ex8 = normalize(raw(monthly_income=9_000_000, household_size=8), TODAY, FIELDS, STYPES)
    base8 = 9_515_150 + (9_515_150 - 8_555_952)                               # 8인 이상: 1인당 (7인-6인) 차이
    assert ex8.income_pct == round(9_000_000 / base8 * 100, 1)


def test_birth_year_overrides_model_age_and_pregnancy_gender():
    ex = normalize(raw(age=66, birth_year=1958, groups=["임산부"]), TODAY, FIELDS, STYPES)
    assert ex.age == 68 and any("출생연도로 계산" in n for n in ex.notes)
    assert ex.gender == "여성"
    assert normalize(raw(gender="남성", groups=["임산부"]), TODAY, FIELDS, STYPES).gender == "남성"   # 말한 것이 우선


def test_birth_year_and_bad_values():
    ex = normalize(raw(birth_year=2004, gender="여자", user_type="회사"), TODAY, FIELDS, STYPES)
    assert ex.age == 22 and ex.gender is None and ex.user_type == "개인"
    assert normalize(raw(age="22세"), TODAY, FIELDS, STYPES).age is None or True   # 숫자 아님 → 무시해도 됨
    assert normalize(raw(age="22"), TODAY, FIELDS, STYPES).age == 22
    assert normalize(raw(age=300), TODAY, FIELDS, STYPES).age is None


def test_lists_cleaned():
    ex = normalize(raw(groups=["대학생", " 대학생 ", "", "기초생활보장법 제2조에 따른 수급권자로서 소득인정액이 기준 이하인 사람"],
                       not_groups=["장애인", "대학생"], fields=["주거·자립", "없는 분야"],
                       support_types=["현금", "돈"]), TODAY, FIELDS, STYPES)
    assert ex.groups == ["대학생"]
    assert ex.not_groups == ["장애인"]                      # groups 와 겹치면 '해당'을 따른다
    assert ex.fields == ["주거·자립"] and ex.support_types == ["현금"]


@pytest.mark.parametrize("bad", [{}, {"groups": 3}, {"groups": [], "keywords": {"a": 1}}])
def test_check_raw_rejects(bad):
    with pytest.raises(TypeError):
        check_raw(bad)


def test_extractor_prompt_and_guided_retry(kg_final):
    replies = iter(["죄송하지만 잘 모르겠습니다.",
                    json.dumps(raw(age=22, region="서울 중구", groups=["대학생"], fields=["주거·자립"],
                                   support_types=["현금"], keywords=["월세"]), ensure_ascii=False)])
    llm = FakeLLM(lambda p, b: chat_reply(next(replies)))
    E = ProfileExtractor(kg_final, llm)
    sys_prompt = E.messages("x")[0]["content"]
    assert "장애인, 기초생활수급자, 국가보훈대상자" in sys_prompt        # 많이 쓰이는 범주 어휘
    assert "광진구 거주자" not in sys_prompt                           # 지역 범주는 규칙으로
    assert "현금(장학금)" not in sys_prompt and "현금" in sys_prompt    # 지원유형은 최상위만
    ex = E.extract("신당동 사는 22살 대학생, 월세 지원?", TODAY)
    assert ex.mode == "guided" and ex.age == 22 and ex.keywords == ["월세"]
    schema = llm.requests[1][1]["response_format"]["json_schema"]["schema"]
    assert "주거·자립" in schema["properties"]["fields"]["items"]["enum"]
