"""Bulk rename with preview (dry-run), JSON undo log and undo."""

from __future__ import annotations

import datetime as dt
import fnmatch
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from data_tools import DataToolsError

log = logging.getLogger("data_tools")

LOG_PREFIX = "_log_rename_"
TOKEN_RE = re.compile(r"\{(\w+)(?::([^{}]*))?\}")
TOKENS = ("nama", "ext", "no", "tanggal", "hari_ini", "folder")
INVALID = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

STATUS_OK = "ok"
STATUS_SAME = "tetap"
STATUS_CONFLICT = "bentrok"


@dataclass
class RenameOptions:
    pattern: str = "{nama}"
    prefix: str = ""
    suffix: str = ""
    find: str | None = None
    replace: str = ""
    regex: bool = False
    ignore_case: bool = False
    start: int = 1
    digits: int = 3
    case: str | None = None  # kecil | besar | judul
    spaces: str | None = None  # replacement for spaces, e.g. "_"
    date_format: str = "%Y-%m-%d"
    lower_ext: bool = False
    match: str = "*"
    sort: str = "nama"  # nama | tanggal | ukuran
    recursive: bool = False


@dataclass
class RenamePlan:
    source: Path
    target: Path
    status: str = STATUS_OK
    note: str = ""


@dataclass
class RenameLog:
    path: Path
    entries: list[dict] = field(default_factory=list)


