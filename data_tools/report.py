"""Excel + PNG + HTML reports: a generic summary/pivot report and the ``penjualan`` preset."""

from __future__ import annotations

import base64
import datetime as dt
import logging
import numbers
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from data_tools import DataToolsError, charts
from data_tools.cleaning import normalize_column_name, unify_variants
from data_tools.formatting import (
    format_bulan,
    format_number,
    format_rupiah,
    format_tanggal,
    is_money_column,
)
from data_tools.io import write_excel

log = logging.getLogger("data_tools")

TEMPLATE_DIR = Path(__file__).parent / "templates"

SALES_COLUMNS = {
    "tanggal": ["tanggal", "tgl", "tanggal_transaksi", "tgl_transaksi", "tanggal_penjualan",
                "date", "waktu", "tanggal_order", "order_date"],
    "produk": ["produk", "nama_produk", "barang", "nama_barang", "item", "nama_item", "product",
               "menu"],
    "qty": ["qty", "jumlah", "kuantitas", "quantity", "jml", "banyak", "jumlah_barang", "pcs"],
    "harga": ["harga", "harga_satuan", "harga_jual", "price", "unit_price"],
    "total": ["total", "total_harga", "subtotal", "omzet", "pendapatan", "total_penjualan",
              "nilai_penjualan", "amount", "grand_total", "jumlah_harga"],
    "kategori": ["kategori", "kategori_produk", "category", "jenis", "golongan"],
    "cabang": ["cabang", "toko", "outlet", "store", "lokasi", "channel", "kanal"],
    "transaksi": ["no_transaksi", "nomor_transaksi", "id_transaksi", "no_invoice", "invoice",
                  "no_nota", "nota", "order_id", "no_order"],
}


@dataclass
class ReportResult:
    excel: Path
    html: Path
    charts: list[Path] = field(default_factory=list)
    sheets: dict[str, Any] = field(default_factory=dict)
    kpis: list[tuple[str, str]] = field(default_factory=list)


# --------------------------------------------------------------------------- helpers


def find_column(df: pd.DataFrame, candidates: list[str], override: str | None = None,
                kind: str = "") -> str | None:
    columns = list(df.columns)
    if override:
        for name in (override, normalize_column_name(override)):
            if name in columns:
                return name
        raise DataToolsError(
            f"Kolom {kind or ''} '{override}' tidak ditemukan. Kolom tersedia: "
            f"{', '.join(map(str, columns))}"
        )
    for cand in candidates:
        if cand in columns:
            return cand
    for cand in candidates:  # prefix match, e.g. "qty_terjual", "harga_satuan_rp"
        for col in columns:
            if kind == "qty" and is_money_column(col):
                continue
            if str(col).startswith(cand + "_"):
                return col
    return None


def resolve_columns(df: pd.DataFrame, names: list[str] | None) -> list[str]:
    result = []
    for name in names or []:
        for part in str(name).split(","):
            part = part.strip()
            if not part:
                continue
            col = find_column(df, [], override=part)
            if col not in result:
                result.append(col)
    return result


def numeric_columns(df: pd.DataFrame) -> list[str]:
    return [
        c for c in df.columns
        if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c])
    ]


def date_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])]


def describe_frame(df: pd.DataFrame, columns: list[str] | None = None) -> pd.DataFrame:
    """``describe()`` with Indonesian row labels plus the column total."""
    cols = columns or numeric_columns(df)
    if not cols:
        return pd.DataFrame({"statistik": ["(tidak ada kolom angka)"]})
    data = df[cols].astype("float64")
    stats = pd.DataFrame({
        "Jumlah data": data.count(),
        "Total": data.sum(),
        "Rata-rata": data.mean(),
        "Simpangan baku": data.std(),
        "Minimum": data.min(),
        "Kuartil 1 (25%)": data.quantile(0.25),
        "Median": data.median(),
        "Kuartil 3 (75%)": data.quantile(0.75),
        "Maksimum": data.max(),
    }).T.round(2)
    stats.index.name = "statistik"
    return stats.reset_index()


