"""Write items to CSV / XLSX / JSON / JSONL, chosen by file extension."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

SUPPORTED = (".csv", ".xlsx", ".json", ".jsonl")


class OutputError(ValueError):
    pass


def check_output_path(path: str | Path) -> Path:
    path = Path(path)
    if path.suffix.lower() not in SUPPORTED:
        raise OutputError(
            f"Ekstensi file output '{path.suffix or '(kosong)'}' tidak didukung. "
            f"Gunakan salah satu: {', '.join(SUPPORTED)}"
        )
    return path


def _columns(items: list[dict], columns: list[str] | None) -> list[str]:
    cols = list(columns or [])
    for item in items:
        for key in item:
            if key not in cols:
                cols.append(key)
    return cols


def _cell(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value


def write_items(items: list[dict], path: str | Path, columns: list[str] | None = None,
                sheet_name: str = "data") -> Path:
    path = check_output_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = _columns(items, columns)
    ext = path.suffix.lower()
    if ext == ".csv":
        # utf-8-sig so Excel (common in Indonesian offices) shows characters correctly
        with path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            writer.writeheader()
            for item in items:
                writer.writerow({c: _cell(item.get(c)) for c in cols})
    elif ext == ".xlsx":
        import pandas as pd

        df = pd.DataFrame([{c: _cell(item.get(c)) for c in cols} for item in items],
                          columns=cols)
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name=sheet_name[:31])
            _autofit(writer.sheets[sheet_name[:31]])
    elif ext == ".json":
        path.write_text(json.dumps([{c: item.get(c) for c in cols} for item in items],
                                   ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    else:  # .jsonl
        with path.open("w", encoding="utf-8") as fh:
            for item in items:
                fh.write(json.dumps({c: item.get(c) for c in cols}, ensure_ascii=False,
                                    default=str) + "\n")
    return path


def _autofit(ws) -> None:
    for column in ws.columns:
        width = max((len(str(c.value)) for c in column if c.value is not None), default=8)
        ws.column_dimensions[column[0].column_letter].width = min(max(width + 2, 8), 60)
    ws.freeze_panes = "A2"


def write_summary(summary: dict, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")
    return path
