"""Load and validate YAML scraper configs.

User-facing error messages are in Bahasa Indonesia because configs are written
by non-programmers.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

FIELD_TYPES = ("text", "int", "float", "price_idr", "url", "date")

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; otomasi-kit-scraper/0.1; +https://github.com/otomasi-kit)"
)


class ConfigError(ValueError):
    """Raised when a config file is invalid. Message is user-facing (Indonesian)."""


@dataclass
class FieldSpec:
    name: str
    selector: str | None = None  # CSS selector relative to the item; None = the item itself
    attr: str = "text"  # "text", "html", or an attribute name such as "href"
    path: str | None = None  # dotted path into JSON (JSON-LD item or JSON <script>)
    regex: str | None = None
    type: str = "text"
    default: Any = None
    required: bool = False
    multiple: bool = False  # take every match and join them
    join: str = ", "
    decimal: str | None = None  # "," or "." to force the decimal separator
    date_format: str | None = None  # explicit strptime format, e.g. "%d/%m/%Y"
    map: dict[str, Any] | None = None  # replace values, e.g. {"Three": 3}


@dataclass
class PaginationSpec:
    next: str | None = None  # CSS selector of the "next page" link
    url_pattern: str | None = None  # e.g. "https://site/katalog?page={page}"
    start: int = 1
    end: int | None = None
    max_pages: int | None = None
    stop_when_empty: bool = True


@dataclass
class DetailSpec:
    link: str  # field name whose value is the detail-page URL
    fields: list[FieldSpec] = field(default_factory=list)
    json_ld: bool = False
    json_ld_type: str | None = "Product"


@dataclass
class MonitorSpec:
    key: str | None = None  # field identifying an item across runs
    price: str | None = None  # price field to compare
    label: str | None = None  # human-readable name field for the report
    track: list[str] = field(default_factory=list)  # other fields to watch


@dataclass
class ScraperConfig:
    name: str
    start_urls: list[str]
    fields: list[FieldSpec]
    items: str | None = None  # CSS selector of one item (card/row)
    json_ld: bool = False
    json_ld_type: str | None = "Product"
    json_script: str | None = None  # CSS selector of a <script> containing JSON
    json_items_path: str | None = None  # dotted path to the item list inside that JSON
    pagination: PaginationSpec | None = None
    detail: DetailSpec | None = None
    monitor: MonitorSpec = field(default_factory=MonitorSpec)
    headers: dict[str, str] = field(default_factory=dict)
    user_agent: str = DEFAULT_USER_AGENT
    delay: tuple[float, float] = (1.0, 3.0)
    timeout: float = 20.0
    retries: int = 3
    backoff: float = 1.0
    max_items: int | None = None
    max_pages: int | None = None
    dedupe_by: list[str] | None = None
    respect_robots: bool = True
    render: str = "html"  # "html" or "js"
    render_wait: str | None = None  # selector to wait for in js mode
    source_path: Path | None = None

    @property
    def field_names(self) -> list[str]:
        names = [f.name for f in self.fields]
        if self.detail:
            names += [f.name for f in self.detail.fields if f.name not in names]
        return names

    def all_fields(self) -> list[FieldSpec]:
        out = list(self.fields)
        if self.detail:
            out += self.detail.fields
        return out


# --------------------------------------------------------------------------- helpers

_TOP_KEYS = {
    "name",
    "nama",
    "start_urls",
    "items",
    "fields",
    "json_ld",
    "json_ld_type",
    "json_script",
    "json_items_path",
    "pagination",
    "detail",
    "monitor",
    "headers",
    "user_agent",
    "delay",
    "timeout",
    "retries",
    "backoff",
    "max_items",
    "max_pages",
    "dedupe_by",
    "respect_robots",
    "render",
    "render_wait",
    "description",
    "deskripsi",
}
_FIELD_KEYS = {
    "selector",
    "attr",
    "path",
    "regex",
    "type",
    "default",
    "required",
    "multiple",
    "join",
    "decimal",
    "date_format",
    "map",
}
_PAGINATION_KEYS = {"next", "url_pattern", "start", "end", "max_pages", "stop_when_empty"}
_DETAIL_KEYS = {"link", "fields", "json_ld", "json_ld_type"}
_MONITOR_KEYS = {"key", "price", "label", "track"}


def _check_keys(data: dict, allowed: set[str], where: str) -> None:
    for key in data:
        if key not in allowed:
            hint = difflib.get_close_matches(str(key), sorted(allowed), n=1)
            saran = f" Mungkin maksud Anda '{hint[0]}'?" if hint else ""
            raise ConfigError(f"Kunci tidak dikenal '{key}' di {where}.{saran}")


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _parse_delay(value: Any) -> tuple[float, float]:
    try:
        if isinstance(value, (list, tuple)):
            if len(value) != 2:
                raise ValueError
            lo, hi = float(value[0]), float(value[1])
        else:
            lo = hi = float(value)
    except (TypeError, ValueError):
        raise ConfigError(
            "'delay' harus berupa angka (detik) atau daftar dua angka, mis. [1, 3]."
        ) from None
    if lo < 0 or hi < lo:
        raise ConfigError("'delay' tidak valid: nilai minimum harus >= 0 dan <= maksimum.")
    return lo, hi


def _parse_fields(raw: Any, where: str) -> list[FieldSpec]:
    if not isinstance(raw, dict) or not raw:
        raise ConfigError(f"'{where}' harus berisi minimal satu field (nama: selector).")
    fields: list[FieldSpec] = []
    for name, spec in raw.items():
        name = str(name)
        if spec is None or isinstance(spec, str):
            fields.append(FieldSpec(name=name, selector=spec or None))
            continue
        if not isinstance(spec, dict):
            raise ConfigError(f"Field '{name}' di {where} harus berupa teks selector atau objek.")
        _check_keys(spec, _FIELD_KEYS, f"field '{name}'")
        ftype = str(spec.get("type", "text"))
        if ftype not in FIELD_TYPES:
            raise ConfigError(
                f"Tipe '{ftype}' pada field '{name}' tidak dikenal. "
                f"Pilihan: {', '.join(FIELD_TYPES)}."
            )
        decimal = spec.get("decimal")
        if decimal not in (None, ",", "."):
            raise ConfigError(f"'decimal' pada field '{name}' harus ',' atau '.'.")
        regex = spec.get("regex")
        if regex is not None:
            import re

            try:
                re.compile(regex)
            except re.error as exc:
                raise ConfigError(f"Regex pada field '{name}' tidak valid: {exc}") from None
        mapping = spec.get("map")
        if mapping is not None and not isinstance(mapping, dict):
            raise ConfigError(f"'map' pada field '{name}' harus berupa pasangan nilai: ganti.")
        fields.append(
            FieldSpec(
                name=name,
                selector=spec.get("selector") or None,
                attr=str(spec.get("attr", "text")),
                path=spec.get("path"),
                regex=regex,
                type=ftype,
                default=spec.get("default"),
                required=bool(spec.get("required", False)),
                multiple=bool(spec.get("multiple", False)),
                join=str(spec.get("join", ", ")),
                decimal=decimal,
                date_format=spec.get("date_format"),
                map={str(k): v for k, v in mapping.items()} if mapping else None,
            )
        )
    return fields


def _opt_int(data: dict, key: str, where: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"'{key}' di {where} harus bilangan bulat.") from None
    if number < 0:
        raise ConfigError(f"'{key}' di {where} tidak boleh negatif.")
    return number


def parse_config(data: Any, source_path: Path | None = None) -> ScraperConfig:
    """Build a ScraperConfig from already-parsed YAML data."""
    if not isinstance(data, dict):
        raise ConfigError("Isi config harus berupa pasangan kunci: nilai (mapping YAML).")
    _check_keys(data, _TOP_KEYS, "config")

    name = data.get("name") or data.get("nama")
    if not name:
        name = source_path.stem if source_path else "scraper"

    pagination = None
    if data.get("pagination"):
        p = data["pagination"]
        if not isinstance(p, dict):
            raise ConfigError("'pagination' harus berupa objek (next / url_pattern / ...).")
        _check_keys(p, _PAGINATION_KEYS, "pagination")
        pagination = PaginationSpec(
            next=p.get("next"),
            url_pattern=p.get("url_pattern"),
            start=_opt_int(p, "start", "pagination") or 1,
            end=_opt_int(p, "end", "pagination"),
            max_pages=_opt_int(p, "max_pages", "pagination"),
            stop_when_empty=bool(p.get("stop_when_empty", True)),
        )
        if pagination.next and pagination.url_pattern:
            raise ConfigError("Pilih salah satu: 'pagination.next' ATAU 'pagination.url_pattern'.")
        if pagination.url_pattern and "{page}" not in pagination.url_pattern:
            raise ConfigError("'pagination.url_pattern' harus mengandung '{page}'.")
        if not pagination.next and not pagination.url_pattern:
            raise ConfigError("'pagination' butuh 'next' (selector) atau 'url_pattern'.")

    start_urls = [str(u) for u in _as_list(data.get("start_urls"))]
    if not start_urls and not (pagination and pagination.url_pattern):
        raise ConfigError("'start_urls' wajib diisi (minimal satu URL).")

    json_ld = bool(data.get("json_ld", False))
    json_script = data.get("json_script")
    items = data.get("items")
    if not items and not json_ld and not json_script:
        raise ConfigError(
            "Tentukan cara menemukan item: 'items' (CSS selector), 'json_ld: true', "
            "atau 'json_script' + 'json_items_path'."
        )

    fields = _parse_fields(data.get("fields"), "fields")

    detail = None
    if data.get("detail"):
        d = data["detail"]
        if not isinstance(d, dict):
            raise ConfigError("'detail' harus berupa objek (link + fields).")
        _check_keys(d, _DETAIL_KEYS, "detail")
        if not d.get("link"):
            raise ConfigError("'detail.link' wajib: nama field yang berisi URL halaman detail.")
        if d["link"] not in [f.name for f in fields]:
            raise ConfigError(f"'detail.link' = '{d['link']}' bukan nama field yang ada.")
        detail = DetailSpec(
            link=str(d["link"]),
            fields=_parse_fields(d.get("fields"), "detail.fields"),
            json_ld=bool(d.get("json_ld", False)),
            json_ld_type=d.get("json_ld_type", "Product"),
        )

    monitor = MonitorSpec()
    if data.get("monitor"):
        m = data["monitor"]
        if not isinstance(m, dict):
            raise ConfigError("'monitor' harus berupa objek (key / price / label / track).")
        _check_keys(m, _MONITOR_KEYS, "monitor")
        monitor = MonitorSpec(
            key=m.get("key"),
            price=m.get("price"),
            label=m.get("label"),
            track=[str(t) for t in _as_list(m.get("track"))],
        )

    headers = data.get("headers") or {}
    if not isinstance(headers, dict):
        raise ConfigError("'headers' harus berupa pasangan nama: nilai.")

    render = str(data.get("render", "html"))
    if render not in ("html", "js"):
        raise ConfigError("'render' harus 'html' atau 'js'.")

    timeout = data.get("timeout", 20)
    backoff = data.get("backoff", 1.0)
    try:
        timeout = float(timeout)
        backoff = float(backoff)
    except (TypeError, ValueError):
        raise ConfigError("'timeout' dan 'backoff' harus berupa angka (detik).") from None

    cfg = ScraperConfig(
        name=str(name),
        start_urls=start_urls,
        fields=fields,
        items=items,
        json_ld=json_ld,
        json_ld_type=data.get("json_ld_type", "Product"),
        json_script=json_script,
        json_items_path=data.get("json_items_path"),
        pagination=pagination,
        detail=detail,
        monitor=monitor,
        headers={str(k): str(v) for k, v in headers.items()},
        user_agent=str(data.get("user_agent") or DEFAULT_USER_AGENT),
        delay=_parse_delay(data.get("delay", [1, 3])),
        timeout=timeout,
        retries=_opt_int(data, "retries", "config") if data.get("retries") is not None else 3,
        backoff=backoff,
        max_items=_opt_int(data, "max_items", "config"),
        max_pages=_opt_int(data, "max_pages", "config"),
        dedupe_by=[str(x) for x in _as_list(data.get("dedupe_by"))] or None,
        respect_robots=bool(data.get("respect_robots", True)),
        render=render,
        render_wait=data.get("render_wait"),
        source_path=source_path,
    )

    known = set(cfg.field_names)
    for fname in cfg.dedupe_by or []:
        if fname not in known:
            raise ConfigError(f"'dedupe_by' menyebut field '{fname}' yang tidak ada.")
    for label, fname in (
        ("monitor.key", monitor.key),
        ("monitor.price", monitor.price),
        ("monitor.label", monitor.label),
    ):
        if fname and fname not in known:
            raise ConfigError(f"'{label}' menyebut field '{fname}' yang tidak ada.")
    for fname in monitor.track:
        if fname not in known:
            raise ConfigError(f"'monitor.track' menyebut field '{fname}' yang tidak ada.")
    return cfg


def load_config(path: str | Path) -> ScraperConfig:
    """Read a YAML config file."""
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"File config tidak ditemukan: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Format YAML di {path} tidak valid:\n{exc}") from None
    return parse_config(data, source_path=path)
