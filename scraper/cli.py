"""Command line interface: python -m scraper <jalankan|monitor|uji> ..."""

from __future__ import annotations

import argparse
import importlib.util
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import ClassVar

from .config import ConfigError, ScraperConfig, load_config
from .extract import extract_detail, extract_items, json_ld_objects, page_base
from .fetch import Fetcher, FetchError, is_local
from .monitor import History, compare, resolve_fields, write_report
from .output import OutputError, check_output_path, write_items, write_summary
from .paginate import PageRef, next_pages
from .runner import scrape

log = logging.getLogger("scraper")

ROBOTS_WARNING = (
    "robots.txt DIABAIKAN (--abaikan-robots). Pastikan Anda punya izin dari pemilik situs "
    "dan tetap mematuhi Syarat & Ketentuan situs serta hukum yang berlaku."
)


class _Formatter(logging.Formatter):
    PREFIX: ClassVar[dict[int, str]] = {logging.WARNING: "PERINGATAN: ", logging.ERROR: "ERROR: "}

    def format(self, record: logging.LogRecord) -> str:
        return self.PREFIX.get(record.levelno, "") + record.getMessage()


class _StderrHandler(logging.StreamHandler):
    """Always writes to the *current* sys.stderr (safe when stderr is swapped, e.g. tests)."""

    @property
    def stream(self):
        return sys.stderr

    @stream.setter
    def stream(self, _value) -> None:
        pass


def _setup_logging(verbose: bool, quiet: bool) -> None:
    handler = _StderrHandler()
    handler.setFormatter(_Formatter())
    log.handlers[:] = [handler]
    log.propagate = False
    log.setLevel(logging.DEBUG if verbose else logging.WARNING if quiet else logging.INFO)


def _slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", text).strip("_") or "scraper"


# --------------------------------------------------------------------------- argparse


def _add_fetch_options(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--abaikan-robots",
        action="store_true",
        help="abaikan robots.txt (hanya jika Anda punya izin pemilik situs)",
    )
    p.add_argument(
        "--render",
        choices=["html", "js"],
        default=None,
        help="'js' = render JavaScript dengan Playwright (opsional, harus dipasang)",
    )
    p.add_argument(
        "--jeda",
        type=float,
        default=None,
        metavar="DETIK",
        help="jeda antar-request (menimpa 'delay' di config)",
    )


