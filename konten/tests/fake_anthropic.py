"""A minimal stand-in for ``anthropic.Anthropic`` used by the konten tests.

It implements only what ``konten.llm.LLMClient`` calls: ``client.beta.messages.stream(**params)``
as a context manager whose ``get_final_message()`` returns a message-like object.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any


def message(data: Any, stop_reason: str = "end_turn", category: str | None = None) -> Any:
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking=""),
                 SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(category=category) if category else None,
        usage=SimpleNamespace(input_tokens=120, output_tokens=80, cache_read_input_tokens=1000,
                              cache_creation_input_tokens=0),
    )


def api_error(cls: type, status: int, headers: dict[str, str] | None = None) -> Exception:
    import httpx2

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(status, request=request, headers=headers or {})
    return cls("simulated error", response=response, body=None)


class _Stream:
    def __init__(self, msg: Any) -> None:
        self._msg = msg

    def __enter__(self) -> _Stream:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def get_final_message(self) -> Any:
        return self._msg


class _Messages:
    def __init__(self, responder: Callable[[dict, int], Any]) -> None:
        self.responder = responder
        self.calls: list[dict] = []

    def stream(self, **params: Any) -> _Stream:
        self.calls.append(params)
        result = self.responder(params, len(self.calls))
        if isinstance(result, Exception):
            raise result
        return _Stream(result)


class FakeClient:
    def __init__(self, responder: Callable[[dict, int], Any]) -> None:
        self.beta = SimpleNamespace(messages=_Messages(responder))

    @property
    def calls(self) -> list[dict]:
        return self.beta.messages.calls


def prompt_text(params: dict) -> str:
    content = params["messages"][0]["content"]
    return "\n".join(block.get("text", "") for block in content)


def task_text(params: dict) -> str:
    return params["system"][-1]["text"]


# --------------------------------------------------------------------------- canned responders


def sosmed_responder(params: dict, n: int) -> Any:
    text = prompt_text(params)
    lo, hi = map(int, re.search(r"hashtags (\d+)-(\d+)", text).groups())
    platform = re.search(r"- platform: (.+)", text).group(1)
    video = "Video format" in text
    return message({
        "topik": f"Topik unik {n}",
        "hook": f"Hook nomor {n} untuk {platform}",
        "caption": f"Hook nomor {n} untuk {platform}\n\nIsi posting {n}.\n\nYuk order!",
        "hashtag": [f"#tag{n}x{i}" for i in range(lo)],
        "cta": "Yuk order!",
        "ide_visual": "Foto produk di meja kayu.",
        "naskah_video": "0–3 dtk: hook\n3–10 dtk: isi" if video else "",
    })


def artikel_responder(params: dict, n: int) -> Any:
    task = task_text(params)
    if "step 1 of 2" in task:
        return message({
            "intent": "informasional",
            "kata_kunci_terkait": ["budidaya lele pemula", "kolam terpal", "pakan lele"],
            "long_tail": ["cara budidaya lele di ember untuk pemula", "modal budidaya lele",
                          "budidaya lele tanpa bau"],
            "judul_h1": "Cara Budidaya Lele di Ember untuk Pemula",
            "meta_title": "Cara Budidaya Lele di Ember untuk Pemula",
            "meta_description": "Panduan budidaya lele di ember: persiapan, pakan, dan panen. "
                                "Cocok untuk pemula dengan modal kecil. Baca sekarang!",
            "slug": "budidaya-lele-ember",
            "outline": [{"h2": "Persiapan", "h3": ["Ember", "Bibit"]},
                        {"h2": "Perawatan", "h3": []}, {"h2": "Panen", "h3": []}],
        })
    body = "Pendahuluan singkat tentang budidaya lele.\n\n" + "\n\n".join(
        f"## Bagian {i}\n\n" + "Kalimat penjelasan yang cukup panjang. " * 30 for i in range(4)
    ) + "\n\n## Kesimpulan\n\nPenutup."
    return message({
        "artikel_markdown": body,
        "faq": [{"pertanyaan": f"Pertanyaan {i}?", "jawaban": f"Jawaban {i}."} for i in range(4)],
        "anchor_internal": [{"anchor": "pakan lele", "topik_tujuan": "pakan lele murah"}],
    })


def produk_responder(params: dict, n: int) -> Any:
    return message({
        "judul_shopee": f"Produk Bagus Nomor {n} Bahan Premium Ukuran Lengkap",
        "judul_tokopedia": f"Produk Bagus Nomor {n}",
        "poin_fitur": ["Fitur satu — manfaat", "Fitur dua — manfaat", "Fitur tiga — manfaat"],
        "deskripsi": "Deskripsi lengkap produk.\n\nSpesifikasi:\n- A\n- B",
        "tag_seo": ["tag satu", "tag dua", "tag tiga", "tag empat", "tag lima"],
        "promo_wa": "Halo Kak! Produk baru ready. Balas *MAU* ya 🙏",
    })


def kuis_responder(params: dict, n: int) -> Any:
    text = prompt_text(params)
    types = json.loads(re.search(r"type sequence: (\[.*?\])", text).group(1))
    soal = []
    for i, t in enumerate(types):
        q = {"tipe": t, "pertanyaan": f"Pertanyaan {n}-{i}?", "opsi": [], "jawaban": "x",
             "penjelasan": "Karena materi menyebutkannya."}
        if t == "pilihan_ganda":
            q["opsi"] = ["A. Fotosintesis", "B. Respirasi", "C. Klorofil", "D. Stomata"]
            q["jawaban"] = "A. Fotosintesis"
        elif t == "benar_salah":
            q["opsi"] = ["Benar", "Salah"]
            q["jawaban"] = "Salah"
        elif t == "isian":
            q["pertanyaan"] = f"Pigmen hijau disebut _____ ({n}-{i})."
            q["jawaban"] = "klorofil"
        soal.append(q)
    return message({"soal": soal})
