import os
from pathlib import Path

import pytest

from devtools.sync import SyncConfig, SyncError, configs_from_mapping, sync_folders


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def folders(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    write(src / "a.txt", "satu")
    write(src / "sub" / "b.txt", "dua")
    write(src / ".git" / "config", "x")
    write(src / "cache.tmp", "tmp")
    return src, dst


def test_dry_run_changes_nothing(folders):
    src, dst = folders
    summary = sync_folders(SyncConfig(src, dst), dry_run=True)
    assert sorted(summary.new) == ["a.txt", "cache.tmp", "sub/b.txt"]
    assert summary.dry_run and "[dry-run]" in summary.text()
    assert not dst.exists()


def test_actual_sync_then_idempotent(folders):
    src, dst = folders
    summary = sync_folders(SyncConfig(src, dst, exclude=["*.tmp"]))
    assert sorted(summary.new) == ["a.txt", "sub/b.txt"]
    assert (dst / "sub" / "b.txt").read_text(encoding="utf-8") == "dua"
    assert not (dst / ".git").exists() and not (dst / "cache.tmp").exists()
    # mtime is preserved, so a second run copies nothing
    again = sync_folders(SyncConfig(src, dst, exclude=["*.tmp"]))
    assert again.new == [] and again.changed == [] and again.unchanged == 2


def test_changed_file_detected(folders):
    src, dst = folders
    sync_folders(SyncConfig(src, dst))
    write(src / "a.txt", "satu-diubah")
    summary = sync_folders(SyncConfig(src, dst))
    assert summary.changed == ["a.txt"]
    assert (dst / "a.txt").read_text(encoding="utf-8") == "satu-diubah"


def test_hash_mode_detects_same_size_change(folders):
    src, dst = folders
    sync_folders(SyncConfig(src, dst))
    write(src / "a.txt", "SATU")  # same size
    st = (dst / "a.txt").stat()
    os.utime(src / "a.txt", (st.st_atime, st.st_mtime))  # same mtime too
    assert sync_folders(SyncConfig(src, dst)).changed == []
    assert sync_folders(SyncConfig(src, dst, mode="hash")).changed == ["a.txt"]


def test_hapus_removes_extras_but_keeps_excluded(folders):
    src, dst = folders
    sync_folders(SyncConfig(src, dst))
    write(dst / "lama.txt", "hapus saya")
    write(dst / "folder-lama" / "c.txt", "hapus juga")
    write(dst / ".git" / "keep", "dilindungi")
    no_delete = sync_folders(SyncConfig(src, dst))
    assert no_delete.deleted == [] and (dst / "lama.txt").exists()

    preview = sync_folders(SyncConfig(src, dst, delete_extra=True), dry_run=True)
    assert sorted(preview.deleted) == ["folder-lama/c.txt", "lama.txt"]
    assert (dst / "lama.txt").exists()

    summary = sync_folders(SyncConfig(src, dst, delete_extra=True))
    assert sorted(summary.deleted) == ["folder-lama/c.txt", "lama.txt"]
    assert not (dst / "lama.txt").exists()
    assert not (dst / "folder-lama").exists()
    assert (dst / ".git" / "keep").exists()
    assert (dst / "a.txt").exists()


def test_rejects_same_or_parent_folder(tmp_path):
    src = tmp_path / "src"
    write(src / "a.txt", "x")
    with pytest.raises(SyncError, match="sama"):
        sync_folders(SyncConfig(src, src))
    with pytest.raises(SyncError, match="di dalam folder tujuan"):
        sync_folders(SyncConfig(src, tmp_path))
    with pytest.raises(SyncError, match="tidak ditemukan"):
        sync_folders(SyncConfig(tmp_path / "nope", tmp_path / "dst"))


def test_destination_inside_source_is_excluded(tmp_path):
    src = tmp_path / "src"
    write(src / "a.txt", "x")
    dst = src / "mirror"
    sync_folders(SyncConfig(src, dst))
    summary = sync_folders(SyncConfig(src, dst))
    assert summary.new == [] and not (dst / "mirror").exists()


def test_configs_from_mapping(tmp_path):
    jobs = configs_from_mapping(
        [{"sumber": "a", "tujuan": "/b", "hapus": True, "mode": "hash", "exclude": "*.tmp"}],
        tmp_path,
    )
    assert jobs[0].source == tmp_path / "a"
    assert jobs[0].destination == Path("/b")
    assert jobs[0].delete_extra and jobs[0].mode == "hash" and jobs[0].exclude == ["*.tmp"]
    with pytest.raises(SyncError, match="butuh"):
        configs_from_mapping([{"sumber": "a"}], tmp_path)
