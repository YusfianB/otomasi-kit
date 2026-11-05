import datetime as dt

import pandas as pd
import pytest

from data_tools.formatting import (
    detect_number_style,
    format_bulan,
    format_number,
    format_rupiah,
    format_rupiah_short,
    format_tanggal,
    parse_date,
    parse_number,
    terbilang,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Rp 1.234.567,50", 1234567.5),
        ("Rp1.234.567,50", 1234567.5),
        ("1.234", 1234),
        ("12,5%", 12.5),
        ("12,5", 12.5),
        ("12.5", 12.5),
        ("0.125", 0.125),
        ("Rp 15.000,-", 15000),
        ("15rb", 15000),
        ("1,5jt", 1_500_000),
        ("(5.000)", -5000),
        ("-Rp 2.500", -2500),
        ("IDR 1,000,000", 1_000_000),
        ("1,234.56", 1234.56),
        ("  7  ", 7),
        (42, 42),
    ],
)
def test_parse_number_indonesian(text, expected):
    assert parse_number(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["abc", "1.2.3", "", "Rp", "12-11", "0812-3456"])
def test_parse_number_rejects_non_numbers(text):
    assert parse_number(text) is None


def test_number_style_detection():
    assert detect_number_style(["1.234,50", "2.000"]) == "id"
    assert detect_number_style(["1,234.50", "2,000,000"]) == "us"
    assert parse_number("1,234", style="us") == 1234
    assert parse_number("1,234", style="id") == pytest.approx(1.234)


def test_format_rupiah_and_number():
    assert format_rupiah(1250000) == "Rp 1.250.000"
    assert format_rupiah(-5000.4) == "-Rp 5.000"
    assert format_rupiah(1234.5, decimals=2) == "Rp 1.234,50"
    assert format_rupiah(None) == ""
    assert format_number(1234567.5) == "1.234.567,5"
    assert format_number(1000) == "1.000"
    assert format_number(3.14159, 2) == "3,14"
    assert format_rupiah_short(1_500_000) == "Rp 1,5 jt"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("05/11/2026", dt.datetime(2026, 11, 5)),
        ("5-11-26", dt.datetime(2026, 11, 5)),
        ("2026-11-05", dt.datetime(2026, 11, 5)),
        ("5 Nov 2026", dt.datetime(2026, 11, 5)),
        ("5 Agustus 2026", dt.datetime(2026, 8, 5)),
        ("17 Agt 2026", dt.datetime(2026, 8, 17)),
        ("Kamis, 5 November 2026", dt.datetime(2026, 11, 5)),
        ("05-Nov-2026 14:30", dt.datetime(2026, 11, 5, 14, 30)),
        ("1 Mei 2026", dt.datetime(2026, 5, 1)),
        ("Nov 5, 2026", dt.datetime(2026, 11, 5)),
        ("25/12/2026", dt.datetime(2026, 12, 25)),
    ],
)
def test_parse_date_formats(text, expected):
    assert parse_date(text) == pd.Timestamp(expected)


@pytest.mark.parametrize("text", ["1.234", "31/02/2026", "November 2026", "abc", "0812345"])
def test_parse_date_rejects(text):
    assert parse_date(text) is None


def test_format_tanggal():
    assert format_tanggal(dt.date(2026, 11, 5)) == "5 November 2026"
    assert format_tanggal("2026-11-05", with_day=True) == "Kamis, 5 November 2026"
    assert format_tanggal(pd.Timestamp("2026-08-17"), short=True) == "17 Agu 2026"
    assert format_bulan(pd.Period("2026-11", "M")) == "November 2026"


@pytest.mark.parametrize(
    ("number", "words"),
    [
        (0, "nol"),
        (11, "sebelas"),
        (115, "seratus lima belas"),
        (1001, "seribu satu"),
        (1250000, "satu juta dua ratus lima puluh ribu"),
        (21_000_000, "dua puluh satu juta"),
        (2_000_000_000, "dua miliar"),
    ],
)
def test_terbilang(number, words):
    assert terbilang(number) == words
