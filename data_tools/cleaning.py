"""Data cleaning: column names, whitespace, empties, duplicates, dates and numbers."""

from __future__ import annotations

import datetime as dt
import logging
import numbers
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from data_tools.formatting import (
    detect_dayfirst,
    detect_number_style,
    is_missing,
    is_money_column,
    parse_date,
    parse_number,
)

log = logging.getLogger("data_tools")

TYPE_TEXT = "teks"
TYPE_ID = "kode/ID (teks)"
TYPE_NUMBER = "angka"
TYPE_DATE = "tanggal"

# Column-name tokens that mean "this is an identifier, never convert to number".
ID_TOKENS = {
    "id", "kode", "code", "no", "nomor", "nomer", "number", "nik", "npwp", "nip", "nisn", "ktp",
    "telp", "telpon", "telepon", "tlp", "hp", "phone", "wa", "whatsapp", "rekening", "rek", "sku",
    "barcode", "kodepos", "zip", "postal", "nopol", "resi", "akun", "va",
}
DATE_TOKENS = {"tanggal", "tgl", "date", "waktu", "tanggal_lahir", "periode_tanggal"}
DETECT_RATIO = 0.8


@dataclass
class CleanOptions:
    normalize_columns: bool = True
    drop_duplicates: bool = True
    drop_empty: bool = True
    convert_types: bool = True
    text_columns: Iterable[str] = ()
    number_columns: Iterable[str] = ()
    date_columns: Iterable[str] = ()


@dataclass
class CleanReport:
    rows_before: int = 0
    rows_after: int = 0
    cols_before: int = 0
    cols_after: int = 0
    empty_rows: int = 0
    empty_cols: int = 0
    duplicates: int = 0
    renamed: dict[str, str] = field(default_factory=dict)
    types: dict[str, str] = field(default_factory=dict)

    def summary(self) -> str:
        parts = [
            f"{self.rows_before} -> {self.rows_after} baris",
            f"{self.cols_before} -> {self.cols_after} kolom",
        ]
        if self.empty_rows:
            parts.append(f"{self.empty_rows} baris kosong dibuang")
        if self.empty_cols:
            parts.append(f"{self.empty_cols} kolom kosong dibuang")
        if self.duplicates:
            parts.append(f"{self.duplicates} duplikat dibuang")
        return ", ".join(parts)


# --------------------------------------------------------------------------- column names


def normalize_column_name(name: Any) -> str:
    """``" Harga Satuan (Rp) "`` -> ``"harga_satuan_rp"``; ``"TotalHarga"`` -> ``"total_harga"``."""
    if name is None or (not isinstance(name, str) and is_missing(name)):
        return ""
    text = str(name).strip()
    if re.match(r"^Unnamed: \d+", text):
        return ""
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z][a-z])", "_", text)  # camelCase -> camel_Case
    text = text.lower().replace("%", " persen ").replace("&", " dan ")
    text = re.sub(r"[^0-9a-z]+", "_", text)
    return text.strip("_")


def normalize_columns(columns: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: dict[str, int] = {}
    for i, col in enumerate(columns, start=1):
        name = normalize_column_name(col) or f"kolom_{i}"
        if name[0].isdigit():
            name = f"kolom_{name}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 1
        result.append(name)
    return result


# --------------------------------------------------------------------------- type detection


def _tokens(name: str) -> list[str]:
    return [t for t in normalize_column_name(name).split("_") if t]


def is_id_name(name: str) -> bool:
    tokens = _tokens(name)
    return bool(tokens) and (tokens[0] in ID_TOKENS or tokens[-1] in ID_TOKENS)


def is_date_name(name: str) -> bool:
    tokens = _tokens(name)
    return any(t in DATE_TOKENS for t in tokens)


_ID_VALUE_RE = re.compile(r"^\+?[\d\s\-().]+$")


def looks_like_id_value(value: Any, long_digits: bool = True) -> bool:
    """Phone numbers / NIK / codes: leading zero, ``+62``, or 10+ digits without separators."""
    if not isinstance(value, str):
        return False
    s = value.strip()
    if not _ID_VALUE_RE.match(s):
        return False
    digits = re.sub(r"\D", "", s)
    if len(digits) < 2:
        return False
    if s.startswith("+") or (digits.startswith("0") and not re.match(r"^0[.,]", s)):
        return True
    return long_digits and len(digits) >= 10 and not re.search(r"[.,\s]", s)


def _to_id_string(value: Any) -> Any:
    if is_missing(value):
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _parse_series(values: list[Any], parser) -> tuple[list[Any], int]:
    parsed, ok = [], 0
    for v in values:
        result = parser(v)
        if result is not None:
            ok += 1
        parsed.append(result)
    return parsed, ok


def _date_parser(dayfirst: bool, allow_serial: bool):
    def parse(v):
        if isinstance(v, numbers.Real) and not isinstance(v, bool):
            if allow_serial and 20000 <= float(v) <= 80000:  # Excel serial date
                return pd.Timestamp("1899-12-30") + pd.Timedelta(days=float(v))
            return None
        return parse_date(v, dayfirst=dayfirst)

    return parse


def _number_parser(style: str):
    def parse(v):
        if isinstance(v, (dt.date, dt.datetime, pd.Timestamp)):
            return None
        return parse_number(v, style=style)

    return parse


def _finish_numbers(parsed: list[Any], index) -> pd.Series:
    series = pd.Series([float("nan") if v is None else v for v in parsed], index=index,
                       dtype="float64")
    non_null = series.dropna()
    if len(non_null) and (non_null % 1 == 0).all() and non_null.abs().max() < 2**53:
        return series.astype("Int64")
    return series


def detect_and_convert(series: pd.Series, name: str, force: str | None = None
                       ) -> tuple[pd.Series, str]:
    """Convert one column to number/date/text. Returns the new series and its type label."""
    values = series.tolist()
    present = [v for v in values if not is_missing(v)]
    if pd.api.types.is_datetime64_any_dtype(series):
        return series, TYPE_DATE
    if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series) and (
        force is None or force == TYPE_NUMBER
    ):
        if is_id_name(name) and force is None:
            return series.map(_to_id_string).astype(object), TYPE_ID
        return series, TYPE_NUMBER

    text_values = [v for v in present if isinstance(v, str)]

    if force == TYPE_TEXT:
        return series.map(_to_id_string).astype(object), TYPE_TEXT
    if force is None and is_id_name(name):
        return series.map(_to_id_string).astype(object), TYPE_ID
    if not present:
        return series, TYPE_TEXT

    # Dates
    if force in (None, TYPE_DATE):
        dayfirst = detect_dayfirst(text_values)
        parsed, ok = _parse_series(values, _date_parser(dayfirst, is_date_name(name)))
        if force == TYPE_DATE or ok >= DETECT_RATIO * len(present):
            return pd.Series(pd.to_datetime(parsed), index=series.index), TYPE_DATE

    # IDs detected from values (leading zeros, phone numbers)
    if force is None and text_values:
        long_ok = not is_money_column(name)
        id_like = sum(1 for v in text_values if looks_like_id_value(v, long_ok))
        if id_like and id_like >= 0.3 * len(text_values):
            return series.map(_to_id_string).astype(object), TYPE_ID

    # Numbers
    if force in (None, TYPE_NUMBER):
        style = detect_number_style(text_values)
        parsed, ok = _parse_series(values, _number_parser(style))
        if force == TYPE_NUMBER or ok >= DETECT_RATIO * len(present):
            return _finish_numbers(parsed, series.index), TYPE_NUMBER

    return series, TYPE_TEXT


