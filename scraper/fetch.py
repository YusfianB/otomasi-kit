"""HTTP fetching: polite delays, retries with backoff, robots.txt, local files, JS render."""

from __future__ import annotations

import logging
import random
import time
import urllib.robotparser
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import unquote, urlparse, urlunparse

import requests
from bs4 import UnicodeDammit

log = logging.getLogger("scraper")

RETRY_STATUS = {429, 500, 502, 503, 504}


class FetchError(RuntimeError):
    """A page could not be fetched (message is user-facing)."""


class RobotsBlocked(FetchError):
    """robots.txt disallows this URL."""


@dataclass
class Page:
    url: str  # final URL (after redirects), used as base for relative links
    html: str
    status: int = 200


def is_local(url: str) -> bool:
    if url.startswith("file://"):
        return True
    parsed = urlparse(url)
    return parsed.scheme == "" or (len(parsed.scheme) == 1 and Path(url).exists())


def to_file_url(path: str) -> str:
    return Path(path).resolve().as_uri()


def _file_path(url: str) -> Path:
    if url.startswith("file://"):
        parsed = urlparse(url)
        return Path(unquote(parsed.path))
    return Path(url)


def _decode(content: bytes, declared: str | None) -> str:
    if declared:
        try:
            return content.decode(declared, errors="replace")
        except LookupError:
            pass
    dammit = UnicodeDammit(content, is_html=True)
    return dammit.unicode_markup or content.decode("utf-8", errors="replace")


