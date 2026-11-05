"""Crawl orchestration: pages -> items -> detail pages, with dedupe, limits and resume."""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import ScraperConfig
from .extract import extract_detail, extract_items, page_base
from .fetch import Fetcher, FetchError, RobotsBlocked
from .paginate import PageRef, initial_pages, next_pages, page_limit

log = logging.getLogger("scraper")


@dataclass
class RunSummary:
    config: str
    started_at: str = ""
    finished_at: str = ""
    duration_s: float = 0.0
    pages: int = 0
    detail_pages: int = 0
    items: int = 0
    duplicates: int = 0
    dropped: int = 0
    robots_blocked: int = 0
    resumed_pages: int = 0
    stopped_reason: str = "selesai"
    errors: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def format_text(self) -> str:
        lines = [
            f"Ringkasan '{self.config}':",
            f"  Item tersimpan     : {self.items}",
            f"  Halaman daftar     : {self.pages}"
            + (f" ({self.resumed_pages} dari checkpoint)" if self.resumed_pages else ""),
            f"  Halaman detail     : {self.detail_pages}",
            f"  Duplikat dilewati  : {self.duplicates}",
        ]
        if self.dropped:
            lines.append(f"  Item tidak lengkap : {self.dropped} (field wajib kosong)")
        if self.robots_blocked:
            lines.append(f"  Diblokir robots.txt: {self.robots_blocked}")
        lines.append(f"  Error              : {len(self.errors)}")
        for err in self.errors[:10]:
            lines.append(f"    - {err['url']}: {err['pesan']}")
        if len(self.errors) > 10:
            lines.append(f"    ... dan {len(self.errors) - 10} error lainnya")
        lines.append(f"  Status             : {self.stopped_reason}")
        lines.append(f"  Durasi             : {self.duration_s:.1f} detik")
        return "\n".join(lines)


def dedupe_fields(cfg: ScraperConfig) -> list[str]:
    if cfg.dedupe_by:
        return cfg.dedupe_by
    url_fields = [f.name for f in cfg.fields if f.type == "url"]
    if url_fields:
        return url_fields[:1]
    return [f.name for f in cfg.fields]


def item_key(item: dict, fields: list[str]) -> str:
    return json.dumps([item.get(f) for f in fields], ensure_ascii=False, default=str)


