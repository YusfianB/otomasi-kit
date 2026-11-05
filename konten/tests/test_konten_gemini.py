"""Gemini backend tests with a fake ``requests`` session (no network)."""

from __future__ import annotations

import json

import pytest

from konten import cli
from konten.common import FatalError, KontenError
from konten.gemini import GeminiClient
from konten.llm import build_llm, obj, resolve_provider, str_list

SCHEMA = obj({"judul": {"type": "string"}, "tags": str_list(2)})


class FakeResponse:
    def __init__(self, status: int, payload: dict, headers: dict | None = None) -> None:
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return self.responses.pop(0)


def ok(data: dict, finish: str = "STOP") -> FakeResponse:
    return FakeResponse(200, {
        "candidates": [{"content": {"parts": [{"text": json.dumps(data)}]},
                        "finishReason": finish}],
        "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 30,
                          "thoughtsTokenCount": 5},
    })


def err(status: int, message: str = "boom", reason: str = "") -> FakeResponse:
    details = [{"reason": reason}] if reason else []
    return FakeResponse(status, {"error": {"code": status, "message": message,
                                           "status": "X", "details": details}})


def client(session: FakeSession, endpoint: str = "aistudio") -> GeminiClient:
    return GeminiClient("gemini-test", session=session, api_key="test-key", endpoint=endpoint,
                        backoff_base=0)


GOOD = {"judul": "Kopi Susu Gula Aren", "tags": ["kopi", "kopisusu"]}


def test_request_shape_and_usage():
    s = FakeSession([ok(GOOD)])
    c = client(s)
    out = c.generate_json(task_system="Tugas uji", prompt="Buat judul", schema=SCHEMA)
    assert out == GOOD
    call = s.calls[0]
    assert call["url"].startswith("https://generativelanguage.googleapis.com/v1beta/models/"
                                  "gemini-test:generateContent")
    assert call["headers"]["x-goog-api-key"] == "test-key"
    body = call["json"]
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    sys_text = body["systemInstruction"]["parts"][0]["text"]
    assert "Tugas uji" in sys_text and '"judul"' in sys_text and "x-minItems" not in sys_text
    assert body["contents"][0]["parts"] == [{"text": "Buat judul"}]
    assert c.usage.requests == 1 and c.usage.input_tokens == 120 and c.usage.output_tokens == 35
    assert "Gemini" in c.usage_summary()


def test_block_prompt_is_flattened_to_parts():
    s = FakeSession([ok(GOOD)])
    blocks = [{"type": "text", "text": "<materi>x</materi>", "cache_control": {"type": "ephemeral"}},
              {"type": "text", "text": "instruksi"}]
    client(s).generate_json(task_system="t", prompt=blocks, schema=SCHEMA)
    assert s.calls[0]["json"]["contents"][0]["parts"] == [{"text": "<materi>x</materi>"},
                                                          {"text": "instruksi"}]


def test_invalid_json_is_retried_with_feedback():
    bad = FakeResponse(200, {"candidates": [{"content": {"parts": [{"text": "bukan json"}]},
                                             "finishReason": "STOP"}]})
    s = FakeSession([bad, ok(GOOD)])
    assert client(s).generate_json(task_system="t", prompt="p", schema=SCHEMA) == GOOD
    assert len(s.calls) == 2
    assert "not valid" in s.calls[1]["json"]["contents"][0]["parts"][-1]["text"]


def test_schema_violation_is_retried():
    s = FakeSession([ok({"judul": "x", "tags": ["satu"]}), ok(GOOD)])
    assert client(s).generate_json(task_system="t", prompt="p", schema=SCHEMA) == GOOD


def test_rate_limit_then_success(monkeypatch):
    monkeypatch.setattr("konten.llm.time.sleep", lambda _s: None)
    s = FakeSession([err(429, "quota"), ok(GOOD)])
    assert client(s).generate_json(task_system="t", prompt="p", schema=SCHEMA) == GOOD


def test_server_error_exhausts_attempts(monkeypatch):
    monkeypatch.setattr("konten.llm.time.sleep", lambda _s: None)
    s = FakeSession([err(503)] * 3)
    with pytest.raises(KontenError, match="Server Gemini"):
        client(s).generate_json(task_system="t", prompt="p", schema=SCHEMA)


