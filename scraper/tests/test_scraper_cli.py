import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from scraper.cli import main

ROOT = Path(__file__).resolve().parents[2]


def test_jalankan_writes_xlsx_and_summary(tmp_path, shop_config_file, capsys):
    out = tmp_path / "hasil.xlsx"
    ringkasan = tmp_path / "ringkasan.json"
    code = main(["jalankan", str(shop_config_file), "-o", str(out), "--ringkasan", str(ringkasan)])
    assert code == 0
    df = pd.read_excel(out)
    assert len(df) == 10
    assert list(df.columns)[:3] == ["nama", "harga", "harga_coret"]
    assert "stok" in df.columns
    assert df.loc[df["nama"] == "Kopi Arabika Gayo 250g", "harga"].item() == 125000
    summary = json.loads(ringkasan.read_text(encoding="utf-8"))
    assert summary["items"] == 10 and summary["pages"] == 3 and summary["errors"] == []
    assert not (tmp_path / "hasil.xlsx.progress.jsonl").exists()
    captured = capsys.readouterr()
    assert "Item tersimpan     : 10" in captured.out
    assert "Halaman 1:" in captured.err


def test_monitor_detects_price_change(tmp_path, shop_config_file, toko_dir, capsys):
    db = tmp_path / "riwayat.sqlite"
    lap1 = tmp_path / "lap1.md"
    assert (
        main(["-q", "monitor", str(shop_config_file), "--riwayat", str(db), "--laporan", str(lap1)])
        == 0
    )
    assert "Run pertama" in capsys.readouterr().out

    page = toko_dir / "halaman-1.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            '<span class="harga-jual">Rp1.250.000</span>',
            '<span class="harga-jual">Rp1.099.000</span>',
        ),
        encoding="utf-8",
    )
    detail = toko_dir / "produk" / "kaos-katun-polos.html"
    detail.write_text(
        detail.read_text(encoding="utf-8").replace("Stok: 340", "Stok: 300"), encoding="utf-8"
    )

    lap2 = tmp_path / "lap2.csv"
    code = main(
        [
            "-q",
            "monitor",
            str(shop_config_file),
            "--riwayat",
            str(db),
            "--laporan",
            str(lap2),
            "--gagal-jika-berubah",
        ]
    )
    assert code == 3
    out = capsys.readouterr().out
    assert "Harga turun       : 1" in out
    report = pd.read_csv(lap2)
    turun = report[report["jenis"] == "harga_turun"].iloc[0]
    assert turun["nama"] == "Sepatu Lari Ringan"
    assert (turun["nilai_lama"], turun["nilai_baru"], turun["selisih"]) == (
        1250000,
        1099000,
        -151000,
    )
    berubah = report[report["jenis"] == "berubah"].iloc[0]
    assert berubah["field"] == "stok" and berubah["nilai_baru"] == 300

    # third run without changes -> exit 0 even with --gagal-jika-berubah
    assert (
        main(
            [
                "-q",
                "monitor",
                str(shop_config_file),
                "--riwayat",
                str(db),
                "--laporan",
                str(tmp_path / "lap3.json"),
                "--gagal-jika-berubah",
            ]
        )
        == 0
    )


def test_monitor_new_and_removed_items(tmp_path, shop_config_file, toko_dir, capsys):
    db = tmp_path / "r.sqlite"
    main(
        [
            "-q",
            "monitor",
            str(shop_config_file),
            "--riwayat",
            str(db),
            "--laporan",
            str(tmp_path / "a.csv"),
        ]
    )
    # page 3 disappears -> its 3 products are "hilang"
    page2 = toko_dir / "halaman-2.html"
    page2.write_text(
        page2.read_text(encoding="utf-8").replace(
            '<a class="next" href="halaman-3.html">Berikutnya &raquo;</a>', ""
        ),
        encoding="utf-8",
    )
    main(
        [
            "-q",
            "monitor",
            str(shop_config_file),
            "--riwayat",
            str(db),
            "--laporan",
            str(tmp_path / "b.csv"),
        ]
    )
    report = pd.read_csv(tmp_path / "b.csv")
    assert sorted(report[report["jenis"] == "hilang"]["nama"]) == [
        "Jaket Hoodie Fleece",
        "Lampu LED 12W Hemat Energi",
        "Rice Cooker Mini 0,8L",
    ]


def test_uji_prints_fields(shop_config_file, toko_url, capsys):
    code = main(["uji", str(shop_config_file), "-n", "2", "--detail"])
    assert code == 0
    out = capsys.readouterr().out
    assert "5 cocok dengan CSS 'div.produk-card', 1 dibuang" in out
    assert "harga       : 1250000   (mentah: 'Rp1.250.000')" in out
    assert "Halaman berikut: " + toko_url + "/halaman-2.html" in out
    assert "Item #3" not in out
    assert "stok       : 25" in out


def test_uji_hints_for_json_ld_page(shop_config_file, toko_url, capsys):
    code = main(["uji", str(shop_config_file), toko_url + "/jsonld.html"])
    assert code == 1
    out = capsys.readouterr().out
    assert "json_ld: true" in out and "5 objek Product" in out


def test_abaikan_robots_warns(tmp_path, shop_config_file, toko_url, capsys):
    out = tmp_path / "r.json"
    text = shop_config_file.read_text(encoding="utf-8").replace(
        "/halaman-1.html", "/rahasia/index.html"
    )
    shop_config_file.write_text(text, encoding="utf-8")
    assert main(["-q", "jalankan", str(shop_config_file), "-o", str(out)]) == 1
    assert json.loads(out.read_text(encoding="utf-8")) == []
    assert main(["-q", "jalankan", str(shop_config_file), "-o", str(out), "--abaikan-robots"]) == 0
    assert "DIABAIKAN" in capsys.readouterr().err
    assert len(json.loads(out.read_text(encoding="utf-8"))) == 1


def test_config_error_exit_code(tmp_path, capsys):
    bad = tmp_path / "salah.yaml"
    bad.write_text(
        "start_urls: [x]\nitems: .a\nfields: {a: {selector: b, type: uang}}\n", encoding="utf-8"
    )
    assert main(["jalankan", str(bad)]) == 1
    assert "ERROR: Tipe 'uang'" in capsys.readouterr().err
    assert main(["jalankan", str(bad), "-o", "x.txt"]) == 1


def test_python_dash_m_help():
    proc = subprocess.run(
        [sys.executable, "-m", "scraper", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "jalankan" in proc.stdout and "monitor" in proc.stdout and "uji" in proc.stdout


def test_render_js_without_playwright_gives_clear_error(shop_config_file, capsys, monkeypatch):
    import importlib.util

    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *a: None if name == "playwright" else real_find_spec(name, *a),
    )
    assert main(["uji", str(shop_config_file), "--render", "js"]) == 1
    assert "pip install playwright" in capsys.readouterr().err
