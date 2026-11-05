import datetime as dt

import pandas as pd
import pytest

from data_tools import DataToolsError
from data_tools.cleaning import (
    TYPE_DATE,
    TYPE_ID,
    TYPE_NUMBER,
    TYPE_TEXT,
    CleanOptions,
    clean_dataframe,
    normalize_column_name,
    normalize_columns,
    unify_variants,
)
from data_tools.io import (
    collect_files,
    detect_delimiter,
    detect_encoding,
    find_header_row,
    read_csv_file,
    read_excel_file,
    read_tables,
    write_excel,
)


def test_normalize_column_names():
    assert normalize_column_name(" Harga Satuan (Rp) ") == "harga_satuan_rp"
    assert normalize_column_name("TotalHarga") == "total_harga"
    assert normalize_column_name("No.") == "no"
    assert normalize_column_name("Diskon %") == "diskon_persen"
    assert normalize_columns(["Nama", "nama ", None, "2026"]) == [
        "nama", "nama_2", "kolom_3", "kolom_2026"
    ]


@pytest.mark.parametrize(
    ("delim", "name"), [(",", "koma"), (";", "titik koma"), ("\t", "tab"), ("|", "pipa")]
)
def test_detect_delimiter(delim, name):
    text = "\n".join(delim.join(row) for row in [["a", "b", "c"], ["1", "2,5", "3"],
                                                 ["4", "5", "6"]])
    if delim == ",":
        text = "a,b,c\n1,\"2,5\",3\n4,5,6"
    assert detect_delimiter(text) == delim, name


def test_detect_encoding():
    assert detect_encoding("Kue – enak".encode("cp1252")) == "cp1252"
    assert detect_encoding("Kue – enak".encode()) == "utf-8"
    assert detect_encoding("﻿a,b".encode()) == "utf-8-sig"


def test_find_header_row_skips_title():
    rows = [["LAPORAN PENJUALAN", None, None], [None, None, None], ["Tanggal", "Produk", "Qty"],
            ["05/11/2026", "Teh", "2"]]
    assert find_header_row(rows) == 2


def test_read_csv_semicolon_cp1252(tmp_path):
    path = tmp_path / "data.csv"
    path.write_bytes("Kode;Nama Produk;Harga\n007;Kue – Pandan;Rp 15.000\n".encode("cp1252"))
    table = read_csv_file(path)
    assert table.meta["delimiter"] == ";"
    assert table.meta["encoding"] == "cp1252"
    assert table.df.iloc[0].tolist() == ["007", "Kue – Pandan", "Rp 15.000"]


def test_read_excel_with_title_rows_and_all_sheets(tmp_path):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Jan"
    ws.append(["Laporan Januari"])
    ws.append([])
    ws.append(["Tanggal", "Total"])
    ws.append([dt.datetime(2026, 1, 5), 1000])
    wb.create_sheet("Feb").append(["Tanggal", "Total"])
    wb["Feb"].append(["5 Februari 2026", "Rp 2.000"])
    path = tmp_path / "laporan.xlsx"
    wb.save(path)
    tables = read_excel_file(path, all_sheets=True)
    assert [t.sheet for t in tables] == ["Jan", "Feb"]
    assert list(tables[0].df.columns) == ["Tanggal", "Total"]
    assert len(read_excel_file(path)) == 1


def test_collect_files_and_unsupported(tmp_path):
    (tmp_path / "a.csv").write_text("x\n1\n")
    (tmp_path / "~$tmp.xlsx").write_text("")
    (tmp_path / "b.txt").write_text("y\n2\n")
    (tmp_path / "c.pdf").write_text("")
    assert [p.name for p in collect_files([tmp_path])] == ["a.csv", "b.txt"]
    with pytest.raises(DataToolsError):
        collect_files([tmp_path / "c.pdf"])
    with pytest.raises(DataToolsError):
        collect_files([tmp_path / "tidak_ada.csv"])


def _messy_frame() -> pd.DataFrame:
    return pd.DataFrame({
        " Tanggal ": ["05/11/2026", "5 Nov 2026", "5 Agustus 2026", None, "05/11/2026",
                      dt.datetime(2026, 1, 2)],
        "Nama  Produk": ["  Kopi   Bubuk ", "Teh", "Gula", None, "  Kopi   Bubuk ", "Madu"],
        "Harga (Rp)": ["Rp 1.234.567,50", "1.234", "15rb", None, "Rp 1.234.567,50", 5000],
        "No HP": ["081234567890", "0812-111-222", "+62 812 333", None, "081234567890", "0811"],
        "Diskon %": ["12,5%", "10%", "0", None, "12,5%", "5%"],
        "Kosong": [None] * 6,
        "Kode Pos": ["01234", "40111", "40112", None, "01234", "40113"],
        "Qty": ["1", "2", "3", None, "1", 4],
        "Telpon": ["081234", "0822", "0833", None, "081234", "0855"],
    }, dtype=object)


