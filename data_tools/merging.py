"""Combine several tables: stack them (aligned columns) or join them on a key column."""

from __future__ import annotations

import logging

import pandas as pd

from data_tools import DataToolsError
from data_tools.cleaning import CleanOptions, clean_dataframe, normalize_column_name
from data_tools.io import Table

log = logging.getLogger("data_tools")

SOURCE_COLUMN = "source_file"
SHEET_COLUMN = "source_sheet"


def clean_tables(tables: list[Table], options: CleanOptions | None = None) -> list[Table]:
    cleaned = []
    for t in tables:
        df, report = clean_dataframe(t.df, options)
        log.info("Bersihkan %s: %s", t.label, report.summary())
        cleaned.append(Table(df, t.source, t.sheet, {**t.meta, "report": report}))
    return cleaned


def concat_tables(tables: list[Table], add_source: bool = True) -> pd.DataFrame:
    """Stack tables; columns are aligned by name (union, in order of first appearance)."""
    if not tables:
        raise DataToolsError("Tidak ada tabel untuk digabung.")
    multi_sheet = len({t.sheet for t in tables if t.sheet}) > 1
    frames = []
    for t in tables:
        df = t.df.copy()
        if add_source:
            df[SOURCE_COLUMN] = t.source.name
            if multi_sheet:
                df[SHEET_COLUMN] = t.sheet
        frames.append(df)
    columns: list[str] = []
    for f in frames:
        columns.extend(c for c in f.columns if c not in columns)
    if add_source:  # keep provenance columns at the end
        for extra in (SOURCE_COLUMN, SHEET_COLUMN):
            if extra in columns:
                columns.remove(extra)
                columns.append(extra)
    frames = [f.reindex(columns=columns) for f in frames if len(f)]
    if not frames:
        return pd.DataFrame(columns=columns)
    merged = pd.concat(frames, ignore_index=True)
    # Re-align dtypes that pandas widened to object because of empty columns.
    for col in merged.columns:
        if merged[col].dtype == object:
            non_null = merged[col].dropna()
            if len(non_null) and all(isinstance(v, pd.Timestamp) for v in non_null):
                merged[col] = pd.to_datetime(merged[col])
    return merged


def _key_series(series: pd.Series) -> pd.Series:
    def norm(v):
        if v is None or (not isinstance(v, str) and pd.isna(v)):
            return None
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        return str(v).strip()

    return series.map(norm).astype(object)


def join_tables(tables: list[Table], key: str, how: str = "left") -> pd.DataFrame:
    """Join tables one after another on ``key`` (like VLOOKUP / SQL JOIN)."""
    if how not in {"left", "inner", "outer", "right"}:
        raise DataToolsError("Cara join harus salah satu dari: left, inner, outer, right")
    if len(tables) < 2:
        raise DataToolsError("Join butuh minimal 2 file/tabel.")
    key_norm = normalize_column_name(key)
    result: pd.DataFrame | None = None
    for t in tables:
        df = t.df.copy()
        k = key if key in df.columns else key_norm
        if k not in df.columns:
            raise DataToolsError(
                f"Kolom kunci '{key}' tidak ada di {t.label}. Kolom tersedia: "
                f"{', '.join(map(str, df.columns))}"
            )
        df = df.rename(columns={k: key_norm})
        df[key_norm] = _key_series(df[key_norm])
        if result is None:
            result = df
            continue
        dup = df[key_norm].duplicated().sum()
        if dup:
            log.warning("Peringatan: %d nilai kunci ganda di %s; baris bisa berlipat", dup, t.label)
        suffix = "_" + normalize_column_name(t.source.stem)
        result = result.merge(df, on=key_norm, how=how, suffixes=("", suffix))
    assert result is not None
    return result.reset_index(drop=True)
