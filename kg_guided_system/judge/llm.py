"""OpenAI 호환 LLM 클라이언트 (vLLM · 외부 API 공용) + 호출 캐시.

환경 변수
  LLM_BASE_URL   기본 http://localhost:8000/v1   (연구실 vLLM: `vllm serve Qwen/Qwen3-32B --port 8000`)
  LLM_MODEL      기본 Qwen/Qwen3-32B
  LLM_API_KEY    외부 API 를 쓸 때만
  LLM_CACHE      기본 kg_guided_system/cache/llm_cache.jsonl
  LLM_CACHE_MODE use (기본: 있으면 재사용, 없으면 호출) | only (캐시만, GPU 없이 재현·시연) | off

JSON 출력은 지난 과제와 같은 방식이다: 1차 자유 생성 → JSON 파싱·검사, 실패할 때만 2차 스키마 강제 생성
(response_format=json_schema, vLLM 은 이것을 문법 강제 디코딩으로 처리한다).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Callable

DEFAULT_CACHE = Path(__file__).resolve().parent.parent / "cache" / "llm_cache.jsonl"


class LLMError(RuntimeError):
    pass


class CacheMiss(LLMError):
    pass


class JsonCache:
    """요청 해시 → 응답 문자열. jsonl 에 한 줄씩 덧붙인다."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict[str, str] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    self.data[row["key"]] = row["value"]

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def put(self, key: str, value: str, meta: dict):
        self.data[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"key": key, "value": value, **meta}, ensure_ascii=False) + "\n")


class LLMClient:
    def __init__(self, base_url: str | None = None, model: str | None = None, api_key: str | None = None,
                 cache_path: str | Path | None = None, cache_mode: str | None = None, timeout: float = 120,
                 disable_thinking: bool = True):
        self.base_url = (base_url or os.environ.get("LLM_BASE_URL") or "http://localhost:8000/v1").rstrip("/")
        self.model = model or os.environ.get("LLM_MODEL") or "Qwen/Qwen3-32B"
        self.api_key = api_key if api_key is not None else os.environ.get("LLM_API_KEY", "")
        self.cache_mode = cache_mode or os.environ.get("LLM_CACHE_MODE") or "use"
        self.cache = None if self.cache_mode == "off" else JsonCache(
            cache_path or os.environ.get("LLM_CACHE") or DEFAULT_CACHE)
        self.timeout = timeout
        self.disable_thinking = disable_thinking   # Qwen3 의 <think> 생략 (vLLM chat_template_kwargs)
        self.calls = 0                             # 실제 HTTP 호출 수
        self.cache_hits = 0

    # ---------------------------------------------------------------- HTTP
    def _post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(
            self.base_url + path, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001 - 연결 실패를 한 가지 오류로 알린다
            raise LLMError(f"LLM 호출 실패 ({self.base_url}{path}): {e}") from e

    def _cached(self, kind: str, body: dict, call: Callable[[], str]) -> str:
        key = hashlib.sha256(json.dumps([kind, body], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if self.cache is not None:
            hit = self.cache.get(key)
            if hit is not None:
                self.cache_hits += 1
                return hit
            if self.cache_mode == "only":
                raise CacheMiss(f"캐시에 없는 {kind} 요청 (LLM_CACHE_MODE=only)")
        value = call()
        self.calls += 1
        if self.cache is not None:
            self.cache.put(key, value, {"kind": kind, "model": body.get("model")})
        return value

    # ---------------------------------------------------------------- chat
    def chat(self, messages: list[dict], *, temperature: float = 0.0, max_tokens: int = 1024,
             json_schema: dict | None = None) -> str:
        body: dict = {"model": self.model, "messages": messages, "temperature": temperature,
                      "max_tokens": max_tokens}
        if self.disable_thinking:
            body["chat_template_kwargs"] = {"enable_thinking": False}
        if json_schema is not None:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "output", "schema": json_schema}}

        def call() -> str:
            out = self._post("/chat/completions", body)
            try:
                return out["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError) as e:
                raise LLMError(f"응답 형식이 이상함: {str(out)[:200]}") from e

        return self._cached("chat", body, call)

    def chat_json(self, messages: list[dict], schema: dict,
                  validate: Callable[[dict], dict] | None = None, **kw) -> tuple[dict, str]:
        """JSON 객체를 받는다. 반환: (객체, 'free' | 'guided')."""
        text = self.chat(messages, **kw)
        try:
            obj = parse_json_object(text)
            return (validate(obj) if validate else obj), "free"
        except (ValueError, TypeError):
            pass
        text = self.chat(messages, json_schema=schema, **kw)
        obj = parse_json_object(text)          # 여기서도 실패하면 ValueError 를 그대로 올린다
        return (validate(obj) if validate else obj), "guided"

    # ---------------------------------------------------------------- embeddings
    def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        model = model or os.environ.get("EMBED_MODEL") or "BAAI/bge-m3"
        body = {"model": model, "input": texts}

        def call() -> str:
            out = self._post("/embeddings", body)
            rows = sorted(out["data"], key=lambda d: d["index"])
            return json.dumps([r["embedding"] for r in rows])

        return json.loads(self._cached("embed", body, call))


_THINK = re.compile(r"<think>.*?</think>", re.S)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_json_object(text: str) -> dict:
    """모델 출력에서 첫 JSON 객체를 꺼낸다 (<think>, 코드 펜스, 앞뒤 설명문 허용)."""
    text = _THINK.sub("", text or "").strip()
    m = _FENCE.search(text)
    if m:
        text = m.group(1).strip()
    start = text.find("{")
    if start < 0:
        raise ValueError("JSON 객체가 없음")
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(obj, dict):
        raise ValueError("JSON 객체가 아님")
    return obj
