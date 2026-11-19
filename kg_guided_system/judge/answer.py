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

from .judge import Judgment, Tier
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
    warnings: list[str] = field(default_factory=list)       # 근거 검사에서 걸린 것 (최종 답변)
    mode: str = ""                                          # llm | llm-retry | template
    first_warnings: list[str] = field(default_factory=list)  # 다시 생성하기 전 첫 답변의 경고


def _clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


# ------------------------------------------------------------------ KG 근거
ELIGIBLE_WITH_CAVEAT = "적합(제외 조건 확인)"


def _dedupe(js: list[Judgment]) -> list[Judgment]:
    seen, out = set(), []
    for j in js:
        if j.service.title not in seen:
            seen.add(j.service.title)
            out.append(j)
    return out


def kg_facts(kg, judgments: list[Judgment], relevant: set[str] | None = None,
             n_relevant: int = 7, n_other: int = 3, n_eligible: int = 5, n_needs: int = 5,
             other_ok=None) -> list[Fact]:
    """판정 결과 → 근거 묶음. judgments 는 이미 순위가 매겨진 것.

    relevant(질문과 관련된 서비스 IRI)가 있으면 그 사업을 먼저 n_relevant 건, 그 밖의 '적합' 사업을 n_other 건.
    없으면 적합 n_eligible 건 + 확인 필요 n_needs 건. other_ok 를 주면 '그 밖의 적합' 은 그것을 통과한 것만
    (예: 일반 범주만으로 충족된 사업은 넣지 않는다). 사업명이 같은 것은 하나만 넣는다.
    """
    ok = (Tier.ELIGIBLE, Tier.NEEDS_INFO)
    other_ok = other_ok or (lambda j: True)
    js = _dedupe([j for j in judgments if j.tier in ok])
    if relevant:
        rel = [j for j in js if j.service.iri in relevant][:n_relevant]
        other = [j for j in js if j.service.iri not in relevant and j.tier is Tier.ELIGIBLE and other_ok(j)]
        chosen = rel + other[:n_other]
    else:
        chosen = [j for j in js if j.tier is Tier.ELIGIBLE and other_ok(j)][:n_eligible] + \
                 [j for j in js if j.tier is Tier.NEEDS_INFO][:n_needs]
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
        tier = ELIGIBLE_WITH_CAVEAT if j.tier is Tier.ELIGIBLE and j.caveats else j.tier.value
        facts.append(Fact(f"S{i}", s.id, s.title, tier, lines, s.detail_url))
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
- '적합'만 "받을 수 있어요"라고 쓴다.
  '적합(제외 조건 확인)'은 "○○에 해당하지 않으면 받을 수 있어요"처럼 제외 조건을 붙여 쓴다.
  '확인 필요'는 받을 수 있다고 쓰지 말고, '조건 모름' 항목을 확인해야 한다고 쓴다.
