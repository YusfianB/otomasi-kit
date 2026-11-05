"""LLM wrapper tests with a mocked Anthropic client (no network, no API key)."""

from __future__ import annotations

import anthropic
import pytest

from konten.common import FatalError, KontenError
from konten.llm import (
    DEFAULT_MODEL,
    LLMClient,
    api_schema,
    obj,
    resolve_effort,
    resolve_model,
    str_list,
    validate_schema,
)
from konten.tests.fake_anthropic import FakeClient, api_error, message

SCHEMA = obj({"judul": {"type": "string"}, "tag": str_list(2)})
GOOD = {"judul": "Kopi Senja", "tag": ["kopi", "senja"]}


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("konten.llm.time.sleep", lambda s: None)


def make(responder, **kw) -> tuple[LLMClient, FakeClient]:
    fake = FakeClient(responder)
    kw.setdefault("backoff_base", 0)
    return LLMClient(client=fake, **kw), fake


def test_default_model_and_env_override(monkeypatch):
    monkeypatch.delenv("OTOMASI_MODEL", raising=False)
    assert resolve_model(None) == DEFAULT_MODEL == "claude-opus-5-5"
    monkeypatch.setenv("OTOMASI_MODEL", "claude-sonnet-5-5")
    assert resolve_model(None) == "claude-sonnet-5-5"
    assert resolve_model("claude-haiku-5-5") == "claude-haiku-5-5"  # CLI wins over env


def test_effort_validation(monkeypatch):
    monkeypatch.delenv("OTOMASI_EFFORT", raising=False)
    assert resolve_effort(None) == "medium"
    monkeypatch.setenv("OTOMASI_EFFORT", "low")
    assert resolve_effort(None) == "low"
    with pytest.raises(KontenError):
        resolve_effort("turbo")


