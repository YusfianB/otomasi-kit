from pathlib import Path

import pytest
import yaml

from scraper.config import DEFAULT_USER_AGENT, parse_config
from scraper.fetch import Fetcher, FetchError, _retry_after_seconds
from scraper.runner import scrape

FIXTURES = Path(__file__).parent / "fixtures"


def make_cfg(text: str, **changes):
    data = yaml.safe_load(text)
    data.update(changes)
    return parse_config(data)


def test_full_crawl_pagination_detail_dedupe(shop_config_text):
    items, summary = scrape(make_cfg(shop_config_text))
    assert summary.pages == 3
    assert summary.items == len(items) == 10
    assert summary.duplicates == 1  # promo product repeated on page 2
    assert summary.dropped == 1  # ad card without price
    assert summary.detail_pages == 10
    assert summary.errors == []
    by_name = {i["nama"]: i for i in items}
    assert by_name["Sepatu Lari Ringan"]["harga"] == 1250000
    assert by_name["Sepatu Lari Ringan"]["stok"] == 25
    assert by_name["Sepatu Lari Ringan"]["diperbarui"] == "2026-11-03"
    assert by_name["Botol Minum 1 Liter"]["stok"] == 0  # "Stok habis" -> default
    assert by_name["Meja Lipat Kayu Jati"]["harga"] == 1750000
    assert by_name["Lampu LED 12W Hemat Energi"]["terjual"] == 10000
    assert all(i["link"].startswith("http://127.0.0.1:") for i in items)


def test_limits(shop_config_text):
    items, summary = scrape(make_cfg(shop_config_text), max_items=5)
    assert len(items) == 5 and "max_items" in summary.stopped_reason
    assert summary.detail_pages == 5
    items, summary = scrape(make_cfg(shop_config_text), max_pages=1)
    assert len(items) == 4 and summary.pages == 1 and "max_pages" in summary.stopped_reason


def test_robots_txt_respected_and_override(toko_url, shop_config_text):
    cfg = make_cfg(
        shop_config_text, start_urls=[f"{toko_url}/rahasia/index.html"], detail=None, monitor=None
    )
    items, summary = scrape(cfg)
    assert items == [] and summary.robots_blocked == 1
    assert "robots.txt" in summary.errors[0]["pesan"]

    cfg.respect_robots = False
    items, summary = scrape(cfg)
    assert [i["nama"] for i in items] == ["Tidak boleh di-scrape"]


def test_url_pattern_pagination_stops_at_404(toko_url, shop_config_text):
    cfg = make_cfg(
        shop_config_text,
        start_urls=[],
        pagination={"url_pattern": toko_url + "/halaman-{page}.html"},
    )
    cfg.detail = None
    items, summary = scrape(cfg)
    assert len(items) == 10 and summary.pages == 3 and summary.errors == []

    cfg = make_cfg(
        shop_config_text,
        start_urls=[],
        pagination={"url_pattern": toko_url + "/halaman-{page}.html", "end": 2},
    )
    cfg.detail = None
    items, summary = scrape(cfg)
    assert summary.pages == 2 and len(items) == 7


def test_retry_then_success(serve, toko_dir):
    base = serve(toko_dir)
    sleeps = []
    fetcher = Fetcher(user_agent="uji", delay=(0, 0), retries=3, backoff=0.5, sleep=sleeps.append)
    page = fetcher.get(base + "/flaky")
    assert "Rice Cooker" in page.html
    assert fetcher.request_count == 3
    assert sleeps == []  # Retry-After: 0 overrides the 0.5 s / 1 s backoff


def test_retry_gives_up_and_404_not_retried(toko_url):
    sleeps = []
    fetcher = Fetcher(user_agent="uji", delay=(0, 0), retries=2, backoff=1, sleep=sleeps.append)
    with pytest.raises(FetchError, match="3 percobaan"):
        fetcher.get(toko_url + "/rusak")
    assert sleeps == [1, 2]  # exponential backoff
    with pytest.raises(FetchError) as exc:
        fetcher.get(toko_url + "/tidak-ada.html")
    assert exc.value.status == 404
    assert fetcher.request_count == 4  # 3 for /rusak + 1 for the 404


def test_polite_delay_between_requests(toko_url):
    sleeps = []
    fetcher = Fetcher(user_agent="uji", delay=(0.4, 0.4), retries=0, sleep=sleeps.append)
    fetcher.get(toko_url + "/halaman-1.html")
    fetcher.get(toko_url + "/halaman-2.html")
    assert len(sleeps) == 1 and 0.3 < sleeps[0] <= 0.4


def test_errors_are_collected_not_fatal(toko_url, shop_config_text):
    cfg = make_cfg(
        shop_config_text, start_urls=[f"{toko_url}/hilang.html", f"{toko_url}/halaman-3.html"]
    )
    cfg.detail = None
    items, summary = scrape(cfg)
    assert len(items) == 3 and len(summary.errors) == 1
    assert "404" in summary.errors[0]["pesan"]


def test_local_files_without_server():
    cfg = parse_config(
        {
            "start_urls": [str(FIXTURES / "toko" / "halaman-1.html")],
            "items": "div.produk-card",
            "fields": {
                "nama": "h3.produk-nama",
                "harga": {"selector": "span.harga-jual", "type": "price_idr", "required": True},
                "link": {"selector": "a", "attr": "href", "type": "url"},
            },
            "pagination": {"next": "a.next"},
            "delay": 0,
        }
    )
    items, summary = scrape(cfg)
    assert len(items) == 10 and summary.pages == 3


class InterruptingFetcher(Fetcher):
    def __init__(self, stop_at: str, **kw):
        super().__init__(**kw)
        self.stop_at = stop_at
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        if url.endswith(self.stop_at):
            raise KeyboardInterrupt
        return super().get(url)


def test_resume_from_checkpoint(tmp_path, shop_config_text):
    cfg = make_cfg(shop_config_text)
    cfg.detail = None
    ckpt = tmp_path / "hasil.csv.progress.jsonl"
    f1 = InterruptingFetcher("halaman-3.html", user_agent="uji", delay=(0, 0))
    items, summary = scrape(cfg, fetcher=f1, checkpoint=ckpt)
    assert summary.stopped_reason.startswith("dihentikan")
    assert len(items) == 7 and ckpt.exists()

    f2 = InterruptingFetcher("tidak-pernah", user_agent="uji", delay=(0, 0))
    items, summary = scrape(cfg, fetcher=f2, checkpoint=ckpt, resume=True)
    assert len(items) == 10 and summary.resumed_pages == 2 and summary.pages == 3
    assert [u.rsplit("/", 1)[-1] for u in f2.urls] == ["halaman-3.html"]


def test_retry_after_parsing():
    assert _retry_after_seconds("5") == 5
    assert _retry_after_seconds(None) is None
    assert _retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT") == 0
    assert _retry_after_seconds("omong kosong") is None


def test_robots_rules_for_our_bot_name(serve, tmp_path):
    site = tmp_path / "situs"
    site.mkdir()
    (site / "robots.txt").write_text(
        "User-agent: otomasi-kit-scraper\nDisallow: /\nCrawl-delay: 7\n\nUser-agent: *\nAllow: /\n"
    )
    (site / "a.html").write_text("<p>halo</p>")
    base = serve(site)
    ours = Fetcher(user_agent=DEFAULT_USER_AGENT, delay=(0, 0))
    assert not ours.allowed(base + "/a.html")
    assert ours._crawl_delay(base + "/a.html") == 7
    other = Fetcher(user_agent="BotLain/1.0", delay=(0, 0))
    assert other.allowed(base + "/a.html")
