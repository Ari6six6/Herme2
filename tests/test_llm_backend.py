"""OpenAIBackend talks the chat-completions wire protocol over httpx directly
(no `openai` SDK — its jiter dependency won't build on Termux). These tests
pin the request shaping, response parsing, and retry-on-5xx behaviour using
httpx's MockTransport, so no network or GPU is involved.
"""

import httpx
import pytest

from hermes.llm import LLMTransportError, OpenAIBackend


def make_backend(handler, cfg=None, monkeypatch=None):
    base = {
        "base_url": "http://127.0.0.1:8000/v1",
        "api_key": "hermes",
        "model": "test-model",
        "sampling": {"temperature": 0.6, "top_p": 0.95, "top_k": 20},
        "max_completion_tokens": 8192,
    }
    base.update(cfg or {})

    class DictCfg:
        def get(self, key, default=None):
            return base.get(key, default)

    backend = OpenAIBackend(DictCfg())
    backend.client = httpx.Client(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": f"Bearer {base['api_key']}"},
    )
    return backend


def _message(content=None, tool_calls=None):
    return {
        "choices": [{"message": {"content": content, "tool_calls": tool_calls}}]
    }


def test_plain_text_response():
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = __import__("json").loads(request.content)
        return httpx.Response(200, json=_message(content="hello there"))

    backend = make_backend(handler)
    result = backend.chat([{"role": "user", "content": "hi"}])

    assert result.content == "hello there"
    assert result.tool_calls == []
    # endpoint is base_url + /chat/completions, no double slash
    assert captured["url"] == "http://127.0.0.1:8000/v1/chat/completions"
    assert captured["auth"] == "Bearer hermes"
    # sampling knobs land in the body, top_k included at top level
    body = captured["body"]
    assert body["model"] == "test-model"
    assert body["temperature"] == 0.6
    assert body["top_p"] == 0.95
    assert body["top_k"] == 20
    assert body["max_tokens"] == 8192
    assert "tools" not in body and "tool_choice" not in body


def test_tool_calls_parsed():
    def handler(request):
        return httpx.Response(
            200,
            json=_message(
                tool_calls=[
                    {
                        "id": "call_1",
                        "function": {"name": "write_file", "arguments": '{"x": 1}'},
                    }
                ]
            ),
        )

    backend = make_backend(handler)
    result = backend.chat([{"role": "user", "content": "go"}])

    assert result.content is None
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call.id == "call_1"
    assert call.name == "write_file"
    assert call.arguments == '{"x": 1}'


def test_missing_arguments_defaults_to_empty_object():
    def handler(request):
        return httpx.Response(
            200,
            json=_message(
                tool_calls=[{"id": "c", "function": {"name": "finish_run"}}]
            ),
        )

    backend = make_backend(handler)
    result = backend.chat([{"role": "user", "content": "go"}])
    assert result.tool_calls[0].arguments == "{}"


def test_tools_and_tool_choice_forwarded():
    captured = {}

    def handler(request):
        captured["body"] = __import__("json").loads(request.content)
        return httpx.Response(200, json=_message(content="ok"))

    backend = make_backend(handler)
    tools = [{"type": "function", "function": {"name": "t"}}]
    choice = {"type": "function", "function": {"name": "t"}}
    backend.chat([{"role": "user", "content": "go"}], tools=tools, tool_choice=choice)

    assert captured["body"]["tools"] == tools
    assert captured["body"]["tool_choice"] == choice


def test_retries_on_5xx_then_succeeds(monkeypatch):
    monkeypatch.setattr("hermes.llm.time.sleep", lambda _s: None)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="overloaded")
        return httpx.Response(200, json=_message(content="recovered"))

    backend = make_backend(handler)
    result = backend.chat([{"role": "user", "content": "go"}])
    assert result.content == "recovered"
    assert calls["n"] == 2


def test_transport_error_raises_after_retries(monkeypatch):
    monkeypatch.setattr("hermes.llm.time.sleep", lambda _s: None)

    def handler(request):
        raise httpx.ConnectError("tunnel down")

    backend = make_backend(handler)
    with pytest.raises(LLMTransportError) as exc:
        backend.chat([{"role": "user", "content": "go"}])
    assert "vLLM unreachable" in str(exc.value)


def test_4xx_raises_immediately_with_body(monkeypatch):
    monkeypatch.setattr("hermes.llm.time.sleep", lambda _s: None)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(400, text="context length exceeded")

    backend = make_backend(handler)
    with pytest.raises(LLMTransportError) as exc:
        backend.chat([{"role": "user", "content": "go"}])
    assert "HTTP 400" in str(exc.value)
    assert "context length exceeded" in str(exc.value)
    # client errors aren't retried — a bad request won't fix itself
    assert calls["n"] == 1


def test_persistent_5xx_raises(monkeypatch):
    monkeypatch.setattr("hermes.llm.time.sleep", lambda _s: None)

    def handler(request):
        return httpx.Response(500, text="boom")

    backend = make_backend(handler)
    with pytest.raises(LLMTransportError) as exc:
        backend.chat([{"role": "user", "content": "go"}])
    assert "HTTP 500" in str(exc.value)


def test_empty_choices_raises_clean_error():
    # A 2xx with no choices must not crash with a raw IndexError — it should
    # surface as a clean LLMTransportError like any other backend failure.
    def handler(request):
        return httpx.Response(200, json={"choices": []})

    backend = make_backend(handler)
    with pytest.raises(LLMTransportError) as exc:
        backend.chat([{"role": "user", "content": "go"}])
    assert "unexpected response shape" in str(exc.value)


def test_non_json_2xx_raises_clean_error():
    def handler(request):
        return httpx.Response(200, text="<html>gateway</html>")

    backend = make_backend(handler)
    with pytest.raises(LLMTransportError) as exc:
        backend.chat([{"role": "user", "content": "go"}])
    assert "unexpected response shape" in str(exc.value)