def _add_limit_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--max-items", type=int, default=None, help="batas jumlah item")
    p.add_argument("--max-halaman", type=int, default=None, help="batas jumlah halaman daftar")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scraper",
        description="Scraper web berbasis config YAML. Tulis config sekali, jalankan kapan saja.",
        epilog="Contoh:\n"
        "  python -m scraper uji scraper/contoh/buku_toscrape.yaml\n"
        "  python -m scraper jalankan scraper/contoh/buku_toscrape.yaml -o buku.xlsx\n"
        "  python -m scraper monitor scraper/contoh/produk_toko.yaml "
        "--laporan perubahan.md",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="tampilkan log detail")
    parser.add_argument("-q", "--quiet", action="store_true", help="hanya tampilkan peringatan")
    sub = parser.add_subparsers(dest="perintah", metavar="PERINTAH")

    run = sub.add_parser(
        "jalankan",
        aliases=["scrape"],
        help="jalankan scraping dan simpan hasil (CSV/XLSX/JSON)",
        description="Jalankan scraping sesuai config dan simpan hasilnya.",
    )
    run.add_argument("config", help="file config YAML")
    run.add_argument(
        "-o",
        "--output",
        default=None,
        help="file hasil: .csv, .xlsx, .json, atau .jsonl (default: output/<nama>.csv)",
    )
    run.add_argument(
        "--ringkasan",
        default=None,
        metavar="FILE.json",
        help="simpan ringkasan run (jumlah item, halaman, error) ke file JSON",
    )
    run.add_argument(
        "--lanjut", action="store_true", help="lanjutkan run yang terputus dari checkpoint"
    )
    run.add_argument(
        "--checkpoint",
        default=None,
        help="lokasi file checkpoint (default: <output>.progress.jsonl)",
    )
    _add_limit_options(run)
    _add_fetch_options(run)

    mon = sub.add_parser(
        "monitor",
        help="scrape lalu bandingkan dengan run sebelumnya (item baru/hilang, perubahan harga)",
        description="Jalankan config, bandingkan dengan run sebelumnya yang "
        "tersimpan di riwayat SQLite, dan tulis laporan perubahan.",
    )
    mon.add_argument("config", help="file config YAML")
    mon.add_argument(
        "--riwayat",
        default="output/riwayat_scraper.sqlite",
        help="file SQLite riwayat (default: output/riwayat_scraper.sqlite)",
    )
    mon.add_argument(
        "--laporan",
        default=None,
        help="file laporan perubahan: .csv, .xlsx, .json, atau .md "
        "(default: output/laporan/perubahan_<nama>_<waktu>.csv)",
    )
    mon.add_argument(
        "-o", "--output", default=None, help="(opsional) simpan juga hasil scraping run ini"
    )
    mon.add_argument(
        "--min-persen",
        type=float,
        default=0.0,
        help="abaikan perubahan harga di bawah persentase ini (default 0)",
    )
    mon.add_argument(
        "--simpan-run",
        type=int,
        default=0,
        metavar="N",
        help="simpan hanya N run terakhir di riwayat (default: semua)",
    )
    mon.add_argument(
        "--gagal-jika-berubah",
        action="store_true",
        help="keluar dengan kode 3 jika ada perubahan (berguna untuk notifikasi CI)",
    )
    _add_limit_options(mon)
    _add_fetch_options(mon)

    uji = sub.add_parser(
        "uji",
        aliases=["test"],
        help="uji selector config pada satu URL / file HTML",
        description="Tampilkan apa yang diambil tiap selector dari beberapa "
        "item pertama. Cocok untuk memperbaiki config.",
    )
    uji.add_argument("config", help="file config YAML")
    uji.add_argument(
        "sumber",
        nargs="?",
        default=None,
        help="URL atau file HTML lokal (default: start_urls pertama di config)",
    )
    uji.add_argument("-n", "--jumlah", type=int, default=3, help="jumlah item ditampilkan")
    uji.add_argument(
        "--detail",
        action="store_true",
        help="ikuti juga link detail item pertama dan uji field detail",
    )
    uji.add_argument(
        "--simpan-html",
        default=None,
        metavar="FILE.html",
        help="simpan HTML yang diterima (untuk dicek/dipakai offline)",
    )
    _add_fetch_options(uji)
    return parser


# --------------------------------------------------------------------------- helpers


def _prepare(args) -> ScraperConfig:
    cfg = load_config(args.config)
    if args.abaikan_robots:
        cfg.respect_robots = False
        log.warning(ROBOTS_WARNING)
    if args.render:
        cfg.render = args.render
    if cfg.render == "js" and importlib.util.find_spec("playwright") is None:
        raise ConfigError(
            "Mode render 'js' butuh Playwright, yang belum terpasang. Pasang dengan:\n"
            "  pip install playwright\n  playwright install chromium\n"
            "Atau hapus 'render: js' / --render js untuk memakai mode HTML biasa."
        )
    if args.jeda is not None:
        if args.jeda < 0:
            raise ConfigError("--jeda tidak boleh negatif.")
        cfg.delay = (args.jeda, args.jeda)
    return cfg


def _print(text: str = "") -> None:
    print(text, flush=True)


# --------------------------------------------------------------------------- commands


def cmd_jalankan(args) -> int:
    cfg = _prepare(args)
    output = check_output_path(args.output or f"output/{_slug(cfg.name)}.csv")
    checkpoint = (
        Path(args.checkpoint)
        if args.checkpoint
        else output.with_name(output.name + ".progress.jsonl")
    )
    if checkpoint.exists() and not args.lanjut:
        log.info(
            "Checkpoint lama %s ditemukan; mulai dari awal (pakai --lanjut untuk melanjutkan).",
            checkpoint,
        )
    items, summary = scrape(
        cfg,
        max_items=args.max_items,
        max_pages=args.max_halaman,
        checkpoint=checkpoint,
        resume=args.lanjut,
    )
    write_items(items, output, columns=cfg.field_names)
    interrupted = summary.stopped_reason.startswith("dihentikan")
    if not interrupted:
        checkpoint.unlink(missing_ok=True)
    _print(summary.format_text())
    _print(f"Hasil disimpan ke: {output}")
    if args.ringkasan:
        write_summary(summary.to_dict(), args.ringkasan)
        _print(f"Ringkasan disimpan ke: {args.ringkasan}")
    if interrupted:
        return 130
    return 0 if items or not summary.errors else 1


