"""One-way mirror of a source folder to a destination folder.

New/changed files are copied (compared by size + mtime, or by SHA-256 with ``mode="hash"``).
With ``delete_extra=True`` (CLI ``--hapus``) files in the destination that no longer exist in
the source are removed. Excluded paths are never copied *and* never deleted.

Cloud storage is supported indirectly: point the destination at a mounted drive
(Google Drive for desktop, OneDrive, Dropbox, rclone mount) or use rclone directly.
See devtools/README.md.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from devtools.backup import BackupError, _as_list, _resolve, sha256_file
from devtools.filters import PathFilter, human_size

log = logging.getLogger("devtools.sync")

# Mounted cloud drives and FAT/exFAT disks round mtimes (FAT: 2 seconds).
MTIME_TOLERANCE = 2.0
MODES = ("ukuran", "hash")


class SyncError(BackupError):
    """Sync error with a user-facing message."""


@dataclass
class SyncConfig:
    source: Path
    destination: Path
    delete_extra: bool = False
    mode: str = "ukuran"
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    use_default_excludes: bool = True


@dataclass
class SyncSummary:
    new: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    unchanged: int = 0
    deleted: list[str] = field(default_factory=list)
    bytes_copied: int = 0
    dry_run: bool = False

    def text(self) -> str:
        prefix = "[dry-run] " if self.dry_run else ""
        verb = "akan disalin" if self.dry_run else "disalin"
        return (
            f"{prefix}Ringkasan sync: {len(self.new)} baru, {len(self.changed)} berubah, "
            f"{self.unchanged} sama, {len(self.deleted)} dihapus, "
            f"{human_size(self.bytes_copied)} {verb}"
        )


def _needs_copy(src: Path, dst: Path, mode: str) -> bool:
    s = src.stat()
    d = dst.stat()
    if s.st_size != d.st_size:
        return True
    if mode == "hash":
        return sha256_file(src) != sha256_file(dst)
    return abs(s.st_mtime - d.st_mtime) > MTIME_TOLERANCE


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.devtools-sync.tmp")
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def sync_folders(config: SyncConfig, dry_run: bool = False) -> SyncSummary:
    src = Path(config.source)
    dst = Path(config.destination)
    if config.mode not in MODES:
        raise SyncError(f"Mode '{config.mode}' tidak dikenal. Pilih: {', '.join(MODES)}")
    if not src.is_dir():
        raise SyncError(f"Folder sumber tidak ditemukan: {src}")
    src_r, dst_r = src.resolve(), dst.resolve()
    if src_r == dst_r:
        raise SyncError("Sumber dan tujuan sync tidak boleh folder yang sama.")
    if src_r in dst_r.parents:
        log.info("Tujuan berada di dalam sumber; folder tujuan otomatis dikecualikan.")
    if dst_r in src_r.parents:
        raise SyncError("Folder sumber berada di dalam folder tujuan; ini berbahaya untuk --hapus.")

    flt = PathFilter(
        include=config.include,
        exclude=config.exclude,
        use_default_excludes=config.use_default_excludes,
        skip_paths=[dst_r],
    )
    summary = SyncSummary(dry_run=dry_run)
    seen: set[str] = set()
    for full, rel in flt.walk(src):
        seen.add(rel)
        target = dst / rel
        if target.is_dir():
            raise SyncError(f"Konflik: {rel} adalah file di sumber tetapi folder di tujuan.")
        if not target.exists():
            summary.new.append(rel)
            action = "BARU   "
        elif _needs_copy(full, target, config.mode):
            summary.changed.append(rel)
            action = "UBAH   "
        else:
            summary.unchanged += 1
            continue
        summary.bytes_copied += full.stat().st_size
        log.info("%s%s%s", "[dry-run] " if dry_run else "", action, rel)
        if not dry_run:
            _copy(full, target)

    if config.delete_extra and dst.is_dir():
        # Files in the destination that the source does not have (and that are not excluded).
        protect = PathFilter(exclude=config.exclude, use_default_excludes=config.use_default_excludes)
        for dirpath, dirnames, filenames in os.walk(dst, topdown=False):
            dpath = Path(dirpath)
            rel_dir = dpath.relative_to(dst).as_posix()
            rel_dir = "" if rel_dir == "." else rel_dir
            for f in sorted(filenames):
                rel = f"{rel_dir}/{f}" if rel_dir else f
                if rel in seen or protect.is_excluded(rel):
                    continue
                if config.include and not flt.is_included(rel):
                    continue  # outside the include set -> not managed by this sync
                summary.deleted.append(rel)
                log.info("%sHAPUS  %s", "[dry-run] " if dry_run else "", rel)
                if not dry_run:
                    (dpath / f).unlink()
            if not dry_run and rel_dir and not protect.is_excluded(rel_dir):
                src_equiv = src / rel_dir
                if not src_equiv.is_dir():
                    try:
                        dpath.rmdir()  # only succeeds when empty
                    except OSError:
                        pass

    log.info(summary.text())
    return summary


def configs_from_mapping(items: Any, base_dir: Path) -> list[SyncConfig]:
    """Parse the ``sync:`` section of a YAML config (a list of jobs, or a single mapping)."""
    jobs = _as_list(items)
    allowed = {"sumber", "tujuan", "hapus", "mode", "include", "exclude", "default_exclude"}
    out = []
    for i, job in enumerate(jobs, 1):
        if not isinstance(job, dict):
            raise SyncError(f"Job sync #{i} harus berupa mapping.")
        unknown = set(job) - allowed
        if unknown:
            raise SyncError(f"Kunci job sync #{i} tidak dikenal: {', '.join(sorted(unknown))}")
        if "sumber" not in job or "tujuan" not in job:
            raise SyncError(f"Job sync #{i} butuh 'sumber' dan 'tujuan'.")
        out.append(
            SyncConfig(
                source=_resolve(base_dir, job["sumber"]),
                destination=_resolve(base_dir, job["tujuan"]),
                delete_extra=bool(job.get("hapus", False)),
                mode=str(job.get("mode", "ukuran")),
                include=[str(x) for x in _as_list(job.get("include"))],
                exclude=[str(x) for x in _as_list(job.get("exclude"))],
                use_default_excludes=bool(job.get("default_exclude", True)),
            )
        )
    return out
