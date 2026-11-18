"""LLM 클라이언트: JSON 파싱, 캐시, 자유→문법 강제 재시도, 실제 HTTP 전송 형식."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from judge.llm import CacheMiss, LLMClient, parse_json_object
from tests.fake_llm import FakeLLM, chat_reply


@pytest.mark.parametrize("text", [
    '{"a": 1}',
    '<think>음...</think>\n{"a": 1}',
    '```json\n{"a": 1}\n```',
    '결과는 다음과 같습니다.\n{"a": 1}\n이상입니다.',
    '{"a": 1} {"b": 2}',
])
def test_parse_json_object(text):
    assert parse_json_object(text) == {"a": 1}


@pytest.mark.parametrize("text", ["", "없음", "[1, 2]", '{"a": '])
def test_parse_json_object_fails(text):
    with pytest.raises(ValueError):
        parse_json_object(text)


def test_free_then_guided_retry():
    replies = iter(['{"selected": "첫번째"}', '{"selected": [1]}'])
    llm = FakeLLM(lambda p, b: chat_reply(next(replies)))

    def check(o):
        if not isinstance(o["selected"], list):
            raise TypeError
        return o

    obj, mode = llm.chat_json([{"role": "user", "content": "x"}], {"type": "object"}, validate=check)
    assert obj == {"selected": [1]} and mode == "guided"
    first, second = llm.requests[0][1], llm.requests[1][1]
    assert "response_format" not in first
    assert second["response_format"]["type"] == "json_schema"
    assert first["chat_template_kwargs"] == {"enable_thinking": False}


def test_cache_use_only_off(tmp_path):
    path = tmp_path / "c.jsonl"
    llm = FakeLLM(lambda p, b: chat_reply("안녕"), cache_path=path, cache_mode="use")
    msgs = [{"role": "user", "content": "hi"}]
    assert llm.chat(msgs) == "안녕" and llm.chat(msgs) == "안녕"
    assert llm.calls == 1 and llm.cache_hits == 1
    replay = FakeLLM(lambda p, b: pytest.fail("호출되면 안 됨"), cache_path=path, cache_mode="only")
    assert replay.chat(msgs) == "안녕"
    with pytest.raises(CacheMiss):
        replay.chat([{"role": "user", "content": "다른 질문"}])
    other_model = FakeLLM(lambda p, b: chat_reply("x"), cache_path=path, cache_mode="only")
    other_model.model = "다른 모델"
    with pytest.raises(CacheMiss):
        other_model.chat(msgs)


class _Handler(BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Handler.seen.append((self.path, body, self.headers.get("Authorization")))
        if self.path.endswith("/chat/completions"):
            out = chat_reply('{"ok": true}')
        else:
            out = {"data": [{"index": i, "embedding": [float(i), 1.0]} for i in range(len(body["input"]))][::-1]}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def test_openai_compatible_http():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        llm = LLMClient(base_url=f"http://127.0.0.1:{srv.server_port}/v1", model="m", api_key="k",
                        cache_mode="off")
        obj, mode = llm.chat_json([{"role": "user", "content": "q"}], {"type": "object"})
        assert obj == {"ok": True} and mode == "free"
        assert llm.embed(["a", "b"], model="e") == [[0.0, 1.0], [1.0, 1.0]]   # index 순서로 정렬
        (p1, b1, auth), (p2, b2, _) = _Handler.seen[-2:]
        assert p1 == "/v1/chat/completions" and b1["model"] == "m" and b1["temperature"] == 0.0
        assert auth == "Bearer k"
        assert p2 == "/v1/embeddings" and b2 == {"model": "e", "input": ["a", "b"]}
    finally:
        srv.shutdown()


def test_connection_error_is_llm_error():
    from judge.llm import LLMError
    llm = LLMClient(base_url="http://127.0.0.1:9/v1", cache_mode="off", timeout=2)
    with pytest.raises(LLMError):
        llm.chat([{"role": "user", "content": "q"}])
