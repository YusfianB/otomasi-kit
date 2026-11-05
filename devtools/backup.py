"""Backup folders/files into timestamped .zip or .tar.gz archives.

Features:
- include/exclude glob patterns (see :mod:`devtools.filters`)
- SHA-256 manifest stored inside the archive (``MANIFEST-BACKUP.json``)
- rotation: keep the last N archives and/or GFS (daily/weekly/monthly/yearly) retention
- verify and restore with checksum verification
- optional database dumps (SQLite via the stdlib backup API, PostgreSQL/MySQL via
  ``pg_dump``/``mysqldump`` when those binaries exist)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from devtools.filters import PathFilter, human_size

log = logging.getLogger("devtools.backup")

MANIFEST_NAME = "MANIFEST-BACKUP.json"
DB_FOLDER = "_database"
FORMATS = {"zip": ".zip", "tar.gz": ".tar.gz"}
TIMESTAMP_FMT = "%Y%m%d-%H%M%S"
CHUNK = 1024 * 1024
GFS_KEYS = ("harian", "mingguan", "bulanan", "tahunan")


class BackupError(Exception):
    """Error with a user-facing (Indonesian) message."""


# --------------------------------------------------------------------------- config


@dataclass
class DatabaseConfig:
    sqlite: list[Path] = field(default_factory=list)
    # Each item: {"dsn": "...", "nama": "..."} (dsn may also be a plain database name)
    postgres: list[dict[str, Any]] = field(default_factory=list)
    # Each item: {"database": "...", "user": "...", "host": "...", "port": 3306,
    #             "password_env": "NAMA_ENV", "nama": "..."}
    mysql: list[dict[str, Any]] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.sqlite or self.postgres or self.mysql)


@dataclass
class BackupConfig:
    sources: list[Path]
    destination: Path
    name: str = "backup"
    fmt: str = "zip"
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    use_default_excludes: bool = True
    keep_last: int | None = None
    gfs: dict[str, int] = field(default_factory=dict)
    databases: DatabaseConfig = field(default_factory=DatabaseConfig)

    def validate(self) -> None:
        if self.fmt not in FORMATS:
            raise BackupError(f"Format '{self.fmt}' tidak dikenal. Pilih: {', '.join(FORMATS)}")
        if not self.sources and self.databases.is_empty():
            raise BackupError("Tidak ada sumber yang di-backup (sumber/database kosong).")
        for src in self.sources:
            if not src.exists():
                raise BackupError(f"Sumber tidak ditemukan: {src}")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", self.name):
            raise BackupError(
                f"Nama backup '{self.name}' tidak valid: gunakan huruf, angka, titik, '-' atau '_'."
            )
        if self.keep_last is not None and self.keep_last < 1:
            raise BackupError("--simpan minimal 1.")
        for key, val in self.gfs.items():
            if key not in GFS_KEYS:
                raise BackupError(f"Kunci GFS '{key}' tidak dikenal. Pilih: {', '.join(GFS_KEYS)}")
            if not isinstance(val, int) or val < 1:
                raise BackupError(f"Nilai GFS '{key}' harus bilangan bulat >= 1.")


_BACKUP_KEYS = {
    "nama", "sumber", "tujuan", "format", "include", "exclude",
    "default_exclude", "simpan", "gfs", "database",
}


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _resolve(base: Path, value: str | os.PathLike) -> Path:
    p = Path(os.path.expanduser(os.path.expandvars(str(value))))
    return p if p.is_absolute() else (base / p)


def load_yaml(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise BackupError(f"File config tidak ditemukan: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise BackupError(f"Config YAML tidak valid ({path}): {exc}") from exc
    if not isinstance(data, dict):
        raise BackupError(f"Config {path} harus berupa mapping YAML.")
    return data


def config_from_mapping(data: dict[str, Any], base_dir: Path) -> BackupConfig:
    """Build a BackupConfig from the ``backup:`` section of a YAML config.

    Relative paths are resolved against ``base_dir`` (the folder of the config file).
    """
    unknown = set(data) - _BACKUP_KEYS
    if unknown:
        raise BackupError(
            f"Kunci config backup tidak dikenal: {', '.join(sorted(unknown))}. "
            f"Yang valid: {', '.join(sorted(_BACKUP_KEYS))}"
        )
    db = data.get("database") or {}
    if not isinstance(db, dict):
        raise BackupError("'database' harus berupa mapping (sqlite/postgres/mysql).")
    unknown_db = set(db) - {"sqlite", "postgres", "mysql"}
    if unknown_db:
        raise BackupError(f"Jenis database tidak dikenal: {', '.join(sorted(unknown_db))}")

    def _norm_entries(items: list, key: str) -> list[dict[str, Any]]:
        out = []
        for item in items:
            out.append({key: item} if isinstance(item, str) else dict(item))
        return out

    gfs = data.get("gfs") or {}
    if not isinstance(gfs, dict):
        raise BackupError("'gfs' harus berupa mapping, mis. {harian: 7, mingguan: 4}.")
    return BackupConfig(
        sources=[_resolve(base_dir, s) for s in _as_list(data.get("sumber"))],
        destination=_resolve(base_dir, data.get("tujuan", "backups")),
        name=str(data.get("nama", "backup")),
        fmt=str(data.get("format", "zip")),
        include=[str(x) for x in _as_list(data.get("include"))],
        exclude=[str(x) for x in _as_list(data.get("exclude"))],
        use_default_excludes=bool(data.get("default_exclude", True)),
        keep_last=data.get("simpan"),
        gfs={k: v for k, v in gfs.items() if v},
        databases=DatabaseConfig(
            sqlite=[_resolve(base_dir, s) for s in _as_list(db.get("sqlite"))],
            postgres=_norm_entries(_as_list(db.get("postgres")), "dsn"),
            mysql=_norm_entries(_as_list(db.get("mysql")), "database"),
        ),
    )


# --------------------------------------------------------------------------- helpers


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


class _HashingReader:
    """File wrapper that hashes everything read through it (used for tar streaming)."""

    def __init__(self, fh):
        self._fh = fh
        self.hash = hashlib.sha256()
        self.size = 0

    def read(self, n: int = -1) -> bytes:
        data = self._fh.read(n)
        self.hash.update(data)
        self.size += len(data)
        return data


def _unique_name(name: str, used: set[str]) -> str:
    candidate, i = name, 2
    while candidate in used:
        stem, dot, ext = name.partition(".")
        candidate = f"{stem}-{i}{dot}{ext}"
        i += 1
    used.add(candidate)
    return candidate


def archive_name(name: str, fmt: str, when: datetime) -> str:
    return f"{name}-{when.strftime(TIMESTAMP_FMT)}{FORMATS[fmt]}"


def _archive_regex(name: str) -> re.Pattern[str]:
    return re.compile(rf"^{re.escape(name)}-(\d{{8}}-\d{{6}})(?:-\d+)?(\.zip|\.tar\.gz)$")


def list_archives(destination: Path, name: str) -> list[tuple[datetime, Path]]:
    """Archives of this backup set in ``destination``, newest first."""
    rx = _archive_regex(name)
    found = []
    if not destination.is_dir():
        return found
    for p in destination.iterdir():
        m = rx.match(p.name)
        if m and p.is_file():
            found.append((datetime.strptime(m.group(1), TIMESTAMP_FMT), p))
    found.sort(key=lambda t: (t[0], t[1].name), reverse=True)
    return found


def detect_format(path: Path) -> str:
    n = path.name.lower()
    if n.endswith(".zip"):
        return "zip"
    if n.endswith((".tar.gz", ".tgz")):
        return "tar.gz"
    raise BackupError(f"Format arsip tidak dikenali dari nama file: {path.name}")


# --------------------------------------------------------------------------- database dumps


def dump_databases(db: DatabaseConfig, workdir: Path) -> list[tuple[Path, str]]:
    """Dump the configured databases into ``workdir``.

    Returns a list of ``(file, arcname)`` to put into the archive.
    """
    out: list[tuple[Path, str]] = []
    used: set[str] = set()

    for src in db.sqlite:
        if not src.is_file():
            raise BackupError(f"File SQLite tidak ditemukan: {src}")
        fname = _unique_name(f"{src.stem}.sqlite3", used)
        target = workdir / fname
        log.info("Dump SQLite %s -> %s", src, fname)
        try:
            source_conn = sqlite3.connect(f"{src.resolve().as_uri()}?mode=ro", uri=True)
            try:
                dest_conn = sqlite3.connect(target)
                try:
                    source_conn.backup(dest_conn)
                finally:
                    dest_conn.close()
            finally:
                source_conn.close()
        except sqlite3.Error as exc:
            raise BackupError(f"Gagal dump SQLite {src}: {exc}") from exc
        out.append((target, f"{DB_FOLDER}/{fname}"))

    if db.postgres:
        binary = shutil.which("pg_dump")
        if not binary:
            raise BackupError(
                "pg_dump tidak ditemukan di PATH. Install PostgreSQL client "
                "(mis. 'sudo apt install postgresql-client') atau hapus 'database.postgres' "
                "dari config."
            )
        for entry in db.postgres:
            dsn = entry.get("dsn")
            if not dsn:
                raise BackupError("Entri postgres butuh 'dsn' (mis. postgresql://user@host/db).")
            label = entry.get("nama") or re.sub(r"[^A-Za-z0-9_.-]+", "_", str(dsn).rsplit("/", 1)[-1])
            fname = _unique_name(f"{label or 'postgres'}.sql", used)
            target = workdir / fname
            log.info("Dump PostgreSQL %s -> %s", label, fname)
            # -w: never prompt for a password (use PGPASSWORD or ~/.pgpass for cron jobs)
            cmd = [binary, "-w", f"--dbname={dsn}", f"--file={target}"]
            _run_dump(cmd, "pg_dump", os.environ.copy())
            out.append((target, f"{DB_FOLDER}/{fname}"))

    if db.mysql:
        binary = shutil.which("mysqldump")
        if not binary:
            raise BackupError(
                "mysqldump tidak ditemukan di PATH. Install MySQL/MariaDB client "
                "(mis. 'sudo apt install mariadb-client') atau hapus 'database.mysql' dari config."
            )
        for entry in db.mysql:
            database = entry.get("database")
            if not database:
                raise BackupError("Entri mysql butuh 'database'.")
            fname = _unique_name(f"{entry.get('nama') or database}.sql", used)
            target = workdir / fname
            cmd = [binary, "--single-transaction", "--routines", f"--result-file={target}"]
            if entry.get("host"):
                cmd.append(f"--host={entry['host']}")
            if entry.get("port"):
                cmd.append(f"--port={entry['port']}")
            if entry.get("user"):
                cmd.append(f"--user={entry['user']}")
            cmd.append(str(database))
            env = os.environ.copy()
            pw_env = entry.get("password_env")
            if pw_env:
                if pw_env not in os.environ:
                    raise BackupError(f"Environment variable '{pw_env}' (password MySQL) tidak ada.")
                env["MYSQL_PWD"] = os.environ[pw_env]
            log.info("Dump MySQL %s -> %s", database, fname)
            _run_dump(cmd, "mysqldump", env)
            out.append((target, f"{DB_FOLDER}/{fname}"))
    return out


def _run_dump(cmd: list[str], tool: str, env: dict[str, str]) -> None:
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=3600)
    except subprocess.TimeoutExpired as exc:
        raise BackupError(f"{tool} melebihi batas waktu 1 jam.") from exc
    if proc.returncode != 0:
        raise BackupError(f"{tool} gagal (kode {proc.returncode}): {proc.stderr.strip()}")


# --------------------------------------------------------------------------- create


@dataclass
class BackupResult:
    archive: Path
    file_count: int
    total_bytes: int
    archive_bytes: int
    deleted: list[Path] = field(default_factory=list)


def _collect(config: BackupConfig) -> list[tuple[Path, str]]:
    """List ``(file, arcname)`` for every selected file of every source."""
    items: list[tuple[Path, str]] = []
    labels: set[str] = {DB_FOLDER, MANIFEST_NAME}
    dest = config.destination.resolve()
    for src in config.sources:
        src_resolved = src.resolve()
        label = _unique_name(src_resolved.name or "root", labels)
        if src_resolved.is_file():
            items.append((src_resolved, label))
            continue
        flt = PathFilter(
            include=config.include,
            exclude=config.exclude,
            use_default_excludes=config.use_default_excludes,
            # Never archive our own archives when the destination lives inside a source.
            skip_paths=[dest],
        )
        for full, rel in flt.walk(src_resolved):
            items.append((full, f"{label}/{rel}"))
    return items


def _write_zip(target: Path, items: Iterable[tuple[Path, str]], manifest: dict) -> None:
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path, arcname in items:
            info = zipfile.ZipInfo.from_file(path, arcname, strict_timestamps=False)
            info.compress_type = zipfile.ZIP_DEFLATED
            h = hashlib.sha256()
            size = 0
            with open(path, "rb") as src, zf.open(info, "w", force_zip64=True) as dst:
                for chunk in iter(lambda: src.read(CHUNK), b""):
                    h.update(chunk)
                    size += len(chunk)
                    dst.write(chunk)
            manifest["file"][arcname] = {"sha256": h.hexdigest(), "ukuran": size}
        _finish_manifest(manifest)
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2, ensure_ascii=False))


def _write_tar(target: Path, items: Iterable[tuple[Path, str]], manifest: dict) -> None:
    import io

    with tarfile.open(target, "w:gz", compresslevel=6) as tf:
        for path, arcname in items:
            info = tf.gettarinfo(str(path), arcname)
            if not info.isfile():  # symlink to file -> store content as a regular file
                info = tarfile.TarInfo(arcname)
                st = path.stat()
                info.size, info.mtime, info.mode = st.st_size, st.st_mtime, st.st_mode & 0o777
            info.uname = info.gname = ""
            with open(path, "rb") as fh:
                reader = _HashingReader(fh)
                tf.addfile(info, reader)
            manifest["file"][arcname] = {"sha256": reader.hash.hexdigest(), "ukuran": reader.size}
        _finish_manifest(manifest)
        data = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
        info = tarfile.TarInfo(MANIFEST_NAME)
        info.size = len(data)
        info.mtime = int(datetime.now().timestamp())
        info.mode = 0o644
        tf.addfile(info, io.BytesIO(data))


def _finish_manifest(manifest: dict) -> None:
    manifest["jumlah_file"] = len(manifest["file"])
    manifest["total_ukuran"] = sum(f["ukuran"] for f in manifest["file"].values())


def create_backup(
    config: BackupConfig, now: datetime | None = None, dry_run: bool = False
) -> BackupResult:
    """Create one archive, then apply rotation. Returns details of the new archive."""
    config.validate()
    now = now or datetime.now()
    dest = config.destination
    items = _collect(config)
    total = sum(p.stat().st_size for p, _ in items)
    log.info(
        "Mengumpulkan %d file (%s) dari %d sumber", len(items), human_size(total),
        len(config.sources),
    )
    fname = archive_name(config.name, config.fmt, now)
    target = dest / fname
    i = 1
    while target.exists():
        target = dest / fname.replace(FORMATS[config.fmt], f"-{i}{FORMATS[config.fmt]}")
        i += 1

    if dry_run:
        for _, arc in items:
            log.info("  [dry-run] %s", arc)
        if not config.databases.is_empty():
            log.info("  [dry-run] + dump database ke %s/", DB_FOLDER)
        log.info("[dry-run] Arsip yang akan dibuat: %s", target)
        return BackupResult(target, len(items), total, 0)

    dest.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "format": "otomasi-kit-backup",
        "versi": 1,
        "nama": config.name,
        "dibuat": now.isoformat(timespec="seconds"),
        "host": platform.node(),
        "sumber": [str(s) for s in config.sources],
        "database": [],
        "file": {},
    }
    partial = target.with_name(target.name + ".partial")
    with tempfile.TemporaryDirectory(prefix="devtools-dbdump-") as tmp:
        if not config.databases.is_empty():
            dumps = dump_databases(config.databases, Path(tmp))
            manifest["database"] = [arc for _, arc in dumps]
            items = items + dumps
        try:
            if config.fmt == "zip":
                _write_zip(partial, items, manifest)
            else:
                _write_tar(partial, items, manifest)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
    os.replace(partial, target)
    size = target.stat().st_size
    log.info(
        "Backup selesai: %s (%d file, %s -> %s)", target, manifest["jumlah_file"],
        human_size(manifest["total_ukuran"]), human_size(size),
    )
    result = BackupResult(target, manifest["jumlah_file"], manifest["total_ukuran"], size)
    if config.keep_last or config.gfs:
        result.deleted = rotate(dest, config.name, config.keep_last, config.gfs)
    return result


# --------------------------------------------------------------------------- rotation


def select_keep(
    archives: list[tuple[datetime, Path]], keep_last: int | None, gfs: dict[str, int]
) -> set[Path]:
    """Return the archives to keep. ``archives`` must be sorted newest first."""
    keep: set[Path] = set()
    if keep_last:
        keep.update(p for _, p in archives[:keep_last])
    buckets = {
        "harian": lambda d: d.date(),
        "mingguan": lambda d: tuple(d.isocalendar())[:2],
        "bulanan": lambda d: (d.year, d.month),
        "tahunan": lambda d: d.year,
    }
    for key, count in gfs.items():
        if not count:
            continue
        seen: list = []
        for when, path in archives:  # newest first -> newest archive of each period
            bucket = buckets[key](when)
            if bucket in seen:
                continue
            seen.append(bucket)
            keep.add(path)
            if len(seen) >= count:
                break
    return keep


def rotate(
    destination: Path,
    name: str,
    keep_last: int | None = None,
    gfs: dict[str, int] | None = None,
    dry_run: bool = False,
) -> list[Path]:
    """Delete old archives of backup set ``name``. Returns the deleted (or would-be) paths."""
    gfs = gfs or {}
    if not keep_last and not gfs:
        return []
    archives = list_archives(destination, name)
    keep = select_keep(archives, keep_last, gfs)
    deleted = []
    for _, path in archives:
        if path in keep:
            continue
        deleted.append(path)
        if dry_run:
            log.info("[dry-run] Akan menghapus arsip lama: %s", path.name)
        else:
            path.unlink()
            log.info("Rotasi: menghapus arsip lama %s", path.name)
    if archives:
        log.info("Rotasi: %d disimpan, %d dihapus", len(archives) - len(deleted), len(deleted))
    return deleted


# --------------------------------------------------------------------------- verify / restore


@dataclass
class VerifyResult:
    archive: Path
    ok: bool
    checked: int = 0
    missing: list[str] = field(default_factory=list)
    mismatched: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    manifest: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class _Archive:
    """Small read-only abstraction over zip and tar.gz archives."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.fmt = detect_format(self.path)
        try:
            if self.fmt == "zip":
                self._zf = zipfile.ZipFile(self.path)
                self._members = {i.filename: i for i in self._zf.infolist() if not i.is_dir()}
            else:
                self._tf = tarfile.open(self.path, "r:gz")
                self._members = {m.name: m for m in self._tf.getmembers() if not m.isdir()}
        except (zipfile.BadZipFile, tarfile.TarError, OSError, EOFError) as exc:
            raise BackupError(f"Arsip rusak atau tidak bisa dibuka ({self.path.name}): {exc}") from exc

    def names(self) -> list[str]:
        return list(self._members)

    def is_regular(self, name: str) -> bool:
        m = self._members[name]
        return True if self.fmt == "zip" else m.isfile()

    def mtime(self, name: str) -> float:
        m = self._members[name]
        if self.fmt == "zip":
            return datetime(*m.date_time).timestamp()
        return float(m.mtime)

    def open(self, name: str):
        if self.fmt == "zip":
            return self._zf.open(self._members[name])
        fh = self._tf.extractfile(self._members[name])
        if fh is None:
            raise BackupError(f"Entri {name} bukan file biasa.")
        return fh

    def read_manifest(self) -> dict[str, Any]:
        if MANIFEST_NAME not in self._members:
            raise BackupError(
                f"{self.path.name} tidak memiliki {MANIFEST_NAME} (bukan arsip devtools backup?)."
            )
        with self.open(MANIFEST_NAME) as fh:
            try:
                return json.loads(fh.read().decode("utf-8"))
            except ValueError as exc:
                raise BackupError(f"Manifest rusak: {exc}") from exc

    def close(self) -> None:
        if self.fmt == "zip":
            self._zf.close()
        else:
            self._tf.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _hash_stream(fh) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: fh.read(CHUNK), b""):
        h.update(chunk)
        size += len(chunk)
    return h.hexdigest(), size


