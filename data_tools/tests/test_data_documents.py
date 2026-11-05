import datetime as dt

import pandas as pd
import pytest
from docx import Document
from pypdf import PdfReader

from data_tools import DataToolsError
from data_tools.documents import (
    fill_docx_template,
    generate_documents,
    load_profile,
    make_context,
    render_filename,
    render_text,
    sanitize_filename,
)


@pytest.fixture
def profile(tmp_path):
    from data_tools.sample import make_logo

    make_logo(tmp_path / "logo.png")
    path = tmp_path / "profil.yaml"
    path.write_text("nama: Toko Uji\nalamat: Jl. Uji 1\nkota: Bandung\nlogo: logo.png\n"
                    "penandatangan: Budi\njabatan: Direktur\nppn: 11\n", encoding="utf-8")
    return load_profile(path)


def _pdf_text(path) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(str(path)).pages)


def _docx_text(path) -> str:
    doc = Document(str(path))
    parts = [p.text for p in doc.paragraphs]
    stack = list(doc.tables)
    while stack:
        table = stack.pop()
        for row in table.rows:
            for cell in row.cells:
                parts.extend(p.text for p in cell.paragraphs)
                stack.extend(cell.tables)
    return "\n".join(parts)


def test_profile_resolves_logo(profile):
    assert profile["nama"] == "Toko Uji" and profile["logo"].name == "logo.png"


def test_render_text_filters():
    ctx = make_context({"Nama": "budi", "Gaji Pokok": 1250000,
                        "Tanggal": pd.Timestamp("2026-11-05")}, 1)
    text = "{{nama|judul}} {{gaji_pokok}} {{gaji_pokok|terbilang}} {{tanggal}} {{tanggal|hari}}"
    assert render_text(text, ctx) == (
        "Budi Rp 1.250.000 satu juta dua ratus lima puluh ribu 5 November 2026 "
        "Kamis, 5 November 2026"
    )
    missing = set()
    assert render_text("{{tidak_ada}}", ctx, missing) == "{{tidak_ada}}"
    assert missing == {"tidak_ada"}


def test_filename_pattern_and_sanitize():
    ctx = make_context({"nomor": "INV/2026/001", "nama": "PT Maju: Jaya?",
                        "tanggal": pd.Timestamp("2026-11-05")}, 7)
    assert render_filename("{nomor}_{nama}_{tanggal}", ctx) == \
        "INV-2026-001_PT_Maju_Jaya_2026-11-05"
    assert render_filename("{urut}", ctx) == "007"
    assert sanitize_filename('  a/b\\c*d  ') == "a-b-c-d"
    with pytest.raises(DataToolsError):
        render_filename("{salah}", ctx)


def test_invoice_groups_items(tmp_path, profile):
    df = pd.DataFrame({
        "Nomor Invoice": ["INV-1", "INV-1", "INV-2"],
        "Tanggal": [pd.Timestamp("2026-11-05")] * 3,
        "Nama Pelanggan": ["CV A", "CV A", "UD B"],
        "Item": ["Kopi", "Teh", "Gula"],
        "Qty": [2, 1, 10],
        "Harga": [10000, 5000, 15000],
    })
    result = generate_documents("invoice", df, tmp_path / "out", formats=("pdf", "docx"),
                                name_pattern="{nomor_invoice}", profile=profile,
                                merge_to=tmp_path / "semua.pdf")
    names = sorted(p.name for p in result.files)
    assert names == ["INV-1.docx", "INV-1.pdf", "INV-2.docx", "INV-2.pdf"]
    text = _pdf_text(tmp_path / "out" / "INV-1.pdf")
    # subtotal 25.000 + PPN 11% 2.750 = 27.750
    assert "Rp 25.000" in text and "Rp 2.750" in text and "Rp 27.750" in text
    assert "Kopi" in text and "Teh" in text and "Gula" not in text
    assert "dua puluh tujuh ribu tujuh ratus lima puluh" in text.lower().replace("\n", " ")
    assert len(PdfReader(str(result.merged)).pages) == 2
    assert "Rp 27.750" in _docx_text(tmp_path / "out" / "INV-1.docx")


