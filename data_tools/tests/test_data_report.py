import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from data_tools import DataToolsError
from data_tools.io import Table
from data_tools.merging import SOURCE_COLUMN, concat_tables, join_tables
from data_tools.report import build_report, build_sales_report, pivot_table


def _sales_frame() -> pd.DataFrame:
    rows = []
    start = dt.date(2026, 9, 28)
    products = [("Kopi", "Minuman", 10000), ("Teh", "Minuman", 5000), ("Keripik", "Snack", 8000)]
    for day in range(10):  # 28 Sep .. 7 Okt
        d = pd.Timestamp(start + dt.timedelta(days=day))
        for i, (name, cat, price) in enumerate(products):
            if day % 3 == 0 and i == 2:
                continue  # gaps per product
            qty = day % 4 + i + 1
            rows.append({"no_transaksi": f"T{day}-{i // 2}", "tanggal": d,
                         "nama_produk": name.upper() if day == 5 and i == 0 else name,
                         "kategori": cat, "qty": qty, "harga": price, "total": qty * price})
    return pd.DataFrame(rows)


def test_concat_tables_aligns_columns():
    a = Table(pd.DataFrame({"x": [1], "y": ["a"]}), Path("a.csv"))
    b = Table(pd.DataFrame({"y": ["b"], "z": [2.5]}), Path("b.xlsx"), "Sheet1")
    out = concat_tables([a, b])
    assert list(out.columns) == ["x", "y", "z", SOURCE_COLUMN]
    assert out[SOURCE_COLUMN].tolist() == ["a.csv", "b.xlsx"]
    assert out["y"].tolist() == ["a", "b"]


def test_join_tables_on_key_with_leading_zero():
    left = Table(pd.DataFrame({"id_pelanggan": ["001", "002", "003"], "total": [1, 2, 3]}),
                 Path("jual.csv"))
    right = Table(pd.DataFrame({"ID Pelanggan": ["001", "002"], "nama": ["Ani", "Budi"]}),
                  Path("pelanggan.csv"))
    right.df.columns = ["id_pelanggan", "nama"]
    out = join_tables([left, right], "ID Pelanggan", "left")
    assert out["nama"].tolist()[:2] == ["Ani", "Budi"]
    assert pd.isna(out["nama"][2])
    inner = join_tables([left, right], "id_pelanggan", "inner")
    assert len(inner) == 2
    with pytest.raises(DataToolsError):
        join_tables([left, right], "tidak_ada")


def test_pivot_table_sum_count_mean():
    df = pd.DataFrame({"kota": ["A", "A", "B"], "nilai": [10, 20, 5]})
    pv = pivot_table(df, ["kota"], ["nilai"])
    row = pv[pv["kota"] == "A"].iloc[0]
    assert row["nilai_total"] == 30 and row["nilai_jumlah_data"] == 2
    assert row["nilai_rata_rata"] == 15


def test_generic_report(tmp_path):
    from openpyxl import load_workbook

    df = _sales_frame()
    result = build_report(df, tmp_path, group_by=["kategori"], values=["total"], title="Uji")
    wb = load_workbook(result.excel)
    assert wb.sheetnames[:3] == ["Data", "Ringkasan", "Pivot"]
    assert "Grafik" in wb.sheetnames and len(wb["Grafik"]._images) == len(result.charts)
    assert all(p.exists() and p.stat().st_size > 1000 for p in result.charts)
    html = result.html.read_text(encoding="utf-8")
    assert "<title>Uji</title>" in html and "data:image/png;base64," in html
    with pytest.raises(DataToolsError):
        build_report(df, tmp_path, values=["kategori"])


def test_sales_report_numbers(tmp_path):
    df = _sales_frame()
    result = build_sales_report(df, tmp_path)
    sheets = pd.read_excel(result.excel, sheet_name=None)
    for name in ("Data", "Ringkasan", "Harian", "Bulanan", "Produk Teratas", "Pivot", "Grafik"):
        assert name in sheets
    daily = sheets["Harian"]
    assert len(daily) == 10
    assert daily["omzet"].sum() == df["total"].sum()
    monthly = sheets["Bulanan"]
    assert monthly["bulan"].tolist() == ["September 2026", "Oktober 2026"]
    top = sheets["Produk Teratas"]
    # "KOPI" and "Kopi" are merged into one product
    assert sorted(top["produk"]) == ["Keripik", "Kopi", "Teh"]
    assert top["omzet"].sum() == df["total"].sum()
    assert dict(result.kpis)["Total omzet"].startswith("Rp ")
    assert {p.name for p in result.charts} >= {"tren_omzet_harian.png", "produk_teratas.png",
                                               "omzet_bulanan.png"}


def test_sales_report_computes_total_from_qty_and_price(tmp_path):
    df = _sales_frame().drop(columns=["total"]).rename(columns={"harga": "harga_satuan"})
    result = build_sales_report(df, tmp_path)
    top = pd.read_excel(result.excel, sheet_name="Produk Teratas")
    assert top["omzet"].sum() == (df["qty"] * df["harga_satuan"]).sum()


def test_sales_report_missing_columns(tmp_path):
    with pytest.raises(DataToolsError, match="Preset penjualan"):
        build_sales_report(pd.DataFrame({"a": [1]}), tmp_path)