def test_bad_key_is_fatal():
    s = FakeSession([err(400, "API key not valid", "API_KEY_INVALID")])
    with pytest.raises(FatalError, match="API key Gemini ditolak"):
        client(s).generate_json(task_system="t", prompt="p", schema=SCHEMA)


def test_auto_endpoint_falls_back_to_vertex_and_remembers():
    s = FakeSession([err(401, "unauthenticated"), ok(GOOD), ok(GOOD)])
    c = client(s, endpoint="auto")
    c.generate_json(task_system="t", prompt="p", schema=SCHEMA)
    c.generate_json(task_system="t", prompt="p", schema=SCHEMA)
    urls = [call["url"] for call in s.calls]
    assert urls[0].startswith("https://generativelanguage.googleapis.com/")
    assert urls[1].startswith("https://aiplatform.googleapis.com/v1/publishers/google/models/")
    assert urls[2].startswith("https://aiplatform.googleapis.com/")
    assert c.endpoint == "vertex"


def test_unknown_model_is_fatal():
    s = FakeSession([err(404, "not found")])
    with pytest.raises(FatalError, match="tidak ditemukan"):
        client(s).generate_json(task_system="t", prompt="p", schema=SCHEMA)


def test_safety_block_raises():
    s = FakeSession([FakeResponse(200, {"promptFeedback": {"blockReason": "SAFETY"}})])
    with pytest.raises(KontenError, match="menolak"):
        client(s).generate_json(task_system="t", prompt="p", schema=SCHEMA)


def test_truncated_output_is_retried():
    s = FakeSession([ok(GOOD, finish="MAX_TOKENS"), ok(GOOD)])
    assert client(s).generate_json(task_system="t", prompt="p", schema=SCHEMA) == GOOD
    assert len(s.calls) == 2


def test_thought_parts_are_ignored():
    resp = FakeResponse(200, {"candidates": [{"content": {"parts": [
        {"text": "berpikir...", "thought": True}, {"text": json.dumps(GOOD)}]},
        "finishReason": "STOP"}]})
    assert client(FakeSession([resp])).generate_json(task_system="t", prompt="p",
                                                     schema=SCHEMA) == GOOD


def test_provider_resolution(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert resolve_provider() is None
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    assert resolve_provider() == "gemini"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    assert resolve_provider() == "anthropic"
    assert resolve_provider("gemini") == "gemini"
    monkeypatch.setenv("OTOMASI_PROVIDER", "gemini")
    assert resolve_provider() == "gemini"
    with pytest.raises(KontenError):
        resolve_provider("openai")


def test_build_llm_gemini_defaults(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("OTOMASI_MODEL", "claude-opus-5-5")  # ignored for Gemini
    llm = build_llm(None, None, client=FakeSession([]))
    assert isinstance(llm, GeminiClient) and llm.model == "gemini-flash-latest"
    monkeypatch.setenv("GEMINI_ENDPOINT", "vertex")
    assert build_llm(None, None, client=FakeSession([])).model == "gemini-2.5-flash"
    assert build_llm("gemini-pro-latest", None, client=FakeSession([])).model == \
        "gemini-pro-latest"


def test_cli_sosmed_with_gemini(monkeypatch, tmp_path):
    """End to end: the sosmed command runs on the Gemini backend with a fake session."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "g")

    class EchoSession(FakeSession):
        """Records every request and answers with a server error."""

        def post(self, url, headers=None, json=None, timeout=None):
            self.calls.append({"url": url, "json": json})
            return FakeResponse(500, {"error": {"message": "stop here"}})

    monkeypatch.setattr("konten.llm.time.sleep", lambda _s: None)
    session = EchoSession([])
    code = cli.main(["sosmed", "--brand", "Kopi Senja", "--niche", "kopi", "--jumlah", "1", "--platform", "instagram",
                     "--engine", "ai", "--provider", "gemini", "--concurrency", "1",
                     "--output", str(tmp_path)], client=session)
    # Every request reached the Gemini endpoint; the fake server error makes items fail (exit 2).
    assert session.calls and all("generativelanguage.googleapis.com" in c["url"]
                                 for c in session.calls)
    assert code in (1, 2)


def test_cli_engine_ai_without_any_key(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    code = cli.main(["sosmed", "--niche", "kopi", "--engine", "ai", "--output", str(tmp_path)])
    out = capsys.readouterr()
    assert code == 1 and "GEMINI_API_KEY" in (out.out + out.err)