def cmd_monitor(args) -> int:
    cfg = _prepare(args)
    if args.output:
        check_output_path(args.output)
    stamp = datetime.now()
    report_path = Path(
        args.laporan or f"output/laporan/perubahan_{_slug(cfg.name)}_{stamp:%Y%m%d_%H%M%S}.csv"
    )
    if report_path.suffix.lower() not in (".csv", ".xlsx", ".json", ".md"):
        raise OutputError("Ekstensi --laporan harus .csv, .xlsx, .json, atau .md")

    items, summary = scrape(cfg, max_items=args.max_items, max_pages=args.max_halaman)
    _print(summary.format_text())
    if args.output:
        write_items(items, args.output, columns=cfg.field_names)
        _print(f"Hasil run ini disimpan ke: {args.output}")
    if not items:
        log.error(
            "Tidak ada item yang didapat; riwayat TIDAK diperbarui agar run berikutnya "
            "tidak menganggap semua item hilang."
        )
        return 1

    fields = resolve_fields(cfg)
    if not fields.price:
        log.warning(
            "Tidak ada field harga (type: price_idr atau monitor.price); hanya item "
            "baru/hilang yang dilaporkan."
        )
    with History(args.riwayat) as history:
        prev = history.latest_run(cfg.name)
        previous_items = history.load_items(prev[0]) if prev else {}
        run_at = stamp.isoformat(timespec="seconds")
        report = compare(
            previous_items,
            items,
            fields,
            config=cfg.name,
            previous_run_at=prev[1] if prev else None,
            current_run_at=run_at,
            min_change_pct=args.min_persen,
        )
        if summary.errors:
            report.warnings.append(
                f"Run ini punya {len(summary.errors)} error; item 'hilang' bisa jadi karena "
                "halaman gagal diambil, bukan benar-benar hilang."
            )
        if summary.stopped_reason.startswith("batas"):
            report.warnings.append(
                f"Run dibatasi ({summary.stopped_reason}); item di luar batas akan terlihat "
                "'hilang'."
            )
        history.save_run(cfg.name, items, fields, error_count=len(summary.errors), run_at=run_at)
        if args.simpan_run:
            history.prune(cfg.name, args.simpan_run)

    write_report(report, report_path)
    _print()
    _print(report.summary_text())
    for change in report.changes[:15]:
        _print(
            f"  [{change['jenis']}"
            + (f" {change['field']}" if change["jenis"] == "berubah" else "")
            + f"] {change['nama'] or change['kunci']}: "
            f"{change['nilai_lama']} -> {change['nilai_baru']}"
            + (f" ({change['persen']:+.1f}%)" if change["persen"] is not None else "")
        )
    if len(report.changes) > 15:
        _print(f"  ... dan {len(report.changes) - 15} perubahan lain (lihat laporan)")
    _print(f"Laporan perubahan: {report_path}")
    _print(f"Riwayat: {args.riwayat}")
    if args.gagal_jika_berubah and report.has_changes:
        return 3
    return 0


def _short(value, width: int = 70) -> str:
    text = repr(value)
    return text if len(text) <= width else text[: width - 3] + "..."


