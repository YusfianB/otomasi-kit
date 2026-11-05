"""CLI: help text, engine selection, error messages and exit codes."""

from __future__ import annotations

from pathlib import Path

import pytest

from konten.cli import main
from konten.tests.fake_anthropic import FakeClient, produk_responder

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)


def test_help_is_indonesian(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "sosmed" in out and "kuis" in out and "Contoh:" in out


def test_engine_ai_without_key_fails_clearly(no_key, tmp_path, capsys):
    code = main(["produk", "-i", str(EXAMPLES / "produk_contoh.csv"), "--engine", "ai",
                 "-o", str(tmp_path)])
    assert code == 1
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().out


def test_ai_engine_selected_when_key_present(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("OTOMASI_MODEL", "claude-sonnet-5-5")
    fake = FakeClient(produk_responder)
    code = main(["produk", "-i", str(EXAMPLES / "produk_contoh.csv"), "-o", str(tmp_path),
                 "--effort", "low"], client=fake)
    out = capsys.readouterr().out
    assert code == 0
    assert "model claude-sonnet-5-5" in out and "Pemakaian API" in out
    assert fake.calls[0]["model"] == "claude-sonnet-5-5"
    assert fake.calls[0]["output_config"]["effort"] == "low"


def test_partial_failure_exit_code(no_key, tmp_path, capsys):
    f = tmp_path / "p.csv"
    f.write_text("nama,harga\nKaos,1000\n,2000\n", encoding="utf-8")
    code = main(["produk", "-i", str(f), "-o", str(tmp_path / "out")])
    assert code == 2
    assert "gagal" in capsys.readouterr().out


def test_every_subcommand_runs_with_template_engine(no_key, tmp_path):
    assert main(["sosmed", "--profil", str(EXAMPLES / "brand.yaml"), "--jumlah", "2",
                 "-o", str(tmp_path / "s")]) == 0
    assert main(["artikel", "--topik", "tips foto produk pakai hp", "-o", str(tmp_path / "a")]) == 0
    assert main(["produk", "-i", str(EXAMPLES / "produk_contoh.csv"), "-o",
                 str(tmp_path / "p")]) == 0
    assert main(["kuis", "--materi", str(EXAMPLES / "materi_contoh.txt"), "--jumlah", "5",
                 "--tipe", "pg,isian", "-o", str(tmp_path / "k")]) == 0
    assert (tmp_path / "a" / "tips-foto-produk-pakai-hp.md").exists()


def test_kuis_topic_only_template_explains(no_key, tmp_path, capsys):
    assert main(["kuis", "--topik", "Sejarah", "-o", str(tmp_path)]) == 1
    assert "--engine ai" in capsys.readouterr().out
