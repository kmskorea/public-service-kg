"""작은 KG(tests/fixtures/mini.ttl)로 판정 규칙을 하나씩 확인한다."""
import datetime as dt
from pathlib import Path

import pytest

from judge import Apply, Judge, Profile, Status, Tier, apply_status, load_kg

MINI = Path(__file__).parent / "fixtures" / "mini.ttl"


@pytest.fixture(scope="module")
def J():
    return Judge(load_kg(MINI))


def verdict(J, sid, profile, **kw):
    res = J.run(profile, include_ineligible=True, **kw)
    return next(j for j in res.judgments if j.service.id == sid)


def test_broader_makes_requires_met_with_path(J):
    j = verdict(J, "1", Profile(age=22, region="중구", groups=["생계급여 수급자"]))
    assert j.tier is Tier.ELIGIBLE
    req = next(c for c in j.conditions if c.kind == "requires")
    assert req.evidence == "생계급여 수급자 ⊂ 기초생활수급자 ⊂ 저소득층 (상위 개념)"


def test_altlabel_and_space_insensitive_label(J):
    assert verdict(J, "1", Profile(age=22, groups=["기초수급자"])).tier is Tier.ELIGIBLE
    assert verdict(J, "1", Profile(age=22, groups=["생계급여수급자"])).tier is Tier.ELIGIBLE


def test_unknown_concept_is_needs_info_in_open_and_unmet_in_closed(J):
    p = Profile(age=22, region="중구")
    assert verdict(J, "1", p).tier is Tier.NEEDS_INFO
    assert verdict(J, "1", p, mode="closed").tier is Tier.INELIGIBLE


def test_denied_concept_and_its_descendants_are_unmet(J):
    # '저소득층 아님' → 하위인 기초생활수급자·생계급여 수급자도 아님
    j = verdict(J, "1", Profile(age=22, not_groups=["저소득층"]))
    assert j.tier is Tier.INELIGIBLE


def test_age_out_of_range(J):
    assert verdict(J, "1", Profile(age=40, groups=["저소득층"])).tier is Tier.INELIGIBLE
    j = verdict(J, "1", Profile(groups=["저소득층"]))
    assert j.tier is Tier.NEEDS_INFO and [c.kind for c in j.unknowns] == ["age"]
    assert verdict(J, "1", Profile(groups=["저소득층"]), mode="closed").tier is Tier.ELIGIBLE  # SPARQL 과 같이 미검사


def test_any_of_group(J):
    assert verdict(J, "2", Profile(region="중구", groups=["중증장애인"])).tier is Tier.ELIGIBLE
    assert verdict(J, "2", Profile(region="중구")).tier is Tier.NEEDS_INFO
    j = verdict(J, "2", Profile(region="중구", not_groups=["장애인", "한부모가족"]))
    assert j.tier is Tier.INELIGIBLE
    # 하나만 '아님'이면 아직 모름
    assert verdict(J, "2", Profile(region="중구", not_groups=["장애인"])).tier is Tier.NEEDS_INFO


def test_region(J):
    p = dict(groups=["장애인"])
    assert verdict(J, "2", Profile(region="강남구", **p)).tier is Tier.INELIGIBLE
    assert verdict(J, "2", Profile(region="서울 중구", **p)).tier is Tier.ELIGIBLE
    assert verdict(J, "2", Profile(region="서울특별시 중구", **p)).tier is Tier.ELIGIBLE
    j = verdict(J, "2", Profile(region="서울", **p))           # 구를 모름
    assert j.tier is Tier.NEEDS_INFO and j.unknowns[0].kind == "region"
    assert verdict(J, "2", Profile(**p)).tier is Tier.NEEDS_INFO  # 지역을 모름
    assert verdict(J, "1", Profile(age=22, groups=["저소득층"])).tier is Tier.ELIGIBLE  # 전국 사업은 지역 몰라도 충족


def test_excludes_gender_income(J):
    base = dict(region="중구", groups=["대학생"], gender="여성", income_pct=40)
    j = verdict(J, "3", Profile(**base))
    assert j.tier is Tier.ELIGIBLE and j.caveats == ["무주택세대"]       # 모르는 제외 조건은 주의 사항
    assert verdict(J, "3", Profile(**{**base, "groups": ["대학생", "무주택세대"]})).tier is Tier.INELIGIBLE
    assert verdict(J, "3", Profile(**{**base, "not_groups": ["무주택세대"]})).caveats == []
    assert verdict(J, "3", Profile(**{**base, "gender": "남성"})).tier is Tier.INELIGIBLE
    assert verdict(J, "3", Profile(**{**base, "income_pct": 50})).tier is Tier.ELIGIBLE   # 이하
    assert verdict(J, "3", Profile(**{**base, "income_pct": 51})).tier is Tier.INELIGIBLE


def test_income_min_is_exclusive_and_excludes_via_broader(J):
    assert verdict(J, "5", Profile(income_pct=50)).tier is Tier.INELIGIBLE   # 50% '초과'
    assert verdict(J, "5", Profile(income_pct=60)).tier is Tier.ELIGIBLE
    j = verdict(J, "5", Profile(income_pct=60, groups=["대학생"]))           # 대학생 ⊂ 학생 → 제외
    assert j.tier is Tier.INELIGIBLE
    assert next(c for c in j.conditions if c.kind == "excludes").evidence == "대학생 ⊂ 학생 (상위 개념)"


def test_recipient_of_gives_preference(J):
    j = verdict(J, "5", Profile(income_pct=60, receiving=["근로장려금"]))
    assert j.preferred == ["근로장려금 수급자"]


def test_user_type(J):
    assert verdict(J, "6", Profile(groups=["저소득층"])).tier is Tier.INELIGIBLE
    assert verdict(J, "6", Profile(groups=["저소득층"], user_types=["법인/시설/단체"])).tier is Tier.ELIGIBLE


def test_targeted_only_and_ranking(J):
    res = J.run(Profile(age=22, region="중구", groups=["기초생활수급자", "장애인"]))
    ids = [j.service.id for j in res.judgments]
    assert "4" not in ids                       # 강남구 사업은 부적합 → 기본 결과에서 빠짐
    assert ids[:2] == ["2", "1"]                # 적합 중 가까운 지역(중구) 먼저
    res = J.run(Profile(region="강남구"), targeted_only=True)
    assert "4" not in [j.service.id for j in res.judgments]


def test_unresolved_labels_reported(J):
    res = J.run(Profile(groups=["우주비행사"], not_groups=["외계인"], region="화성시"))
    assert res.prepared.unresolved == ["우주비행사", "¬외계인", "지역:화성시"]


@pytest.mark.parametrize("sid,today,expected", [
    ("1", dt.date(2026, 11, 18), Apply.OPEN),            # ALWAYS
    ("2", dt.date(2026, 11, 18), Apply.UNKNOWN),         # VARIES
    ("3", dt.date(2026, 12, 1), Apply.OPEN),             # ANNUAL 11~2월 (해 넘김)
    ("3", dt.date(2027, 2, 10), Apply.OPEN),
    ("3", dt.date(2026, 6, 1), Apply.CLOSED),
    ("4", dt.date(2026, 11, 18), Apply.NO_APPLICATION),
    ("5", dt.date(2026, 10, 31), Apply.UPCOMING),        # RANGE 11-01~11-30
    ("5", dt.date(2026, 11, 30), Apply.OPEN),
    ("5", dt.date(2026, 12, 1), Apply.CLOSED),
])
def test_apply_status(J, sid, today, expected):
    assert apply_status(J.kg.services[f"http://kg.example.org/resource/svc_{sid}"], today) is expected
