"""Shared fixtures: a local http.server serving the fake shop in tests/fixtures/toko."""

from __future__ import annotations

import functools
import http.server
import shutil
import threading
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


class _Handler(http.server.SimpleHTTPRequestHandler):
    """Static files plus a few special paths for error handling tests."""

    def log_message(self, *args) -> None:  # keep test output clean
        pass

    def do_GET(self) -> None:  # http.server API name
        self.server.hits.append(self.path)
        if self.path.startswith("/flaky"):
            # first two requests fail with 503, then succeed
            count = sum(1 for p in self.server.hits if p.startswith("/flaky"))
            if count <= 2:
                self.send_response(503)
                self.send_header("Retry-After", "0")
                self.end_headers()
                return
            self.path = "/halaman-3.html"
        if self.path.startswith("/rusak"):
            self.send_response(500)
            self.end_headers()
            return
        super().do_GET()


@pytest.fixture
def serve():
    """serve(directory) -> base URL like http://127.0.0.1:PORT"""
    servers = []

    def _serve(directory: Path) -> str:
        handler = functools.partial(_Handler, directory=str(directory))
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server.hits = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_port}"

    yield _serve
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.fixture
def toko_dir(tmp_path: Path) -> Path:
    """A private copy of the fake shop, so tests may edit prices safely."""
    dest = tmp_path / "toko"
    shutil.copytree(FIXTURES / "toko", dest)
    return dest


@pytest.fixture
def toko_url(serve, toko_dir) -> str:
    return serve(toko_dir)


SHOP_CONFIG = """
name: toko_uji
start_urls: ["{base}/halaman-1.html"]
items: div.produk-card
fields:
  nama: h3.produk-nama
  harga: {{selector: span.harga-jual, type: price_idr, required: true}}
  harga_coret: {{selector: span.harga-coret, type: price_idr}}
  rating: {{selector: div.rating, type: float}}
  terjual: {{selector: div.terjual, type: int}}
  sku: {{attr: data-sku}}
  link: {{selector: a.produk-link, attr: href, type: url}}
pagination: {{next: nav.paginasi a.next}}
detail:
  link: link
  fields:
    stok: {{selector: td.stok, type: int, default: 0}}
    merek: td.merek
    diperbarui: {{selector: div.diperbarui, type: date}}
monitor: {{key: link, price: harga, label: nama, track: [stok]}}
delay: 0
retries: 1
backoff: 0
"""


@pytest.fixture
def shop_config_text(toko_url) -> str:
    return SHOP_CONFIG.format(base=toko_url)


@pytest.fixture
def shop_config_file(tmp_path, shop_config_text) -> Path:
    path = tmp_path / "toko.yaml"
    path.write_text(shop_config_text, encoding="utf-8")
    return path
