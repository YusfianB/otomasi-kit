"""Thin wrapper around the Anthropic SDK for JSON (structured output) generation.

Design notes (see konten/README.md "Catatan model & biaya"):
- Default model ``claude-opus-5-5``; override with ``--model`` or env ``OTOMASI_MODEL``.
  Cost is tuned with ``effort`` (``--effort`` / ``OTOMASI_EFFORT``, default ``medium``) rather
  than by silently switching to a weaker model.
- Every request uses structured outputs (``output_config.format`` json_schema), so the reply
  is parseable JSON; we still validate it locally (types/required/enums + task-specific
  checks such as length limits) and re-ask with feedback when it is not acceptable.
- The shared system prompt is sent as stable text blocks with ``cache_control`` on the last
  one, so bulk runs read it from the prompt cache.
- Server-side refusal fallback (``fallbacks: "default"``) is enabled on models that support it.
- The SDK already retries 408/409/429/5xx with backoff; on top of that we back off and retry
  transient errors a few more times, and map every failure to a clear Indonesian message.
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from konten.common import FatalError, KontenError, info, warn

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "medium"
EFFORTS = ("low", "medium", "high", "xhigh", "max")

# Models that accept the server-side ``fallbacks: "default"`` parameter (Claude API).
_FALLBACK_MODELS = ("claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5")
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that reject the ``effort`` parameter.
_NO_EFFORT_PREFIXES = ("claude-haiku-4", "claude-sonnet-4-5", "claude-3")

# USD per 1M tokens: (input, output, cache_read). Used only for the end-of-run estimate.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-opus-5": (5.00, 25.00, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-sonnet-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-fable-5-1": (10.00, 50.00, 0.25),
}

BASE_SYSTEM_PROMPT = """\
You are "Otomasi Konten", a senior content strategist and copywriter for the Indonesian market.
Your clients are small businesses (UMKM), online sellers on Shopee/Tokopedia/TikTok Shop, and
independent content creators and teachers in Indonesia.

General rules for everything you write:
1. Write natural, fluent copy in the requested output language. For Bahasa Indonesia, use
   everyday standard Indonesian (bahasa baku yang luwes). Match the requested tone: "santai"
   may use "kamu", light slang (nih, yuk, banget) and a few relevant emoji; "profesional" and
   "edukatif" use "Anda", no slang and at most one emoji. Never mix registers inside one text.
2. Be concrete and specific to the brand, product, audience, and material you are given. Prefer
   details from the input over generic filler. Do not invent facts that could mislead buyers:
   no fake testimonials, fake statistics, fake certifications (BPOM, SNI, halal), fake awards,
   or guarantees the input does not mention. If social proof is needed, write a clearly marked
   placeholder such as [tempel testimoni asli pelanggan].
3. Respect platform rules and character limits exactly. Count characters, not words, when a
   limit is given. Hashtags: lowercase, no spaces, relevant to the niche and to Indonesian
   search behaviour; never pad with unrelated trending tags.
4. Marketplace copy: put the most searched key terms first in titles, avoid ALL CAPS, avoid
   keyword stuffing and avoid words the marketplaces penalise (e.g. "termurah", "100% original"
   unless stated in the input, competitor brand names).
5. SEO articles: write for people first, follow search intent, use clear H2/H3 structure,
   short paragraphs, and practical steps. Keyword research you provide is an informed estimate
   from your own knowledge, not live search-volume data; never claim exact search volumes.
6. Quizzes: questions must be answerable from the provided material (or from well-established
   knowledge of the topic when no material is given), unambiguous, with exactly one correct
   answer for multiple choice, plausible distractors, and short explanations that teach.
7. Avoid repetition: every post, title, or question in a batch should use a different angle,
   opening and structure. Do not reuse the same hook formula twice in one batch.
8. Output only the JSON object that matches the provided schema. All human-readable text goes
   inside the JSON string values. Use "\\n" for line breaks inside strings.

