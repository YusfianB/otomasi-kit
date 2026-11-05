"""Compare a run with the previous one (SQLite history): new, removed, price changes."""

from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import ScraperConfig

CHANGE_LABELS = {
    "baru": "Item baru",
    "hilang": "Item hilang",
    "harga_naik": "Harga naik",
    "harga_turun": "Harga turun",
    "berubah": "Field berubah",
}
REPORT_COLUMNS = [
    "jenis",
    "kunci",
    "nama",
    "field",
    "nilai_lama",
    "nilai_baru",
    "selisih",
    "persen",
]


@dataclass
class MonitorFields:
    key: list[str]
    price: str | None
    label: str | None
    track: list[str]


def resolve_fields(cfg: ScraperConfig) -> MonitorFields:
    """Work out key / price / label fields, using sensible defaults when not configured."""
    m = cfg.monitor
    specs = cfg.all_fields()
    price = m.price
    if not price:
        price = next((f.name for f in specs if f.type == "price_idr"), None)
    if not price:
        price = next(
            (
                f.name
                for f in specs
                if f.type in ("int", "float")
                and any(w in f.name.lower() for w in ("harga", "price", "gaji"))
            ),
            None,
        )
    label = m.label or next(
        (
            f.name
            for f in specs
            if f.type == "text"
            and f.name.lower() in ("nama", "judul", "title", "name", "posisi", "produk")
        ),
        None,
    )
    if not label:
        label = next((f.name for f in specs if f.type == "text"), None)
    if m.key:
        key = [m.key]
    elif cfg.dedupe_by:
        key = list(cfg.dedupe_by)
    else:
        url_fields = [f.name for f in cfg.fields if f.type == "url"]
        key = url_fields[:1] or [
            f.name for f in cfg.fields if f.name != price and f.name not in m.track
        ]
    return MonitorFields(key=key, price=price, label=label, track=list(m.track))


def make_key(item: dict, fields: list[str]) -> str:
    if len(fields) == 1:
        value = item.get(fields[0])
        return "" if value is None else str(value)
    return json.dumps([item.get(f) for f in fields], ensure_ascii=False, default=str)


# --------------------------------------------------------------------------- history


class History:
    """SQLite store of every monitored run."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                config TEXT NOT NULL,
                run_at TEXT NOT NULL,
                item_count INTEGER NOT NULL,
                error_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS items (
                run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                item_key TEXT NOT NULL,
                label TEXT,
                price REAL,
                data TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_items_run ON items(run_id);
            CREATE INDEX IF NOT EXISTS idx_runs_config ON runs(config, id);
            """
        )

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def latest_run(self, config: str) -> tuple[int, str] | None:
        row = self.conn.execute(
            "SELECT id, run_at FROM runs WHERE config = ? ORDER BY id DESC LIMIT 1", (config,)
        ).fetchone()
        return (row[0], row[1]) if row else None

    def load_items(self, run_id: int) -> dict[str, dict]:
        rows = self.conn.execute("SELECT item_key, data FROM items WHERE run_id = ?", (run_id,))
        return {key: json.loads(data) for key, data in rows}

    def save_run(
        self,
        config: str,
        items: list[dict],
        fields: MonitorFields,
        error_count: int = 0,
        run_at: str | None = None,
    ) -> int:
        run_at = run_at or datetime.now().isoformat(timespec="seconds")
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO runs (config, run_at, item_count, error_count) VALUES (?,?,?,?)",
                (config, run_at, len(items), error_count),
            )
            run_id = cur.lastrowid
            self.conn.executemany(
                "INSERT INTO items (run_id, item_key, label, price, data) VALUES (?,?,?,?,?)",
                [
                    (
                        run_id,
                        make_key(it, fields.key),
                        None if not fields.label else _str(it.get(fields.label)),
                        _num(it.get(fields.price)) if fields.price else None,
                        json.dumps(it, ensure_ascii=False, default=str),
                    )
                    for it in items
                ],
            )
        return int(run_id)

    def prune(self, config: str, keep: int) -> int:
        """Keep only the newest `keep` runs of a config. Returns runs deleted."""
        ids = [
            r[0]
            for r in self.conn.execute(
                "SELECT id FROM runs WHERE config = ? ORDER BY id DESC", (config,)
            )
        ]
        old = ids[keep:]
        if not old:
            return 0
        marks = ",".join("?" * len(old))
        with self.conn:
            self.conn.execute(f"DELETE FROM items WHERE run_id IN ({marks})", old)
            self.conn.execute(f"DELETE FROM runs WHERE id IN ({marks})", old)
        return len(old)


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _str(value: Any) -> str | None:
    return None if value is None else str(value)