- 신청기한에 '신청 기간 아님'이 있으면 "지금은 신청 기간이 아니에요"라고 알려준다.
- 같은 사업은 한 번만 쓴다.
- 순서: ① 한두 문장 요약 ② 받을 수 있는 사업 ③ 확인이 필요한 사업 ④ 되물을 질문이 있으면 마지막 문장으로 묻는다.
- [질문], [근거], [되물을 질문] 같은 대괄호 제목은 답변에 쓰지 않는다. 전체 10~15줄 이내, 존댓말."""


def answer_messages(question: str, facts: list[Fact], ask: str) -> list[dict]:
    body = "\n\n".join(f.block() for f in facts) or "(조건에 맞는 사업을 찾지 못함)"
    user = f"[질문]\n{question}\n\n[근거]\n{body}"
    if ask:
        user += f"\n\n[되물을 질문] (답변 마지막 문장으로, 제목 없이)\n{ask}"
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def template_answer(facts: list[Fact], ask: str) -> str:
    el = [f for f in facts if f.tier in (Tier.ELIGIBLE.value, ELIGIBLE_WITH_CAVEAT)]
    ni = [f for f in facts if f.tier == Tier.NEEDS_INFO.value]
    out = [f"조건에 맞는 사업 {len(el)}개, 확인이 더 필요한 사업 {len(ni)}개를 찾았어요."]
    if el:
        out.append("받을 수 있는 사업")
        for f in el:
            ex = [x.split(": ", 1)[1] for x in f.lines if x.startswith("다음에 해당하면 제외")]
            cond = f" (단, {ex[0]}에 해당하면 제외)" if ex else ""
            out.append(f"- 「{f.title}」{cond} [{f.key}]")
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
    # 확인이 필요한 사업을 조건 없이 '받을 수 있다'고 단정했는가
    for sent in re.split(r"(?<=[.!?。])\s+|\n", text):
        if "받을 수 있" not in sent or any(w in sent for w in ("확인", "않으면", "라면", "이면", "시면", "으면", "경우")):
            continue
        for k in _CITE.findall(sent):
            if k in by_key and by_key[k].tier != Tier.ELIGIBLE.value:
                warnings.append(f"조건부 사업을 받을 수 있다고 단정 [{k}]")
    hay = _norm_num(" ".join([question] + [f.title for f in facts] + [x for f in facts for x in f.lines]))
    hay_ns = hay.replace(" ", "")
    for num, unit in _NUM.findall(text):
        n = _norm_num(num)
        unit_ns = unit.replace(" ", "")
        if f"{n}{unit_ns}" in hay_ns or (n in hay and unit_ns in ("세", "%", "월", "일", "년", "개월", "회", "명")):
            continue
        warnings.append(f"근거에 없는 숫자 '{num}{unit}'")
    return cited, warnings


_LIST_LINE = re.compile(r"^\s*(?:[-•*·]|\d+[.)])\s*")


def clean_answer(text: str) -> str:
    """<think>·제목 태그를 지우고, 앞에서 이미 나온 사업만 다시 나열하는 목록 줄을 지운다 (섹션 간 중복)."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    text = re.sub(r"^\s*\[(?:질문|근거|되물을? 질문)\][^\n]*\n?", "", text, flags=re.M)
    text = re.sub(r"\[(?:되물을? 질문)\]\s*", "", text)
    seen, out = set(), []
    for line in text.splitlines():
        titles = [t.strip() for t in _TITLE.findall(line)]
        if titles and _LIST_LINE.match(line) and all(t in seen for t in titles):
            continue
        seen.update(titles)
        out.append(line)
    return "\n".join(out).strip()


RETRY = """위 답변을 근거 검사기로 확인했더니 다음 문제가 있다.
{problems}
[근거]에 있는 것만 써서 이 문제를 고친 답변을 다시 써라. 설명 없이 답변만 쓴다."""


class AnswerGenerator:
    def __init__(self, llm: LLMClient | None, max_tokens: int = 900, retry: bool = True):
        self.llm, self.max_tokens, self.retry = llm, max_tokens, retry

    def generate(self, question: str, facts: list[Fact], ask: str = "") -> Answer:
        if self.llm is None:
            text = template_answer(facts, ask)
            cited, warnings = verify(text, facts, question)
            return Answer(text, facts, cited, warnings, "template", list(warnings))
        msgs = answer_messages(question, facts, ask)
        text = clean_answer(self.llm.chat(msgs, max_tokens=self.max_tokens))
        cited, warnings = verify(text, facts, question)
        first, mode = list(warnings), "llm"
        if warnings and self.retry:          # 경고가 있으면 문제를 알려주고 한 번 다시 쓰게 한다
            fix = msgs + [{"role": "assistant", "content": text},
                          {"role": "user", "content": RETRY.format(problems="\n".join("- " + w for w in warnings))}]
            text2 = clean_answer(self.llm.chat(fix, max_tokens=self.max_tokens))
            cited2, warnings2 = verify(text2, facts, question)
            if len(warnings2) < len(warnings):
                text, cited, warnings, mode = text2, cited2, warnings2, "llm-retry"
        return Answer(text, facts, cited, warnings, mode, first)


def answer_consultation(c, consultation, llm: LLMClient | None, **kw) -> Answer:
    """pipeline.Consultation → 답변. 되물을 질문은 질문과 관련된 사업 기준."""
    u = consultation.understanding
    rel = [j for j in consultation.ranked if c.relevant(j, u.extraction)]
    ask = follow_up(c.follow_up_items(consultation))
    facts = kg_facts(c.kg, consultation.ranked, {j.service.iri for j in rel},
                     other_ok=lambda j: c.specific(j) > 0, **kw)
    return AnswerGenerator(llm).generate(u.text, facts, ask)


__all__ = ["ELIGIBLE_WITH_CAVEAT", "Answer", "AnswerGenerator", "Fact", "answer_consultation", "follow_up", "kg_facts",
           "template_answer", "verify"]
