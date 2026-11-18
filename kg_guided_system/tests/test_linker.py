"""② 개념 연결 (실제 kg.ttl)."""
import pytest

from judge.linker import ConceptLinker, NgramIndex
from tests.fake_llm import FakeLLM, chat_reply, parse_verify_prompt

SYN = {  # 가짜 LLM 이 '같은 대상'으로 고를 라벨
    ("기초수급자", True): {"기초생활수급자"},
    ("한부모", True): {"한부모가족", "한부모가정"},
    ("장애 있음", True): {"장애인"},
    ("장애", False): {"장애인", "중증장애인"},
}


def fake_verifier():
    seen = []

    def handler(path, body):
        phrase, positive, cands = parse_verify_prompt(body["messages"][-1]["content"])
        seen.append((phrase, positive, [lab for _, lab in cands]))
        want = SYN.get((phrase, positive), set())
        return chat_reply('{"selected": [%s]}' % ", ".join(str(n) for n, lab in cands if lab.strip() in want))

    return handler, seen


@pytest.fixture(scope="module")
def L_offline(kg_final):
    return ConceptLinker(kg_final)


def labels(kg, link):
    return sorted(kg.label(c) for c in link.concepts)


def test_ngram_index_basic():
    idx = NgramIndex([("기초생활수급자", "a"), ("기초연금 수급자", "b"), ("장애인", "c")])
    top = idx.search("기초수급자", 3)
    # 두 라벨이 질의와 겹치는 n-gram 이 같아 동점이다 → 어느 쪽인지는 LLM 확인 단계가 가린다
    assert {top[0][0], top[1][0]} == {"a", "b"} and top[0][1] == top[1][1]
    assert idx.search("기초생활 수급", 1)[0][0] == "a"


def test_offline_exact_and_space(kg_final, L_offline):
    assert labels(kg_final, L_offline.link_one("기초생활 수급자")) == ["기초생활수급자"]
    assert L_offline.link_one("기초생활 수급자").method == "exact"


def test_offline_threshold(kg_final, L_offline):
    hi = L_offline.link_one("전세사기 피해")          # '전세사기 피해자' 와 거의 같음
    assert hi.method == "ngram" and labels(kg_final, hi) == ["전세사기 피해자"]
    lo = L_offline.link_one("싱글맘")
    assert lo.method == "none" and lo.concepts == []


def test_llm_verification_positive(kg_final):
    handler, seen = fake_verifier()
    L = ConceptLinker(kg_final, FakeLLM(handler))
    ln = L.link_one("기초수급자")
    assert ln.method == "llm" and labels(kg_final, ln) == ["기초생활수급자"]
    assert "기초생활수급자" in seen[-1][2]


def test_exact_plus_synonyms(kg_final):
    handler, seen = fake_verifier()
    L = ConceptLinker(kg_final, FakeLLM(handler))
    ln = L.link_one("한부모")
    assert ln.method == "exact+llm"
    assert labels(kg_final, ln) == ["한부모", "한부모가정", "한부모가족"]
    assert "한부모" not in seen[-1][2]                  # 이미 연결된 것은 후보로 다시 묻지 않는다


def test_negative_prompt(kg_final):
    handler, seen = fake_verifier()
    L = ConceptLinker(kg_final, FakeLLM(handler))
    ln = L.link_one("장애", positive=False)
    assert not ln.positive and seen[-1][1] is False
    assert "장애인" in labels(kg_final, ln)


def test_llm_selects_nothing(kg_final):
    L = ConceptLinker(kg_final, FakeLLM(lambda p, b: chat_reply('{"selected": []}')))
    ln = L.link_one("우주비행사")
    assert ln.method == "none" and ln.concepts == []


def test_derive_rules(kg_final, L_offline):
    got = {ln.phrase: ln.note for ln in L_offline.derive(22, "여성", kg_final.resolve_region("광진구"), [])}
    assert set(got) == {"청년", "여성", "광진구 거주자", "지역주민", "대한민국 국민", "일반인"}
    assert "청소년" not in got
    old = {ln.phrase for ln in L_offline.derive(70, None, None, ["외국인"])}
    assert {"노인", "고령자", "경로우대자"} <= old and "대한민국 국민" not in old
