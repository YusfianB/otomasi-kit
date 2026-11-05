import csv
import json

import pandas as pd
import pytest

from scraper.config import parse_config
from scraper.monitor import History, compare, resolve_fields, write_report
from scraper.output import OutputError, write_items

ITEMS = [
    {"nama": "Kopi Gayo", "harga": 125000, "link": "https://t/1", "stok": 5},
    {"nama": "Teh Hijau", "harga": 30000, "link": "https://t/2", "stok": 0, "extra": [1, 2]},
]


@pytest.mark.parametrize("ext", [".csv", ".xlsx", ".json", ".jsonl"])
def test_write_items_by_extension(tmp_path, ext):
    path = write_items(ITEMS, tmp_path / f"sub/hasil{ext}", columns=["nama", "harga", "link"])
    assert path.exists()
    if ext == ".csv":
        rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
        assert rows[0]["nama"] == "Kopi Gayo" and rows[1]["extra"] == "[1, 2]"
        assert list(rows[0]) == ["nama", "harga", "link", "stok", "extra"]
    elif ext == ".xlsx":
        df = pd.read_excel(path)
        assert df["harga"].tolist() == [125000, 30000]
    elif ext == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data[1]["extra"] == [1, 2]
    else:
        lines = path.read_text(encoding="utf-8").splitlines()
        assert json.loads(lines[0])["nama"] == "Kopi Gayo"


def test_write_items_rejects_unknown_extension(tmp_path):
    with pytest.raises(OutputError, match="tidak didukung"):
        write_items(ITEMS, tmp_path / "hasil.txt")


def _cfg(**monitor):
    data = {
        "start_urls": ["x"],
        "items": ".a",
        "fields": {
            "nama": "h3",
            "harga": {"selector": ".p", "type": "price_idr"},
            "link": {"selector": "a", "attr": "href", "type": "url"},
            "stok": ".s",
        },
    }
    if monitor:
        data["monitor"] = monitor
    return parse_config(data)


def test_resolve_fields_defaults():
    f = resolve_fields(_cfg())
    assert f.key == ["link"] and f.price == "harga" and f.label == "nama"
    f = resolve_fields(_cfg(key="nama", track=["stok"]))
    assert f.key == ["nama"] and f.track == ["stok"]


def test_compare_detects_new_removed_and_price_changes():
    fields = resolve_fields(_cfg(track=["stok"]))
    old = {
        "https://t/1": {"nama": "Kopi", "harga": 100000, "link": "https://t/1", "stok": 5},
        "https://t/2": {"nama": "Teh", "harga": 30000, "link": "https://t/2", "stok": 1},
        "https://t/3": {"nama": "Gula", "harga": 15000, "link": "https://t/3", "stok": 9},
    }
    new = [
        {"nama": "Kopi", "harga": 90000, "link": "https://t/1", "stok": 5},
        {"nama": "Teh", "harga": 33000, "link": "https://t/2", "stok": 0},
        {"nama": "Susu", "harga": 20000, "link": "https://t/4", "stok": 3},
    ]
    report = compare(
        old, new, fields, config="c", previous_run_at="kemarin", current_run_at="hari ini"
    )
    kinds = [c["jenis"] for c in report.changes]
    assert kinds == ["harga_turun", "harga_naik", "baru", "hilang", "berubah"]
    turun = report.changes[0]
    assert turun["nama"] == "Kopi" and turun["selisih"] == -10000 and turun["persen"] == -10.0
    assert report.changes[1]["persen"] == 10.0
    assert report.changes[4]["field"] == "stok" and report.changes[4]["nilai_baru"] == 0
    assert "Harga turun       : 1" in report.summary_text()

    filtered = compare(
        old, new, fields, config="c", previous_run_at="k", current_run_at="h", min_change_pct=15
    )
    assert not any(c["jenis"].startswith("harga") for c in filtered.changes)


def test_first_run_has_no_changes():
    report = compare(
        {}, ITEMS, resolve_fields(_cfg()), config="c", previous_run_at=None, current_run_at="now"
    )
    assert report.first_run and not report.changes
    assert "Run pertama" in report.summary_text()


def test_history_roundtrip_and_prune(tmp_path):
    fields = resolve_fields(_cfg())
    with History(tmp_path / "r.sqlite") as h:
        assert h.latest_run("c") is None
        h.save_run("c", ITEMS, fields, run_at="2026-11-01T08:00:00")
        rid = h.save_run("c", ITEMS[:1], fields, run_at="2026-11-02T08:00:00")
        h.save_run("lain", ITEMS, fields)
        assert h.latest_run("c") == (rid, "2026-11-02T08:00:00")
        assert list(h.load_items(rid)) == ["https://t/1"]
        assert h.prune("c", keep=1) == 1
        assert h.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 2


@pytest.mark.parametrize("ext", [".csv", ".xlsx", ".json", ".md"])
def test_write_report_formats(tmp_path, ext):
    fields = resolve_fields(_cfg())
    old = {"https://t/1": {"nama": "Kopi", "harga": 1250000, "link": "https://t/1"}}
    new = [{"nama": "Kopi", "harga": 1100000, "link": "https://t/1"}]
    report = compare(old, new, fields, config="c", previous_run_at="a", current_run_at="b")
    path = write_report(report, tmp_path / f"lap{ext}")
    if ext == ".csv":
        rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
        assert rows[0]["jenis"] == "harga_turun" and rows[0]["selisih"] == "-150000"
    elif ext == ".xlsx":
        assert pd.read_excel(path, sheet_name="perubahan")["nilai_baru"].tolist() == [1100000]
        assert "ringkasan" in pd.ExcelFile(path).sheet_names
    elif ext == ".json":
        assert json.loads(path.read_text(encoding="utf-8"))["ringkasan"]["harga_turun"] == 1
    else:
        text = path.read_text(encoding="utf-8")
        assert "| Harga turun | Kopi | 1.250.000 | 1.100.000 | -150.000 | -12,0% |" in text
