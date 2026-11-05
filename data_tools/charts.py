"""PNG charts with matplotlib (Agg backend, Indonesian labels and number formats)."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.ticker import FuncFormatter

from data_tools.formatting import (
    BULAN_SINGKAT,
    format_number,
    format_rupiah_short,
)

ACCENT = "#1F4E78"
ACCENT_LIGHT = "#9DC3E6"
SECOND = "#C55A11"


def _axis_formatter(money: bool) -> FuncFormatter:
    if money:
        return FuncFormatter(lambda v, _pos: format_rupiah_short(v))
    return FuncFormatter(lambda v, _pos: format_number(v))


def _style(ax, title: str) -> None:
    ax.set_title(title, fontsize=13, fontweight="bold", loc="left", color="#222222")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_color("#999999")
    ax.spines["bottom"].set_color("#999999")
    ax.tick_params(colors="#333333", labelsize=9)
    ax.grid(axis="y", color="#E5E5E5", linewidth=0.8)
    ax.set_axisbelow(True)


def _save(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=110, facecolor="white")
    plt.close(fig)
    return path


def _shorten(label: str, limit: int = 28) -> str:
    label = str(label)
    return label if len(label) <= limit else label[: limit - 1] + "…"


def bar_chart(labels, values, path: Path, title: str, money: bool = False,
              horizontal: bool = True, color: str = ACCENT) -> Path:
    labels = [_shorten(x) for x in labels]
    values = [float(v) for v in values]
    if horizontal:
        fig, ax = plt.subplots(figsize=(10, max(3.0, 0.45 * len(labels) + 1.2)))
        ax.barh(labels[::-1], values[::-1], color=color)
        ax.xaxis.set_major_formatter(_axis_formatter(money))
        _style(ax, title)
        ax.grid(axis="y", visible=False)
        ax.grid(axis="x", color="#E5E5E5", linewidth=0.8)
        top = max(values) if values else 0
        for i, v in enumerate(values[::-1]):
            text = format_rupiah_short(v) if money else format_number(v)
            ax.text(v + top * 0.01, i, text, va="center", fontsize=8, color="#333333")
        ax.set_xlim(0, top * 1.15 if top > 0 else 1)
    else:
        fig, ax = plt.subplots(figsize=(10, 4.8))
        ax.bar(labels, values, color=color)
        ax.yaxis.set_major_formatter(_axis_formatter(money))
        _style(ax, title)
        if len(labels) > 6:
            plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
    return _save(fig, path)


def _date_label(value, _pos=None) -> str:
    d = matplotlib.dates.num2date(value)
    return f"{d.day} {BULAN_SINGKAT[d.month - 1]}"


def line_chart(dates, values, path: Path, title: str, money: bool = False,
               rolling: int | None = 7) -> Path:
    series = pd.Series(list(values), index=pd.to_datetime(list(dates))).astype(float)
    fig, ax = plt.subplots(figsize=(10, 4.8))
    ax.plot(series.index, series.values, color=ACCENT_LIGHT, linewidth=1.2, label="Harian")
    if rolling and len(series) >= rolling * 2:
        ma = series.rolling(rolling, min_periods=1).mean()
        ax.plot(ma.index, ma.values, color=ACCENT, linewidth=2.4,
                label=f"Rata-rata {rolling} hari")
        ax.legend(frameon=False, fontsize=9, loc="upper left")
    ax.yaxis.set_major_formatter(_axis_formatter(money))
    ax.xaxis.set_major_formatter(FuncFormatter(_date_label))
    ax.set_ylim(bottom=0)
    _style(ax, title)
    fig.autofmt_xdate()
    return _save(fig, path)


def histogram(values, path: Path, title: str, money: bool = False) -> Path:
    data = pd.Series(values).dropna().astype(float)
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.hist(data, bins=min(30, max(5, int(len(data) ** 0.5))), color=ACCENT, edgecolor="white")
    ax.xaxis.set_major_formatter(_axis_formatter(money))
    _style(ax, title)
    ax.set_ylabel("Jumlah data", fontsize=9)
    return _save(fig, path)