class Fetcher:
    """Fetches pages politely. One instance per scrape run."""

    def __init__(
        self,
        *,
        user_agent: str,
        headers: dict[str, str] | None = None,
        delay: tuple[float, float] = (1.0, 3.0),
        timeout: float = 20.0,
        retries: int = 3,
        backoff: float = 1.0,
        respect_robots: bool = True,
        render: str = "html",
        render_wait: str | None = None,
        session: requests.Session | None = None,
        sleep=time.sleep,
    ) -> None:
        self.user_agent = user_agent
        self.delay = delay
        self.timeout = timeout
        self.retries = max(0, retries)
        self.backoff = backoff
        self.respect_robots = respect_robots
        self.render = render
        self.render_wait = render_wait
        self.sleep = sleep
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
        })
        self.session.headers.update(headers or {})
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last_request: dict[str, float] = {}
        self._renderer = None
        self.request_count = 0

    @classmethod
    def from_config(cls, cfg, **overrides) -> Fetcher:
        kwargs = dict(
            user_agent=cfg.user_agent,
            headers=cfg.headers,
            delay=cfg.delay,
            timeout=cfg.timeout,
            retries=cfg.retries,
            backoff=cfg.backoff,
            respect_robots=cfg.respect_robots,
            render=cfg.render,
            render_wait=cfg.render_wait,
        )
        kwargs.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**kwargs)

    # ------------------------------------------------------------------ robots.txt
    def _robots_for(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in self._robots:
            return self._robots[origin]
        robots_url = urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))
        parser = urllib.robotparser.RobotFileParser(robots_url)
        try:
            resp = self.session.get(robots_url, timeout=self.timeout)
            if resp.status_code in (401, 403):
                parser.disallow_all = True
            elif resp.status_code >= 400:
                parser.allow_all = True
            else:
                parser.parse(resp.text.splitlines())
        except requests.RequestException as exc:
            log.debug("robots.txt %s tidak bisa diambil (%s); dianggap boleh.", robots_url, exc)
            parser.allow_all = True
        self._robots[origin] = parser
        return parser

    def allowed(self, url: str) -> bool:
        if not self.respect_robots or is_local(url):
            return True
        parser = self._robots_for(url)
        return parser is None or parser.can_fetch(self.user_agent, url)

    def _crawl_delay(self, url: str) -> float:
        if not self.respect_robots or is_local(url):
            return 0.0
        parser = self._robots_for(url)
        if parser is None:
            return 0.0
        try:
            value = parser.crawl_delay(self.user_agent)
        except AttributeError:
            value = None
        return float(value or 0)

    # ------------------------------------------------------------------ politeness
    def _wait_turn(self, url: str) -> None:
        host = urlparse(url).netloc
        lo, hi = self.delay
        wanted = max(random.uniform(lo, hi), self._crawl_delay(url))
        last = self._last_request.get(host)
        if last is not None and wanted > 0:
            remaining = wanted - (time.monotonic() - last)
            if remaining > 0:
                self.sleep(remaining)
        self._last_request[host] = time.monotonic()

    # ------------------------------------------------------------------ fetching
    def get(self, url: str) -> Page:
        if is_local(url):
            path = _file_path(url)
            if not path.exists():
                raise FetchError(f"File tidak ditemukan: {path}")
            return Page(url=path.resolve().as_uri(), html=_decode(path.read_bytes(), None))

        if not self.allowed(url):
            raise RobotsBlocked(f"Diblokir robots.txt: {url}")

        if self.render == "js":
            self._wait_turn(url)
            self.request_count += 1
            return self._get_renderer().get(url, self.timeout, self.render_wait)

        last_error = "tidak diketahui"
        for attempt in range(self.retries + 1):
            self._wait_turn(url)
            self.request_count += 1
            try:
                resp = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                retry_after = None
            else:
                if resp.status_code < 400:
                    charset = None
                    ctype = resp.headers.get("Content-Type", "")
                    if "charset=" in ctype.lower():
                        charset = resp.encoding
                    return Page(url=resp.url, html=_decode(resp.content, charset),
                                status=resp.status_code)
                last_error = f"HTTP {resp.status_code}"
                if resp.status_code not in RETRY_STATUS:
                    raise FetchError(f"{last_error} saat mengambil {url}")
                retry_after = _retry_after_seconds(resp.headers.get("Retry-After"))
            if attempt < self.retries:
                wait = retry_after if retry_after is not None else self.backoff * (2 ** attempt)
                wait = min(wait, 120.0)
                log.warning("  %s gagal (%s), coba lagi dalam %.1f detik (%d/%d)",
                            url, last_error, wait, attempt + 1, self.retries)
                if wait > 0:
                    self.sleep(wait)
        raise FetchError(f"Gagal mengambil {url} setelah {self.retries + 1} percobaan: "
                         f"{last_error}")

    def _get_renderer(self):
        if self._renderer is None:
            self._renderer = JsRenderer(self.user_agent, dict(self.session.headers))
        return self._renderer

    def close(self) -> None:
        if self._renderer is not None:
            self._renderer.close()
            self._renderer = None
        self.session.close()


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, dt.timestamp() - time.time())


class JsRenderer:
    """Optional Playwright (Chromium) renderer for JavaScript-heavy sites."""

    def __init__(self, user_agent: str, headers: dict[str, str]) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise FetchError(
                "Mode --render js butuh Playwright. Pasang dulu:\n"
                "  pip install playwright\n  playwright install chromium"
            ) from None
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        extra = {k: v for k, v in headers.items() if k.lower() not in ("user-agent",)}
        self._context = self._browser.new_context(user_agent=user_agent,
                                                  extra_http_headers=extra)

    def get(self, url: str, timeout: float, wait_for: str | None) -> Page:
        page = self._context.new_page()
        try:
            resp = page.goto(url, timeout=timeout * 1000, wait_until="networkidle")
            if wait_for:
                page.wait_for_selector(wait_for, timeout=timeout * 1000)
            status = resp.status if resp else 200
            if status >= 400:
                raise FetchError(f"HTTP {status} saat mengambil {url}")
            return Page(url=page.url, html=page.content(), status=status)
        except FetchError:
            raise
        except Exception as exc:  # playwright raises its own error types
            raise FetchError(f"Gagal merender {url}: {exc}") from None
        finally:
            page.close()

    def close(self) -> None:
        self._browser.close()
        self._pw.stop()
