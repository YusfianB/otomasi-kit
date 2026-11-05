"""Indonesian-locale helpers: parse and format numbers, Rupiah and dates."""

from __future__ import annotations

import datetime as dt
import math
import re
from typing import Any

import pandas as pd

BULAN = [
    "Januari", "Februari", "Maret", "April", "Mei", "Juni",
    "Juli", "Agustus", "September", "Oktober", "November", "Desember",
]
BULAN_SINGKAT = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]

# Month names/abbreviations (Indonesian + English) -> month number.
_MONTHS: dict[str, int] = {}
for _i, _names in enumerate(
    [
        ("januari", "jan", "january"),
        ("februari", "feb", "pebruari", "peb", "february"),
        ("maret", "mar", "march"),
        ("april", "apr"),
        ("mei", "may"),
        ("juni", "jun", "june"),
        ("juli", "jul", "july"),
        ("agustus", "agu", "agt", "ags", "agus", "aug", "august"),
        ("september", "sep", "sept"),
        ("oktober", "okt", "oct", "october"),
        ("november", "nov", "nopember", "nop"),
        ("desember", "des", "dec", "december"),
    ],
    start=1,
):
    for _n in _names:
        _MONTHS[_n] = _i

_WEEKDAYS = (
    "senin", "selasa", "rabu", "kamis", "jumat", "jum'at", "sabtu", "minggu", "ahad",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "mon", "tue", "wed", "thu", "fri", "sat", "sun",
)
_WEEKDAY_RE = re.compile(r"^(?:%s)\b[,.]?\s*" % "|".join(re.escape(w) for w in _WEEKDAYS))
_TIME = r"(?:[\sT,]+(?:pukul|jam)?\s*(\d{1,2})[:.](\d{2})(?:[:.](\d{2}))?(?:\s*(?:wib|wita|wit))?)?"
_RE_YMD = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})" + _TIME + r"$")
_RE_DMY = re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2}|\d{4})" + _TIME + r"$")
_RE_D_MON_Y = re.compile(r"^(\d{1,2})[\s\-/.]*([a-z']+)\.?[\s\-/.,]+(\d{2}|\d{4})" + _TIME + r"$")
_RE_MON_D_Y = re.compile(r"^([a-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})" + _TIME + r"$")

NA_TOKENS = {"", "-", "--", "n/a", "na", "nan", "nat", "null", "none", "#n/a", "?", "<na>"}


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in NA_TOKENS
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------------------- numbers

MONEY_HINTS = (
    "harga", "total", "subtotal", "omzet", "pendapatan", "gaji", "tunjangan", "potongan",
    "biaya", "nominal", "bayar", "upah", "bonus", "pajak", "ppn", "pph", "diskon", "lembur",
    "insentif", "bpjs", "revenue", "price", "amount", "rupiah", "saldo", "tagihan",
)


def is_money_column(name: str) -> bool:
    lowered = str(name).lower()
    return any(h in lowered for h in MONEY_HINTS)



_CURRENCY_RE = re.compile(r"^(?:rp\.?|idr)\s*|\s*(?:rp\.?|idr)$", re.IGNORECASE)
_MULTIPLIERS = {
    "rb": 1e3, "ribu": 1e3, "k": 1e3,
    "jt": 1e6, "juta": 1e6,
    "m": 1e9, "miliar": 1e9, "milyar": 1e9,
}
_SUFFIX_RE = re.compile(r"^(.*?\d)\s*(rb|ribu|k|jt|juta|miliar|milyar)\.?$", re.IGNORECASE)
_NUMERIC_BODY_RE = re.compile(r"^\d[\d.,]*$|^[.,]\d+$")


def detect_number_style(values) -> str:
    """Return ``"id"`` (1.234,5) or ``"us"`` (1,234.5) based on evidence in a column."""
    id_evidence = us_evidence = 0
    for v in values:
        if not isinstance(v, str):
            continue
        s = v.strip()
        if re.search(r"\d\.\d{3},\d", s) or re.search(r"\d,\d{1,2}(?!\d)", s) or re.search(
            r"\d\.\d{3}\.\d{3}", s
        ):
            id_evidence += 1
        if re.search(r"\d,\d{3}\.\d", s) or re.search(r"\d,\d{3},\d{3}", s):
            us_evidence += 1
    return "us" if us_evidence > id_evidence else "id"


