"""Shared helpers: errors, logging, text utilities, table I/O and the resumable batch runner."""

from __future__ import annotations

import hashlib
import json
import re
import sys
import threading
import unicodedata
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ENGINES = ("auto", "ai", "template")
LANGS = ("id", "en")


class KontenError(Exception):
    """User-facing error. The message is already human readable (Bahasa Indonesia)."""


# --------------------------------------------------------------------------- logging

_print_lock = threading.Lock()


def info(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def warn(msg: str) -> None:
    with _print_lock:
        print(f"PERINGATAN: {msg}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- text utils


def stable_seed(*parts: Any) -> int:
    """Deterministic seed from arbitrary inputs (``hash()`` is randomized per process)."""
    raw = "|".join(json.dumps(p, sort_keys=True, ensure_ascii=False, default=str) for p in parts)
    return int(hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16], 16)


def slugify(text: str, max_len: int = 60) -> str:
    norm = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", norm.lower()).strip("-")
    if len(slug) > max_len:
        slug = slug[:max_len].rsplit("-", 1)[0] or slug[:max_len]
    return slug or "tanpa-judul"


def truncate(text: str, limit: int, ellipsis: str = "") -> str:
    """Cut ``text`` to at most ``limit`` chars on a word boundary."""
    text = text.strip()
    if len(text) <= limit:
        return text
    room = limit - len(ellipsis)
    cut = text[:room]
    if " " in cut and not text[room : room + 1].isspace():
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:-–|") + ellipsis


def ucfirst(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def title_case_id(text: str) -> str:
    """Title case that keeps short Indonesian/English connectors lowercase."""
    small = {"dan", "di", "ke", "dari", "untuk", "yang", "atau", "dengan", "pada", "vs",
             "and", "or", "of", "for", "the", "to", "in", "a", "an", "&"}
    words = text.split()
    out = []
    for i, w in enumerate(words):
        if i > 0 and w.lower() in small:
            out.append(w.lower())
        elif w.isupper() and len(w) > 1:
            out.append(w)  # keep acronyms (UMKM, SEO)
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


def split_list(value: Any) -> list[str]:
    """Turn ``"a; b, c"`` / list / NaN into a clean list of strings."""
    if value is None:
        return []
    if isinstance(value, float) and value != value:  # NaN
        return []
    if isinstance(value, (list, tuple)):
        items: list[str] = []
        for v in value:
            items.extend(split_list(v))
        return items
    text = str(value).strip()
    if not text:
        return []
    sep = r"[;\n|]" if re.search(r"[;\n|]", text) else r","
    return [p.strip(" -•\t") for p in re.split(sep, text) if p.strip(" -•\t")]


def format_rupiah(value: Any) -> str:
    """``89000`` / ``"89.000"`` / ``"Rp 89rb"`` -> ``"Rp89.000"``. Returns "" if unparseable."""
    if value is None or (isinstance(value, float) and value != value):
        return ""
    if isinstance(value, (int, float)):
        num = int(round(value))
    else:
        text = str(value).lower().replace("rp", "").strip()
        mult = 1
        if re.search(r"\d\s*(rb|ribu|k)\b", text):
            mult = 1000
        elif re.search(r"\d\s*(jt|juta)\b", text):
            mult = 1_000_000
        digits = re.sub(r"[^\d,]", "", text)
        if not digits:
            return ""
        if "," in digits and mult > 1:
            num = int(float(digits.replace(",", ".")) * mult)
        else:
            num = int(digits.replace(",", "")) * mult
    return "Rp" + f"{num:,}".replace(",", ".")


# --------------------------------------------------------------------------- file I/O


def read_text_file(path: str | Path) -> str:
    p = Path(path)
    if not p.exists():
        raise KontenError(f"File tidak ditemukan: {p}")
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise KontenError(
                "Membaca PDF butuh paket 'pypdf' (pip install pypdf). "
                "Atau simpan materi sebagai .txt/.md."
            ) from exc
        reader = PdfReader(str(p))
        text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
        if not text.strip():
            raise KontenError(
                f"Tidak ada teks yang bisa dibaca dari {p.name} (mungkin PDF hasil scan). "
                "Salin teksnya ke file .txt lalu coba lagi."
            )
        return text
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return p.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    raise KontenError(f"Tidak bisa membaca encoding file {p}")  # pragma: no cover


def read_table(path: str | Path):
    """Read CSV/XLSX into a DataFrame with stripped column names (all values as str)."""
    import pandas as pd

    p = Path(path)
    if not p.exists():
        raise KontenError(f"File tidak ditemukan: {p}")
    suffix = p.suffix.lower()
    try:
        if suffix in (".xlsx", ".xlsm", ".xls"):
            df = pd.read_excel(p, dtype=str)
        elif suffix in (".csv", ".txt", ".tsv"):
            df = pd.read_csv(p, dtype=str, sep=None, engine="python", encoding="utf-8-sig")
        else:
            raise KontenError(f"Format file tidak didukung: {suffix} (pakai .csv atau .xlsx)")
    except KontenError:
        raise
    except Exception as exc:  # noqa: BLE001 - surface any parse error in Indonesian
        raise KontenError(f"Gagal membaca {p.name}: {exc}") from exc
    df.columns = [str(c).strip() for c in df.columns]
    df = df.dropna(how="all").fillna("")
    return df


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_excel(path: Path, sheets: dict[str, Any]) -> None:
    """Write ``{sheet_name: DataFrame}`` with wrapped text and sensible column widths."""
    import pandas as pd
    from openpyxl.styles import Alignment, Font

    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for name, df in sheets.items():
            sheet = re.sub(r"[\[\]:*?/\\]", "-", name)[:31]
            df.to_excel(writer, sheet_name=sheet, index=False)
            ws = writer.sheets[sheet]
            ws.freeze_panes = "A2"
            for cell in ws[1]:
                cell.font = Font(bold=True)
            for idx, col in enumerate(df.columns, start=1):
                values = [str(col)] + [str(v) for v in df[col].tolist()[:200]]
                longest = max((max((len(line) for line in v.split("\n")), default=0)
                               for v in values), default=10)
                letter = ws.cell(row=1, column=idx).column_letter
                ws.column_dimensions[letter].width = max(10, min(60, longest + 2))
            for row in ws.iter_rows(min_row=2):
                for cell in row:
                    cell.alignment = Alignment(wrap_text=True, vertical="top")


# --------------------------------------------------------------------------- batch runner


@dataclass
class Item:
    key: str  # file-system safe id; result cached at <items_dir>/<key>.json
    payload: Any
    label: str = ""


@dataclass
class BatchResult:
    results: dict[str, Any] = field(default_factory=dict)  # key -> result (incl. skipped)
    done: int = 0
    skipped: int = 0
    failed: dict[str, str] = field(default_factory=dict)  # key -> error message


def run_batch(
    items: Iterable[Item],
    worker: Callable[[Any], Any],
    *,
    items_dir: Path,
    concurrency: int = 1,
    timpa: bool = False,
    label: str = "item",
) -> BatchResult:
    """Run ``worker`` for every item, caching each result as JSON so reruns resume.

    Items whose JSON already exists are skipped unless ``timpa`` (overwrite) is set.
    One failing item never aborts the batch; failures are reported at the end.
    """
    items = list(items)
    total = len(items)
    items_dir.mkdir(parents=True, exist_ok=True)
    out = BatchResult()
    todo: list[tuple[int, Item]] = []
    for i, item in enumerate(items, start=1):
        cache = items_dir / f"{item.key}.json"
        if cache.exists() and not timpa:
            try:
                out.results[item.key] = read_json(cache)
                out.skipped += 1
                info(f"[{i}/{total}] LEWAT  {item.label or item.key} (sudah ada, pakai --timpa)")
                continue
            except (OSError, json.JSONDecodeError):
                warn(f"Cache rusak, dibuat ulang: {cache.name}")
        todo.append((i, item))

    def _run(i: int, item: Item) -> tuple[int, Item, Any]:
        result = worker(item.payload)
        write_json(items_dir / f"{item.key}.json", result)
        return i, item, result

    def _record_ok(i: int, item: Item, result: Any) -> None:
        out.results[item.key] = result
        out.done += 1
        info(f"[{i}/{total}] OK     {item.label or item.key}")

    def _record_fail(i: int, item: Item, exc: Exception) -> None:
        msg = str(exc) or exc.__class__.__name__
        out.failed[item.key] = msg
        info(f"[{i}/{total}] GAGAL  {item.label or item.key}: {msg}")

    if concurrency <= 1 or len(todo) <= 1:
        for i, item in todo:
            try:
                _record_ok(*_run(i, item))
            except FatalError:
                raise
            except Exception as exc:  # noqa: BLE001 - keep batch going
                _record_fail(i, item, exc)
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(_run, i, item): (i, item) for i, item in todo}
            for fut in as_completed(futures):
                i, item = futures[fut]
                try:
                    _record_ok(*fut.result())
                except FatalError:
                    for f in futures:
                        f.cancel()
                    raise
                except Exception as exc:  # noqa: BLE001
                    _record_fail(i, item, exc)
    info(
        f"Selesai {label}: {out.done} dibuat, {out.skipped} dilewati, {len(out.failed)} gagal "
        f"(total {total})."
    )
    return out


class FatalError(KontenError):
    """Error that should stop the whole batch (e.g. invalid API key)."""


def ordered(results: dict[str, Any], keys: Iterable[str]) -> list[Any]:
    return [results[k] for k in keys if k in results]
