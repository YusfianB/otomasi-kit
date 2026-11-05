"""Reading CSV/Excel files (auto-detect delimiter, encoding, header row) and writing results."""

from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from data_tools import DataToolsError
from data_tools.formatting import is_money_column

log = logging.getLogger("data_tools")

CSV_EXTENSIONS = {".csv", ".tsv", ".txt"}
EXCEL_EXTENSIONS = {".xlsx", ".xlsm", ".xls"}
TABLE_EXTENSIONS = CSV_EXTENSIONS | EXCEL_EXTENSIONS
DELIMITERS = [",", ";", "\t", "|"]
DELIMITER_NAMES = {",": "koma", ";": "titik koma", "\t": "tab", "|": "pipa"}


@dataclass
class Table:
    """A table read from a file (one sheet for Excel)."""

    df: pd.DataFrame
    source: Path
    sheet: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{self.source.name} [{self.sheet}]" if self.sheet else self.source.name


# --------------------------------------------------------------------------- discovery


def collect_files(
    inputs: Iterable[str | Path], extensions: set[str] = TABLE_EXTENSIONS, recursive: bool = False
) -> list[Path]:
    """Expand files/folders into a sorted list of matching files (skips Office temp files)."""
    result: list[Path] = []
    for item in inputs:
        path = Path(item)
        if path.is_dir():
            pattern = "**/*" if recursive else "*"
            found = sorted(
                p for p in path.glob(pattern)
                if p.is_file() and p.suffix.lower() in extensions and not p.name.startswith("~$")
            )
            if not found:
                log.warning("Peringatan: tidak ada file yang didukung di folder %s", path)
            result.extend(found)
        elif path.is_file():
            if path.suffix.lower() not in extensions:
                raise DataToolsError(
                    f"Format file tidak didukung: {path.name} "
                    f"(yang didukung: {', '.join(sorted(extensions))})"
                )
            result.append(path)
        else:
            raise DataToolsError(f"File atau folder tidak ditemukan: {path}")
    seen: set[Path] = set()
    unique = []
    for p in result:
        key = p.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


# --------------------------------------------------------------------------- CSV


