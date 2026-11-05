"""artikel: intent/keyword helpers, template drafts, front-matter, index, batch + AI path."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml

from konten import artikel
from konten.common import KontenError
from konten.llm import LLMClient
from konten.tests.fake_anthropic import FakeClient, artikel_responder, message, task_text

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def front_matter(md: str) -> dict:
    assert md.startswith("---\n")
    return yaml.safe_load(md.split("---\n")[1])


@pytest.mark.parametrize(("kw", "intent"), [
    ("cara budidaya lele di ember", "informasional"),
    ("rekomendasi tumbler stainless", "komersial"),
    ("harga kopi susu literan", "transaksional"),
    ("kopi gayo", "informasional"),
])
def test_classify_intent(kw, intent):
    assert artikel.classify_intent(kw) == intent


def test_base_keyword():
    assert artikel.base_keyword("cara budidaya lele") == "budidaya lele"
    assert artikel.base_keyword("harga iphone 15 terbaru 2026") == "iphone 15"


@pytest.mark.parametrize("kw", ["cara budidaya lele di ember", "rekomendasi tumbler stainless",
                                "harga kopi susu literan",
                                ("panduan lengkap memulai usaha katering rumahan untuk ibu rumah "
                                "tangga")])
def test_template_article_meta_limits_and_structure(kw):
    res = artikel.generate_template(artikel.ArticleJob(kw, audiens="pemula"))
    fm = front_matter(res["markdown"])
    assert len(fm["meta_title"]) <= 60
    assert len(fm["meta_description"]) <= 160
    assert fm["slug"] == artikel.slugify(kw)
    assert fm["engine"] == "template"
    assert "bukan data volume pencarian" in fm["catatan"]
    assert fm["kata_kunci_terkait"] and fm["long_tail"] and fm["saran_tautan_internal"]
    body = res["markdown"]
    assert body.count("\n# ") == 1  # single H1
    assert "## Pertanyaan yang Sering Diajukan (FAQ)" in body
    assert body.index("## Pertanyaan yang Sering") < body.index("## Kesimpulan")
    assert "{" not in body


def test_load_jobs_from_csv_and_dedupe(tmp_path):
    jobs = artikel.load_jobs("cara budidaya lele di ember", str(EXAMPLES / "keywords_contoh.csv"),
                             audiens="umum", panjang=1000, bahasa="id")
    assert [j.keyword for j in jobs][:2] == ["cara budidaya lele di ember",
                                            "rekomendasi tumbler stainless"]
    assert len(jobs) == 3  # duplicate topic removed
    assert jobs[1].audiens == "pekerja kantoran" and jobs[2].panjang == 900
    with pytest.raises(KontenError):
        artikel.load_jobs(None, None, audiens="", panjang=1000, bahasa="id")


def test_run_batch_writes_index_and_skips_existing(tmp_path):
    jobs = artikel.load_jobs(None, str(EXAMPLES / "keywords_contoh.csv"), audiens="",
                             panjang=1000, bahasa="id")
    artikel.run(jobs, tmp_path, llm=None, concurrency=1, timpa=False)
    index = pd.read_csv(tmp_path / "index_artikel.csv")
    assert len(index) == 3 and set(index["intent"]) == {"informasional", "komersial",
                                                         "transaksional"}
    md = tmp_path / "harga-kopi-susu-literan.md"
    md.write_text(md.read_text(encoding="utf-8") + "\nEDITAN MANUAL\n", encoding="utf-8")
    artikel.run(jobs, tmp_path, llm=None, concurrency=1, timpa=False)
    assert "EDITAN MANUAL" in md.read_text(encoding="utf-8")  # not overwritten
    artikel.run(jobs, tmp_path, llm=None, concurrency=1, timpa=True)
    assert "EDITAN MANUAL" not in md.read_text(encoding="utf-8")


def test_ai_two_step_flow(tmp_path):
    fake = FakeClient(artikel_responder)
    llm = LLMClient(client=fake, backoff_base=0)
    job = artikel.ArticleJob("cara budidaya lele di ember", audiens="pemula", panjang=300)
    files = artikel.run([job], tmp_path, llm=llm, concurrency=1, timpa=False)
    assert len(fake.calls) == 2
    assert "step 1 of 2" in task_text(fake.calls[0]) and "step 2 of 2" in task_text(fake.calls[1])
    md = files[1].read_text(encoding="utf-8")
    fm = front_matter(md)
    assert fm["engine"] == "ai:claude-opus-5-5"
    assert fm["slug"] == "budidaya-lele-ember"  # model slug kept in front-matter
    assert files[1].name == "cara-budidaya-lele-di-ember.md"  # file name stays stable
    assert fm["saran_tautan_internal"][0]["tujuan"] == "/pakan-lele-murah"
    assert md.index("## Pertanyaan yang Sering") < md.index("## Kesimpulan")


def test_ai_meta_title_too_long_is_retried():
    calls = {"n": 0}

    def responder(params, n):
        msg = artikel_responder(params, n)
        if "step 1 of 2" in task_text(params):
            calls["n"] += 1
            if calls["n"] == 1:
                import json

                data = json.loads(msg.content[1].text)
                data["meta_title"] = "Judul " * 20
                return message(data)
        return msg

    fake = FakeClient(responder)
    llm = LLMClient(client=fake, backoff_base=0)
    res = artikel.generate_ai(llm, artikel.ArticleJob("cara budidaya lele", panjang=300))
    assert calls["n"] == 2 and len(res["meta_title"]) <= 60
    feedback = fake.calls[1]["messages"][0]["content"][-1]["text"]
    assert "meta_title" in feedback