def test_clean_dataframe_full():
    out, report = clean_dataframe(_messy_frame())
    assert list(out.columns) == ["tanggal", "nama_produk", "harga_rp", "no_hp", "diskon_persen",
                                 "kode_pos", "qty", "telpon"]
    assert report.empty_rows == 1 and report.empty_cols == 1 and report.duplicates == 1
    assert len(out) == 4
    assert report.types["tanggal"] == TYPE_DATE
    assert out["tanggal"].tolist()[:3] == [pd.Timestamp("2026-11-05"), pd.Timestamp("2026-11-05"),
                                           pd.Timestamp("2026-08-05")]
    assert out["nama_produk"][0] == "Kopi Bubuk"
    assert report.types["harga_rp"] == TYPE_NUMBER
    assert out["harga_rp"].tolist() == [1234567.5, 1234.0, 15000.0, 5000.0]
    assert out["diskon_persen"].tolist() == [12.5, 10.0, 0.0, 5.0]
    assert report.types["qty"] == TYPE_NUMBER and str(out["qty"].dtype) == "Int64"
    # identifiers keep their leading zeros
    assert report.types["no_hp"] == TYPE_ID and out["no_hp"][0] == "081234567890"
    assert report.types["kode_pos"] == TYPE_ID and out["kode_pos"][0] == "01234"
    assert report.types["telpon"] == TYPE_ID and out["telpon"][1] == "0822"


def test_clean_forced_types_and_keep_duplicates():
    df = pd.DataFrame({"a": ["001", "002", "001"], "b": ["1", "2", "1"],
                       "c": ["x", "05/11/2026", "x"]}, dtype=object)
    opts = CleanOptions(number_columns=["a"], text_columns=["b"], date_columns=["c"],
                        drop_duplicates=False)
    out, report = clean_dataframe(df, opts)
    assert report.types == {"a": TYPE_NUMBER, "b": TYPE_TEXT, "c": TYPE_DATE}
    assert out["a"].tolist() == [1, 2, 1]
    assert out["b"].tolist() == ["1", "2", "1"]
    assert pd.isna(out["c"][0]) and out["c"][1] == pd.Timestamp("2026-11-05")
    assert len(out) == 3


def test_big_money_numbers_are_not_ids():
    df = pd.DataFrame({"total": ["1500000000", "2500000000"]}, dtype=object)
    _out, report = clean_dataframe(df)
    assert report.types["total"] == TYPE_NUMBER
    nik = pd.DataFrame({"identitas_penduduk": ["3273012345670001", "3273012345670002"]}, dtype=object)
    assert clean_dataframe(nik)[1].types["identitas_penduduk"] == TYPE_ID


def test_unify_variants():
    s = pd.Series(["Teh Melati", "TEH MELATI", "Teh  Melati", "Kopi", None], dtype=object)
    out, changed = unify_variants(s)
    assert changed == 2
    assert out.tolist()[:4] == ["Teh Melati"] * 3 + ["Kopi"]


def test_write_excel_formats(tmp_path):
    from openpyxl import load_workbook

    df = pd.DataFrame({"tanggal": pd.to_datetime(["2026-11-05"]), "harga": [15000],
                       "qty": [2], "kode": ["007"]})
    path = write_excel(tmp_path / "x.xlsx", {"Data": df, "Blok": [("Judul", df)]})
    wb = load_workbook(path)
    ws = wb["Data"]
    assert ws["A2"].number_format == "dd/mm/yyyy"
    assert ws["B2"].number_format == '"Rp" #,##0'
    assert ws["C2"].number_format == "#,##0"
    assert ws["D2"].value == "007"
    assert wb["Blok"]["A1"].value == "Judul"


def test_read_tables_folder(tmp_path):
    (tmp_path / "a.csv").write_text("nama,nilai\nA,1\n", encoding="utf-8")
    (tmp_path / "b.csv").write_text("nama;nilai\nB;2\n", encoding="utf-8")
    tables = read_tables([tmp_path])
    assert [t.source.name for t in tables] == ["a.csv", "b.csv"]