def detect_encoding(raw: bytes) -> str:
    """Return the first encoding that decodes ``raw``: utf-8(-sig), utf-16, cp1252, latin-1."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    for enc in ("utf-8", "cp1252"):
        try:
            raw.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return "latin-1"


def detect_delimiter(text: str) -> str:
    """Pick the delimiter (, ; tab |) that gives the most consistent multi-column rows."""
    lines = [ln for ln in text.splitlines() if ln.strip()][:50]
    if not lines:
        return ","
    best, best_score = ",", (-1.0, 0)
    for delim in DELIMITERS:
        counts = [len(row) for row in csv.reader(lines, delimiter=delim)]
        if not counts:
            continue
        mode = max(set(counts), key=counts.count)
        if mode < 2:
            continue
        consistency = counts.count(mode) / len(counts)
        score = (consistency, mode)
        if score > best_score:
            best, best_score = delim, score
    return best


def find_header_row(rows: list[list[Any]], scan: int = 20) -> int:
    """Index of the header row: the first row that is (nearly) as wide as the table.

    Handles title lines ("LAPORAN PENJUALAN ...") and blank lines above the real header.
    """
    def filled(row):
        return sum(1 for v in row if v is not None and str(v).strip() != "")

    sample = rows[:scan]
    if not sample:
        return 0
    width = max(filled(r) for r in sample)
    threshold = max(1, math.ceil(width * 0.6))
    for i, row in enumerate(sample):
        if filled(row) >= threshold:
            return i
    return 0


def _rows_to_frame(rows: list[list[Any]], header_row: int | None) -> tuple[pd.DataFrame, int]:
    if not rows:
        return pd.DataFrame(), 0
    idx = find_header_row(rows) if header_row is None else header_row
    if idx >= len(rows):
        raise DataToolsError(f"Baris header {idx + 1} melebihi jumlah baris ({len(rows)})")
    width = max(len(r) for r in rows[idx:]) if rows[idx:] else 0
    header = list(rows[idx]) + [None] * (width - len(rows[idx]))
    header = ["" if h is None else str(h) for h in header]
    body = [list(r) + [None] * (width - len(r)) for r in rows[idx + 1:]]
    return pd.DataFrame(body, columns=header, dtype=object), idx


def read_csv_file(
    path: str | Path,
    encoding: str | None = None,
    delimiter: str | None = None,
    header_row: int | None = None,
) -> Table:
    """Read a CSV as text cells (no type guessing, so leading zeros survive)."""
    path = Path(path)
    raw = path.read_bytes()
    enc = encoding or detect_encoding(raw)
    try:
        text = raw.decode(enc)
    except (UnicodeDecodeError, LookupError) as exc:
        raise DataToolsError(f"Gagal membaca {path.name} dengan encoding {enc}: {exc}") from exc
    if enc == "utf-16":
        text = text.lstrip("﻿")
    delim = delimiter or ("\t" if path.suffix.lower() == ".tsv" else detect_delimiter(text))
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    rows = [[None if c == "" else c for c in r] for r in rows]
    df, idx = _rows_to_frame(rows, header_row)
    log.debug("%s: encoding=%s pemisah=%r header=baris %d", path.name, enc, delim, idx + 1)
    return Table(df, path, None, {"encoding": enc, "delimiter": delim, "header_row": idx})


# --------------------------------------------------------------------------- Excel


def excel_sheet_names(path: str | Path) -> list[str]:
    path = Path(path)
    if path.suffix.lower() == ".xls":
        return list(_read_xls(path, None).keys())
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True)
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()


def _read_xls(path: Path, sheet: str | None):
    try:
        import xlrd  # noqa: F401
    except ImportError as exc:
        raise DataToolsError(
            f"File .xls (Excel lama) butuh paket 'xlrd': pip install xlrd  — atau simpan ulang "
            f"{path.name} sebagai .xlsx"
        ) from exc
    frames = pd.read_excel(path, sheet_name=sheet, header=None, dtype=object, engine="xlrd")
    if isinstance(frames, pd.DataFrame):
        frames = {sheet: frames}
    return {
        name: [[None if pd.isna(v) else v for v in row] for row in frame.itertuples(index=False)]
        for name, frame in frames.items()
    }


def _read_xlsx_rows(path: Path, sheets: list[str] | None) -> dict[str, list[list[Any]]]:
    from openpyxl import load_workbook

    try:
        wb = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises many types for broken files
        raise DataToolsError(f"Gagal membuka file Excel {path.name}: {exc}") from exc
    try:
        names = sheets or [wb.sheetnames[0]]
        out = {}
        for name in names:
            if name not in wb.sheetnames:
                raise DataToolsError(
                    f"Sheet '{name}' tidak ada di {path.name}. Sheet tersedia: "
                    f"{', '.join(wb.sheetnames)}"
                )
            ws = wb[name]
            rows = [list(r) for r in ws.iter_rows(values_only=True)]
            while rows and all(v is None for v in rows[-1]):
                rows.pop()
            out[name] = rows
        return out
    finally:
        wb.close()


def read_excel_file(
    path: str | Path,
    all_sheets: bool = False,
    sheet: str | None = None,
    header_row: int | None = None,
) -> list[Table]:
    """Read the first sheet (or ``sheet``, or every sheet) of an Excel file."""
    path = Path(path)
    if path.suffix.lower() == ".xls":
        data = _read_xls(path, None if all_sheets else (sheet or 0))
        if not all_sheets and sheet is None:
            first = next(iter(data))
            data = {str(first) if not isinstance(first, int) else "Sheet1": data[first]}
    else:
        names = excel_sheet_names(path) if all_sheets else ([sheet] if sheet else None)
        data = _read_xlsx_rows(path, names)
    tables = []
    for name, rows in data.items():
        if not any(any(v is not None for v in r) for r in rows):
            log.info("Lewati sheet kosong: %s [%s]", path.name, name)
            continue
        df, idx = _rows_to_frame(rows, header_row)
        tables.append(Table(df, path, str(name), {"header_row": idx}))
    return tables


def read_tables(
    inputs: Iterable[str | Path],
    all_sheets: bool = False,
    sheet: str | None = None,
    header_row: int | None = None,
    recursive: bool = False,
) -> list[Table]:
    """Read every CSV/Excel file in ``inputs`` (files and/or folders)."""
    tables: list[Table] = []
    for path in collect_files(inputs, recursive=recursive):
        if path.suffix.lower() in CSV_EXTENSIONS:
            tables.append(read_csv_file(path, header_row=header_row))
        else:
            tables.extend(read_excel_file(path, all_sheets, sheet, header_row))
    if not tables:
        raise DataToolsError("Tidak ada data yang bisa dibaca dari input yang diberikan.")
    return tables


# --------------------------------------------------------------------------- writing

def to_excel_value(value: Any) -> Any:
    """Convert pandas/numpy values to something openpyxl can write."""
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        value = value.to_pydatetime()
    if isinstance(value, dt.datetime):
        if value.tzinfo is not None:
            value = value.replace(tzinfo=None)
        return value
    if isinstance(value, pd.Period):
        return str(value)
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):  # numpy scalar
        return value.item()
    return value


def write_excel(
    path: str | Path,
    sheets: dict[str, pd.DataFrame],
    money_columns: set[str] | None = None,
    percent_columns: set[str] | None = None,
    index: bool = False,
) -> Path:
    """Write several DataFrames into one nicely formatted workbook."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    wb.remove(wb.active)
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1F4E78")
    for sheet_name, df in sheets.items():
        ws = wb.create_sheet(safe_sheet_name(sheet_name, wb.sheetnames))
        frame = df.reset_index() if index else df
        columns = [str(c) for c in frame.columns]
        ws.append(columns)
        for cell in ws[1]:
            cell.font, cell.fill = header_font, header_fill
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        for row in frame.itertuples(index=False, name=None):
            ws.append([to_excel_value(v) for v in row])
        for ci, col in enumerate(columns, start=1):
            series = frame.iloc[:, ci - 1]
            letter = get_column_letter(ci)
            fmt = _number_format(col, series, money_columns, percent_columns)
            if fmt:
                for (cell,) in ws.iter_rows(min_row=2, min_col=ci, max_col=ci):
                    cell.number_format = fmt
            sample = [col] + [_display_len(v) for v in series.head(500).tolist()]
            width = max(len(s) if isinstance(s, str) else s for s in sample)
            ws.column_dimensions[letter].width = min(max(width + 2, 8), 50)
        ws.freeze_panes = "A2"
        if len(frame) and columns:
            ws.auto_filter.ref = ws.dimensions
    if not wb.sheetnames:
        wb.create_sheet("Data")
    wb.save(path)
    return path


