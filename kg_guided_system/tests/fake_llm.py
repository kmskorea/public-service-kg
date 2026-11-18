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
