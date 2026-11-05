"""Google Gemini backend for the ``ai`` engine (``--provider gemini``).

Uses the REST API directly through ``requests`` (already a dependency), so no extra SDK is
needed. Two endpoints are supported because Google issues two kinds of API keys:

- ``aistudio``: Gemini API from Google AI Studio (https://aistudio.google.com/apikey)
  ``https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent``
- ``vertex``: Vertex AI "express mode" keys from Google Cloud
  ``https://aiplatform.googleapis.com/v1/publishers/google/models/{model}:generateContent``

``GEMINI_ENDPOINT=auto`` (default) tries AI Studio first and, when the key is rejected there,
retries once on Vertex AI (and remembers the endpoint that worked).

JSON output: ``responseMimeType: application/json`` plus the schema written into the
instructions. The reply is validated locally by ``LLMClient.generate_json`` and re-asked with
feedback when invalid, exactly like the Anthropic backend.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any

from konten.common import FatalError, KontenError
from konten.llm import (
    BASE_SYSTEM_PROMPT,
    DEFAULT_EFFORT,
    InvalidOutput,
    LLMClient,
    Usage,
    _Transient,
    api_schema,
)

GEMINI_KEY_ENVS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
ENDPOINTS = ("auto", "aistudio", "vertex")
# "-latest" aliases always point at Google's current Flash model on AI Studio. Vertex AI does not
# offer these aliases, so it needs a concrete model name (override with --model).
DEFAULT_MODELS = {"aistudio": "gemini-flash-latest", "vertex": "gemini-2.5-flash"}

_URLS = {
    "aistudio": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
    "vertex": "https://aiplatform.googleapis.com/v1/publishers/google/models/{model}:generateContent",
}
_REFUSAL_REASONS = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION",
                    "IMAGE_SAFETY"}
_lock = threading.Lock()


def gemini_api_key() -> str | None:
    for name in GEMINI_KEY_ENVS:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def resolve_endpoint() -> str:
    endpoint = os.environ.get("GEMINI_ENDPOINT", "auto").strip().lower() or "auto"
    if endpoint not in ENDPOINTS:
        raise KontenError(
            f"GEMINI_ENDPOINT tidak dikenal: {endpoint} (pilih: {', '.join(ENDPOINTS)})"
        )
    return endpoint


def default_model(endpoint: str) -> str:
    return DEFAULT_MODELS["vertex" if endpoint == "vertex" else "aistudio"]


class _KeyRejected(Exception):
    """The endpoint rejected the API key (may work on the other endpoint)."""


class GeminiClient(LLMClient):
    """Same interface as :class:`LLMClient`, backed by Google Gemini."""

    provider = "gemini"

    def __init__(
        self,
        model: str,
        effort: str = DEFAULT_EFFORT,
        *,
        session: Any = None,
        api_key: str | None = None,
        endpoint: str | None = None,
        max_attempts: int = 3,
        backoff_base: float = 2.0,
        timeout: float = 600.0,
    ) -> None:
        self.model = model
        self.effort = effort  # not sent: Gemini models differ in their thinking controls
        self.max_attempts = max_attempts
        self.backoff_base = backoff_base
        self.timeout = timeout
        self.usage = Usage()
        self._api_key = api_key or gemini_api_key()
        if not self._api_key:
            raise FatalError(
                "GEMINI_API_KEY belum diset. Set dulu, misalnya: export GEMINI_API_KEY=... "
                "atau jalankan dengan --engine template untuk mode offline."
            )
        self.endpoint = endpoint or resolve_endpoint()
        if session is None:
            import requests

            session = requests.Session()
        self._session = session

    def usage_summary(self) -> str:
        u = self.usage
        return (
            f"Pemakaian API Gemini ({self.model}, endpoint {self._active_endpoint()}): "
            f"{u.requests} request, input {u.input_tokens:,} token "
            f"(cache {u.cache_read:,}), output {u.output_tokens:,} token. "
            "Cek biaya di Google AI Studio / Google Cloud Billing."
        )

    # -- internals ----------------------------------------------------------------------

    def _active_endpoint(self) -> str:
        return "aistudio" if self.endpoint == "auto" else self.endpoint

    def _body(
        self, task_system: str, prompt: str | list[dict], schema: dict, feedback: str,
        max_tokens: int,
    ) -> dict:
        parts = [{"text": prompt}] if isinstance(prompt, str) else [
            {"text": block["text"]} for block in prompt if block.get("text")
        ]
        if feedback:
            parts.append({"text": feedback})
        schema_text = json.dumps(api_schema(schema), ensure_ascii=False)
        system = (
            f"{BASE_SYSTEM_PROMPT}\n{task_system}\n\n"
            "Answer with ONE JSON object only (no markdown fences) that validates against this "
            f"JSON Schema; every listed property is required:\n{schema_text}"
        )
        return {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "maxOutputTokens": max_tokens,
            },
        }

    def _request(
        self, task_system: str, prompt: str | list[dict], schema: dict, feedback: str,
        max_tokens: int,
    ) -> str:
        body = self._body(task_system, prompt, schema, feedback, max_tokens)
        if self.endpoint != "auto":
            return self._call(self.endpoint, body)
        try:
            return self._call("aistudio", body)
        except _KeyRejected:
            text = self._call("vertex", body)
            with _lock:
                self.endpoint = "vertex"
            return text

    def _call(self, endpoint: str, body: dict) -> str:
        import requests

        url = _URLS[endpoint].format(model=self.model)
        try:
            resp = self._session.post(
                url,
                headers={"x-goog-api-key": self._api_key, "Content-Type": "application/json"},
                json=body,
                timeout=self.timeout,
            )
        except requests.Timeout as exc:
            raise _Transient("Permintaan ke API Gemini timeout.", None) from exc
        except requests.RequestException as exc:
            raise _Transient(
                "Tidak bisa terhubung ke API Gemini. Periksa koneksi internet/proxy.", None
            ) from exc

        status = resp.status_code
        if status != 200:
            self._raise_for_status(endpoint, status, resp)
        try:
            data = resp.json()
        except ValueError as exc:
            raise _Transient("Balasan API Gemini bukan JSON (server bermasalah).", None) from exc
        self._record_gemini_usage(data)
        return self._extract_text(data)

    def _raise_for_status(self, endpoint: str, status: int, resp: Any) -> None:
        message, reason = _error_details(resp)
        key_problem = status in (401, 403) or "API_KEY" in reason or "API key" in message
        if key_problem and self.endpoint == "auto" and endpoint == "aistudio":
            raise _KeyRejected(message)
        if key_problem:
            raise FatalError(
                "API key Gemini ditolak"
                + (f" ({message})" if message else "")
                + ". Periksa GEMINI_API_KEY. Key dari Google AI Studio memakai "
                "GEMINI_ENDPOINT=aistudio, key Vertex AI (Google Cloud) memakai "
                "GEMINI_ENDPOINT=vertex; pastikan API-nya sudah diaktifkan di project Anda."
            )
        if status == 404:
            raise FatalError(
                f"Model '{self.model}' tidak ditemukan di endpoint {endpoint}. Pakai --model "
                "dengan nama model yang tersedia di akun Anda (lihat daftar model di Google AI "
                "Studio atau Vertex AI Model Garden)."
            )
        if status == 429:
            raise _Transient(
                "Kena batas kuota/rate limit Gemini. Kurangi --concurrency atau tunggu sebentar"
                + (f" ({message})" if message else "") + ".",
                _retry_after_header(resp),
            )
        if status >= 500:
            raise _Transient(f"Server Gemini sedang sibuk/bermasalah ({status}).",
                             _retry_after_header(resp))
        if status == 400 and "billing" in message.lower():
            raise FatalError(f"Billing Google belum aktif untuk key ini: {message}")
        raise KontenError(f"Permintaan ke API Gemini ditolak ({status}): {message or reason}")

    def _record_gemini_usage(self, data: dict) -> None:
        meta = data.get("usageMetadata") or {}
        with _lock:
            self.usage.requests += 1
            self.usage.input_tokens += int(meta.get("promptTokenCount") or 0)
            self.usage.output_tokens += int(meta.get("candidatesTokenCount") or 0) + int(
                meta.get("thoughtsTokenCount") or 0
            )
            self.usage.cache_read += int(meta.get("cachedContentTokenCount") or 0)

    @staticmethod
    def _extract_text(data: dict) -> str:
        block = (data.get("promptFeedback") or {}).get("blockReason")
        if block:
            raise KontenError(
                f"Gemini menolak permintaan ini (alasan: {block}). Ubah input/brief lalu coba lagi."
            )
        candidates = data.get("candidates") or []
        if not candidates:
            raise InvalidOutput("jawaban kosong (tidak ada kandidat)")
        cand = candidates[0]
        finish = cand.get("finishReason", "")
        if finish in _REFUSAL_REASONS:
            raise KontenError(
                f"Gemini menghentikan jawaban karena kebijakan keamanan (alasan: {finish}). "
                "Ubah input/brief lalu coba lagi."
            )
        parts = (cand.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if finish == "MAX_TOKENS":
            raise InvalidOutput("jawaban terpotong (batas token tercapai); buat lebih ringkas")
        if not text.strip():
            raise InvalidOutput("jawaban kosong")
        return text


def _error_details(resp: Any) -> tuple[str, str]:
    try:
        err = resp.json().get("error", {})
    except (ValueError, AttributeError):
        return (getattr(resp, "text", "") or "")[:300], ""
    reasons = [d.get("reason", "") for d in err.get("details", []) if isinstance(d, dict)]
    return str(err.get("message", ""))[:300], " ".join(filter(None, [err.get("status", "")]
                                                             + reasons))


def _retry_after_header(resp: Any) -> float | None:
    try:
        value = resp.headers.get("retry-after")
        return min(float(value), 120.0) if value else None
    except (AttributeError, TypeError, ValueError):
        return None