# --------------------------------------------------------------------------- diff


@dataclass
class ChangeReport:
    config: str
    previous_run_at: str | None
    current_run_at: str
    previous_count: int
    current_count: int
    changes: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def count(self, kind: str) -> int:
        return sum(1 for c in self.changes if c["jenis"] == kind)

    @property
    def first_run(self) -> bool:
        return self.previous_run_at is None

    @property
    def has_changes(self) -> bool:
        return bool(self.changes)

    def summary_text(self) -> str:
        if self.first_run:
            return (
                f"Run pertama untuk '{self.config}': {self.current_count} item disimpan "
                "sebagai acuan. Perubahan akan terdeteksi mulai run berikutnya."
            )
        lines = [
            f"Perbandingan '{self.config}': {self.previous_run_at} -> {self.current_run_at}",
            f"  Jumlah item       : {self.previous_count} -> {self.current_count}",
            f"  Item baru         : {self.count('baru')}",
            f"  Item hilang       : {self.count('hilang')}",
            f"  Harga naik        : {self.count('harga_naik')}",
            f"  Harga turun       : {self.count('harga_turun')}",
        ]
        if self.count("berubah"):
            lines.append(f"  Field lain berubah: {self.count('berubah')}")
        for w in self.warnings:
            lines.append(f"  PERINGATAN: {w}")
        return "\n".join(lines)


def compare(
    previous: dict[str, dict],
    current: list[dict],
    fields: MonitorFields,
    *,
    config: str,
    previous_run_at: str | None,
    current_run_at: str,
    min_change_pct: float = 0.0,
) -> ChangeReport:
    report = ChangeReport(
        config=config,
        previous_run_at=previous_run_at,
        current_run_at=current_run_at,
        previous_count=len(previous),
        current_count=len(current),
    )
    if previous_run_at is None:
        return report

    cur_map: dict[str, dict] = {}
    for item in current:
        cur_map.setdefault(make_key(item, fields.key), item)

    def label_of(item: dict) -> Any:
        return item.get(fields.label) if fields.label else None

    def price_of(item: dict) -> float | None:
        return _num(item.get(fields.price)) if fields.price else None

    for key, item in cur_map.items():
        old = previous.get(key)
        if old is None:
            report.changes.append(
                _row("baru", key, label_of(item), fields.price, None, price_of(item))
            )
            continue
        old_price, new_price = price_of(old), price_of(item)
        if old_price is not None and new_price is not None and old_price != new_price:
            pct = (new_price - old_price) / old_price * 100 if old_price else None
            if pct is None or abs(pct) >= min_change_pct:
                kind = "harga_naik" if new_price > old_price else "harga_turun"
                report.changes.append(
                    _row(kind, key, label_of(item), fields.price, old_price, new_price)
                )
        for fname in fields.track:
            if fname == fields.price:
                continue
            if old.get(fname) != item.get(fname):
                report.changes.append(
                    _row("berubah", key, label_of(item), fname, old.get(fname), item.get(fname))
                )
    for key, old in previous.items():
        if key not in cur_map:
            report.changes.append(
                _row("hilang", key, label_of(old), fields.price, price_of(old), None)
            )

    order = {"harga_turun": 0, "harga_naik": 1, "baru": 2, "hilang": 3, "berubah": 4}
    report.changes.sort(key=lambda c: (order[c["jenis"]], str(c["nama"] or c["kunci"])))
    return report


