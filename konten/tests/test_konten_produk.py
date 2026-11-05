"""produk: flexible columns, title limits, write-back Excel, and the AI path."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from konten import produk
from konten.common import KontenError
from konten.llm import LLMClient
from konten.tests.fake_anthropic import FakeClient, produk_responder, prompt_text

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "produk_contoh.csv"


def test_map_columns_aliases():
    m = produk.map_columns(["Nama Produk", "Price", "Spesifikasi", "Fitur", "Merk", "Garansi"])
    assert m["nama"] == ["Nama Produk"] and m["harga"] == ["Price"]
    assert m["fitur"] == ["Spesifikasi", "Fitur"] and m["merek"] == ["Merk"]
    p = produk.row_to_product({"Nama Produk": "Kaos", "Price": "10", "Spesifikasi": "a; b",
                               "Fitur": "c", "Merk": "X", "Garansi": "1 tahun"}, m)
    assert p["fitur"] == ["a", "b", "c"]
    assert p["lainnya"] == {"Garansi": "1 tahun"}  # unknown columns are kept as specs


def test_titles_put_key_terms_first_and_respect_limits():
    p = {"nama": "Kaos Polos Oversize Unisex", "merek": "Rupa Lokal", "bahan": "Katun Combed 24s",
         "kategori": "Kaos", "fitur": ["Sablon awet", "Tidak menerawang"] * 10,
         "ukuran": "S, M, L, XL"}
    shopee, tokped = produk.make_titles(p)
    assert shopee.startswith("Kaos Polos Oversize Unisex")
    assert len(shopee) <= 120 and len(tokped) <= 70
    assert shopee.lower().count("kaos") == 1  # no keyword stuffing


def test_template_copy_quality():
    p = produk.row_to_product(
        {"nama": "Tumbler Stainless 500ml", "kategori": "Tumbler", "harga": "Rp 125.000",
         "fitur": "Tahan panas 12 jam; BPA free", "bahan": "Stainless steel 304"},
        produk.map_columns(["nama", "kategori", "harga", "fitur", "bahan"]))
    out = produk.generate_template(p)
    assert out["engine"] == "template"
    assert "Rp125.000" in out["promo_wa"]
    assert "original" not in out["tag_seo"] and "premium" not in out["tag_seo"]
    assert 3 <= len(out["tag_seo"].split(", ")) <= 10
    assert out["poin_fitur"].startswith("• Tahan panas 12 jam")
    assert len(out["deskripsi"]) <= 2000 and "Spesifikasi:" in out["deskripsi"]
    with pytest.raises(KontenError):
        produk.generate_template({"nama": ""})


def test_run_appends_columns_next_to_original(tmp_path):
    files = produk.run(str(EXAMPLE), tmp_path, llm=None, concurrency=1, timpa=False)
    df = pd.read_excel(files[0], dtype=str)
    original = pd.read_csv(EXAMPLE, dtype=str)
    assert list(df.columns)[: len(original.columns)] == list(original.columns)
    assert list(df.columns)[len(original.columns):] == produk.NEW_COLUMNS
    assert df["nama"].tolist() == original["nama"].tolist()
    assert df["judul_tokopedia"].str.len().max() <= 70
    assert df["judul_shopee"].str.len().max() <= 120


def test_missing_name_column_is_explained(tmp_path):
    f = tmp_path / "x.csv"
    f.write_text("barang_x,harga\nA,1\n", encoding="utf-8")
    with pytest.raises(KontenError, match="Kolom nama produk"):
        produk.run(str(f), tmp_path, llm=None, concurrency=1, timpa=False)


def test_ai_path_and_resume(tmp_path):
    fake = FakeClient(produk_responder)
    llm = LLMClient(client=fake, backoff_base=0)
    produk.run(str(EXAMPLE), tmp_path, llm=llm, concurrency=3, timpa=False)
    assert len(fake.calls) == 4
    assert any("Rupa Lokal" in prompt_text(c) for c in fake.calls)
    df = pd.read_csv(tmp_path / "produk_contoh_konten.csv")
    assert df["engine"].str.startswith("ai:").all()
    produk.run(str(EXAMPLE), tmp_path, llm=llm, concurrency=3, timpa=False)
    assert len(fake.calls) == 4  # everything cached, no new requests
