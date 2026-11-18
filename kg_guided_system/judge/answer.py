"""⑥ 답변 생성 + 근거 검사.

근거(facts)는 번호 [S1], [S2] … 가 붙은 서비스별 사실 묶음이다.
  - KG 시스템: 판정기 결과(조건별 충족·모름·근거 경로, 신청기한, 지원내용 원문, 링크)
  - 텍스트 RAG: 검색된 원문(대상·지원내용·신청기한 텍스트)
LLM 은 근거에 있는 것만 써서 답하고, 답변은 코드가 다시 검사한다 (verify):
  - 인용 [S#] 이 근거에 있는가
  - 「사업명」 이 근거의 사업명과 맞는가, 같은 문장의 인용 번호가 그 사업을 가리키는가
  - 금액·%·날짜 같은 숫자가 근거나 질문에 나오는가
LLM 이 없으면 같은 근거로 틀에 맞춘 답변을 만든다 (template).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .judge import Judgment, Tier, missing_info
from .llm import LLMClient


@dataclass
class Fact:
    key: str                 # S1, S2 …
    service_id: str
    title: str
    tier: str                # 적합 | 확인 필요
    lines: list[str]         # 근거 문장들
    url: str = ""

    def block(self) -> str:
        return f"[{self.key}] 「{self.title}」 ({self.tier})\n" + "\n".join("  - " + x for x in self.lines)


@dataclass
class Answer:
    text: str
    facts: list[Fact]
    cited: list[str] = field(default_factory=list)          # 인용된 서비스 ID
    warnings: list[str] = field(default_factory=list)       # 근거 검사에서 걸린 것
    mode: str = ""                                          # llm | template


def _clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


# ------------------------------------------------------------------ KG 근거
def kg_facts(kg, judgments: list[Judgment], relevant: set[str] | None = None,
             n_relevant: int = 7, n_other: int = 3, n_eligible: int = 5, n_needs: int = 5) -> list[Fact]:
    """판정 결과 → 근거 묶음. judgments 는 이미 순위가 매겨진 것.

    relevant(질문과 관련된 서비스 IRI)가 있으면 그 사업을 먼저 n_relevant 건, 그 밖의 '적합' 사업을 n_other 건.
    없으면 적합 n_eligible 건 + 확인 필요 n_needs 건.
    """
    ok = (Tier.ELIGIBLE, Tier.NEEDS_INFO)
    if relevant:
        rel = [j for j in judgments if j.service.iri in relevant and j.tier in ok][:n_relevant]
        other = [j for j in judgments if j.service.iri not in relevant and j.tier is Tier.ELIGIBLE][:n_other]
        chosen = rel + other
    else:
        chosen = [j for j in judgments if j.tier is Tier.ELIGIBLE][:n_eligible] + \
                 [j for j in judgments if j.tier is Tier.NEEDS_INFO][:n_needs]
    facts = []
    for i, j in enumerate(chosen, 1):
        s = j.service
        lines = [f"지원 내용: {_clip(s.benefit_text, 220)}"]
        for c in j.conditions:
            if c.kind == "user_type":
                continue
            ev = f" ← {c.evidence}" if c.evidence else ""
            lines.append(f"조건 {c.status.value}: {c.text}{ev}")
        if j.caveats:
            lines.append("다음에 해당하면 제외: " + ", ".join(j.caveats))
        if j.preferred:
            lines.append("우대: " + ", ".join(j.preferred))
        dl = f"신청기한: {_clip(s.deadline_text, 80)}"
        if j.apply:
            dl += f" ({j.apply.value})"
        lines.append(dl)
        if s.budget_limited:
            lines.append("예산 소진 시 마감")
        lines.append(f"기관: {kg.label(s.org)} / 지역: {kg.label(s.region)}")
        facts.append(Fact(f"S{i}", s.id, s.title, j.tier.value, lines, s.detail_url))
    return facts


# ------------------------------------------------------------------ 되물을 질문
ASK = {
    "소득(기준중위소득 %)": "가구 월소득과 가구원 수(본인 포함)를 알려주시면 소득 기준을 확인할 수 있어요.",
    "나이": "나이(만 나이)를 알려주시겠어요?",
    "성별": "성별을 알려주시겠어요?",
    "거주 구": "어느 구에 사시나요?",
}


def follow_up(missing: list[tuple[str, int]]) -> str:
    if not missing:
        return ""
    name, n = missing[0]
    if name in ASK:
        q = ASK[name]
    elif " | " in name:
        q = f"다음 중 해당하는 것이 있나요? {name.replace(' | ', ', ')}"
    else:
        q = f"혹시 '{name}'에 해당하시나요?"
    return f"{q} (확인되면 {n}개 사업의 판정이 정리됩니다)"


# ------------------------------------------------------------------ 생성
SYSTEM = """너는 한국 공공서비스 상담원이다. 아래 [근거]에 적힌 사실만 써서 사용자의 질문에 답한다.
규칙
- 근거에 없는 사업·금액·날짜·조건은 절대 쓰지 않는다. 모르면 모른다고 쓴다.
- 사업 이름은 「」 안에 근거에 적힌 그대로 쓰고, 문장 끝에 근거 번호를 [S1] 처럼 붙인다.
- '적합'은 "받을 수 있어요", '확인 필요'는 무엇을 확인해야 하는지 함께 쓴다. '조건 모름' 항목이 확인할 것이다.
- 순서: ① 한두 문장 요약 ② 받을 수 있는 사업 ③ 확인이 필요한 사업 ④ [되물을 질문]이 있으면 마지막에 그대로 묻기.
- 신청기한과 '제외' 조건이 있으면 짧게 알려준다. 전체 10~15줄 이내, 존댓말."""


def answer_messages(question: str, facts: list[Fact], ask: str) -> list[dict]:
    body = "\n\n".join(f.block() for f in facts) or "(조건에 맞는 사업을 찾지 못함)"
    user = f"[질문]\n{question}\n\n[근거]\n{body}"
    if ask:
        user += f"\n\n[되물을 질문]\n{ask}"
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def template_answer(facts: list[Fact], ask: str) -> str:
    el = [f for f in facts if f.tier == Tier.ELIGIBLE.value]
    ni = [f for f in facts if f.tier == Tier.NEEDS_INFO.value]
    out = [f"조건에 맞는 사업 {len(el)}개, 확인이 더 필요한 사업 {len(ni)}개를 찾았어요."]
    if el:
        out.append("받을 수 있는 사업")
        for f in el:
            out.append(f"- 「{f.title}」 [{f.key}]")
    if ni:
        out.append("확인이 필요한 사업")
        for f in ni:
            todo = [x.split(": ", 1)[1] for x in f.lines if x.startswith("조건 모름")]
            out.append(f"- 「{f.title}」: {', '.join(todo) or '세부 조건'} 확인 필요 [{f.key}]")
    if ask:
        out.append(ask)
    return "\n".join(out)


# ------------------------------------------------------------------ 검사
_CITE = re.compile(r"\[(S\d+)\]")
_TITLE = re.compile(r"「([^」]+)」")
_NUM = re.compile(r"(?<![\w\[S])(\d[\d,.]*)\s*(만\s?원|천\s?원|억\s?원|원|%|세|개월|년|월|일|회|명|배)")


def _norm_num(s: str) -> str:
    return s.replace(",", "").rstrip(".")


def verify(text: str, facts: list[Fact], question: str = "") -> tuple[list[str], list[str]]:
    """반환: (인용된 서비스 ID, 경고 목록)."""
    by_key = {f.key: f for f in facts}
    warnings, cited = [], []
    for k in _CITE.findall(text):
        if k in by_key:
            if by_key[k].service_id not in cited:
                cited.append(by_key[k].service_id)
        else:
            warnings.append(f"없는 근거 번호 [{k}]")
    titles = [f.title for f in facts]
    for t in _TITLE.findall(text):
        t = t.strip()
        if not any(t == x or t in x or x in t for x in titles):
            warnings.append(f"근거에 없는 사업명 「{t}」")
    # 같은 문장 안의 사업명과 인용 번호가 서로 맞는가 (「A」 … [S1] 인데 S1 이 B 인 경우)
    for sent in re.split(r"(?<=[.!?。])\s+|\n", text):
        names = [t.strip() for t in _TITLE.findall(sent)]
        keys = [k for k in _CITE.findall(sent) if k in by_key]
        if len(names) == 1 and keys:
            t = names[0]
            if not any(t == by_key[k].title or t in by_key[k].title or by_key[k].title in t for k in keys):
                warnings.append(f"인용 불일치 「{t}」 [{', '.join(keys)}]")
    hay = _norm_num(" ".join([question] + [f.title for f in facts] + [x for f in facts for x in f.lines]))
    hay_ns = hay.replace(" ", "")
    for num, unit in _NUM.findall(text):
        n = _norm_num(num)
        unit_ns = unit.replace(" ", "")
        if f"{n}{unit_ns}" in hay_ns or (n in hay and unit_ns in ("세", "%", "월", "일", "년", "개월", "회", "명")):
            continue
        warnings.append(f"근거에 없는 숫자 '{num}{unit}'")
    return cited, warnings


class AnswerGenerator:
    def __init__(self, llm: LLMClient | None, max_tokens: int = 900):
        self.llm, self.max_tokens = llm, max_tokens

    def generate(self, question: str, facts: list[Fact], ask: str = "") -> Answer:
        if self.llm is None:
            text, mode = template_answer(facts, ask), "template"
        else:
            text = self.llm.chat(answer_messages(question, facts, ask), max_tokens=self.max_tokens)
            text, mode = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip(), "llm"
        cited, warnings = verify(text, facts, question)
        return Answer(text, facts, cited, warnings, mode)


def answer_consultation(c, consultation, llm: LLMClient | None, **kw) -> Answer:
    """pipeline.Consultation → 답변. 되물을 질문은 질문과 관련된 사업 기준."""
    u = consultation.understanding
    rel = [j for j in consultation.ranked if c.relevant(j, u.extraction)]
    ask = follow_up(missing_info(consultation.result, 3, rel or None))
    facts = kg_facts(c.kg, consultation.ranked, {j.service.iri for j in rel}, **kw)
    return AnswerGenerator(llm).generate(u.text, facts, ask)


__all__ = ["Answer", "AnswerGenerator", "Fact", "answer_consultation", "follow_up", "kg_facts",
           "template_answer", "verify"]
