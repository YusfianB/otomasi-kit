import json
import sqlite3
import tarfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from devtools.backup import (
    MANIFEST_NAME,
    BackupConfig,
    BackupError,
    DatabaseConfig,
    config_from_mapping,
    create_backup,
    list_archives,
    restore_archive,
    rotate,
    select_keep,
    verify_archive,
)


def make_tree(root: Path) -> Path:
    src = root / "proyek"
    (src / "data").mkdir(parents=True)
    (src / "app.py").write_text("print('halo')\n", encoding="utf-8")
    (src / "data" / "harga.csv").write_text("nama,harga\nkopi,20000\n", encoding="utf-8")
    (src / "debug.log").write_text("log", encoding="utf-8")
    (src / ".git").mkdir()
    (src / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "app.cpython-313.pyc").write_bytes(b"\x00\x01")
    (src / "node_modules" / "lib").mkdir(parents=True)
    (src / "node_modules" / "lib" / "index.js").write_text("x", encoding="utf-8")
    return src


def archive_names(path: Path) -> set[str]:
    if path.name.endswith(".zip"):
        with zipfile.ZipFile(path) as zf:
            return set(zf.namelist())
    with tarfile.open(path) as tf:
        return {m.name for m in tf.getmembers()}


@pytest.mark.parametrize("fmt", ["zip", "tar.gz"])
def test_backup_verify_restore_roundtrip(tmp_path, fmt):
    src = make_tree(tmp_path)
    cfg = BackupConfig(sources=[src], destination=tmp_path / "bk", name="proyek", fmt=fmt)
    result = create_backup(cfg, now=datetime(2026, 1, 2, 3, 4, 5))
    assert result.archive.name == f"proyek-20260102-030405.{fmt}"
    assert result.file_count == 3

    names = archive_names(result.archive)
    assert MANIFEST_NAME in names
    assert "proyek/app.py" in names and "proyek/data/harga.csv" in names
    # default excludes
    assert not any(n.startswith(("proyek/.git", "proyek/__pycache__", "proyek/node_modules"))
                   for n in names)

    res = verify_archive(result.archive)
    assert res.ok and res.checked == 3

    out = tmp_path / "pulih"
    assert restore_archive(result.archive, out) == 3
    assert (out / "proyek" / "data" / "harga.csv").read_text(encoding="utf-8") == (
        "nama,harga\nkopi,20000\n"
    )
    assert not (out / MANIFEST_NAME).exists()


def test_manifest_contains_sha256(tmp_path):
    src = make_tree(tmp_path)
    cfg = BackupConfig(sources=[src], destination=tmp_path / "bk", name="m")
    result = create_backup(cfg)
    with zipfile.ZipFile(result.archive) as zf:
        manifest = json.loads(zf.read(MANIFEST_NAME))
    entry = manifest["file"]["proyek/app.py"]
    import hashlib

    assert entry["sha256"] == hashlib.sha256(b"print('halo')\n").hexdigest()
    assert entry["ukuran"] == len(b"print('halo')\n")
    assert manifest["jumlah_file"] == 3


def test_exclude_and_include_patterns(tmp_path):
    src = make_tree(tmp_path)
    cfg = BackupConfig(
        sources=[src], destination=tmp_path / "bk", name="x", exclude=["*.log", "data"]
    )
    names = archive_names(create_backup(cfg).archive)
    assert "proyek/app.py" in names
    assert "proyek/debug.log" not in names
    assert "proyek/data/harga.csv" not in names

    cfg2 = BackupConfig(sources=[src], destination=tmp_path / "bk2", name="y", include=["*.csv"])
    names2 = archive_names(create_backup(cfg2).archive) - {MANIFEST_NAME}
    assert names2 == {"proyek/data/harga.csv"}

    cfg3 = BackupConfig(
        sources=[src], destination=tmp_path / "bk3", name="z", use_default_excludes=False
    )
    names3 = archive_names(create_backup(cfg3).archive)
    assert "proyek/.git/HEAD" in names3


def test_destination_inside_source_is_skipped(tmp_path):
    src = make_tree(tmp_path)
    cfg = BackupConfig(sources=[src], destination=src / "backups", name="self")
    create_backup(cfg, now=datetime(2026, 1, 1, 0, 0, 0))
    second = create_backup(cfg, now=datetime(2026, 1, 1, 0, 0, 1))
    assert not any("backups/" in n for n in archive_names(second.archive))


def test_verify_detects_tampering(tmp_path):
    src = make_tree(tmp_path)
    archive = create_backup(BackupConfig(sources=[src], destination=tmp_path / "bk")).archive
    tampered = tmp_path / "rusak.zip"
    with zipfile.ZipFile(archive) as zin, zipfile.ZipFile(tampered, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item)
            if item.filename == "proyek/app.py":
                data = b"print('diubah')\n"
            zout.writestr(item, data)
    res = verify_archive(tampered)
    assert not res.ok and res.mismatched == ["proyek/app.py"]
    with pytest.raises(BackupError, match="checksum"):
        restore_archive(tampered, tmp_path / "out")


def test_verify_garbage_file(tmp_path):
    bad = tmp_path / "bukan-arsip.zip"
    bad.write_bytes(b"bukan zip")
    res = verify_archive(bad)
    assert not res.ok and res.error


