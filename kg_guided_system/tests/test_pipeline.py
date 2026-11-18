"""문장 → 추출 → 연결 → 판정, 가짜 LLM 으로 끝까지."""
import datetime as dt
import json

from judge import Tier
from judge.pipeline import Consultant
from tests.fake_llm import FakeLLM, chat_reply, parse_verify_prompt

TODAY = dt.date(2026, 11, 18)
EXTRACTED = {"age": 22, "birth_year": None, "region": "서울 중구", "gender": None, "income_pct": None,
             "monthly_income": None, "household_size": None, "groups": ["대학생", "기초수급자"],
             "not_groups": ["장애인"], "receiving": [], "user_type": "개인", "fields": ["주거·자립"],
             "support_types": ["현금"], "keywords": ["월세"]}


def handler(path, body):
    msgs = body["messages"]
    if msgs[0]["role"] == "system":                         # 프로필 추출
        return chat_reply(json.dumps(EXTRACTED, ensure_ascii=False))
    phrase, positive, cands = parse_verify_prompt(msgs[-1]["content"])
    want = {"기초수급자": {"기초생활수급자"}}.get(phrase, set())
    return chat_reply(json.dumps({"selected": [n for n, lab in cands if lab.strip() in want]}))


def test_end_to_end(kg_final):
    c = Consultant(kg_final, FakeLLM(handler))
    out = c.consult("신당동 사는 22살 대학생인데 기초수급자예요. 장애는 없어요. 월세 지원 있나요?", TODAY)
    u, kg = out.understanding, kg_final
    pos = {kg.label(x) for x in u.profile.groups}
    assert {"대학생", "기초생활수급자", "청년", "지역주민", "대한민국 국민"} <= pos   # KG 에 '중구 거주자' 개념은 없다
    assert "장애인" in {kg.label(x) for x in u.profile.not_groups}
    assert u.profile.region.endswith("region_서울_중구") and u.unresolved == []
    assert u.profile.user_types == ["개인", "가구"]

    # 질문('월세')과 관련된 사업이 맨 위 묶음, 그 안에서는 판정 단계 순
    top = out.ranked[:5]
    assert all(c.relevant(j, u.extraction) for j in top)
    assert all("월세" in (j.service.title + j.service.benefit_text) for j in top)
    rel = [j for j in out.ranked if c.relevant(j, u.extraction)]
    order = {Tier.ELIGIBLE: 0, Tier.NEEDS_INFO: 1}
    assert [order[j.tier] for j in rel] == sorted(order[j.tier] for j in rel)
    # 월세 사업은 무주택 여부를 몰라 '확인 필요' → 되묻기 대상
    wolse = next(j for j in rel if j.service.title == "청년월세 지원")
    assert wolse.tier is Tier.NEEDS_INFO and "필수: 무주택세대" in [x.text for x in wolse.unknowns]
    from judge import missing_info
    asked = [name for name, _ in missing_info(out.result, 3, rel)]
    assert "무주택세대" in asked                               # 월세 질문이면 무주택 여부를 먼저 묻는다
    # 장애인 아님을 밝혔으므로 장애인 필수 사업은 부적합 → 기본 결과에서 빠진다
    assert not any(kg.label(r) == "장애인" for j in out.ranked for r in j.service.requires)
    text = "\n".join(u.summary(kg))
    assert "프로필: 22세, 중구" in text
    assert "기초수급자 → 기초생활수급자  [llm]" in text and "¬장애인 → 장애인" in text


def test_unknown_region_reported(kg_final):
    def h(path, body):
        if body["messages"][0]["role"] == "system":
            return chat_reply(json.dumps({**EXTRACTED, "region": "부산 해운대구", "groups": [], "not_groups": []}))
        return chat_reply('{"selected": []}')
    u = Consultant(kg_final, FakeLLM(h)).understand("부산 사는데요", TODAY)
    assert "지역:부산 해운대구" in u.unresolved and u.profile.region is None