def parse_number(value: Any, style: str = "id") -> float | None:
    """Parse numbers written the Indonesian way.

    Examples: ``"Rp 1.234.567,50"`` -> 1234567.5, ``"1.234"`` -> 1234, ``"12,5%"`` -> 12.5,
    ``"15rb"`` -> 15000, ``"(5.000)"`` -> -5000. Returns ``None`` when not a number.
    Percentages keep the written value (``"12,5%"`` -> 12.5).
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None if (isinstance(value, float) and math.isnan(value)) else float(value)
    if not isinstance(value, str):
        return None
    s = value.strip().replace("\xa0", " ")
    if not s:
        return None
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1].strip()
    if s.startswith(("-", "−")):
        negative, s = not negative, s[1:].strip()
    elif s.startswith("+"):
        s = s[1:].strip()
    s = _CURRENCY_RE.sub("", s).strip()
    if s.startswith(("-", "−")):  # "Rp -5.000"
        negative, s = not negative, s[1:].strip()
    if s.endswith("%"):
        s = s[:-1].strip()
    multiplier = 1.0
    m = _SUFFIX_RE.match(s)
    if m:
        s, multiplier = m.group(1), _MULTIPLIERS[m.group(2).lower()]
    if s.endswith(",-") or s.endswith(".-"):  # "Rp 15.000,-"
        s = s[:-2]
    s = s.replace(" ", "")
    if not _NUMERIC_BODY_RE.match(s):
        return None
    number = _parse_separators(s, style)
    if number is None:
        return None
    number *= multiplier
    return -number if negative else number


def _valid_groups(groups: list[str]) -> bool:
    return 1 <= len(groups[0]) <= 3 and all(len(g) == 3 for g in groups[1:])


def _parse_separators(s: str, style: str) -> float | None:
    has_dot, has_comma = "." in s, "," in s
    try:
        if has_dot and has_comma:
            dec = "," if s.rfind(",") > s.rfind(".") else "."
            thou = "." if dec == "," else ","
            int_part, _, frac = s.rpartition(dec)
            if dec in int_part or not _valid_groups(int_part.split(thou)):
                return None
            return float(int_part.replace(thou, "") + "." + frac)
        if has_dot or has_comma:
            sep = "." if has_dot else ","
            groups = s.split(sep)
            # "thousands" separator for this style
            thousands_sep = "." if style == "id" else ","
            if len(groups) > 2:
                return float("".join(groups)) if _valid_groups(groups) else None
            head, tail = groups
            if not head:
                return float("0." + tail)
            looks_thousands = len(tail) == 3 and _valid_groups(groups) and head != "0"
            if sep == thousands_sep and looks_thousands:
                return float(head + tail)
            return float(head + "." + tail)
        return float(s)
    except ValueError:
        return None


def _id_separators(text: str) -> str:
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def format_number(value: Any, decimals: int | None = None) -> str:
    """Format with Indonesian separators: 1234567.5 -> ``"1.234.567,5"``.

    With ``decimals=None`` integers get no decimals and other numbers up to two
    (trailing zeros removed).
    """
    if is_missing(value):
        return ""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    auto = decimals is None
    if auto:
        decimals = 0 if num.is_integer() else 2
    if round(num, decimals) == 0:
        num = 0.0
    text = f"{num:,.{decimals}f}"
    if auto and "." in text:
        text = text.rstrip("0").rstrip(".")
    return _id_separators(text)


def format_rupiah(value: Any, decimals: int = 0) -> str:
    """1250000 -> ``"Rp 1.250.000"``; negative -> ``"-Rp 1.250.000"``."""
    if is_missing(value):
        return ""
    try:
        num = float(value)
    except (TypeError, ValueError):
        parsed = parse_number(str(value))
        if parsed is None:
            return str(value)
        num = parsed
    text = _id_separators(f"{abs(num):,.{decimals}f}")
    return ("-Rp " if round(num, decimals) < 0 else "Rp ") + text


def format_rupiah_short(value: float) -> str:
    """Compact Rupiah for chart axes: 1500000 -> ``"Rp 1,5 jt"``."""
    num = float(value)
    for limit, suffix in ((1e12, "T"), (1e9, "M"), (1e6, "jt"), (1e3, "rb")):
        if abs(num) >= limit:
            return f"Rp {format_number(round(num / limit, 1))} {suffix}"
    return f"Rp {format_number(round(num))}"


_SATUAN = ["", "satu", "dua", "tiga", "empat", "lima", "enam", "tujuh", "delapan", "sembilan",
           "sepuluh", "sebelas"]


def terbilang(value: Any) -> str:
    """Spell an integer in Indonesian: 1250000 -> ``"satu juta dua ratus lima puluh ribu"``."""
    n = int(round(float(value)))
    if n == 0:
        return "nol"
    if n < 0:
        return "minus " + terbilang(-n)

    def spell(x: int) -> str:
        if x < 12:
            return _SATUAN[x]
        if x < 20:
            return spell(x - 10) + " belas"
        if x < 100:
            return (spell(x // 10) + " puluh " + spell(x % 10)).strip()
        if x < 200:
            return ("seratus " + spell(x - 100)).strip()
        if x < 1000:
            return (spell(x // 100) + " ratus " + spell(x % 100)).strip()
        if x < 2000:
            return ("seribu " + spell(x - 1000)).strip()
        for unit, name in ((10**12, "triliun"), (10**9, "miliar"), (10**6, "juta"), (10**3, "ribu")):
            if x >= unit:
                return (spell(x // unit) + f" {name} " + spell(x % unit)).strip()
        return ""

    return " ".join(spell(n).split())


# --------------------------------------------------------------------------- dates


def _to_year(text: str) -> int:
    y = int(text)
    if len(text) == 2:
        y += 2000 if y < 70 else 1900
    return y


def _build(y: int, mo: int, d: int, hh, mm, ss) -> pd.Timestamp | None:
    try:
        return pd.Timestamp(
            dt.datetime(y, mo, d, int(hh or 0), int(mm or 0), int(ss or 0))
        )
    except (ValueError, OverflowError):
        return None


def parse_date(value: Any, dayfirst: bool = True) -> pd.Timestamp | None:
    """Parse a date in common Indonesian formats.

    Supports ``05/11/2026``, ``5-11-26``, ``2026-11-05``, ``5 Nov 2026``, ``5 Agustus 2026``,
    ``Kamis, 5 November 2026``, ``05-Nov-2026 14:30`` and real date/datetime objects.
    Returns ``None`` when the value is not a date.
    """
    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value
    if isinstance(value, dt.datetime):
        return pd.Timestamp(value)
    if isinstance(value, dt.date):
        return pd.Timestamp(dt.datetime(value.year, value.month, value.day))
    if not isinstance(value, str):
        return None
    s = " ".join(value.strip().lower().split())
    if not s or len(s) > 40:
        return None
    s = _WEEKDAY_RE.sub("", s)
    m = _RE_YMD.match(s)
    if m:
        y, mo, d, hh, mi, ss = m.groups()
        return _build(int(y), int(mo), int(d), hh, mi, ss)
    m = _RE_DMY.match(s)
    if m:
        a, b, y, hh, mi, ss = m.groups()
        a, b = int(a), int(b)
        d, mo = (a, b) if dayfirst else (b, a)
        if mo > 12 and d <= 12:
            d, mo = mo, d
        return _build(_to_year(y), mo, d, hh, mi, ss)
    m = _RE_D_MON_Y.match(s)
    if m:
        d, mon, y, hh, mi, ss = m.groups()
        mo = _MONTHS.get(mon.rstrip("."))
        return _build(_to_year(y), mo, int(d), hh, mi, ss) if mo else None
    m = _RE_MON_D_Y.match(s)
    if m:
        mon, d, y, hh, mi, ss = m.groups()
        mo = _MONTHS.get(mon)
        return _build(int(y), mo, int(d), hh, mi, ss) if mo else None
    return None


def detect_dayfirst(values) -> bool:
    """Look for ``mm/dd/yyyy`` evidence (second part > 12); default to day-first."""
    day_first = month_first = 0
    for v in values:
        if isinstance(v, str):
            m = _RE_DMY.match(v.strip())
            if m:
                a, b = int(m.group(1)), int(m.group(2))
                if a > 12 >= b:
                    day_first += 1
                elif b > 12 >= a:
                    month_first += 1
    return not (month_first > day_first)


def _as_date(value: Any) -> dt.date | None:
    if is_missing(value):
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    ts = parse_date(str(value))
    return ts.date() if ts is not None else None


def format_tanggal(value: Any, with_day: bool = False, short: bool = False) -> str:
    """``2026-11-05`` -> ``"5 November 2026"`` (``with_day`` -> ``"Kamis, 5 November 2026"``)."""
    d = _as_date(value)
    if d is None:
        return "" if is_missing(value) else str(value)
    month = (BULAN_SINGKAT if short else BULAN)[d.month - 1]
    text = f"{d.day} {month} {d.year}"
    return f"{HARI[d.weekday()]}, {text}" if with_day else text


def format_bulan(value: Any, short: bool = False) -> str:
    """Month label: ``"November 2026"`` / ``"Nov 2026"``."""
    if isinstance(value, pd.Period):
        y, m = value.year, value.month
    else:
        d = _as_date(value)
        if d is None:
            return str(value)
        y, m = d.year, d.month
    return f"{(BULAN_SINGKAT if short else BULAN)[m - 1]} {y}"