class Checkpoint:
    """Append-only JSON-lines log of finished listing pages, for resuming a crawl."""

    def __init__(self, path: str | Path, config_name: str) -> None:
        self.path = Path(path)
        self.config_name = config_name

    def load(self) -> list[dict]:
        if not self.path.exists():
            return []
        records = []
        with self.path.open(encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue  # partial last line after a crash
                if i == 0 and rec.get("_checkpoint"):
                    if rec.get("config") != self.config_name:
                        log.warning(
                            "Checkpoint %s milik config lain ('%s'); diabaikan.",
                            self.path,
                            rec.get("config"),
                        )
                        return []
                    continue
                records.append(rec)
        return records

    def start(self, keep: bool) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if keep and self.path.exists():
            return
        header = {
            "_checkpoint": 1,
            "config": self.config_name,
            "dibuat": datetime.now().isoformat(timespec="seconds"),
        }
        self.path.write_text(json.dumps(header) + "\n", encoding="utf-8")

    def append(self, record: dict) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            fh.flush()

    def remove(self) -> None:
        self.path.unlink(missing_ok=True)


def scrape(
    cfg: ScraperConfig,
    *,
    fetcher: Fetcher | None = None,
    max_items: int | None = None,
    max_pages: int | None = None,
    checkpoint: str | Path | None = None,
    resume: bool = False,
) -> tuple[list[dict], RunSummary]:
    """Run a config and return (items, summary)."""
    t0 = time.monotonic()
    summary = RunSummary(config=cfg.name, started_at=datetime.now().isoformat(timespec="seconds"))
    own_fetcher = fetcher is None
    fetcher = fetcher or Fetcher.from_config(cfg)
    item_cap = max_items or cfg.max_items
    page_cap = page_limit(cfg, max_pages)
    key_fields = dedupe_fields(cfg)
    listing_names = {f.name for f in cfg.fields}
    dedupe_before_detail = set(key_fields) <= listing_names
    pattern_mode = bool(cfg.pagination and cfg.pagination.url_pattern)

    items: list[dict] = []
    seen: set[str] = set()
    visited: set[str] = set()
    queue: deque[PageRef] = deque()

    ckpt = Checkpoint(checkpoint, cfg.name) if checkpoint else None
    if ckpt and resume:
        records = ckpt.load()
        pending: list[PageRef] = []
        for rec in records:
            if rec.get("error"):
                continue
            visited.add(rec["url"])
            summary.resumed_pages += 1
            summary.pages += 1
            for it in rec.get("items", []):
                k = item_key(it, key_fields)
                if k not in seen:
                    seen.add(k)
                    items.append(it)
            pending += [PageRef(u, n) for u, n in rec.get("next", [])]
        queue.extend(r for r in initial_pages(cfg) if r.url not in visited)
        queue.extend(r for r in pending if r.url not in visited)
        if records:
            log.info(
                "Melanjutkan dari checkpoint: %d halaman, %d item sudah ada.",
                summary.resumed_pages,
                len(items),
            )
    else:
        queue.extend(initial_pages(cfg))
    if ckpt:
        ckpt.start(keep=resume)

    def cap_reached() -> bool:
        return item_cap is not None and len(items) >= item_cap

    try:
        while queue:
            if cap_reached():
                summary.stopped_reason = f"batas max_items ({item_cap}) tercapai"
                break
            if page_cap is not None and summary.pages >= page_cap:
                summary.stopped_reason = f"batas max_pages ({page_cap}) tercapai"
                break
            ref = queue.popleft()
            if ref.url in visited:
                continue
            visited.add(ref.url)
            summary.pages += 1
            log.info("Halaman %d: %s", summary.pages, ref.url)
            try:
                page = fetcher.get(ref.url)
            except RobotsBlocked as exc:
                summary.robots_blocked += 1
                summary.errors.append({"url": ref.url, "pesan": str(exc)})
                log.warning("  %s", exc)
                if ckpt:
                    ckpt.append({"url": ref.url, "error": str(exc)})
                continue
            except FetchError as exc:
                if exc.status == 404 and pattern_mode and summary.pages > 1:
                    log.info("  Halaman tidak ada (404): dianggap halaman terakhir sudah lewat.")
                    summary.pages -= 1
                    continue
                summary.errors.append({"url": ref.url, "pesan": str(exc)})
                log.warning("  %s", exc)
                if ckpt:
                    ckpt.append({"url": ref.url, "error": str(exc)})
                continue

            result = extract_items(page.html, page.url, cfg)
            summary.dropped += result.dropped
            new_items: list[dict] = []
            for item in result.items:
                if cap_reached() or (
                    item_cap is not None and len(items) + len(new_items) >= item_cap
                ):
                    break
                if dedupe_before_detail:
                    k = item_key(item, key_fields)
                    if k in seen:
                        summary.duplicates += 1
                        continue
                if cfg.detail:
                    _follow_detail(cfg, fetcher, item, summary)
                k = item_key(item, key_fields)
                if k in seen:
                    summary.duplicates += 1
                    continue
                seen.add(k)
                new_items.append(item)
            items.extend(new_items)
            log.info(
                "  %d item ditemukan, %d baru (total %d)", result.found, len(new_items), len(items)
            )
            if result.found == 0:
                log.warning(
                    "  Tidak ada item yang cocok di halaman ini. Cek selector 'items' "
                    "dengan perintah: python -m scraper uji <config> %s",
                    ref.url,
                )

            nxt = next_pages(cfg, result.soup, ref, page_base(result.soup, page.url), result.found)
            queue.extend(n for n in nxt if n.url not in visited)
            if ckpt:
                ckpt.append(
                    {"url": ref.url, "items": new_items, "next": [[n.url, n.page_no] for n in nxt]}
                )
        if cap_reached() and summary.stopped_reason == "selesai":
            summary.stopped_reason = f"batas max_items ({item_cap}) tercapai"
    except KeyboardInterrupt:
        summary.stopped_reason = "dihentikan pengguna (Ctrl+C) - jalankan lagi dengan --lanjut"
        log.warning(
            "Dihentikan. Progres tersimpan di checkpoint; pakai --lanjut untuk melanjutkan."
        )
    finally:
        if own_fetcher:
            fetcher.close()

    if item_cap is not None:
        items = items[:item_cap]
    summary.items = len(items)
    summary.finished_at = datetime.now().isoformat(timespec="seconds")
    summary.duration_s = round(time.monotonic() - t0, 2)
    return items, summary


def _follow_detail(
    cfg: ScraperConfig, fetcher: Fetcher, item: dict[str, Any], summary: RunSummary
) -> None:
    detail = cfg.detail
    assert detail is not None
    for spec in detail.fields:
        item.setdefault(spec.name, None)
    url = item.get(detail.link)
    if not url:
        return
    try:
        page = fetcher.get(str(url))
    except RobotsBlocked as exc:
        summary.robots_blocked += 1
        summary.errors.append({"url": str(url), "pesan": str(exc)})
        return
    except FetchError as exc:
        summary.errors.append({"url": str(url), "pesan": str(exc)})
        log.warning("  detail gagal: %s", exc)
        return
    summary.detail_pages += 1
    values, _ = extract_detail(
        page.html, page.url, detail.fields, detail.json_ld, detail.json_ld_type
    )
    for name, value in values.items():
        if value not in (None, "") or item.get(name) in (None, ""):
            item[name] = value
