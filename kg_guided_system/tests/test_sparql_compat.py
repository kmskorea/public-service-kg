"""실제 KG에서 판정기(closed 모드)가 기존 SPARQL 질의와 서비스 ID 단위로 같은 결과를 내는지 확인한다.

정답은 scripts/make_sparql_fixtures.py 가 만든 tests/fixtures/sparql_results.json.
"""
import datetime as dt
import json
import time
from pathlib import Path

import pytest

from judge import Apply, Judge, Profile, Tier, apply_status, load_kg

ROOT = Path(__file__).resolve().parent.parent
KG_DIR = ROOT.parent / "public_service_kg_final_20261011" / "output"
FIX = json.loads((ROOT / "tests" / "fixtures" / "sparql_results.json").read_text(encoding="utf-8"))

# queries/*.rq 의 조건을 프로필로 옮긴 것
Q0 = Profile(age=22, region="중구", income_pct=30, groups=["대학생", "대학생/대학원생", "무주택세대"])
Q1 = Profile(age=22, region="중구", groups=["대학생", "대학생/대학원생", "기초생활수급자"])
README_COUNTS = {("kg_base.ttl", "q0"): 77, ("kg.ttl", "q0"): 41, ("kg_base.ttl", "q1"): 54, ("kg.ttl", "q1"): 69}


@pytest.fixture(scope="module", params=["kg_base.ttl", "kg.ttl"])
def kg(request):
    return request.param, Judge(load_kg(KG_DIR / request.param))


@pytest.mark.parametrize("qname,profile", [("q0_eligible_junggu_homeless", Q0),
                                           ("q1_eligible_junggu_basic_student", Q1)])
def test_eligibility_matches_sparql(kg, qname, profile):
    name, J = kg
    res = J.run(profile, mode="closed", targeted_only=True)
    got = sorted(j.service.id for j in res.judgments)
    assert all(j.tier is Tier.ELIGIBLE for j in res.judgments)
    assert got == FIX[f"{name}:{qname}"]["ids"]
    assert len(got) == README_COUNTS[(name, qname[:2])]


def test_scholarship_this_month_matches_q5(kg):
    name, J = kg
    today = dt.date(2026, 10, 9)
    got = sorted(s.id for s in J.kg.services.values()
                 if "http://kg.example.org/concept/st_현금_장학금" in s.support_types
                 and apply_status(s, today) is Apply.OPEN)
    assert got == FIX[f"{name}:q5_scholarship_this_month"]["ids"]


def test_fast_enough(kg):
    _, J = kg
    t = time.perf_counter()
    for _ in range(5):
        J.run(Q1, include_ineligible=True)
    assert (time.perf_counter() - t) / 5 < 1.0
