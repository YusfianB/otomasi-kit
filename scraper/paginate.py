"""Pagination: follow a "next" link or generate URLs from a pattern with {page}."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .config import ScraperConfig


@dataclass(frozen=True)
class PageRef:
    url: str
    page_no: int  # 1-based position in its chain (pattern mode: the {page} value)


def initial_pages(cfg: ScraperConfig) -> list[PageRef]:
    p = cfg.pagination
    if p and p.url_pattern:
        if cfg.start_urls:  # an explicit first page (e.g. without ?page=1)
            return [PageRef(cfg.start_urls[0], p.start)]
        return [PageRef(p.url_pattern.format(page=p.start), p.start)]
    return [PageRef(url, 1) for url in cfg.start_urls]


def next_pages(
    cfg: ScraperConfig,
    soup: BeautifulSoup | None,
    current: PageRef,
    base_url: str,
    found_items: int,
) -> list[PageRef]:
    """Pages to visit after `current`. `found_items` is how many items the page had."""
    p = cfg.pagination
    if p is None:
        return []
    if p.url_pattern:
        if p.stop_when_empty and found_items == 0:
            return []
        nxt = current.page_no + 1
        if p.end is not None and nxt > p.end:
            return []
        return [PageRef(p.url_pattern.format(page=nxt), nxt)]
    if p.next and soup is not None:
        link = soup.select_one(p.next)
        if link is None:
            return []
        href = link.get("href") if link.name == "a" or link.has_attr("href") else None
        if not href:
            inner = link.find("a", href=True)
            href = inner.get("href") if inner else None
        if not href or href.startswith(("javascript:", "#")):
            return []
        return [PageRef(urljoin(base_url, href), current.page_no + 1)]
    return []


def page_limit(cfg: ScraperConfig, override: int | None = None) -> int | None:
    """Effective maximum number of listing pages (None = unlimited). CLI override wins."""
    if override:
        return override
    limits = [
        x
        for x in (
            cfg.max_pages,
            cfg.pagination.max_pages if cfg.pagination else None,
        )
        if x
    ]
    return min(limits) if limits else None