def _row(kind: str, key: str, label: Any, fname: str | None, old: Any, new: Any) -> dict:
    diff = pct = None
    if isinstance(old, (int, float)) and isinstance(new, (int, float)):
        diff = new - old
        pct = round(diff / old * 100, 2) if old else None
    return {
        "jenis": kind,
        "kunci": key,
        "nama": label,
        "field": fname,
        "nilai_lama": _tidy(old),
        "nilai_baru": _tidy(new),
        "selisih": _tidy(diff),
        "persen": pct,
    }


def _tidy(value: Any) -> Any:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


# --------------------------------------------------------------------------- report files


def write_report(report: ChangeReport, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ext = path.suffix.lower()
    if ext == ".csv":
        with path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=REPORT_COLUMNS)
            writer.writeheader()
            writer.writerows(report.changes)
    elif ext == ".xlsx":
        import pandas as pd

        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            pd.DataFrame(report.changes, columns=REPORT_COLUMNS).to_excel(
                writer, index=False, sheet_name="perubahan"
            )
            pd.DataFrame(_summary_rows(report), columns=["keterangan", "nilai"]).to_excel(
                writer, index=False, sheet_name="ringkasan"
            )
    elif ext == ".json":
        path.write_text(
            json.dumps(
                {
                    "config": report.config,
                    "run_sebelumnya": report.previous_run_at,
                    "run_sekarang": report.current_run_at,
                    "ringkasan": dict(_summary_rows(report)),
                    "peringatan": report.warnings,
                    "perubahan": report.changes,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
    elif ext == ".md":
        path.write_text(_markdown(report), encoding="utf-8")
    else:
        raise ValueError(f"Ekstensi laporan '{ext}' tidak didukung (.csv, .xlsx, .json, .md).")
    return path


def _summary_rows(report: ChangeReport) -> list[tuple[str, Any]]:
    return [
        ("config", report.config),
        ("run_sebelumnya", report.previous_run_at or "-"),
        ("run_sekarang", report.current_run_at),
        ("jumlah_item_sebelumnya", report.previous_count),
        ("jumlah_item_sekarang", report.current_count),
        *[(kind, report.count(kind)) for kind in CHANGE_LABELS],
    ]


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        text = f"{value:,.2f}".rstrip("0").rstrip(".")
        return text.replace(",", "_").replace(".", ",").replace("_", ".")  # 1.250.000
    return str(value).replace("|", "\\|")


def _markdown(report: ChangeReport) -> str:
    out = [f"# Laporan perubahan: {report.config}", ""]
    if report.first_run:
        out += [report.summary_text(), ""]
        return "\n".join(out)
    out += [
        (
            f"Run sebelumnya: **{report.previous_run_at}** | run sekarang: "
            f"**{report.current_run_at}**"
        ),
        "",
        "| Jenis | Jumlah |",
        "|---|---|",
        *[f"| {label} | {report.count(kind)} |" for kind, label in CHANGE_LABELS.items()],
        "",
    ]
    for w in report.warnings:
        out.append(f"> **Peringatan:** {w}")
    if report.changes:
        out += ["", "| Jenis | Nama | Lama | Baru | Selisih | % |", "|---|---|---|---|---|---|"]
        for c in report.changes:
            pct = "-" if c["persen"] is None else f"{c['persen']:+.1f}%".replace(".", ",")
            kind = CHANGE_LABELS[c["jenis"]]
            if c["jenis"] == "berubah":
                kind += f" ({c['field']})"
            out.append(
                f"| {kind} | {_fmt(c['nama'] or c['kunci'])} | "
                f"{_fmt(c['nilai_lama'])} | {_fmt(c['nilai_baru'])} | "
                f"{_fmt(c['selisih'])} | {pct} |"
            )
    else:
        out.append("Tidak ada perubahan.")
    return "\n".join(out) + "\n"
