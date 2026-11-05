from datetime import date
from pathlib import Path

import pytest

from scraper.config import parse_config
from scraper.extract import (
    dotted_get,
    extract_detail,
    extract_items,
    json_ld_objects,
    load_json_text,
    make_soup,
    to_date,
    to_float,
    to_int,
    to_price_idr,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Rp1.250.000", 1250000),
        ("Rp 89.900", 89900),
        ("Rp. 459.000", 459000),
        ("Rp1.750.000,00", 1750000),
        ("IDR 1,250,000", 1250000),
        ("Rp1,2 jt", 1200000),
        ("Rp 15rb", 15000),
        ("Rp 2,5 juta", 2500000),
        ("Rp100.000 - Rp200.000", 100000),
        ("Harga: Rp 500", 500),
        (1250000, 1250000),
        ("1250000.0", 1250000),
        ("Gaji dirahasiakan", None),
        (None, None),
    ],
)
def test_price_idr(text, expected):
    assert to_price_idr(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("1,2rb terjual", 1200),
        ("5rb+ terjual", 5000),
        ("870 terjual", 870),
        ("Stok: 25", 25),
        ("In stock (22 available)", 22),
        ("12pcs", 12),
        ("10.000", 10000),
        ("Stok habis", None),
    ],
)
def test_int(text, expected):
    assert to_int(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("4,8", 4.8),
        ("★ 4,6", 4.6),
        ("£51.77", 51.77),
        ("1.234,56", 1234.56),
        ("1,234.56", 1234.56),
        ("0.125", 0.125),
    ],
)
def test_float(text, expected):
    assert to_float(text) == pytest.approx(expected)


def test_float_forced_decimal():
    assert to_float("1.234", decimal=".") == pytest.approx(1.234)
    assert to_float("1.234", decimal=",") == 1234


@pytest.mark.parametrize(
    "text, expected",
    [
        ("3 November 2026", "2026-11-03"),
        ("Diperbarui 1 Nov 2026", "2026-11-01"),
        ("02/11/2026", "2026-11-02"),
        ("2026-11-03T10:00:00+07:00", "2026-11-03"),
        ("Selasa, 5 Agustus 2025", "2025-08-05"),
        ("Nov 5, 2026", "2026-11-05"),
        ("2 hari yang lalu", "2026-11-03"),
        ("kemarin", "2026-11-04"),
        ("1 minggu yang lalu", "2026-10-29"),
        ("3 days ago", "2026-11-02"),
        ("31/02/2026", None),
        ("bukan tanggal", None),
    ],
)
def test_date(text, expected):
    assert to_date(text, today=date(2026, 11, 5)) == expected


def test_date_explicit_format():
    assert to_date("11-05-2026", fmt="%m-%d-%Y") == "2026-11-05"


def test_dotted_get():
    data = {"offers": [{"price": 10}, {"price": 20}], "a": {"b": [{"c": 1}, {"c": 2}]}}
    assert dotted_get(data, "offers.price") == 10  # list -> first element
    assert dotted_get(data, "offers.1.price") == 20
    assert dotted_get(data, "offers[1].price") == 20
    assert dotted_get(data, "a.b.*.c") == [1, 2]
    assert dotted_get(data, "a.x.y") is None
    assert dotted_get(data, "offers.9.price") is None


def test_load_json_text_with_js_wrapper():
    assert load_json_text('window.__STATE__ = {"a": [1, 2]};') == {"a": [1, 2]}
    assert load_json_text("bukan json") is None


def _cfg(extra: str = "", **kw):
    import yaml

    base = {
        "start_urls": ["http://x/"],
        "items": "div.produk-card",
        "fields": {"nama": "h3.produk-nama"},
    }
    base.update(kw)
    if extra:
        base.update(yaml.safe_load(extra))
    return parse_config(base)


def test_extract_html_listing_with_required_and_types():
    html = (FIXTURES / "toko" / "halaman-1.html").read_text(encoding="utf-8")
    cfg = _cfg(
        fields={
            "nama": "h3.produk-nama",
            "harga": {"selector": "span.harga-jual", "type": "price_idr", "required": True},
            "harga_coret": {"selector": "span.harga-coret", "type": "price_idr"},
            "rating": {"selector": "div.rating", "type": "float"},
            "sku": {"attr": "data-sku"},
            "link": {"selector": "a.produk-link", "attr": "href", "type": "url"},
        }
    )
    result = extract_items(html, "http://toko.test/katalog/halaman-1.html", cfg)
    assert result.found == 5 and result.dropped == 1  # the ad card has no price
    first = result.items[0]
    assert first == {
        "nama": "Sepatu Lari Ringan",
        "harga": 1250000,
        "harga_coret": 1500000,
        "rating": 4.8,
        "sku": "SKU-001",
        "link": "http://toko.test/katalog/produk/sepatu-lari-ringan.html",
    }
    assert result.items[1]["harga_coret"] is None
    assert result.raw[0]["harga"] == "Rp1.250.000"