def verify_archive(path: Path) -> VerifyResult:
    """Check every file in the archive against the SHA-256 manifest."""
    path = Path(path)
    if not path.is_file():
        raise BackupError(f"Arsip tidak ditemukan: {path}")
    result = VerifyResult(path, ok=False)
    try:
        with _Archive(path) as arc:
            manifest = arc.read_manifest()
            result.manifest = manifest
            files = manifest.get("file", {})
            names = set(arc.names())
            for arcname, meta in files.items():
                if arcname not in names:
                    result.missing.append(arcname)
                    continue
                try:
                    with arc.open(arcname) as fh:
                        digest, size = _hash_stream(fh)
                except (zipfile.BadZipFile, tarfile.TarError, OSError, EOFError) as exc:
                    log.debug("Gagal membaca %s: %s", arcname, exc)
                    result.mismatched.append(arcname)
                    continue
                result.checked += 1
                if digest != meta.get("sha256") or size != meta.get("ukuran"):
                    result.mismatched.append(arcname)
            result.unexpected = sorted(names - set(files) - {MANIFEST_NAME})
    except BackupError as exc:
        result.error = str(exc)
        return result
    except (zipfile.BadZipFile, tarfile.TarError, OSError, EOFError) as exc:
        result.error = f"Arsip rusak: {exc}"
        return result
    result.ok = not (result.missing or result.mismatched or result.unexpected)
    return result