def natural_key(text: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", text)]


def list_files(folder: Path, opts: RenameOptions) -> list[Path]:
    if not folder.is_dir():
        raise DataToolsError(f"Folder tidak ditemukan: {folder}")
    pattern = "**/*" if opts.recursive else "*"
    patterns = [p.strip() for p in opts.match.split(",") if p.strip()] or ["*"]
    files = [
        p for p in folder.glob(pattern)
        if p.is_file() and not p.name.startswith(LOG_PREFIX) and not p.name.startswith(".")
        and any(fnmatch.fnmatch(p.name.lower(), pat.lower()) for pat in patterns)
    ]
    if opts.sort == "tanggal":
        files.sort(key=lambda p: (p.stat().st_mtime, natural_key(p.name)))
    elif opts.sort == "ukuran":
        files.sort(key=lambda p: (p.stat().st_size, natural_key(p.name)))
    else:
        files.sort(key=lambda p: natural_key(str(p.relative_to(folder))))
    return files


def _apply_find(name: str, opts: RenameOptions) -> str:
    if not opts.find:
        return name
    flags = re.IGNORECASE if opts.ignore_case else 0
    if opts.regex:
        try:
            return re.sub(opts.find, opts.replace, name, flags=flags)
        except re.error as exc:
            raise DataToolsError(f"Regex tidak valid: {exc}") from exc
    if opts.ignore_case:
        return re.sub(re.escape(opts.find), lambda _m: opts.replace, name, flags=flags)
    return name.replace(opts.find, opts.replace)


def new_name(path: Path, number: int, opts: RenameOptions) -> str:
    stem, ext = path.stem, path.suffix
    if opts.lower_ext:
        ext = ext.lower()
    mtime = dt.datetime.fromtimestamp(path.stat().st_mtime)

    def token(m: re.Match) -> str:
        key, fmt = m.group(1), m.group(2)
        if key == "nama":
            return _apply_find(stem, opts)
        if key == "ext":
            return ext.lstrip(".")
        if key == "no":
            digits = int(fmt) if fmt and fmt.isdigit() else opts.digits
            return f"{number:0{digits}d}"
        if key == "tanggal":
            return mtime.strftime(fmt or opts.date_format)
        if key == "hari_ini":
            return dt.date.today().strftime(fmt or opts.date_format)
        if key == "folder":
            return path.parent.name
        raise DataToolsError(
            f"Token {{{key}}} tidak dikenal. Token yang bisa dipakai: "
            + ", ".join("{" + t + "}" for t in TOKENS)
        )

    body = TOKEN_RE.sub(token, opts.pattern)
    if opts.case == "kecil":
        body = body.lower()
    elif opts.case == "besar":
        body = body.upper()
    elif opts.case == "judul":
        body = body.title()
    body = f"{opts.prefix}{body}{opts.suffix}"
    if opts.spaces is not None:
        body = re.sub(r"\s+", opts.spaces, body)
    body = INVALID.sub("_", body).strip().rstrip(".")
    if not body:
        raise DataToolsError(f"Nama baru untuk {path.name} kosong; periksa pola/cari-ganti.")
    return body + ext


def plan_renames(folder: str | Path, opts: RenameOptions) -> list[RenamePlan]:
    folder = Path(folder)
    files = list_files(folder, opts)
    plans = [
        RenamePlan(f, f.with_name(new_name(f, opts.start + i, opts)))
        for i, f in enumerate(files)
    ]
    sources = {p.source.resolve() for p in plans}
    seen: dict[str, RenamePlan] = {}
    for plan in plans:
        if plan.target == plan.source:
            plan.status = STATUS_SAME
            continue
        key = str(plan.target.resolve()).lower()
        if key in seen:
            plan.status, plan.note = STATUS_CONFLICT, f"sama dengan {seen[key].source.name}"
            continue
        seen[key] = plan
        if plan.target.exists() and plan.target.resolve() not in sources and \
                plan.target.resolve() != plan.source.resolve():
            plan.status, plan.note = STATUS_CONFLICT, "file tujuan sudah ada"
    # A target equal (case-insensitively) to an unchanged file is also a conflict.
    unchanged = {str(p.source.resolve()).lower() for p in plans if p.status == STATUS_SAME}
    for plan in plans:
        if plan.status == STATUS_OK and str(plan.target.resolve()).lower() in unchanged and \
                str(plan.source.resolve()).lower() != str(plan.target.resolve()).lower():
            plan.status, plan.note = STATUS_CONFLICT, "bentrok dengan file yang tidak diubah"
    return plans


def apply_renames(plans: list[RenamePlan], folder: str | Path,
                  log_path: str | Path | None = None) -> RenameLog | None:
    """Rename in two phases (via temporary names, so swaps work) and write an undo log."""
    todo = [p for p in plans if p.status == STATUS_OK]
    if not todo:
        return None
    folder = Path(folder)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = Path(log_path) if log_path else folder / f"{LOG_PREFIX}{stamp}.json"
    temps = []
    token = uuid.uuid4().hex[:8]
    for i, plan in enumerate(todo):
        tmp = plan.source.with_name(f".__rename_{token}_{i}{plan.source.suffix}")
        plan.source.rename(tmp)
        temps.append(tmp)
    done = []
    for tmp, plan in zip(temps, todo):
        tmp.rename(plan.target)
        done.append({"dari": str(plan.source.resolve()), "ke": str(plan.target.resolve())})
    payload = {
        "dibuat": dt.datetime.now().isoformat(timespec="seconds"),
        "folder": str(folder.resolve()),
        "jumlah": len(done),
        "dibatalkan": False,
        "perubahan": done,
    }
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return RenameLog(log_file, done)


def latest_log(folder: Path) -> Path:
    logs = sorted(folder.glob(f"{LOG_PREFIX}*.json"))
    active = [p for p in logs if not json.loads(p.read_text(encoding="utf-8")).get("dibatalkan")]
    if not active:
        raise DataToolsError(f"Tidak ada log rename yang bisa dibatalkan di {folder}")
    return active[-1]


def undo_renames(log_or_folder: str | Path) -> tuple[Path, int, list[str]]:
    """Revert a rename log. Returns (log file, files restored, problems)."""
    path = Path(log_or_folder)
    log_file = latest_log(path) if path.is_dir() else path
    if not log_file.is_file():
        raise DataToolsError(f"File log tidak ditemukan: {log_file}")
    data = json.loads(log_file.read_text(encoding="utf-8"))
    if data.get("dibatalkan"):
        raise DataToolsError(f"Log {log_file.name} sudah pernah dibatalkan.")
    entries = data.get("perubahan", [])
    problems = []
    movable = []
    for e in entries:
        src, dst = Path(e["ke"]), Path(e["dari"])
        if not src.exists():
            problems.append(f"{src.name} tidak ditemukan (sudah dipindah/dihapus?)")
        else:
            movable.append((src, dst))
    targets = {s.resolve() for s, _ in movable}
    for src, dst in list(movable):
        if dst.exists() and dst.resolve() not in targets:
            problems.append(f"{dst.name} sudah ada, {src.name} tidak dikembalikan")
            movable.remove((src, dst))
    token = uuid.uuid4().hex[:8]
    temps = []
    for i, (src, _dst) in enumerate(movable):
        tmp = src.with_name(f".__undo_{token}_{i}{src.suffix}")
        src.rename(tmp)
        temps.append(tmp)
    for tmp, (_src, dst) in zip(temps, movable):
        tmp.rename(dst)
    data["dibatalkan"] = True
    data["dibatalkan_pada"] = dt.datetime.now().isoformat(timespec="seconds")
    log_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return log_file, len(movable), problems
