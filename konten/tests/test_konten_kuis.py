"""kuis: text analysis, template questions, exports, PDF input and the AI path."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from konten import kuis
from konten.common import KontenError, read_text_file
from konten.llm import LLMClient
from konten.tests.fake_anthropic import FakeClient, kuis_responder, prompt_text

MATERI = Path(__file__).resolve().parents[1] / "examples" / "materi_contoh.txt"


def job(**over) -> kuis.QuizJob:
    base = {"nama": "materi_contoh", "materi": MATERI.read_text(encoding="utf-8"), "jumlah": 12,
            "tipe": list(kuis.TYPES), "mapel": "IPA", "jenjang": "SMP kelas 8"}
    return kuis.QuizJob(**{**base, **over})


def test_split_sentences_handles_headings_and_abbreviations():
    sents = kuis.split_sentences("# Judul\n\nSuhu naik 2.5 derajat dll. sehingga es mencair. "
                                 "Ini kalimat kedua!\n- poin daftar")
    assert sents == ["Suhu naik 2.5 derajat dll. sehingga es mencair.", "Ini kalimat kedua!",
                     "poin daftar"]


def test_analysis_finds_definitions_phrases_and_names():
    an = kuis.analyse(MATERI.read_text(encoding="utf-8"))
    terms = {t for t, _ in an.definitions.values()}
    assert {"Fotosintesis", "Respirasi"} <= terms
    assert "reaksi terang" in an.pools["frasa"] and "karbon dioksida" in an.pools["frasa"]
    assert "Melvin Calvin" in an.pools["nama"]
    assert "membutuhkan" not in an.pools["istilah"]  # verbs are not used as answers


def test_cause_effect():
    assert kuis.cause_effect("Suhu tinggi merusak enzim karena enzim tersusun atas protein.") == (
        "suhu tinggi merusak enzim", "Enzim tersusun atas protein.")
    assert kuis.cause_effect("Air laut menguap sehingga awan terbentuk.") == (
        "awan terbentuk", "Air laut menguap.")
    assert kuis.cause_effect("Ini kalimat biasa tanpa sebab akibat.") is None


@pytest.mark.parametrize("level", kuis.LEVELS)
def test_template_questions_are_valid(level):
    qs = kuis.generate_template(job(kesulitan=level))
    assert len(qs) == 12
    assert [q["no"] for q in qs] == list(range(1, 13))
    assert {q["tipe"] for q in qs} == set(kuis.TYPES)
    for q in qs:
        if q["tipe"] == "pilihan_ganda":
            assert len(q["opsi"]) == 4 and len({o.lower() for o in q["opsi"]}) == 4
            assert q["jawaban"] in q["opsi"] and q["kunci"] in "abcd"
            # Same capitalisation style for all options (no give-away answer).
            assert len({o[:1].isupper() for o in q["opsi"]}) == 1, q["opsi"]
        elif q["tipe"] == "benar_salah":
            assert q["jawaban"] in ("Benar", "Salah")
        elif q["tipe"] == "isian":
            assert "_____" in q["pertanyaan"] and q["jawaban"]
        else:
            assert q["pertanyaan"].startswith("Jelaskan") and q["jawaban"]
    assert len({q["pertanyaan"] for q in qs}) == 12


def test_false_statements_are_explained_with_the_original():
    qs = kuis.generate_template(job(jumlah=8, tipe=["benar_salah"]))
    false = [q for q in qs if q["jawaban"] == "Salah"]
    assert false and all("Yang benar:" in q["penjelasan"] for q in false)


def test_template_needs_material():
    with pytest.raises(KontenError, match="--engine ai"):
        kuis.generate_template(kuis.QuizJob(nama="x", topik="Sejarah"))


def test_parse_types():
    assert kuis.parse_types("pg, bs,esai") == ["pilihan_ganda", "benar_salah", "esai"]
    with pytest.raises(KontenError):
        kuis.parse_types("menjodohkan")


def test_run_writes_docs_and_exports(tmp_path):
    files = kuis.run([job()], tmp_path, llm=None, timpa=False)
    names = {f.name for f in files}
    assert {"soal.md", "kunci_jawaban.md", "kuis_export.xlsx", "kuis_export.csv"} <= names
    soal = (tmp_path / "materi-contoh" / "soal.md").read_text(encoding="utf-8")
    assert soal.startswith("# Soal: Fotosintesis (IPA — SMP kelas 8)")
    assert "## A. Pilihan Ganda" in soal and "## D. Esai" in soal
    kunci = (tmp_path / "materi-contoh" / "kunci_jawaban.md").read_text(encoding="utf-8")
    assert "Penjelasan" in kunci
    sheets = pd.read_excel(tmp_path / "materi-contoh" / "kuis_export.xlsx", sheet_name=None,
                           dtype=str)
    qz = sheets["Quizizz"]
    assert list(qz.columns)[:3] == ["Question Text", "Question Type", "Option 1"]
    mc = qz[qz["Question Type"] == "Multiple Choice"]
    assert mc["Correct Answer"].isin(["1", "2", "3", "4"]).all()
    if any(f.suffix == ".docx" for f in files):
        from docx import Document

        text = "\n".join(p.text for p in Document(str(tmp_path / "materi-contoh" / "soal.docx"))
                         .paragraphs)
        assert "Pilihan Ganda" in text


def test_pdf_material(tmp_path):
    pytest.importorskip("pypdf")
    canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    pdf = tmp_path / "materi.pdf"
    c = canvas.Canvas(str(pdf))
    y = 800
    for line in MATERI.read_text(encoding="utf-8").splitlines()[:6]:
        c.drawString(40, y, line[:95])
        y -= 20
    c.save()
    assert "Fotosintesis" in read_text_file(pdf)


def test_ai_chunks_avoid_repeats_and_clean_options(tmp_path):
    fake = FakeClient(kuis_responder)
    llm = LLMClient(client=fake, backoff_base=0)
    files = kuis.run([job(jumlah=12)], tmp_path, llm=llm, timpa=False)
    assert len(fake.calls) == 2  # 10 + 2
    first, second = fake.calls
    assert first["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert "<materi>" in prompt_text(first)
    assert "Existing questions" in prompt_text(second) and "Pertanyaan 1-0?" in prompt_text(second)
    export = pd.read_csv(next(f for f in files if f.name == "kuis_export.csv"), dtype=str)
    pg = export[export["tipe"] == "pilihan_ganda"]
    assert (pg["opsi_a"] == "Fotosintesis").all() and (pg["kunci"] == "a").all()


def test_ai_topic_only(tmp_path):
    fake = FakeClient(kuis_responder)
    llm = LLMClient(client=fake, backoff_base=0)
    kuis.run([kuis.QuizJob(nama="Sejarah Proklamasi", topik="Proklamasi kemerdekaan",
                           jumlah=3, tipe=["isian"])], tmp_path, llm=llm, timpa=False)
    assert "<materi>" not in prompt_text(fake.calls[0])
    assert "Topic: Proklamasi kemerdekaan" in prompt_text(fake.calls[0])


def test_soft_wrapped_pdf_lines_are_joined():
    text = ("Siklus Calvin pertama kali dijelaskan oleh Melvin\nCalvin pada tahun 1950. Reaksi "
            "terang terjadi di\nmembran tilakoid.\n\nJudul Bagian\n- poin satu\n- poin dua")
    assert kuis.split_sentences(text) == [
        "Siklus Calvin pertama kali dijelaskan oleh Melvin Calvin pada tahun 1950.",
        "Reaksi terang terjadi di membran tilakoid.", "Judul Bagian", "poin satu", "poin dua"]
