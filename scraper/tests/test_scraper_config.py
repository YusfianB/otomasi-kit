from pathlib import Path

import pytest

from scraper.config import ConfigError, load_config, parse_config

CONTOH = Path(__file__).resolve().parents[1] / "contoh"


def base(**kw):
    data = {"start_urls": ["https://x.test/"], "items": ".item", "fields": {"nama": "h3"}}
    data.update(kw)
    return data


@pytest.mark.parametrize("path", sorted(CONTOH.glob("*.yaml")), ids=lambda p: p.name)
def test_example_configs_are_valid(path):
    cfg = load_config(path)
    assert cfg.fields and cfg.name


def test_example_configs_exist():
    names = {p.stem for p in CONTOH.glob("*.yaml")}
    assert {"produk_toko", "lowongan_kerja", "buku_toscrape", "produk_json_ld"} <= names


def test_defaults_and_shorthand():
    cfg = parse_config(base(delay=2))
    assert cfg.name == "scraper"
    assert cfg.delay == (2.0, 2.0)
    assert cfg.fields[0].selector == "h3" and cfg.fields[0].type == "text"
    assert cfg.respect_robots is True and cfg.retries == 3


def test_unknown_key_suggests_correction():
    with pytest.raises(ConfigError, match="Mungkin maksud Anda 'start_urls'"):
        parse_config({"start_url": ["x"], "items": ".a", "fields": {"a": "b"}})
    with pytest.raises(ConfigError, match="selector"):
        parse_config(base(fields={"harga": {"selektor": ".p"}}))


@pytest.mark.parametrize(
    "data, message",
    [
        (base(fields={"h": {"selector": ".p", "type": "uang"}}), "Tipe 'uang'"),
        (base(fields={"h": {"selector": ".p", "regex": "("}}), "Regex"),
        (base(start_urls=[]), "start_urls"),
        ({"start_urls": ["x"], "fields": {"a": "b"}}, "cara menemukan item"),
        (base(fields={}), "minimal satu field"),
        (base(pagination={"url_pattern": "https://x/?p=1"}), "{page}"),
        (base(pagination={"next": "a", "url_pattern": "x{page}"}), "Pilih salah satu"),
        (base(detail={"link": "url", "fields": {"a": "b"}}), "bukan nama field"),
        (base(delay=[3, 1]), "delay"),
        (base(dedupe_by=["harga"]), "dedupe_by"),
        (base(monitor={"price": "harga"}), "monitor.price"),
        (base(render="chrome"), "render"),
        ([1, 2], "mapping"),
    ],
)
def test_invalid_configs(data, message):
    with pytest.raises(ConfigError, match=message.replace("(", r"\(").replace("{", r"\{")):
        parse_config(data)


def test_pattern_pagination_without_start_urls():
    cfg = parse_config(
        {
            "items": ".a",
            "fields": {"a": "b"},
            "pagination": {"url_pattern": "https://x/?page={page}", "end": 3},
        }
    )
    assert cfg.start_urls == [] and cfg.pagination.end == 3


def test_missing_file_and_bad_yaml(tmp_path):
    with pytest.raises(ConfigError, match="tidak ditemukan"):
        load_config(tmp_path / "tidak_ada.yaml")
    bad = tmp_path / "rusak.yaml"
    bad.write_text("fields: [a\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="YAML"):
        load_config(bad)


def test_name_from_file_stem(tmp_path):
    path = tmp_path / "toko_saya.yaml"
    path.write_text("start_urls: [https://x/]\nitems: .a\nfields: {a: b}\n", encoding="utf-8")
    assert load_config(path).name == "toko_saya"
