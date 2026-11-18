"""테스트용 가짜 LLM. HTTP 대신 handler(path, body) 가 응답을 만든다 (캐시·파싱·재시도 로직은 진짜 것을 탄다)."""
import re

from judge.llm import LLMClient


def chat_reply(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


class FakeLLM(LLMClient):
    def __init__(self, handler, **kw):
        kw.setdefault("cache_mode", "off")
        super().__init__(base_url="http://fake/v1", model="fake", **kw)
        self.handler = handler
        self.requests = []

    def _post(self, path, body):
        self.requests.append((path, body))
        return self.handler(path, body)


def parse_verify_prompt(prompt: str):
    """개념 확인 프롬프트에서 (표현, 긍정 여부, [(번호, 라벨)]) 를 꺼낸다."""
    phrase = re.search(r'알려진 사실: "(.+?)"', prompt).group(1)
    positive = "해당하지 **않는다**" not in prompt
    cands = [(int(n), lab) for n, lab in re.findall(r"^(\d+)\. ([^(\n]+?)(?: \(.*\))?$", prompt, re.M)]
    return phrase, positive, cands


def universal_handler(extraction: dict, synonyms: dict | None = None, rag_verdict=None, answer=None, log=None):
    """프롬프트 종류(추출 · 개념 확인 · RAG 판정 · 답변)를 보고 응답한다.

    rag_verdict(doc_text) -> (verdict, reason), answer(user_content) -> str
    """
    import json as _json
    synonyms = synonyms or {}

    def handler(path, body):
        msgs = body["messages"]
        first, last = msgs[0]["content"], msgs[-1]["content"]
        if msgs[0]["role"] == "system" and "프로필 추출기" in first:
            kind, out = "extract", _json.dumps(extraction, ensure_ascii=False)
        elif msgs[0]["role"] == "system" and "상담원" in first:
            kind, out = "answer", (answer(last) if answer else "답변입니다.")
        elif "알려진 사실:" in last:
            phrase, positive, cands = parse_verify_prompt(last)
            want = synonyms.get(phrase, set()) if positive else set()
            kind, out = "verify", _json.dumps({"selected": [n for n, lab in cands if lab.strip() in want]})
        elif "[서비스]" in last:
            docs = re.findall(r"\[D(\d+)\]\n(.*?)(?=\n\n\[D\d+\]|\n\nJSON)", last, re.S)
            res = []
            for n, text in docs:
                v, r = rag_verdict(text) if rag_verdict else ("확인 필요", "정보 부족")
                if v is not None:
                    res.append({"id": f"D{n}", "verdict": v, "reason": r})
            kind, out = "rag", _json.dumps({"results": res}, ensure_ascii=False)
        else:
            raise AssertionError("알 수 없는 프롬프트: " + last[:80])
        if log is not None:
            log.append(kind)
        return chat_reply(out)

    return handler