def cmd_uji(args) -> int:
    cfg = _prepare(args)
    source = args.sumber or (cfg.start_urls[0] if cfg.start_urls else None)
    if source is None and cfg.pagination and cfg.pagination.url_pattern:
        source = cfg.pagination.url_pattern.format(page=cfg.pagination.start)
    if source is None:
        raise ConfigError("Berikan URL atau file HTML untuk diuji.")
    if not is_local(source) and not re.match(r"^[a-z][a-z0-9+.-]*://", source, re.IGNORECASE):
        source = "https://" + source

    fetcher = Fetcher.from_config(cfg)
    try:
        page = fetcher.get(source)
        if args.simpan_html:
            Path(args.simpan_html).write_text(page.html, encoding="utf-8")
            _print(f"HTML disimpan ke: {args.simpan_html}")
        result = extract_items(page.html, page.url, cfg)
        soup = result.soup
        mode = (
            "JSON-LD (@type " + str(cfg.json_ld_type) + ")"
            if cfg.json_ld
            else f"JSON di {cfg.json_script} -> {cfg.json_items_path}"
            if cfg.json_script
            else f"CSS '{cfg.items}'"
        )
        _print(f"Sumber   : {page.url}")
        _print(
            f"Item     : {result.found} cocok dengan {mode}"
            + (f", {result.dropped} dibuang karena field wajib kosong" if result.dropped else "")
        )
        if cfg.pagination:
            nxt = next_pages(
                cfg,
                soup,
                PageRef(page.url, cfg.pagination.start),
                page_base(soup, page.url),
                result.found,
            )
            _print(f"Halaman berikut: {nxt[0].url if nxt else '(tidak ditemukan)'}")

        names = [f.name for f in cfg.fields]
        width = max(len(n) for n in names)
        for i, (item, raw) in enumerate(
            zip(result.items[: args.jumlah], result.raw[: args.jumlah]), start=1
        ):
            _print(f"\nItem #{i}")
            for name in names:
                line = f"  {name.ljust(width)} : {_short(item.get(name), 60)}"
                if raw.get(name) != item.get(name):
                    line += f"   (mentah: {_short(raw.get(name), 45)})"
                _print(line)

        if result.items:
            empty = [n for n in names if all(it.get(n) in (None, "") for it in result.items)]
            if empty:
                _print(
                    f"\nPERINGATAN: field selalu kosong: {', '.join(empty)} "
                    "-> periksa selector/attr/regex-nya."
                )
        else:
            _hints(soup, cfg)

        if args.detail and cfg.detail and result.items:
            link = result.items[0].get(cfg.detail.link)
            _print(f"\nDetail item #1: {link}")
            if link:
                dpage = fetcher.get(str(link))
                values, raws = extract_detail(
                    dpage.html,
                    dpage.url,
                    cfg.detail.fields,
                    cfg.detail.json_ld,
                    cfg.detail.json_ld_type,
                )
                dwidth = max(len(n) for n in values)
                for name, value in values.items():
                    line = f"  {name.ljust(dwidth)} : {_short(value, 45)}"
                    if raws.get(name) != value:
                        line += f"   (mentah: {_short(raws.get(name), 45)})"
                    _print(line)
        elif cfg.detail and result.items:
            _print("\n(Tambahkan --detail untuk menguji field halaman detail.)")
    finally:
        fetcher.close()
    return 0 if result.items else 1


def _hints(soup, cfg: ScraperConfig) -> None:
    _print("\nTidak ada item. Beberapa petunjuk:")
    lds = json_ld_objects(soup, None)
    if lds:
        types = sorted({str(o.get("@type")) for o in lds})
        _print(
            f"  - Halaman punya JSON-LD dengan @type: {', '.join(types)}. "
            "Coba 'json_ld: true' (+ 'json_ld_type')."
        )
        products = json_ld_objects(soup, "Product")
        if products:
            _print(
                f"    Di dalamnya ada {len(products)} objek Product -> 'json_ld: true' "
                "kemungkinan besar langsung berhasil."
            )
    if soup.select_one("script#__NEXT_DATA__"):
        _print(
            '  - Ada <script id="__NEXT_DATA__">. Coba \'json_script: '
            "\"script#__NEXT_DATA__\"' + 'json_items_path'."
        )
    text_len = len(soup.get_text(" ", strip=True))
    scripts = len(soup.find_all("script"))
    if text_len < 500 and scripts > 3:
        _print(
            "  - Halaman hampir tanpa teks tapi banyak <script>: kemungkinan dirender "
            "JavaScript. Coba --render js (butuh Playwright)."
        )
    _print(
        "  - Buka halaman di browser, klik kanan item -> Inspect, lalu salin selector "
        "kartunya ke 'items'."
    )


# --------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.perintah:
        parser.print_help()
        return 2
    _setup_logging(args.verbose, args.quiet)
    commands = {
        "jalankan": cmd_jalankan,
        "scrape": cmd_jalankan,
        "monitor": cmd_monitor,
        "uji": cmd_uji,
        "test": cmd_uji,
    }
    try:
        return commands[args.perintah](args)
    except (ConfigError, OutputError) as exc:
        log.error("%s", exc)
        return 1
    except FetchError as exc:
        log.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        log.warning("Dibatalkan.")
        return 130