def test_request_shape_structured_output_cache_and_fallback():
    llm, fake = make(lambda p, n: message(GOOD))
    assert llm.generate_json(task_system="TUGAS", prompt="halo", schema=SCHEMA) == GOOD
    params = fake.calls[0]
    assert params["model"] == "claude-opus-5-5"
    # Shared system prompt first, cache breakpoint on the last system block.
    assert len(params["system"]) == 2
    assert "cache_control" not in params["system"][0]
    assert params["system"][1]["cache_control"] == {"type": "ephemeral"}
    assert params["system"][1]["text"] == "TUGAS"
    fmt = params["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert "x-minItems" not in str(fmt["schema"])  # local-only keys are stripped
    assert params["output_config"]["effort"] == "medium"
    assert params["fallbacks"] == "default"
    assert params["betas"] == ["server-side-fallback-2026-07-01"]
    assert "thinking" not in params  # Opus 5.5: adaptive thinking is the default
    assert "temperature" not in params


def test_no_fallback_param_for_haiku():
    llm, fake = make(lambda p, n: message(GOOD), model="claude-haiku-5-5")
    llm.generate_json(task_system="t", prompt="p", schema=SCHEMA)
    assert "fallbacks" not in fake.calls[0]
    assert "betas" not in fake.calls[0]


def test_invalid_json_is_retried_with_feedback():
    replies = [message("ini bukan json"), message(GOOD)]
    llm, fake = make(lambda p, n: replies[n - 1])
    assert llm.generate_json(task_system="t", prompt="p", schema=SCHEMA) == GOOD
    assert len(fake.calls) == 2
    second = fake.calls[1]["messages"][0]["content"]
    assert "not valid" in second[-1]["text"]


def test_schema_violation_is_retried():
    replies = [message({"judul": "x", "tag": ["satu"]}), message(GOOD)]  # tag needs 2 items
    llm, fake = make(lambda p, n: replies[n - 1])
    assert llm.generate_json(task_system="t", prompt="p", schema=SCHEMA) == GOOD
    assert len(fake.calls) == 2


def test_check_problems_retry_then_accept_last():
    llm, fake = make(lambda p, n: message({"judul": "x" * 80, "tag": ["a", "b"]}))
    data = llm.generate_json(task_system="t", prompt="p", schema=SCHEMA,
                             check=lambda d: ["judul terlalu panjang"] if len(d["judul"]) > 60
                             else [])
    assert len(fake.calls) == 3  # max_attempts
    assert data["judul"] == "x" * 80  # caller enforces limits afterwards
    assert "judul terlalu panjang" in fake.calls[1]["messages"][0]["content"][-1]["text"]


def test_rate_limit_is_retried():
    replies = [api_error(anthropic.RateLimitError, 429), message(GOOD)]
    llm, fake = make(lambda p, n: replies[n - 1])
    assert llm.generate_json(task_system="t", prompt="p", schema=SCHEMA) == GOOD
    assert len(fake.calls) == 2


def test_rate_limit_exhausted_gives_indonesian_message():
    llm, _ = make(lambda p, n: api_error(anthropic.RateLimitError, 429))
    with pytest.raises(KontenError, match="rate limit"):
        llm.generate_json(task_system="t", prompt="p", schema=SCHEMA)


def test_server_overloaded_is_retried():
    replies = [api_error(anthropic.InternalServerError, 529), message(GOOD)]
    llm, _ = make(lambda p, n: replies[n - 1])
    assert llm.generate_json(task_system="t", prompt="p", schema=SCHEMA) == GOOD


def test_auth_error_is_fatal():
    llm, fake = make(lambda p, n: api_error(anthropic.AuthenticationError, 401))
    with pytest.raises(FatalError, match="API key"):
        llm.generate_json(task_system="t", prompt="p", schema=SCHEMA)
    assert len(fake.calls) == 1  # never retried


def test_unknown_model_is_fatal():
    llm, _ = make(lambda p, n: api_error(anthropic.NotFoundError, 404), model="claude-xyz")
    with pytest.raises(FatalError, match="claude-xyz"):
        llm.generate_json(task_system="t", prompt="p", schema=SCHEMA)


def test_refusal_is_reported():
    llm, _ = make(lambda p, n: message("", stop_reason="refusal", category="cyber"))
    with pytest.raises(KontenError, match="menolak"):
        llm.generate_json(task_system="t", prompt="p", schema=SCHEMA)


def test_truncated_output_is_retried():
    replies = [message('{"judul": "pot', stop_reason="max_tokens"), message(GOOD)]
    llm, fake = make(lambda p, n: replies[n - 1])
    assert llm.generate_json(task_system="t", prompt="p", schema=SCHEMA) == GOOD
    assert len(fake.calls) == 2


def test_usage_summary_includes_cache_and_cost():
    llm, _ = make(lambda p, n: message(GOOD))
    llm.generate_json(task_system="t", prompt="p", schema=SCHEMA)
    summary = llm.usage_summary()
    assert "cache baca 1,000" in summary
    assert "Perkiraan biaya" in summary


def test_missing_key_message(monkeypatch):
    from konten.llm import make_anthropic_client

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    with pytest.raises(FatalError, match="ANTHROPIC_API_KEY belum diset"):
        make_anthropic_client()


def test_validate_schema_and_api_schema():
    assert validate_schema(GOOD, SCHEMA) == []
    errors = validate_schema({"judul": 3, "tag": []}, SCHEMA)
    assert any("judul" in e for e in errors) and any("minimal" in e for e in errors)
    assert validate_schema({"judul": " ", "tag": ["a", "b"]}, SCHEMA)  # empty string
    assert validate_schema(True, {"type": "integer"})  # bool is not an int here
    cleaned = api_schema(SCHEMA)
    assert cleaned["properties"]["tag"] == {"type": "array", "items": {"type": "string"}}
    assert cleaned["additionalProperties"] is False


# --------------------------------------------------------------------------- real SDK, mock HTTP


def _sdk_client(handler):
    import httpx2

    return anthropic.Anthropic(
        api_key="sk-ant-test", max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))


def _sse(text: str) -> bytes:
    import json

    events = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 1, "cache_read_input_tokens": 600,
                      "cache_creation_input_tokens": 0}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": text}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn",
                                                              "stop_sequence": None},
                           "usage": {"output_tokens": 20}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events).encode()


def test_real_sdk_request_and_stream_parsing():
    """The installed anthropic SDK accepts our params and we parse its streamed message."""
    import json

    import httpx2

    captured = {}

    def handler(request):
        captured["beta"] = request.headers.get("anthropic-beta")
        captured["body"] = json.loads(request.content)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                               content=_sse(json.dumps(GOOD)))

    llm = LLMClient(client=_sdk_client(handler))
    assert llm.generate_json(task_system="TUGAS", prompt="halo", schema=SCHEMA) == GOOD
    body = captured["body"]
    assert captured["beta"] == "server-side-fallback-2026-07-01"
    assert body["stream"] is True and body["fallbacks"] == "default"
    assert body["output_config"]["format"]["schema"]["additionalProperties"] is False
    assert body["system"][1]["cache_control"] == {"type": "ephemeral"}
    assert llm.usage.cache_read == 600 and llm.usage.output_tokens == 20


def test_real_sdk_auth_error_is_mapped():
    import httpx2

    def handler(request):
        return httpx2.Response(401, json={"type": "error", "error": {
            "type": "authentication_error", "message": "invalid x-api-key"}})

    llm = LLMClient(client=_sdk_client(handler))
    with pytest.raises(FatalError, match="tidak valid"):
        llm.generate_json(task_system="t", prompt="p", schema=SCHEMA)