def test_extract_regex_map_multiple_default():
    html = (FIXTURES / "lain" / "buku.html").read_text(encoding="utf-8")
    cfg = _cfg(
        items="article.product_pod",
        fields={
            "rating": {
                "selector": "p.star-rating",
                "attr": "class",
                "regex": r"star-rating (\w+)",
                "map": {"One": 1, "Three": 3, "Four": 4},
                "type": "int",
            },
            "kelas": {"selector": "p", "attr": "class", "multiple": True, "join": "|"},
            "tidak_ada": {"selector": ".nope", "default": "-"},
        },
    )
    items = extract_items(html, "https://books.toscrape.com/", cfg).items
    assert [i["rating"] for i in items] == [3, 1, 1, 4]
    assert items[0]["kelas"] == "star-rating Three|price_color|instock availability"
    assert items[0]["tidak_ada"] == "-"


def test_extract_multiple_tags_job_listing():
    html = (FIXTURES / "lain" / "lowongan.html").read_text(encoding="utf-8")
    cfg = _cfg(
        items="article.lowongan",
        fields={
            "tag": {"selector": "ul.tag li", "multiple": True},
            "tanggal": {"selector": "time", "attr": "datetime", "type": "date"},
            "gaji_maks": {"selector": "div.gaji", "regex": r"-\s*(.+)", "type": "price_idr"},
        },
    )
    items = extract_items(html, "https://loker.test/cari", cfg).items
    assert items[0] == {"tag": "Full time, SMA/SMK", "tanggal": "2026-11-03", "gaji_maks": 5500000}
    assert items[2]["gaji_maks"] is None


def test_json_ld_listing_finds_products_in_graph():
    html = (FIXTURES / "toko" / "jsonld.html").read_text(encoding="utf-8")
    soup = make_soup(html)
    assert len(json_ld_objects(soup, "Product")) == 5
    assert {o["@type"] for o in json_ld_objects(soup, None)} == {"BreadcrumbList", "ItemList"}
    cfg = parse_config(
        {
            "start_urls": ["http://x/"],
            "json_ld": True,
            "fields": {
                "nama": "name",
                "harga": {"path": "offers.price", "type": "price_idr"},
                "rating": {"path": "aggregateRating.ratingValue", "type": "float"},
                "link": {"path": "url", "type": "url"},
            },
        }
    )
    items = extract_items(soup, "http://toko.test/jsonld.html", cfg).items
    assert len(items) == 5
    assert items[0] == {
        "nama": "Sepatu Lari Ringan",
        "harga": 1250000,
        "rating": 4.8,
        "link": "http://toko.test/produk/sepatu-lari-ringan.html",
    }


def test_next_data_listing():
    html = (FIXTURES / "toko" / "next.html").read_text(encoding="utf-8")
    cfg = parse_config(
        {
            "start_urls": ["http://x/"],
            "json_script": "script#__NEXT_DATA__",
            "json_items_path": "props.pageProps.initialState.search.products",
            "fields": {
                "id": "id",
                "nama": "name",
                "harga": {"path": "price.value", "type": "price_idr"},
                "kota": "shop.city",
                "terjual": {"path": "stats.sold", "type": "int"},
            },
        }
    )
    items = extract_items(html, "http://toko.test/next.html", cfg).items
    assert [i["id"] for i in items] == [1005, 1006, 1007]
    assert items[0]["kota"] == "Aceh Tengah" and items[0]["terjual"] == 1100


def test_field_from_json_script_in_html_mode():
    html = (FIXTURES / "toko" / "next.html").read_text(encoding="utf-8")
    cfg = _cfg(
        items="body",
        fields={
            "build": {"selector": "script#__NEXT_DATA__", "path": "buildId"},
            "jumlah": {
                "selector": "script#__NEXT_DATA__",
                "path": "props.pageProps.initialState.search.products.*.id",
            },
        },
    )
    item = extract_items(html, "http://x/", cfg).items[0]
    assert item["build"] == "abc123"
    assert item["jumlah"] == "1005, 1006, 1007"


def test_detail_page_html_and_json_ld():
    html = (FIXTURES / "toko" / "produk" / "botol-minum-1l.html").read_text(encoding="utf-8")
    cfg = _cfg(
        detail={
            "link": "nama",
            "fields": {
                "stok": {"selector": "td.stok", "type": "int", "default": 0},
                "diperbarui": {"selector": "div.diperbarui", "type": "date"},
            },
        }
    )
    values, raws = extract_detail(html, "http://x/p.html", cfg.detail.fields)
    assert values == {"stok": 0, "diperbarui": "2026-11-02"}
    assert raws["stok"] == "Stok habis"

    cfg = _cfg(
        detail={
            "link": "nama",
            "json_ld": True,
            "fields": {
                "merek": "brand.name",
                "harga": {"path": "offers.price", "type": "price_idr"},
                "tersedia": {"path": "offers.availability", "regex": r"/(\w+)$"},
            },
        }
    )
    values, _ = extract_detail(html, "http://x/p.html", cfg.detail.fields, json_ld=True)
    assert values == {"merek": "Segar", "harga": 45500, "tersedia": "OutOfStock"}


def test_base_href_used_for_urls():
    html = (
        '<base href="https://cdn.test/a/"><div class="produk-card"><a href="x.html">'
        '<h3 class="produk-nama">A</h3></a></div>'
    )
    cfg = _cfg(fields={"link": {"selector": "a", "attr": "href", "type": "url"}})
    assert (
        extract_items(html, "https://toko.test/", cfg).items[0]["link"]
        == "https://cdn.test/a/x.html"
    )
