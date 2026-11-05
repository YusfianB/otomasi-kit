"""Extract items and fields from HTML (CSS selectors) or embedded JSON (JSON-LD, __NEXT_DATA__)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from .config import FieldSpec, ScraperConfig

# --------------------------------------------------------------------------- numbers

# Single letters like "m"/"b" are ambiguous ("M" means miliar in Indonesian prices but
# million in English), so only unambiguous suffixes are supported.
_SUFFIX = {
    "rb": 1e3,
    "ribu": 1e3,
    "k": 1e3,
    "jt": 1e6,
    "juta": 1e6,
    "miliar": 1e9,
    "milyar": 1e9,
    "triliun": 1e12,
}
_NUM_RE = re.compile(
    r"(?P<sign>-)?(?P<num>\d[\d.,]*\d|\d)"
    r"(?:\s*(?P<suf>ribu|rb|juta|jt|miliar|milyar|triliun|k)(?![a-z]))?",
    re.IGNORECASE,
)


def _normalize_number(num: str, decimal: str | None) -> float:
    num = re.sub(r"\s", "", num)
    if decimal == ",":
        return float(num.replace(".", "").replace(",", "."))
    if decimal == ".":
        return float(num.replace(",", ""))
    has_dot, has_comma = "." in num, "," in num
    if has_dot and has_comma:
        # whichever comes last is the decimal separator: 1.234,56 / 1,234.56
        if num.rfind(",") > num.rfind("."):
            return float(num.replace(".", "").replace(",", "."))
        return float(num.replace(",", ""))
    sep = "." if has_dot else "," if has_comma else None
    if sep is None:
        return float(num)
    parts = num.split(sep)
    if len(parts) > 2 or (len(parts[-1]) == 3 and parts[0] != "0"):
        return float(num.replace(sep, ""))  # thousands: 1.250.000 / 1,234
    return float(num.replace(sep, "."))  # decimal: 4,5 / 51.77


def parse_number(text: Any, decimal: str | None = None) -> float | None:
    """Parse numbers as written in Indonesian/English pages.

    "Rp1.250.000" -> 1250000, "4,5" -> 4.5, "1,2rb terjual" -> 1200, "£51.77" -> 51.77
    """
    if text is None:
        return None
    if isinstance(text, bool):
        return None
    if isinstance(text, (int, float)):
        return float(text)
    match = _NUM_RE.search(str(text))
    if not match:
        return None
    suffix = (match.group("suf") or "").lower()
    num = match.group("num")
    # "1,2 jt" / "1.5 juta": a single separator before a suffix is a decimal mark
    if suffix and decimal is None and num.count(",") + num.count(".") == 1:
        decimal = "," if "," in num else "."
    try:
        value = _normalize_number(num, decimal)
    except ValueError:
        return None
    if suffix:
        value *= _SUFFIX.get(suffix, 1)
    if match.group("sign"):
        value = -value
    return value


def to_int(text: Any, decimal: str | None = None) -> int | None:
    value = parse_number(text, decimal)
    return None if value is None else round(value)


def to_float(text: Any, decimal: str | None = None) -> float | None:
    return parse_number(text, decimal)


def to_price_idr(text: Any, decimal: str | None = None) -> int | None:
    """Rupiah prices: Indonesian format uses '.' for thousands and ',' for decimals."""
    if text is None:
        return None
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return round(text)
    raw = str(text)
    # skip the currency label: "Rp1.250.000,00", "Rp. 50.000", "IDR 1,250,000"
    match = re.search(r"(?:rp\.?|idr)\s*", raw, re.IGNORECASE)
    if match:
        raw = raw[match.end() :]
    value = parse_number(raw, decimal)
    return None if value is None else round(value)


# --------------------------------------------------------------------------- dates

_MONTHS = {
    "januari": 1,
    "jan": 1,
    "january": 1,
    "februari": 2,
    "feb": 2,
    "pebruari": 2,
    "february": 2,
    "maret": 3,
    "mar": 3,
    "march": 3,
    "april": 4,
    "apr": 4,
    "mei": 5,
    "may": 5,
    "juni": 6,
    "jun": 6,
    "june": 6,
    "juli": 7,
    "jul": 7,
    "july": 7,
    "agustus": 8,
    "agu": 8,
    "agt": 8,
    "ags": 8,
    "aug": 8,
    "august": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "oktober": 10,
    "okt": 10,
    "oct": 10,
    "october": 10,
    "november": 11,
    "nov": 11,
    "nopember": 11,
    "desember": 12,
    "des": 12,
    "dec": 12,
    "december": 12,
}
_REL_UNITS = {
    "detik": "seconds",
    "second": "seconds",
    "seconds": "seconds",
    "menit": "minutes",
    "minute": "minutes",
    "minutes": "minutes",
    "jam": "hours",
    "hour": "hours",
    "hours": "hours",
    "hari": "days",
    "day": "days",
    "days": "days",
    "minggu": "weeks",
    "pekan": "weeks",
    "week": "weeks",
    "weeks": "weeks",
    "bulan": "months",
    "month": "months",
    "months": "months",
    "tahun": "years",
    "year": "years",
    "years": "years",
}


def to_date(text: Any, fmt: str | None = None, today: date | None = None) -> str | None:
    """Return an ISO date (YYYY-MM-DD) from common Indonesian/English date formats."""
    if text is None:
        return None
    raw = re.sub(r"\s+", " ", str(text)).strip()
    if not raw:
        return None
    today = today or date.today()
    if fmt:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            return None
    low = raw.lower()

    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", low)
    if m:
        return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b", low)  # day-first (Indonesia)
    if m:
        return _safe_date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    m = re.search(r"\b(\d{1,2})\s+([a-z]+)\.?,?\s+(\d{4})\b", low)  # 5 November 2026
    if m and m.group(2) in _MONTHS:
        return _safe_date(int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1)))
    m = re.search(r"\b([a-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})\b", low)  # November 5, 2026
    if m and m.group(1) in _MONTHS:
        return _safe_date(int(m.group(3)), _MONTHS[m.group(1)], int(m.group(2)))

    if "hari ini" in low or low in ("today", "baru saja", "just now"):
        return today.isoformat()
    if "kemarin" in low or "yesterday" in low:
        return (today - timedelta(days=1)).isoformat()
    m = re.search(r"(\d+|se|a|an)\s*([a-z]+)\s+(?:yang\s+)?(?:lalu|ago)", low)
    if m and m.group(2) in _REL_UNITS:
        n = 1 if not m.group(1).isdigit() else int(m.group(1))
        unit = _REL_UNITS[m.group(2)]
        if unit == "months":
            delta = timedelta(days=30 * n)
        elif unit == "years":
            delta = timedelta(days=365 * n)
        else:
            delta = timedelta(**{unit: n})
        return (datetime.combine(today, datetime.min.time()) - delta).date().isoformat()
    return None


def _safe_date(y: int, m: int, d: int) -> str | None:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


# --------------------------------------------------------------------------- JSON helpers


def dotted_get(obj: Any, path: str | None) -> Any:
    """Follow a simple dotted path: "offers.price", "offers.0.price", "items[2].name",
    "products.*.name". When a list meets a non-numeric key, the first element is used."""
    if path is None or path == "":
        return obj
    path = re.sub(r"\[(\d+|\*)\]", r".\1", path)
    parts = [p for p in path.split(".") if p != ""]
    return _walk(obj, parts)


def _walk(obj: Any, parts: list[str]) -> Any:
    for i, part in enumerate(parts):
        if obj is None:
            return None
        if part == "*":
            if isinstance(obj, dict):
                obj = list(obj.values())
            if not isinstance(obj, list):
                return None
            return [_walk(x, parts[i + 1 :]) for x in obj]
        if isinstance(obj, list):
            if part.lstrip("-").isdigit():
                idx = int(part)
                obj = obj[idx] if -len(obj) <= idx < len(obj) else None
                continue
            obj = obj[0] if obj else None
            if obj is None:
                return None
        if isinstance(obj, dict):
            obj = obj.get(part)
        else:
            return None
    return obj


def load_json_text(text: str) -> Any:
    """Parse JSON from a <script>, also handling "window.__STATE__ = {...};" wrappers."""
    text = text.strip()
    text = text.removeprefix("<!--").removesuffix("-->")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(text[min(starts) :])
        return value
    except json.JSONDecodeError:
        return None


def _type_matches(obj: dict, wanted: str) -> bool:
    types = obj.get("@type")
    types = types if isinstance(types, list) else [types]
    wanted = wanted.lower()
    return any(isinstance(t, str) and t.lower().rsplit("/", 1)[-1] == wanted for t in types)


def json_ld_objects(soup: BeautifulSoup, wanted_type: str | None = "Product") -> list[dict]:
    """All JSON-LD objects of a type (searching @graph, ItemList, nested values)."""
    roots: list[Any] = []
    for script in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.IGNORECASE)}):
        data = load_json_text(script.string or script.get_text() or "")
        if data is not None:
            roots.append(data)
    if not wanted_type:
        flat: list[dict] = []
        for root in roots:
            items = root if isinstance(root, list) else [root]
            for obj in items:
                if isinstance(obj, dict) and "@graph" in obj:
                    flat += [x for x in obj["@graph"] if isinstance(x, dict)]
                elif isinstance(obj, dict):
                    flat.append(obj)
        return flat

    found: list[dict] = []

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for x in node:
                visit(x)
        elif isinstance(node, dict):
            if _type_matches(node, wanted_type):
                found.append(node)
                return
            for value in node.values():
                if isinstance(value, (dict, list)):
                    visit(value)

    visit(roots)
    return found


# --------------------------------------------------------------------------- fields


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _element_value(el: Tag, attr: str) -> Any:
    if attr == "text":
        return clean_text(el.get_text(" ", strip=True))
    if attr == "html":
        return el.decode_contents().strip()
    if attr == "json":
        return load_json_text(el.string or el.get_text() or "")
    value = el.get(attr)
    if isinstance(value, list):  # e.g. class
        return " ".join(value)
    return value


def convert(value: Any, spec: FieldSpec, base_url: str) -> Any:
    """Apply regex and type conversion to a raw value."""
    if value is None:
        return None
    if isinstance(value, (dict, list)) and spec.type == "text":
        if isinstance(value, list) and all(isinstance(v, (str, int, float)) for v in value):
            value = spec.join.join(str(v) for v in value)
        else:
            value = json.dumps(value, ensure_ascii=False)
    if spec.regex:
        m = re.search(spec.regex, str(value), re.IGNORECASE | re.DOTALL)
        if not m:
            return None
        groups = [g for g in m.groups() if g is not None]
        value = groups[0] if groups else m.group(0)
    if spec.map:
        key = clean_text(str(value))
        if key in spec.map:
            value = spec.map[key]
        else:
            folded = {k.casefold(): v for k, v in spec.map.items()}
            value = folded.get(key.casefold(), value)
    t = spec.type
    if t == "text":
        return clean_text(str(value)) if isinstance(value, str) else value
    if t == "int":
        return to_int(value, spec.decimal)
    if t == "float":
        return to_float(value, spec.decimal)
    if t == "price_idr":
        return to_price_idr(value, spec.decimal)
    if t == "url":
        text = str(value).strip()
        return urljoin(base_url, text) if text else None
    if t == "date":
        return to_date(value, spec.date_format)
    return value


def raw_field(target: Tag | dict | Any, spec: FieldSpec) -> Any:
    """Get the raw (unconverted) value of a field from an element or JSON object."""
    if isinstance(target, Tag):
        if spec.selector:
            elements = (
                target.select(spec.selector)
                if spec.multiple
                else [target.select_one(spec.selector)]
            )
            elements = [e for e in elements if e is not None]
        else:
            elements = [target]
        if not elements:
            return None
        values = []
        for el in elements:
            if spec.path is not None:
                data = load_json_text(el.string or el.get_text() or "")
                values.append(dotted_get(data, spec.path))
            else:
                values.append(_element_value(el, spec.attr))
        values = [v for v in values if v not in (None, "")]
        if not values:
            return None
        return values if spec.multiple else values[0]
    # JSON object (JSON-LD / script data): shorthand "nama: offers.price" means a path
    return dotted_get(target, spec.path or spec.selector or spec.name)


def extract_field(target: Any, spec: FieldSpec, base_url: str) -> tuple[Any, Any]:
    """Return (raw, value) for one field."""
    raw = raw_field(target, spec)
    if spec.multiple and isinstance(raw, list):
        converted = [convert(r, spec, base_url) for r in raw]
        converted = [c for c in converted if c not in (None, "")]
        if spec.type in ("text", "url"):
            value = spec.join.join(str(c) for c in converted) if converted else None
        else:
            value = converted or None
    else:
        value = convert(raw, spec, base_url)
    if value in (None, "") and spec.default is not None:
        value = spec.default
    return raw, value


@dataclass
class ExtractResult:
    items: list[dict]
    raw: list[dict] = field(default_factory=list)  # raw values per item (for `uji`)
    found: int = 0  # elements/objects matched before 'required' filtering
    dropped: int = 0  # items dropped because a required field was empty
    soup: BeautifulSoup | None = None


def make_soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def page_base(soup: BeautifulSoup, page_url: str) -> str:
    base = soup.find("base", href=True)
    return urljoin(page_url, base["href"]) if base else page_url


def find_item_targets(soup: BeautifulSoup, cfg: ScraperConfig) -> list[Any]:
    if cfg.json_ld:
        return json_ld_objects(soup, cfg.json_ld_type)
    if cfg.json_script:
        script = soup.select_one(cfg.json_script)
        if script is None:
            return []
        data = dotted_get(
            load_json_text(script.string or script.get_text() or ""), cfg.json_items_path
        )
        if data is None:
            return []
        if isinstance(data, dict):
            return (
                list(data.values()) if all(isinstance(v, dict) for v in data.values()) else [data]
            )
        return [x for x in data if x is not None] if isinstance(data, list) else []
    return soup.select(cfg.items) if cfg.items else []


def extract_items(html: str | BeautifulSoup, page_url: str, cfg: ScraperConfig) -> ExtractResult:
    soup = html if isinstance(html, BeautifulSoup) else make_soup(html)
    base = page_base(soup, page_url)
    targets = find_item_targets(soup, cfg)
    result = ExtractResult(items=[], found=len(targets), soup=soup)
    for target in targets:
        item, raws, ok = {}, {}, True
        for spec in cfg.fields:
            raw, value = extract_field(target, spec, base)
            if spec.required and value in (None, ""):
                ok = False
            item[spec.name] = value
            raws[spec.name] = raw
        if not ok:
            result.dropped += 1
            continue
        result.items.append(item)
        result.raw.append(raws)
    return result


def extract_detail(
    html: str | BeautifulSoup,
    page_url: str,
    fields: list[FieldSpec],
    json_ld: bool = False,
    json_ld_type: str | None = "Product",
) -> tuple[dict, dict]:
    """Extract fields from a whole (detail) page. Returns (values, raw values)."""
    soup = html if isinstance(html, BeautifulSoup) else make_soup(html)
    base = page_base(soup, page_url)
    target: Any = soup
    if json_ld:
        objects = json_ld_objects(soup, json_ld_type)
        target = objects[0] if objects else {}
    values, raws = {}, {}
    for spec in fields:
        raw, value = extract_field(target, spec, base)
        values[spec.name] = value
        raws[spec.name] = raw
    return values, raws
