"""웹 판정기(web/judge.js)가 Python 판정기·상담 순서와 같은 결과를 내는지 node 로 확인한다.

Python 으로 여러 프로필을 판정해 정답(cases.json)을 만들고, 방금 내보낸 kg.json 으로 node 가 같은 것을 계산해 비교한다.
비교: 전체 순위, 조건별 상태·근거 문자열(표본 서비스), 제외·우대·신청 가능, 되묻기, 상담 순위(관련성·일반 범주).
"""
import datetime as dt
import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from judge import Judge, Profile
from judge.extract import Extraction, median_income
from judge.judge import missing_info
from judge.pipeline import DEFAULT_KG, DEFAULT_PATCHES, Consultant
from scripts.export_web import export

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
TODAY = dt.date(2026, 11, 19)


def canon(j):
    return json.dumps([j.tier.value, [[c.kind, c.status.value, c.text, c.evidence, list(c.pending)] for c in j.conditions],
                       j.caveats, j.preferred, j.apply.value if j.apply else None],
                      ensure_ascii=False, separators=(",", ":"))


@pytest.mark.skipif(NODE is None, reason="node 가 없음")
def test_web_judge_matches_python(tmp_path):
    kg_json = tmp_path / "kg.json"
    data = export(DEFAULT_KG, DEFAULT_PATCHES, kg_json)
    c = Consultant.from_paths(DEFAULT_KG, llm=None)          # 웹과 같은 KG (보정 포함), LLM 없음
    kg, J = c.kg, Judge(c.kg)
    rng = random.Random(20261119)
    common = [x["l"] for x in sorted(data["concepts"], key=lambda x: -x.get("u", 0)) if x.get("t")][:80]
    ages, regions = [None, 5, 16, 22, 30, 45, 68, 80], [None, "중구", "서울", "광진구", "강남구", "region_서울_노원구", "부산"]
    incomes, genders = [None, 30, 42.857142, 55, 120], [None, "여성", "남성"]

    judge_cases = []
    for n in range(36):
        p = dict(age=rng.choice(ages), region=rng.choice(regions), income_pct=rng.choice(incomes),
                 gender=rng.choice(genders), groups=rng.sample(common, rng.randint(0, 4)),
                 not_groups=rng.sample(common, rng.randint(0, 2)),
                 user_types=rng.choice([["개인"], ["개인", "가구"], ["소상공인"], ["법인/시설/단체"]]))
        if n % 9 == 0:
            p["groups"] = p["groups"] + ["없는범주"]
        mode = "closed" if n % 6 == 5 else "open"
        prof = Profile(**p)
        r = J.run(prof, mode=mode, include_ineligible=(n % 2 == 0), targeted_only=(n % 4 == 3), today=TODAY)
        sample = r.judgments[:25] + rng.sample(r.judgments, min(25, len(r.judgments)))
        judge_cases.append({
            "name": f"p{n:02d}",
            "profile": {"age": p["age"], "region": p["region"], "incomePct": p["income_pct"], "gender": p["gender"],
                        "groups": p["groups"], "notGroups": p["not_groups"], "userTypes": p["user_types"]},
            "opts": {"mode": mode, "includeIneligible": n % 2 == 0, "targetedOnly": n % 4 == 3,
                     "today": TODAY.isoformat()},
            "order": [j.service.id for j in r.judgments],
            "unresolved": r.prepared.unresolved,
            "detail": {j.service.id: canon(j) for j in sample},
            "missing": [list(x) for x in missing_info(r, 15)],
        })

    consult_cases = []
    for n, (age, region, groups, nots, kws, gender, ut) in enumerate([
        (22, "서울 중구", ["대학생", "기초생활수급자"], ["장애인"], ["월세"], None, "개인"),
        (68, "서울 노원구", ["1인가구"], [], [], None, "개인"),
        (31, "서울 서대문구", ["임산부", "신혼부부"], [], [], "여성", "개인"),
        (28, "서울 영등포구", ["북한이탈주민", "구직자/실업자"], [], ["취업"], None, "개인"),
        (45, "서울 서초구", ["근로자/직장인"], ["장애인", "무주택세대"], [], None, "개인"),
        (None, "서울 성북구", ["소상공인"], [], ["대출"], None, "소상공인"),
        (17, "서울 중랑구", ["학교 밖 청소년"], [], ["검정고시"], None, "개인"),
        (35, None, ["한부모가족"], [], ["양육비"], "여성", "개인"),
    ]):
        ex = Extraction(raw={}, age=age, region=region, gender=gender, income_pct=None, groups=groups,
                        not_groups=nots, user_type=ut, keywords=kws)
        u = c.from_extraction("", ex)
        out = c.consult_understood(u, TODAY)
        consult_cases.append({
            "name": f"c{n}",
            "input": {"age": age, "region": region, "gender": gender, "incomePct": None, "groups": groups,
                      "notGroups": nots, "userType": ut, "keywords": kws, "today": TODAY.isoformat()},
            "ranked": [j.service.id for j in out.ranked[:80]],
            "derived": [ln.phrase for ln in u.links if ln.method == "rule"],
            "followUps": [list(x) for x in c.follow_up_items(out, 5)],
            "genericOnly": sorted(j.service.id for j in out.ranked if c.generic_only(j)),
        })

    cases = {"judge": judge_cases, "consult": consult_cases,
             "income": [{"args": [k], "median": median_income(k)} for k in range(1, 11)]}
    cases_json = tmp_path / "cases.json"
    cases_json.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run([NODE, str(ROOT / "web" / "test" / "parity.js"), str(kg_json), str(cases_json)],
                       capture_output=True, text=True, timeout=600)
    print(r.stdout[-3000:], r.stderr[-2000:])
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