def _display_len(value: Any) -> int:
    if value is None or (not isinstance(value, str) and _isna(value)):
        return 0
    if isinstance(value, (pd.Timestamp, dt.date)):
        return 10
    if isinstance(value, float):
        return len(f"{value:,.2f}")
    return len(str(value))


def _isna(value: Any) -> bool:
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _number_format(col, series, money_columns, percent_columns) -> str | None:
    if percent_columns and col in percent_columns:
        return "0.0%"
    if pd.api.types.is_datetime64_any_dtype(series):
        non_null = series.dropna()
        has_time = bool(len(non_null)) and bool(
            ((non_null.dt.hour != 0) | (non_null.dt.minute != 0)).any()
        )
        return "dd/mm/yyyy hh:mm" if has_time else "dd/mm/yyyy"
    if pd.api.types.is_bool_dtype(series):
        return None
    if pd.api.types.is_numeric_dtype(series):
        if (money_columns is not None and col in money_columns) or (
            money_columns is None and is_money_column(col)
        ):
            return '"Rp" #,##0'
        non_null = series.dropna()
        if len(non_null) and (non_null % 1 == 0).all():
            return "#,##0"
        return "#,##0.00"
    return None


def safe_sheet_name(name: str, existing: Iterable[str] = ()) -> str:
    cleaned = "".join("_" if c in '[]:*?/\\' else c for c in str(name)).strip() or "Sheet"
    cleaned = cleaned[:31]
    existing = set(existing)
    candidate, n = cleaned, 2
    while candidate in existing:
        suffix = f"_{n}"
        candidate = cleaned[: 31 - len(suffix)] + suffix
        n += 1
    return candidate


def write_csv(df: pd.DataFrame, path: str | Path, delimiter: str = ",") -> Path:
    """Write CSV as UTF-8 with BOM so Excel opens it with correct characters."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            non_null = out[col].dropna()
            has_time = bool(len(non_null)) and bool((non_null.dt.normalize() != non_null).any())
            out[col] = out[col].dt.strftime("%Y-%m-%d %H:%M:%S" if has_time else "%Y-%m-%d")
    out.to_csv(path, index=False, sep=delimiter, encoding="utf-8-sig")
    return path


def write_table(df: pd.DataFrame, path: str | Path, sheet_name: str = "Data") -> Path:
    path = Path(path)
    if path.suffix.lower() in (".csv", ".txt"):
        return write_csv(df, path)
    if path.suffix.lower() == ".tsv":
        return write_csv(df, path, "\t")
    if path.suffix.lower() not in (".xlsx", ".xlsm"):
        path = path.with_suffix(".xlsx")
    return write_excel(path, {sheet_name: df})