def column_overview(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for col in df.columns:
        s = df[col]
        if pd.api.types.is_datetime64_any_dtype(s):
            kind = "tanggal"
        elif pd.api.types.is_numeric_dtype(s):
            kind = "angka"
        else:
            kind = "teks"
        rows.append({"kolom": col, "tipe": kind, "terisi": int(s.notna().sum()),
                     "kosong": int(s.isna().sum()), "nilai_unik": int(s.nunique())})
    return pd.DataFrame(rows)


def pivot_table(df: pd.DataFrame, group_by: list[str], values: list[str]) -> pd.DataFrame:
    grouped = df.groupby(group_by, dropna=False, sort=False)
    if not values:
        out = grouped.size().rename("jumlah_baris").reset_index()
        return out.sort_values("jumlah_baris", ascending=False, ignore_index=True)
    parts = {}
    for v in values:
        num = df[v].astype("float64")
        g = num.groupby([df[c] for c in group_by], dropna=False, sort=False)
        parts[f"{v}_total"] = g.sum()
        parts[f"{v}_jumlah_data"] = g.count()
        parts[f"{v}_rata_rata"] = g.mean().round(2)
    out = pd.DataFrame(parts).reset_index()
    return out.sort_values(f"{values[0]}_total", ascending=False, ignore_index=True)


def _fmt_cell(col: str, value: Any, money: set[str], percent: set[str]) -> str:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ""
    if isinstance(value, (pd.Timestamp, dt.date)):
        return format_tanggal(value)
    if isinstance(value, pd.Period):
        return format_bulan(value)
    if isinstance(value, numbers.Number) and not isinstance(value, bool):
        if col in percent:
            return f"{format_number(round(float(value) * 100, 1))}%"
        if col in money:
            return format_rupiah(value)
        return format_number(value)
    return str(value)


def html_table(title: str, df: pd.DataFrame, money: set[str] | None = None,
               percent: set[str] | None = None, limit: int = 50) -> dict:
    money = money if money is not None else {c for c in df.columns if is_money_column(c)}
    percent = percent or set()
    shown = df.head(limit)
    numeric = [pd.api.types.is_numeric_dtype(df[c]) for c in df.columns]
    rows = [
        [_fmt_cell(str(c), v, money, percent) for c, v in zip(df.columns, row)]
        for row in shown.itertuples(index=False, name=None)
    ]
    return {
        "title": title, "columns": [str(c).replace("_", " ").capitalize() for c in df.columns],
        "rows": rows,
        "numeric": numeric, "truncated": max(0, len(df) - limit),
    }


def render_html(path: Path, title: str, kpis, tables, chart_files, notes=()) -> Path:
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR),
                      autoescape=select_autoescape(["html", "j2"]))
    images = [
        {"name": p.stem.replace("_", " ").title(),
         "data": base64.b64encode(p.read_bytes()).decode("ascii")}
        for p in chart_files
    ]
    html = env.get_template("laporan.html.j2").render(
        title=title, generated=format_tanggal(dt.date.today(), with_day=True),
        kpis=kpis, tables=tables, images=images, notes=list(notes),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path


def _unify(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    out = df.copy()
    for col in columns:
        out[col], changed = unify_variants(out[col])
        if changed:
            log.info("Kolom '%s': %d nilai disamakan penulisannya (huruf besar/kecil)", col, changed)
    return out


def _auto_group_column(df: pd.DataFrame) -> str | None:
    for col in df.columns:
        s = df[col]
        if s.dtype == object or pd.api.types.is_string_dtype(s):
            n = s.nunique()
            if 2 <= n <= 50 and n < len(df):
                return col
    return None


# --------------------------------------------------------------------------- generic report


def build_report(df: pd.DataFrame, out_dir: str | Path, group_by: list[str] | None = None,
                 values: list[str] | None = None, title: str = "Laporan Data",
                 name: str = "laporan") -> ReportResult:
    """Data + Ringkasan + Pivot sheets, charts and an HTML report."""
    out_dir = Path(out_dir)
    chart_dir = out_dir / "grafik"
    if df.empty:
        raise DataToolsError("Data kosong, laporan tidak bisa dibuat.")
    value_cols = resolve_columns(df, values) or [
        c for c in numeric_columns(df) if not str(c).startswith("source_")
    ]
    for v in value_cols:
        if not pd.api.types.is_numeric_dtype(df[v]):
            raise DataToolsError(
                f"Kolom nilai '{v}' bukan angka. Bersihkan dulu atau pilih kolom lain."
            )
    groups = resolve_columns(df, group_by)
    notes = []
    if not groups:
        auto = _auto_group_column(df)
        if auto:
            groups = [auto]
            notes.append(f"Kolom pengelompokan dipilih otomatis: {auto} (ubah dengan --group-by)")
            log.info("Kelompokkan otomatis berdasarkan kolom '%s'", auto)

    df = _unify(df, groups)
    describe = describe_frame(df, value_cols)
    overview = column_overview(df)
    sheets: dict[str, Any] = {
        "Data": df,
        "Ringkasan": [("Statistik kolom angka", describe), ("Informasi kolom", overview)],
    }
    pivot = None
    if groups:
        pivot = pivot_table(df, groups, value_cols)
        sheets["Pivot"] = pivot

    chart_files: list[Path] = []
    first = value_cols[0] if value_cols else None
    money = bool(first and is_money_column(first))
    if pivot is not None:
        top = pivot.head(15)
        label = top[groups].astype(str).agg(" / ".join, axis=1)
        metric = f"{first}_total" if first else "jumlah_baris"
        chart_files.append(charts.bar_chart(
            label, top[metric], chart_dir / f"{name}_per_{'_'.join(groups)}.png",
            f"{'Total ' + first if first else 'Jumlah baris'} per {', '.join(groups)}",
            money=money,
        ))
    dates = date_columns(df)
    if dates:
        dcol = dates[0]
        values_ = df[first].astype(float).to_numpy() if first else [1.0] * len(df)
        series = pd.Series(values_, index=pd.DatetimeIndex(df[dcol]))
        series = series[series.index.notna()]
        monthly = series.groupby(series.index.to_period("M")).sum()
        if len(monthly) >= 1:
            chart_files.append(charts.bar_chart(
                [format_bulan(p, short=True) for p in monthly.index], monthly.values,
                chart_dir / f"{name}_bulanan.png",
                f"{'Total ' + first if first else 'Jumlah data'} per bulan ({dcol})",
                money=money, horizontal=False,
            ))
    if first:
        chart_files.append(charts.histogram(
            df[first], chart_dir / f"{name}_sebaran_{first}.png", f"Sebaran nilai {first}",
            money=money,
        ))

    excel = write_excel(out_dir / f"{name}.xlsx", sheets, images={"Grafik": chart_files})
    kpis = [("Jumlah baris", format_number(len(df))), ("Jumlah kolom", format_number(df.shape[1]))]
    for v in value_cols[:3]:
        total = df[v].astype(float).sum()
        label = str(v).replace("_", " ")
        kpis.append((label.capitalize() if label.startswith("total") else f"Total {label}",
                     format_rupiah(total) if is_money_column(v)
                     else format_number(round(total, 2))))
    tables = [html_table("Statistik kolom angka", describe, money=set())]
    if pivot is not None:
        tables.append(html_table(f"Pivot per {', '.join(groups)}", pivot))
    tables.append(html_table("Informasi kolom", overview, money=set()))
    html = render_html(out_dir / f"{name}.html", title, kpis, tables, chart_files, notes)
    return ReportResult(excel, html, chart_files, sheets, kpis)


# --------------------------------------------------------------------------- sales preset


def sales_columns(df: pd.DataFrame, overrides: dict[str, str | None] | None = None
                  ) -> dict[str, str | None]:
    overrides = overrides or {}
    return {
        role: find_column(df, cands, overrides.get(role), kind=role)
        for role, cands in SALES_COLUMNS.items()
    }


def build_sales_report(df: pd.DataFrame, out_dir: str | Path, title: str = "Laporan Penjualan",
                       overrides: dict[str, str | None] | None = None,
                       name: str = "laporan_penjualan", top_n: int = 10) -> ReportResult:
    """Daily/monthly revenue, top products, trend chart (preset ``penjualan``)."""
    out_dir = Path(out_dir)
    chart_dir = out_dir / "grafik"
    cols = sales_columns(df, overrides)
    tgl, produk, qty, harga, total = (cols[k] for k in ("tanggal", "produk", "qty", "harga",
                                                        "total"))
    missing = []
    if not tgl or not pd.api.types.is_datetime64_any_dtype(df[tgl]):
        missing.append("tanggal (berisi tanggal)")
    if not produk:
        missing.append("produk")
    if not total and not (qty and harga):
        missing.append("total, atau qty + harga")
    if missing:
        raise DataToolsError(
            "Preset penjualan butuh kolom: " + "; ".join(missing)
            + f". Kolom yang ada: {', '.join(map(str, df.columns))}. "
            "Gunakan --kolom-tanggal/--kolom-produk/--kolom-qty/--kolom-harga/--kolom-total."
        )
    log.info("Kolom penjualan: %s", ", ".join(f"{k}={v}" for k, v in cols.items() if v))

    data = _unify(df, [c for c in (produk, cols["kategori"], cols["cabang"]) if c])
    data = data[data[tgl].notna()]
    if total and pd.api.types.is_numeric_dtype(data[total]):
        omzet = data[total].astype(float)
        if qty and harga:  # fill missing totals from qty x harga
            omzet = omzet.fillna(data[qty].astype(float) * data[harga].astype(float))
    else:
        omzet = data[qty].astype(float) * data[harga].astype(float)
    data["_omzet"] = omzet.fillna(0.0)
    data["_qty"] = data[qty].astype(float).fillna(0.0) if qty else 1.0
    data["_tanggal"] = data[tgl].dt.normalize()
    trx_col = cols["transaksi"]

    def agg(frame: pd.DataFrame, by) -> pd.DataFrame:
        g = frame.groupby(by, sort=True)
        res = pd.DataFrame({
            "omzet": g["_omzet"].sum(),
            "qty": g["_qty"].sum(),
            "transaksi": g[trx_col].nunique() if trx_col else g.size(),
        })
        res["rata_rata_per_transaksi"] = (res["omzet"] / res["transaksi"]).round(0)
        return res

    daily = agg(data, "_tanggal")
    full_range = pd.date_range(daily.index.min(), daily.index.max(), freq="D")
    daily = daily.reindex(full_range, fill_value=0)
    daily.index.name = "tanggal"
    daily = daily.reset_index()
    daily.insert(1, "hari", daily["tanggal"].map(lambda d: format_tanggal(d, True).split(",")[0]))

    data["_bulan"] = data["_tanggal"].dt.to_period("M")
    monthly = agg(data, "_bulan")
    monthly["pertumbuhan"] = monthly["omzet"].pct_change().round(4)
    monthly.index = [format_bulan(p) for p in monthly.index]
    monthly.index.name = "bulan"
    monthly = monthly.reset_index()

    products = agg(data, produk).sort_values("omzet", ascending=False)
    products["porsi_omzet"] = (products["omzet"] / products["omzet"].sum()).round(4)
    products = products.reset_index().rename(columns={produk: "produk"})
    products.insert(0, "peringkat", range(1, len(products) + 1))

    sheets: dict[str, Any] = {}
    extra_tables = []
    breakdowns = []
    for role in ("kategori", "cabang"):
        col = cols[role]
        if col:
            breakdowns.append((role, col))
    if not cols["cabang"] and "source_file" in data.columns and data["source_file"].nunique() > 1:
        breakdowns.append(("sumber", "source_file"))
    for role, col in breakdowns:
        b = agg(data, col).sort_values("omzet", ascending=False)
        b["porsi_omzet"] = (b["omzet"] / b["omzet"].sum()).round(4)
        b = b.reset_index().rename(columns={col: role})
        extra_tables.append((role, b))

    top_names = products["produk"].head(20).tolist()
    pivot = (
        data[data[produk].isin(top_names)]
        .pivot_table(index=produk, columns="_bulan", values="_omzet", aggfunc="sum", fill_value=0)
    )
    pivot = pivot.reindex(top_names)
    pivot.columns = [format_bulan(p) for p in pivot.columns]
    pivot["total"] = pivot.sum(axis=1)
    pivot = pivot.reset_index().rename(columns={produk: "produk"})

    total_omzet = data["_omzet"].sum()
    n_trx = int(data[trx_col].nunique()) if trx_col else len(data)
    best_day = daily.loc[daily["omzet"].idxmax()]
    best_product = products.iloc[0]
    period = f"{format_tanggal(data['_tanggal'].min())} – {format_tanggal(data['_tanggal'].max())}"
    kpis = [
        ("Total omzet", format_rupiah(total_omzet)),
        ("Jumlah transaksi", format_number(n_trx)),
        ("Total barang terjual", format_number(data["_qty"].sum())),
        ("Rata-rata per transaksi", format_rupiah(total_omzet / n_trx if n_trx else 0)),
        ("Rata-rata omzet per hari", format_rupiah(daily["omzet"].mean())),
        ("Periode", period),
        ("Produk terlaris", f"{best_product['produk']} ({format_rupiah(best_product['omzet'])})"),
        ("Hari terbaik", (f"{format_tanggal(best_day['tanggal'], True)} "
                         f"({format_rupiah(best_day['omzet'])})")),
    ]
    kpi_df = pd.DataFrame(kpis, columns=["indikator", "nilai"])

    money_cols = {"omzet", "rata_rata_per_transaksi", "total", "_omzet"} | {
        c for c in pivot.columns if c != "produk"
    } | {c for c in df.columns if is_money_column(c)}
    percent_cols = {"porsi_omzet", "pertumbuhan"}
    sheets["Data"] = df
    sheets["Ringkasan"] = [("Indikator utama", kpi_df),
                           ("Statistik kolom angka", describe_frame(df))]
    sheets["Harian"] = daily
    sheets["Bulanan"] = monthly
    sheets["Produk Teratas"] = products
    for role, table in extra_tables:
        sheets[f"Per {role.title()}"] = table
    sheets["Pivot"] = [("Omzet per produk per bulan (20 produk teratas)", pivot)]

    chart_files = [
        charts.line_chart(daily["tanggal"], daily["omzet"], chart_dir / "tren_omzet_harian.png",
                          "Tren omzet harian", money=True),
        charts.bar_chart(monthly["bulan"], monthly["omzet"], chart_dir / "omzet_bulanan.png",
                         "Omzet per bulan", money=True, horizontal=False),
        charts.bar_chart(products["produk"].head(top_n), products["omzet"].head(top_n),
                         chart_dir / "produk_teratas.png", f"{top_n} produk dengan omzet tertinggi",
                         money=True),
    ]
    for role, table in extra_tables:
        chart_files.append(charts.bar_chart(
            table[role].astype(str), table["omzet"], chart_dir / f"omzet_per_{role}.png",
            f"Omzet per {role}", money=True, color=charts.SECOND,
        ))

    excel = write_excel(out_dir / f"{name}.xlsx", sheets, money_columns=money_cols,
                        percent_columns=percent_cols, images={"Grafik": chart_files})
    tables = [
        html_table("Omzet per bulan", monthly, money={"omzet", "rata_rata_per_transaksi"},
                   percent=percent_cols),
        html_table(f"Produk teratas (top {top_n})", products.head(top_n),
                   money={"omzet", "rata_rata_per_transaksi"}, percent=percent_cols),
    ]
    for role, table in extra_tables:
        tables.append(html_table(f"Omzet per {role}", table,
                                 money={"omzet", "rata_rata_per_transaksi"}, percent=percent_cols))
    html = render_html(out_dir / f"{name}.html", title, kpis, tables, chart_files)
    return ReportResult(excel, html, chart_files, sheets, kpis)