Indonesian market context you can rely on: popular payment methods (transfer bank, e-wallet such
as GoPay/OVO/DANA, COD), delivery via JNE/J&T/SiCepat/GoSend/GrabExpress, shopping moments such
as tanggal kembar (11.11, 12.12), Harbolnas, gajian (end of month), Ramadan and Lebaran. Common
calls to action: "cek keranjang kuning", "klik link di bio", "chat admin via WhatsApp",
"checkout sekarang", "simpan postingan ini", "tag temanmu".
"""

_lock = threading.Lock()


def resolve_model(cli_value: str | None) -> str:
    return cli_value or os.environ.get("OTOMASI_MODEL") or DEFAULT_MODEL


def resolve_effort(cli_value: str | None) -> str:
    effort = cli_value or os.environ.get("OTOMASI_EFFORT") or DEFAULT_EFFORT
    if effort not in EFFORTS:
        raise KontenError(f"Nilai effort tidak dikenal: {effort} (pilih: {', '.join(EFFORTS)})")
    return effort


def has_api_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


# --------------------------------------------------------------------------- schema validation


class InvalidOutput(Exception):
    """Model output did not satisfy the schema or task checks (retryable)."""


def validate_schema(data: Any, schema: dict, path: str = "$") -> list[str]:
    """Minimal JSON-schema validator for the subset used in this package."""
    errors: list[str] = []
    typ = schema.get("type")
    py_types = {
        "object": dict, "array": list, "string": str, "boolean": bool,
        "integer": int, "number": (int, float),
    }
    if typ:
        wrong_type = not isinstance(data, py_types[typ])
        if typ in ("integer", "number") and isinstance(data, bool):
            wrong_type = True
        if wrong_type:
            return [f"{path}: harus bertipe {typ}"]
    if "enum" in schema and data not in schema["enum"]:
        errors.append(f"{path}: nilai {data!r} tidak ada di {schema['enum']}")
    if typ == "object":
        for req in schema.get("required", []):
            if req not in data:
                errors.append(f"{path}: field '{req}' wajib ada")
        for key, sub in schema.get("properties", {}).items():
            if key in data:
                errors.extend(validate_schema(data[key], sub, f"{path}.{key}"))
    elif typ == "array":
        if len(data) < schema.get("x-minItems", 0):
            errors.append(f"{path}: minimal {schema['x-minItems']} item")
        for i, item in enumerate(data):
            errors.extend(validate_schema(item, schema.get("items", {}), f"{path}[{i}]"))
    elif typ == "string" and schema.get("x-nonEmpty", True) and not data.strip():
        errors.append(f"{path}: tidak boleh kosong")
    return errors


def api_schema(schema: Any) -> Any:
    """Strip our local ``x-*`` keys so the schema only uses features the API supports."""
    if isinstance(schema, dict):
        return {k: api_schema(v) for k, v in schema.items() if not k.startswith("x-")}
    if isinstance(schema, list):
        return [api_schema(v) for v in schema]
    return schema


def obj(properties: dict, **extra: Any) -> dict:
    """Object schema with every property required and no additional properties."""
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False, **extra}


STR = {"type": "string"}


def str_list(min_items: int = 1) -> dict:
    return {"type": "array", "items": {"type": "string"}, "x-minItems": min_items}


# --------------------------------------------------------------------------- client


@dataclass
class Usage:
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0


class LLMClient:
    """JSON generation with validation, retries and Indonesian error messages."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        effort: str = DEFAULT_EFFORT,
        *,
        client: Any = None,
        max_attempts: int = 3,
        backoff_base: float = 2.0,
        sdk_max_retries: int = 3,
        timeout: float = 600.0,
    ) -> None:
        self.model = model
        self.effort = effort
        self.max_attempts = max_attempts
        self.backoff_base = backoff_base
        self.usage = Usage()
        if client is None:
            client = make_anthropic_client(max_retries=sdk_max_retries, timeout=timeout)
        self._client = client

    # -- public -------------------------------------------------------------------------

    def generate_json(
        self,
        *,
        task_system: str,
        prompt: str | list[dict],
        schema: dict,
        check: Callable[[dict], list[str]] | None = None,
        max_tokens: int = 16000,
    ) -> dict:
        """Ask for JSON matching ``schema``; re-ask (with feedback) when invalid.

        ``check`` returns a list of human-readable problems (e.g. a caption over the limit).
        If problems remain after the last attempt, the last parsed output is returned and the
        caller is expected to enforce limits itself.
        """
        feedback = ""
        last_data: dict | None = None
        last_problem = ""
        for attempt in range(1, self.max_attempts + 1):
            try:
                text = self._request(task_system, prompt, schema, feedback, max_tokens)
                data = _parse_json(text)
                errors = validate_schema(data, schema)
                if errors:
                    raise InvalidOutput("; ".join(errors[:8]))
                last_data = data
                problems = check(data) if check else []
                if not problems:
                    return data
                last_problem = "; ".join(problems[:8])
                feedback = (
                    "Your previous answer had these problems, fix all of them and answer "
                    f"again with the full JSON: {last_problem}"
                )
            except InvalidOutput as exc:
                last_problem = str(exc)
                feedback = (
                    "Your previous answer was not valid for the schema "
                    f"({last_problem}). Answer again with the complete JSON."
                )
            except _Transient as exc:
                last_problem = str(exc)
                if attempt == self.max_attempts:
                    raise KontenError(str(exc)) from exc
                delay = exc.retry_after or self.backoff_base * (2 ** (attempt - 1))
                delay += random.uniform(0, 0.5)
                warn(f"{exc} Mencoba lagi dalam {delay:.0f} detik...")
                time.sleep(delay)
                continue
            if attempt < self.max_attempts:
                warn(f"Output AI belum sesuai (percobaan {attempt}): {last_problem}. Mengulang...")
        if last_data is not None:
            warn(f"Output AI dipakai dengan perbaikan otomatis: {last_problem}")
            return last_data
        raise KontenError(
            f"Output AI tetap tidak valid setelah {self.max_attempts} percobaan: {last_problem}"
        )

    def usage_summary(self) -> str:
        u = self.usage
        line = (
            f"Pemakaian API ({self.model}): {u.requests} request, input {u.input_tokens:,} token "
            f"(cache baca {u.cache_read:,}, cache tulis {u.cache_write:,}), "
            f"output {u.output_tokens:,} token"
        )
        price = PRICES.get(self.model)
        if price:
            p_in, p_out, p_cache = price
            cost = (
                u.input_tokens * p_in + u.cache_write * p_in * 1.25 + u.cache_read * p_cache
                + u.output_tokens * p_out
            ) / 1_000_000
            line += f". Perkiraan biaya: ${cost:.4f} (estimasi kasar, cek tagihan di Console)"
        return line

    # -- internals ----------------------------------------------------------------------

    def _request(
        self, task_system: str, prompt: str | list[dict], schema: dict, feedback: str,
        max_tokens: int,
    ) -> str:
        import anthropic

        if isinstance(prompt, str):
            content: list[dict] = [{"type": "text", "text": prompt}]
        else:
            content = list(prompt)
        if feedback:
            content.append({"type": "text", "text": feedback})
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            # Stable prefix first; cache breakpoint on the last system block.
            "system": [
                {"type": "text", "text": BASE_SYSTEM_PROMPT},
                {"type": "text", "text": task_system, "cache_control": {"type": "ephemeral"}},
            ],
            "messages": [{"role": "user", "content": content}],
            "output_config": {"format": {"type": "json_schema", "schema": api_schema(schema)}},
        }
        if not self.model.startswith(_NO_EFFORT_PREFIXES):
            params["output_config"]["effort"] = self.effort
        if self.model in _FALLBACK_MODELS:
            params["betas"] = [_FALLBACK_BETA]
            params["fallbacks"] = "default"

        try:
            # Streaming avoids HTTP timeouts on long outputs (articles, quiz sets).
            with self._client.beta.messages.stream(**params) as stream:
                message = stream.get_final_message()
        except anthropic.AuthenticationError as exc:
            raise FatalError(
                "API key Anthropic tidak valid atau sudah dicabut. Periksa ANTHROPIC_API_KEY "
                "(buat key baru di https://platform.claude.com/settings/keys)."
            ) from exc
        except anthropic.PermissionDeniedError as exc:
            raise FatalError(
                "API key tidak punya izin untuk model/fitur ini. Cek workspace & izin key Anda."
            ) from exc
        except anthropic.NotFoundError as exc:
            raise FatalError(
                f"Model '{self.model}' tidak ditemukan. Periksa --model / OTOMASI_MODEL "
                "(contoh: claude-opus-5-5, claude-sonnet-5-5, claude-haiku-5-5)."
            ) from exc
        except anthropic.RateLimitError as exc:
            retry_after = _retry_after(exc)
            raise _Transient(
                "Kena batas pemakaian (rate limit) API Anthropic. Kurangi --concurrency "
                "atau tunggu sebentar.",
                retry_after,
            ) from exc
        except anthropic.BadRequestError as exc:
            msg = getattr(exc, "message", str(exc))
            if "credit" in msg.lower() or "billing" in msg.lower():
                raise FatalError(
                    "Saldo/kredit API Anthropic habis. Isi ulang di Console (Billing)."
                ) from exc
            raise KontenError(f"Permintaan ke API ditolak (400): {msg}") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500 or exc.status_code in (408, 409, 529):
                raise _Transient(
                    f"Server Anthropic sedang sibuk/bermasalah ({exc.status_code}).",
                    _retry_after(exc),
                ) from exc
            raise KontenError(f"Error API Anthropic ({exc.status_code}): {exc.message}") from exc
        except anthropic.APITimeoutError as exc:
            raise _Transient("Permintaan ke API Anthropic timeout.", None) from exc
        except anthropic.APIConnectionError as exc:
            raise _Transient(
                "Tidak bisa terhubung ke API Anthropic. Periksa koneksi internet/proxy.", None
            ) from exc

        self._record_usage(message)
        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise KontenError(
                "Model menolak permintaan ini karena kebijakan keamanan"
                + (f" (kategori: {category})" if category else "")
                + ". Ubah input/brief lalu coba lagi."
            )
        text = "".join(
            getattr(b, "text", "") for b in message.content if getattr(b, "type", "") == "text"
        )
        if message.stop_reason == "max_tokens":
            raise InvalidOutput("jawaban terpotong (max_tokens tercapai); buat lebih ringkas")
        if not text.strip():
            raise InvalidOutput("jawaban kosong")
        return text

    def _record_usage(self, message: Any) -> None:
        usage = getattr(message, "usage", None)
        with _lock:
            self.usage.requests += 1
            if usage is None:
                return
            self.usage.input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
            self.usage.output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
            self.usage.cache_read += int(getattr(usage, "cache_read_input_tokens", 0) or 0)
            self.usage.cache_write += int(getattr(usage, "cache_creation_input_tokens", 0) or 0)