# --------------------------------------------------------------------------- cleaning


def _clean_cell(value: Any) -> Any:
    if isinstance(value, str):
        text = " ".join(value.replace("\xa0", " ").split())
        return None if text.lower() in {"", "-", "--", "n/a", "na", "nan", "null", "none",
                                        "#n/a", "<na>"} else text
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _resolve(names: Iterable[str], columns: list[str]) -> set[str]:
    wanted = set()
    for n in names:
        for part in str(n).split(","):
            part = part.strip()
            if not part:
                continue
            norm = normalize_column_name(part)
            if part in columns:
                wanted.add(part)
            elif norm in columns:
                wanted.add(norm)
            else:
                log.warning("Peringatan: kolom '%s' tidak ditemukan", part)
    return wanted


def clean_dataframe(df: pd.DataFrame, options: CleanOptions | None = None
                    ) -> tuple[pd.DataFrame, CleanReport]:
    """Clean a raw table. Returns a new DataFrame plus a report of what changed."""
    opts = options or CleanOptions()
    report = CleanReport(rows_before=len(df), cols_before=len(df.columns))
    out = df.copy()
    out = out.astype(object) if len(out.columns) else out

    if opts.normalize_columns:
        new_cols = normalize_columns(out.columns)
        report.renamed = {str(o): n for o, n in zip(out.columns, new_cols) if str(o) != n}
        out.columns = new_cols
    else:
        out.columns = [str(c) for c in out.columns]

    for col in out.columns:
        out[col] = out[col].map(_clean_cell).astype(object)

    if opts.drop_empty:
        before_rows, before_cols = len(out), len(out.columns)
        out = out.dropna(how="all")
        out = out.dropna(axis=1, how="all")
        report.empty_rows = before_rows - len(out)
        report.empty_cols = before_cols - len(out.columns)

    if opts.convert_types:
        cols = list(out.columns)
        forced: dict[str, str] = {}
        for c in _resolve(opts.text_columns, cols):
            forced[c] = TYPE_TEXT
        for c in _resolve(opts.number_columns, cols):
            forced[c] = TYPE_NUMBER
        for c in _resolve(opts.date_columns, cols):
            forced[c] = TYPE_DATE
        converted = {}
        for col in cols:
            converted[col], report.types[col] = detect_and_convert(out[col], col, forced.get(col))
        out = pd.DataFrame(converted, index=out.index)

    if opts.drop_duplicates and len(out.columns):
        before = len(out)
        out = out.drop_duplicates()
        report.duplicates = before - len(out)

    out = out.reset_index(drop=True)
    report.rows_after, report.cols_after = len(out), len(out.columns)
    return out, report


def describe_types(report: CleanReport) -> str:
    return ", ".join(f"{c}={t}" for c, t in report.types.items())


def unify_variants(series: pd.Series) -> tuple[pd.Series, int]:
    """Map text variants that differ only in letter case/spacing ("MINYAK GORENG 2L" vs
    "Minyak Goreng 2L") to the most frequent spelling. Returns (series, values changed)."""
    if not (series.dtype == object or pd.api.types.is_string_dtype(series)):
        return series, 0
    texts = series.dropna()
    texts = texts[texts.map(lambda v: isinstance(v, str))]
    if texts.empty:
        return series, 0
    keys = texts.map(lambda v: " ".join(v.split()).casefold())
    canonical = {}
    for key, group in texts.groupby(keys):
        canonical[key] = group.value_counts().index[0]
    mapped = series.map(
        lambda v: canonical.get(" ".join(v.split()).casefold(), v) if isinstance(v, str) else v
    )
    changed = int((mapped[series.notna()] != series[series.notna()]).sum())
    return mapped.astype(object), changed
