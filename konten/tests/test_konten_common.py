"""Shared helpers: text utils, table reading and the resumable batch runner."""

from __future__ import annotations

import pytest

from konten.common import (
    FatalError,
    Item,
    KontenError,
    format_rupiah,
    read_table,
    run_batch,
    slugify,
    split_list,
    truncate,
)


def test_slugify_and_truncate():
    assert slugify("Cara Budidaya Lele di Ember!") == "cara-budidaya-lele-di-ember"
    assert slugify("Kopi Susu Gula Aren — Édition") == "kopi-susu-gula-aren-edition"
    assert slugify("") == "tanpa-judul"
    assert truncate("satu dua tiga empat", 12) == "satu dua"
    assert len(truncate("x" * 100, 60)) <= 60
    assert truncate("pendek", 60) == "pendek"


@pytest.mark.parametrize(("raw", "expected"), [
    (89000, "Rp89.000"), ("Rp 125.000", "Rp125.000"), ("25rb", "Rp25.000"),
    ("1,5 jt", "Rp1.500.000"), ("", ""), (None, ""), (float("nan"), ""),
])
def test_format_rupiah(raw, expected):
    assert format_rupiah(raw) == expected


def test_split_list():
    assert split_list("a; b; c") == ["a", "b", "c"]
    assert split_list("a, b") == ["a", "b"]
    assert split_list(["x", "y; z"]) == ["x", "y", "z"]
    assert split_list(float("nan")) == []


def test_read_table_csv_and_errors(tmp_path):
    f = tmp_path / "p.csv"
    f.write_text(" nama ,harga\nKaos,1000\n,\n", encoding="utf-8")
    df = read_table(f)
    assert list(df.columns) == ["nama", "harga"] and len(df) == 1
    with pytest.raises(KontenError, match="tidak ditemukan"):
        read_table(tmp_path / "tidak-ada.csv")
    bad = tmp_path / "p.json"
    bad.write_text("{}")
    with pytest.raises(KontenError, match="tidak didukung"):
        read_table(bad)


def test_run_batch_resume_overwrite_and_failures(tmp_path):
    calls = []

    def worker(x):
        calls.append(x)
        if x == 3:
            raise ValueError("gagal tiga")
        return {"nilai": x * 10}

    items = [Item(f"i{x}", x) for x in (1, 2, 3)]
    res = run_batch(items, worker, items_dir=tmp_path)
    assert res.done == 2 and res.failed == {"i3": "gagal tiga"}
    assert (tmp_path / "i1.json").exists() and not (tmp_path / "i3.json").exists()

    calls.clear()
    res = run_batch(items, worker, items_dir=tmp_path)  # resume: only the failed one reruns
    assert calls == [3] and res.skipped == 2 and res.results["i2"] == {"nilai": 20}

    calls.clear()
    run_batch(items, worker, items_dir=tmp_path, timpa=True)
    assert sorted(calls) == [1, 2, 3]


def test_run_batch_concurrent(tmp_path):
    items = [Item(f"k{i}", i) for i in range(20)]
    res = run_batch(items, lambda x: {"x": x}, items_dir=tmp_path, concurrency=4)
    assert res.done == 20 and res.results["k7"] == {"x": 7}


def test_fatal_error_stops_batch(tmp_path):
    def worker(x):
        raise FatalError("API key tidak valid")

    with pytest.raises(FatalError):
        run_batch([Item("a", 1), Item("b", 2)], worker, items_dir=tmp_path)