def test_restore_refuses_non_empty_folder(tmp_path):
    src = make_tree(tmp_path)
    archive = create_backup(BackupConfig(sources=[src], destination=tmp_path / "bk")).archive
    out = tmp_path / "out"
    out.mkdir()
    (out / "lain.txt").write_text("x", encoding="utf-8")
    with pytest.raises(BackupError, match="tidak kosong"):
        restore_archive(archive, out)
    assert restore_archive(archive, out, force=True) == 3
    assert restore_archive(archive, tmp_path / "sebagian", only=["*.csv"]) == 1


def test_restore_rejects_path_traversal(tmp_path):
    evil = tmp_path / "jahat.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../keluar.txt", "x")
    with pytest.raises(BackupError, match="berbahaya"):
        restore_archive(evil, tmp_path / "out", verify=False)
    assert not (tmp_path / "keluar.txt").exists()


def test_rotation_keep_last(tmp_path):
    src = make_tree(tmp_path)
    dest = tmp_path / "bk"
    base = datetime(2026, 3, 1, 1, 0, 0)
    for i in range(5):
        cfg = BackupConfig(sources=[src], destination=dest, name="r", keep_last=3)
        create_backup(cfg, now=base + timedelta(days=i))
    remaining = [p.name for _, p in list_archives(dest, "r")]
    assert remaining == [
        "r-20260305-010000.zip", "r-20260304-010000.zip", "r-20260303-010000.zip",
    ]


def test_rotation_ignores_other_backup_sets(tmp_path):
    dest = tmp_path / "bk"
    dest.mkdir()
    for name in ["a-20260101-000000.zip", "a-20260102-000000.zip", "lain-20260101-000000.zip",
                 "catatan.txt"]:
        (dest / name).write_bytes(b"x")
    deleted = rotate(dest, "a", keep_last=1)
    assert [p.name for p in deleted] == ["a-20260101-000000.zip"]
    assert (dest / "lain-20260101-000000.zip").exists() and (dest / "catatan.txt").exists()


def test_gfs_selection():
    # One archive per day for 60 days, newest first.
    start = datetime(2026, 1, 1, 2, 0, 0)
    archives = [(start + timedelta(days=i), Path(f"b-{i:02d}")) for i in range(60)][::-1]
    keep = select_keep(archives, keep_last=None, gfs={"harian": 7, "mingguan": 4, "bulanan": 3})
    days = {p.name for p in keep}
    # 7 newest days
    assert {f"b-{i:02d}" for i in range(53, 60)} <= days
    # newest archive of each month: Jan 31 (index 30) and Feb 28 (index 58); Mar 1 (59)
    assert {"b-30", "b-58", "b-59"} <= days
    assert len(keep) < 60


def test_dry_run_creates_nothing(tmp_path):
    src = make_tree(tmp_path)
    cfg = BackupConfig(sources=[src], destination=tmp_path / "bk")
    result = create_backup(cfg, dry_run=True)
    assert result.file_count == 3
    assert not (tmp_path / "bk").exists()


def test_sqlite_dump_included(tmp_path):
    db = tmp_path / "toko.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE produk (nama TEXT)")
    conn.execute("INSERT INTO produk VALUES ('kopi')")
    conn.commit()
    conn.close()
    cfg = BackupConfig(
        sources=[], destination=tmp_path / "bk", name="db", databases=DatabaseConfig(sqlite=[db])
    )
    result = create_backup(cfg)
    assert "_database/toko.sqlite3" in archive_names(result.archive)
    out = tmp_path / "out"
    restore_archive(result.archive, out)
    conn = sqlite3.connect(out / "_database" / "toko.sqlite3")
    assert conn.execute("SELECT nama FROM produk").fetchall() == [("kopi",)]
    conn.close()


def test_missing_pg_dump_gives_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr("devtools.backup.shutil.which", lambda name: None)
    cfg = BackupConfig(
        sources=[], destination=tmp_path / "bk",
        databases=DatabaseConfig(postgres=[{"dsn": "postgresql://localhost/toko"}]),
    )
    with pytest.raises(BackupError, match="pg_dump tidak ditemukan"):
        create_backup(cfg)
    assert not list((tmp_path / "bk").glob("*.zip"))
    assert not list((tmp_path / "bk").glob("*.partial"))


def test_config_from_mapping_resolves_relative_paths(tmp_path):
    cfg = config_from_mapping(
        {"nama": "x", "sumber": ["data", "/abs/path"], "tujuan": "../out", "simpan": 3,
         "gfs": {"harian": 7}, "database": {"sqlite": ["app.db"]}},
        tmp_path / "conf",
    )
    assert cfg.sources[0] == tmp_path / "conf" / "data"
    assert cfg.sources[1] == Path("/abs/path")
    assert cfg.destination == tmp_path / "out"
    assert cfg.keep_last == 3 and cfg.gfs == {"harian": 7}
    assert cfg.databases.sqlite == [tmp_path / "conf" / "app.db"]
    with pytest.raises(BackupError, match="tidak dikenal"):
        config_from_mapping({"sumbr": "x"}, tmp_path)


def test_validation_errors(tmp_path):
    with pytest.raises(BackupError, match="tidak ditemukan"):
        create_backup(BackupConfig(sources=[tmp_path / "nope"], destination=tmp_path / "bk"))
    with pytest.raises(BackupError, match="Format"):
        create_backup(BackupConfig(sources=[tmp_path], destination=tmp_path / "bk", fmt="rar"))
    with pytest.raises(BackupError, match="Nama backup"):
        create_backup(BackupConfig(sources=[tmp_path], destination=tmp_path / "bk", name="a b"))
