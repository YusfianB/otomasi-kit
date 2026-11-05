"""End-to-end: sample -> clean -> merge -> report -> docs -> files via the CLI entry point."""

import pandas as pd
import pytest
from openpyxl import load_workbook
from pypdf import PdfReader

from data_tools.cli import build_parser, main


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    root = tmp_path_factory.mktemp("demo")
    assert main(["sample", "-o", str(root / "contoh")]) == 0
    return root


def test_help_is_indonesian(capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--help"])
    out = capsys.readouterr().out
    assert "cara pakai" in out and "perintah" in out


def test_sample_files_exist(demo):
    c = demo / "contoh"
    assert (c / "penjualan" / "penjualan_toko_pusat.csv").exists()
    assert (c / "penjualan" / "penjualan_cabang_bandung.xlsx").exists()
    assert (c / "dokumen" / "invoice.xlsx").exists()
    assert len(list((c / "foto").iterdir())) == 6


def test_clean_and_merge(demo, capsys):
    out = demo / "bersih"
    assert main(["clean", str(demo / "contoh" / "penjualan"), "-o", str(out)]) == 0
    files = sorted(out.glob("*.xlsx"))
    assert len(files) == 3
    df = pd.read_excel(out / "penjualan_toko_pusat_bersih.xlsx", dtype={"id_pelanggan": str})
    assert df["id_pelanggan"].str.len().eq(4).all()  # leading zeros kept
    assert pd.api.types.is_datetime64_any_dtype(df["tanggal"])
    assert pd.api.types.is_numeric_dtype(df["total"])
    assert not df.duplicated().any()

    merged = demo / "gabungan.xlsx"
    assert main(["merge", str(demo / "contoh" / "penjualan"), "-o", str(merged)]) == 0
    mdf = pd.read_excel(merged)
    assert set(mdf["source_file"]) == {"penjualan_toko_pusat.csv", "penjualan_online.csv",
                                       "penjualan_cabang_bandung.xlsx"}
    joined = demo / "join.csv"
    assert main(["merge", str(demo / "contoh" / "penjualan" / "penjualan_online.csv"),
                 str(demo / "contoh" / "pelanggan.csv"), "--kunci", "id_pelanggan",
                 "-o", str(joined)]) == 0
    jdf = pd.read_csv(joined, dtype=str)
    assert jdf["nama_pelanggan"].notna().all()
    assert jdf["no_hp"].str.startswith("08").all()


def test_report_sales_preset(demo):
    out = demo / "laporan"
    assert main(["report", str(demo / "contoh" / "penjualan"), "--preset", "penjualan",
                 "-o", str(out)]) == 0
    wb = load_workbook(out / "laporan_penjualan.xlsx", read_only=True)
    assert {"Data", "Ringkasan", "Harian", "Bulanan", "Produk Teratas", "Pivot"} <= set(
        wb.sheetnames)
    assert len(list((out / "grafik").glob("*.png"))) >= 3
    assert (out / "laporan_penjualan.html").stat().st_size > 10_000


def test_report_generic(demo):
    out = demo / "laporan_umum"
    assert main(["report", str(demo / "contoh" / "pelanggan.csv"), "--group-by", "kota",
                 "-o", str(out)]) == 0
    assert set(pd.read_excel(out / "laporan.xlsx", sheet_name=None)) >= {
        "Data", "Ringkasan", "Pivot"}


def test_docs_all_templates(demo):
    d = demo / "contoh" / "dokumen"
    profil = str(d / "profil.yaml")
    out = demo / "dokumen"
    assert main(["docs", "invoice", str(d / "invoice.xlsx"), "--profil", profil, "-o",
                 str(out / "invoice"), "--gabung", str(out / "invoice.pdf")]) == 0
    assert len(list((out / "invoice").glob("*.pdf"))) == 8
    assert len(PdfReader(str(out / "invoice.pdf")).pages) == 8
    assert main(["docs", "sertifikat", str(d / "sertifikat.csv"), "--profil", profil,
                 "-o", str(out / "sertifikat"), "--nama-file", "{nomor_sertifikat}_{nama}"]) == 0
    names = [p.name for p in (out / "sertifikat").glob("*.pdf")]
    assert len(names) == 10 and all("/" not in n for n in names)
    assert main(["docs", "surat", str(d / "surat.xlsx"), "--isi", str(d / "isi_surat.txt"),
                 "--profil", profil, "-o", str(out / "surat"), "--format", "docx"]) == 0
    assert len(list((out / "surat").glob("*.docx"))) == 6
    assert main(["docs", "slip-gaji", str(d / "slip_gaji.xlsx"), "--profil", profil,
                 "-o", str(out / "slip"), "--format", "semua", "--batas", "2"]) == 0
    assert len(list((out / "slip").iterdir())) == 4
    assert main(["docs", str(d / "template_surat_keterangan.docx"), str(d / "slip_gaji.xlsx"),
                 "-o", str(out / "kustom"), "--format", "docx"]) == 0
    assert len(list((out / "kustom").glob("*.docx"))) == 8


def test_files_commands(demo, capsys):
    foto = demo / "contoh" / "foto"
    assert main(["files", "rename", str(foto), "--pola", "produk_{no}", "--ext-kecil"]) == 0
    assert "PRATINJAU" in capsys.readouterr().out
    assert (foto / "DSC 0012.JPG").exists()  # dry-run by default
    assert main(["files", "images", str(foto), "-o", str(demo / "kecil"), "--lebar-maks", "600",
                 "--watermark", "Uji"]) == 0
    assert len(list((demo / "kecil").iterdir())) == 6
    assert main(["files", "convert", "gambar-ke-pdf", str(demo / "kecil"), "-o",
                 str(demo / "foto.pdf")]) == 0
    assert len(PdfReader(str(demo / "foto.pdf")).pages) == 6
    assert main(["files", "rename", str(demo / "kecil"), "--pola", "img_{no}",
                 "--jalankan"]) == 0
    assert min(p.name for p in (demo / "kecil").glob("img_*")).startswith("img_001")
    assert main(["files", "undo", str(demo / "kecil")]) == 0
    assert not list((demo / "kecil").glob("img_*"))


def test_cli_errors_return_1(tmp_path, capsys):
    assert main(["clean", str(tmp_path / "tidak_ada.csv")]) == 1
    assert "tidak ditemukan" in capsys.readouterr().err
