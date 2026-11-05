from pathlib import Path

import yaml

from devtools.cli import main


def make_src(tmp_path: Path) -> Path:
    src = tmp_path / "data"
    (src / "sub").mkdir(parents=True)
    (src / "a.txt").write_text("a", encoding="utf-8")
    (src / "sub" / "b.log").write_text("b", encoding="utf-8")
    return src


def test_help_without_command(capsys):
    assert main([]) == 0
    assert "buat-proyek" in capsys.readouterr().out


def test_backup_verify_restore_via_cli(tmp_path, capsys):
    src = make_src(tmp_path)
    dest = tmp_path / "bk"
    assert main(["backup", str(src), "--tujuan", str(dest), "--nama", "uji",
                 "--format", "tar.gz", "--exclude", "*.log", "-q"]) == 0
    archive = Path(capsys.readouterr().out.strip())
    assert archive.is_file() and archive.name.startswith("uji-")

    assert main(["verify", str(archive)]) == 0
    assert "OK" in capsys.readouterr().out

    assert main(["restore", str(archive), "--ke", str(tmp_path / "out")]) == 0
    assert (tmp_path / "out" / "data" / "a.txt").exists()
    assert not (tmp_path / "out" / "data" / "sub" / "b.log").exists()

    assert main(["daftar-backup", str(dest), "--nama", "uji"]) == 0
    assert archive.name in capsys.readouterr().out


def test_backup_with_config_file(tmp_path, capsys):
    make_src(tmp_path)
    cfg = tmp_path / "backup.yaml"
    cfg.write_text(yaml.safe_dump({
        "backup": {"nama": "conf", "sumber": ["data"], "tujuan": "arsip", "simpan": 2},
        "sync": [{"sumber": "data", "tujuan": "cermin", "hapus": True}],
    }), encoding="utf-8")
    assert main(["backup", "--config", str(cfg), "-q"]) == 0
    assert list((tmp_path / "arsip").glob("conf-*.zip"))
    assert main(["sync", "--config", str(cfg), "-q"]) == 0
    assert "2 baru" in capsys.readouterr().out
    assert (tmp_path / "cermin" / "sub" / "b.log").exists()


def test_errors_return_exit_code_1(tmp_path):
    assert main(["backup", "-q"]) == 1  # no source
    assert main(["verify", str(tmp_path / "nope.zip"), "-q"]) == 1
    bad = tmp_path / "rusak.zip"
    bad.write_bytes(b"x")
    assert main(["verify", str(bad), "-q"]) == 1
    assert main(["sync", str(tmp_path), "-q"]) == 1  # missing TUJUAN


def test_sync_dry_run_cli(tmp_path, capsys):
    src = make_src(tmp_path)
    assert main(["sync", str(src), str(tmp_path / "dst"), "--dry-run", "-q"]) == 0
    assert "[dry-run]" in capsys.readouterr().out
    assert not (tmp_path / "dst").exists()


def test_rotate_cli(tmp_path, capsys):
    dest = tmp_path / "bk"
    dest.mkdir()
    for d in ["20260101", "20260102", "20260103"]:
        (dest / f"x-{d}-000000.zip").write_bytes(b"x")
    assert main(["rotasi", str(dest), "--nama", "x", "--simpan", "1", "--dry-run", "-q"]) == 0
    assert "Akan dihapus: 2" in capsys.readouterr().out
    assert main(["rotasi", str(dest), "--nama", "x", "--simpan", "1", "-q"]) == 0
    assert [p.name for p in dest.iterdir()] == ["x-20260103-000000.zip"]


def test_template_commands(tmp_path, capsys):
    assert main(["daftar-template"]) == 0
    out = capsys.readouterr().out
    for name in ["api-fastapi", "web-statis", "bot-telegram", "cli-python"]:
        assert name in out
    target = tmp_path / "situs"
    assert main(["buat-proyek", "web-statis", "Warung Bu Sri", "--folder", str(target),
                 "--penulis", "Sri", "-q"]) == 0
    assert "Langkah berikutnya" in capsys.readouterr().out
    assert main(["buat-proyek", "web-statis", "Warung Bu Sri", "--folder", str(target),
                 "-q"]) == 1
    assert main(["buat-proyek", "web-statis", "Warung Bu Sri", "--folder", str(target),
                 "--paksa", "-q"]) == 0