class _Transient(Exception):
    def __init__(self, msg: str, retry_after: float | None) -> None:
        super().__init__(msg)
        self.retry_after = retry_after


def _retry_after(exc: Any) -> float | None:
    try:
        value = exc.response.headers.get("retry-after")
        return min(float(value), 120.0) if value else None
    except (AttributeError, TypeError, ValueError):
        return None


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):] if "{" in text else text
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidOutput(f"bukan JSON yang valid ({exc.msg})") from exc
    if not isinstance(data, dict):
        raise InvalidOutput("JSON harus berupa object")
    return data


def make_anthropic_client(max_retries: int = 3, timeout: float = 600.0) -> Any:
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise FatalError("Paket 'anthropic' belum terpasang: pip install anthropic") from exc
    if not has_api_key():
        raise FatalError(
            "ANTHROPIC_API_KEY belum diset. Set dulu, misalnya: export ANTHROPIC_API_KEY=sk-ant-... "
            "atau jalankan dengan --engine template untuk mode offline."
        )
    return anthropic.Anthropic(max_retries=max_retries, timeout=timeout)


def build_llm(model: str | None, effort: str | None, client: Any = None) -> LLMClient:
    llm = LLMClient(resolve_model(model), resolve_effort(effort), client=client)
    info(f"Mesin: AI (Anthropic, model {llm.model}, effort {llm.effort})")
    return llm