@pytest.mark.parametrize("template", ["sertifikat", "surat", "slip-gaji"])
def test_builtin_templates_pdf_and_docx(tmp_path, profile, template):
    df = pd.DataFrame({
        "nama": ["Siti Aminah", "Joko"],
        "nik": ["0123456", "0987654"],
        "acara": ["Pelatihan UMKM", "Pelatihan UMKM"],
        "tanggal": [pd.Timestamp("2026-11-05")] * 2,
        "perihal": ["Undangan", "Undangan"],
        "periode": ["November 2026"] * 2,
        "gaji_pokok": [4000000, 5000000],
        "tunjangan_makan": [500000, 500000],
        "potongan_bpjs": [40000, 50000],
    })
    result = generate_documents(template, df, tmp_path, formats=("pdf", "docx"), profile=profile)
    pdfs = [p for p in result.files if p.suffix == ".pdf"]
    docs = [p for p in result.files if p.suffix == ".docx"]
    assert len(pdfs) == 2 and len(docs) == 2
    reader = PdfReader(str(pdfs[0]))
    assert len(reader.pages) == 1
    text = _pdf_text(pdfs[0])
    assert "Siti Aminah" in text
    assert "Siti Aminah" in _docx_text(docs[0])
    if template == "sertifikat":
        assert float(reader.pages[0].mediabox.width) > float(reader.pages[0].mediabox.height)
    if template == "slip-gaji":
        assert "Rp 4.460.000" in text  # 4.000.000 + 500.000 - 40.000
        assert "0123456" in text


def test_letter_body_placeholders(tmp_path, profile):
    df = pd.DataFrame({"nama": ["Ani"], "tanggal_acara": [pd.Timestamp("2026-11-20")]})
    body = "Yth. {{nama}}, acara pada {{tanggal_acara|hari}}. {{kolom_salah}}"
    result = generate_documents("surat", df, tmp_path, profile=profile, body=body)
    text = _pdf_text(result.files[0])
    assert "Jumat, 20 November 2026" in text
    assert result.missing == {"kolom_salah"}


def test_custom_docx_split_runs_and_formatting(tmp_path):
    doc = Document()
    p = doc.add_paragraph()
    p.add_run("Halo ")
    p.add_run("{{na").bold = True
    p.add_run("ma}}").bold = True
    p.add_run(", gaji {{gaji|rupiah}} pada {{tanggal}}.")
    table = doc.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "Kode: {{kode}}"
    doc.sections[0].header.paragraphs[0].text = "Header {{nama}}"
    template = tmp_path / "t.docx"
    doc.save(template)

    ctx = make_context({"nama": "Budi", "gaji": 3500000, "kode": "007",
                        "tanggal": pd.Timestamp(dt.date(2026, 11, 5))}, 1)
    out = fill_docx_template(template, ctx, tmp_path / "hasil.docx")
    filled = Document(str(out))
    para = filled.paragraphs[0]
    assert para.text == "Halo Budi, gaji Rp 3.500.000 pada 5 November 2026."
    assert [r.text for r in para.runs if r.bold and r.text] == ["Budi"]
    assert filled.tables[0].cell(0, 0).text == "Kode: 007"
    assert filled.sections[0].header.paragraphs[0].text == "Header Budi"


def test_custom_docx_generation(tmp_path):
    doc = Document()
    doc.add_paragraph("Nama: {{Nama Lengkap}}")
    template = tmp_path / "tpl.docx"
    doc.save(template)
    df = pd.DataFrame({"Nama Lengkap": ["Ani", "Budi"]})
    result = generate_documents(str(template), df, tmp_path / "o", formats=("docx",),
                                name_pattern="surat_{nama_lengkap}")
    assert sorted(p.name for p in result.files) == ["surat_Ani.docx", "surat_Budi.docx"]
    assert Document(str(result.files[1])).paragraphs[0].text == "Nama: Budi"


def test_unknown_template(tmp_path):
    with pytest.raises(DataToolsError):
        generate_documents("kwitansi", pd.DataFrame({"a": [1]}), tmp_path)