def _safe_target(root: Path, name: str) -> Path:
    pure = PurePosixPath(name)
    windows_abs = re.match(r"^[A-Za-z]:", name) is not None
    if pure.is_absolute() or ".." in pure.parts or name.startswith(("/", "\\")) or windows_abs:
        raise BackupError(f"Path berbahaya di dalam arsip ditolak: {name}")
    target = (root / Path(*pure.parts)).resolve()
    if target != root and root not in target.parents:
        raise BackupError(f"Path keluar dari folder tujuan ditolak: {name}")
    return target


def restore_archive(
    path: Path,
    target_dir: Path,
    force: bool = False,
    verify: bool = True,
    only: list[str] | None = None,
) -> int:
    """Extract an archive into ``target_dir`` after verifying its checksums.

    ``only``: optional glob patterns (matched against archive paths) to restore a subset.
    Returns the number of restored files.
    """
    import fnmatch

    path = Path(path)
    target_dir = Path(target_dir)
    if verify:
        res = verify_archive(path)
        if not res.ok:
            raise BackupError(
                "Verifikasi checksum gagal, restore dibatalkan. " + describe_verify(res)
            )
        log.info("Checksum OK (%d file)", res.checked)
    if target_dir.exists() and any(target_dir.iterdir()) and not force:
        raise BackupError(
            f"Folder tujuan {target_dir} tidak kosong. Pakai --paksa untuk menimpa file."
        )
    target_dir.mkdir(parents=True, exist_ok=True)
    root = target_dir.resolve()
    count = 0
    with _Archive(path) as arc:
        for name in sorted(arc.names()):
            if name == MANIFEST_NAME:
                continue
            if only and not any(fnmatch.fnmatchcase(name, pat) for pat in only):
                continue
            if not arc.is_regular(name):
                log.warning("Melewati entri non-file (symlink/device): %s", name)
                continue
            dest = _safe_target(root, name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with arc.open(name) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out, CHUNK)
            mtime = arc.mtime(name)
            os.utime(dest, (mtime, mtime))
            count += 1
    log.info("Restore selesai: %d file ke %s", count, target_dir)
    return count


def describe_verify(res: VerifyResult) -> str:
    if res.error:
        return res.error
    parts = []
    if res.missing:
        parts.append(f"{len(res.missing)} file hilang ({', '.join(res.missing[:5])})")
    if res.mismatched:
        parts.append(f"{len(res.mismatched)} checksum tidak cocok ({', '.join(res.mismatched[:5])})")
    if res.unexpected:
        parts.append(f"{len(res.unexpected)} file tak terdaftar di manifest")
    return "; ".join(parts) if parts else f"OK, {res.checked} file cocok dengan manifest"
