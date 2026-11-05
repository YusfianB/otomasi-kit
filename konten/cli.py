"""Command line interface: ``python -m konten <subperintah> [opsi]``."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from konten import __version__
from konten.common import (
    ENGINES,
    FAILED_ITEMS,
    LANGS,
    FatalError,
    KontenError,
    info,
    read_text_file,
)
from konten.llm import DEFAULT_EFFORT, DEFAULT_MODEL, EFFORTS, LLMClient, build_llm, has_api_key

EPILOG = """\
Contoh:
  python -m konten sosmed --profil konten/examples/brand.yaml --jumlah 7
  python -m konten artikel --keywords konten/examples/keywords_contoh.csv
  python -m konten produk --input konten/examples/produk_contoh.csv
  python -m konten kuis --materi konten/examples/materi_contoh.txt --jumlah 10 \\
      --tipe pilihan_ganda,benar_salah,isian,esai

Mesin: --engine auto (default) memakai AI bila ANTHROPIC_API_KEY ada, selain itu template.
"""


def _common(p: argparse.ArgumentParser, default_out: str) -> None:
    g = p.add_argument_group("opsi umum")
    g.add_argument("--engine", choices=ENGINES, default="auto",
                   help="auto = AI jika ANTHROPIC_API_KEY ada, selain itu template (default: auto)")
    g.add_argument("--bahasa", choices=LANGS, default="id", help="bahasa output (default: id)")
    g.add_argument("--model", default=None,
                   help=f"model Claude untuk mesin AI (default: env OTOMASI_MODEL atau "
                        f"{DEFAULT_MODEL})")
    g.add_argument("--effort", choices=EFFORTS, default=None,
                   help=f"tingkat effort AI; lebih rendah = lebih hemat (default: env "
                        f"OTOMASI_EFFORT atau {DEFAULT_EFFORT})")
    g.add_argument("--concurrency", type=int, default=3,
                   help="jumlah permintaan AI paralel (default: 3; turunkan jika kena rate limit)")
    g.add_argument("--timpa", action="store_true",
                   help="buat ulang item yang sudah ada di folder output (default: dilewati)")
    g.add_argument("--output", "-o", default=default_out,
                   help=f"folder output (default: {default_out})")


def _localize(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
    p._optionals.title = "opsi"
    p._positionals.title = "argumen"
    p.add_argument("-h", "--help", action="help", help="tampilkan bantuan ini lalu keluar")
    return p


def build_parser() -> argparse.ArgumentParser:
    parser = _localize(argparse.ArgumentParser(
        prog="python -m konten", add_help=False,
        description="Generator konten untuk UMKM, seller online, dan kreator Indonesia.",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter))
    parser.add_argument("--version", action="version", version=f"konten {__version__}",
                        help="tampilkan versi")
    sub = parser.add_subparsers(dest="perintah", metavar="<subperintah>", title="subperintah")

    s = sub.add_parser("sosmed", add_help=False, help="kalender & caption media sosial",
                       description="Buat kalender konten + caption, hashtag, CTA, ide visual, "
                                   "dan naskah video pendek per platform.")
    s.add_argument("--profil", help="file YAML profil brand (lihat konten/examples/brand.yaml)")
    s.add_argument("--brand", help="nama brand/usaha")
    s.add_argument("--niche", help="niche/kategori, mis. 'kopi susu kekinian'")
    s.add_argument("--audiens", help="target audiens, mis. 'mahasiswa di Bandung'")
    s.add_argument("--tone", help="santai | profesional | lucu | inspiratif | edukatif")
    s.add_argument("--platform", help="daftar dipisah koma: instagram,tiktok,linkedin,x,facebook")
    s.add_argument("--jumlah", type=int, help="jumlah posting per platform (default 7)")
    s.add_argument("--mulai", help="tanggal mulai YYYY-MM-DD (default: hari ini)")
    s.add_argument("--frekuensi",
                   help="harian | 2x-seminggu | 3x-seminggu | 5x-seminggu | mingguan | angka hari")
    s.add_argument("--produk", help="daftar produk dipisah ';'")
    s.add_argument("--promo", help="promo yang sedang berjalan")
    _localize(s)
    _common(s, "output/sosmed")

    a = sub.add_parser("artikel", add_help=False, help="draf artikel SEO (tunggal atau batch)",
                       description="Riset kata kunci (estimasi), outline, draf artikel Markdown "
                                   "dengan front-matter, FAQ, dan saran tautan internal.")
    a.add_argument("--topik", help="satu topik/kata kunci utama")
    a.add_argument("--keywords", help="file CSV/XLSX berisi kolom 'keyword' (batch)")
    a.add_argument("--audiens", default="", help="target pembaca")
    a.add_argument("--panjang", type=int, default=1200, help="target jumlah kata (mesin AI)")
    _localize(a)
    _common(a, "output/artikel")

    p = sub.add_parser("produk", add_help=False, help="deskripsi produk Shopee/Tokopedia + promo WhatsApp",
                       description="Baca spreadsheet produk lalu tambahkan kolom judul, poin "
                                   "fitur, deskripsi, tag SEO, dan copy promo WhatsApp.")
    p.add_argument("--input", "-i", required=True, help="file CSV/XLSX daftar produk")
    p.add_argument("--tone", default="santai", help="santai | profesional (default: santai)")
    _localize(p)
    _common(p, "output/produk")

    k = sub.add_parser("kuis", add_help=False, help="bank soal + kunci jawaban + export Quizizz/Forms",
                       description="Buat soal dari materi (.txt/.md/.pdf) atau dari topik "
                                   "(khusus mesin AI).")
    k.add_argument("--materi", nargs="+", help="satu atau beberapa file materi (.txt/.md/.pdf)")
    k.add_argument("--topik", help="topik (tanpa materi: butuh mesin AI)")
    k.add_argument("--jumlah", type=int, default=10, help="jumlah soal (default 10)")
    k.add_argument("--kesulitan", default="sedang", choices=["mudah", "sedang", "sulit"])
    k.add_argument("--tipe", default="pilihan_ganda",
                   help="pilihan_ganda,benar_salah,isian,esai (boleh beberapa, dipisah koma)")
    k.add_argument("--mapel", default="", help="mata pelajaran, mis. IPA")
    k.add_argument("--jenjang", default="", help="jenjang/kelas, mis. 'SMP kelas 8'")
    _localize(k)
    _common(k, "output/kuis")
    return parser


def resolve_engine(name: str) -> str:
    if name == "auto":
        return "ai" if has_api_key() else "template"
    if name == "ai" and not has_api_key():
        raise FatalError(
            "--engine ai butuh ANTHROPIC_API_KEY. Set dulu: export ANTHROPIC_API_KEY=sk-ant-... "
            "(Windows PowerShell: $env:ANTHROPIC_API_KEY=\"sk-ant-...\"), atau pakai "
            "--engine template."
        )
    return name


def make_llm(args: argparse.Namespace, client: Any = None) -> LLMClient | None:
    engine = resolve_engine(args.engine)
    if engine == "template":
        if args.engine == "auto":
            info("ANTHROPIC_API_KEY tidak ditemukan -> memakai mesin TEMPLATE (offline).")
        return None
    return build_llm(args.model, args.effort, client=client)


def main(argv: list[str] | None = None, *, client: Any = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    FAILED_ITEMS.clear()
    if not args.perintah:
        parser.print_help()
        return 0
    if args.concurrency < 1:
        parser.error("--concurrency minimal 1")
    try:
        llm = make_llm(args, client=client)
        outdir = Path(args.output)
        outdir.mkdir(parents=True, exist_ok=True)
        files = _dispatch(args, llm, outdir)
    except KeyboardInterrupt:
        info("\nDihentikan. Jalankan ulang perintah yang sama untuk melanjutkan.")
        return 130
    except KontenError as exc:
        info(f"ERROR: {exc}")
        return 1
    info("File hasil:")
    for f in files[:25]:
        info(f"  - {f}")
    if len(files) > 25:
        info(f"  ... dan {len(files) - 25} file lainnya")
    if llm is not None:
        info(llm.usage_summary())
    if FAILED_ITEMS:
        info(f"Ada {len(FAILED_ITEMS)} item gagal. Jalankan ulang perintah yang sama untuk "
             "mencoba lagi (item yang sudah jadi tidak dibuat ulang).")
        return 2
    return 0


def _dispatch(args: argparse.Namespace, llm: LLMClient | None, outdir: Path) -> list[Path]:
    if args.perintah == "sosmed":
        from konten import sosmed

        profile = sosmed.load_profile(args.profil) if args.profil else {}
        overrides = {"brand": args.brand, "niche": args.niche, "audiens": args.audiens,
                     "tone": args.tone, "platforms": args.platform, "jumlah": args.jumlah,
                     "mulai": args.mulai, "frekuensi": args.frekuensi, "produk": args.produk,
                     "promo": args.promo, "bahasa": args.bahasa}
        if args.bahasa == "id" and profile.get("bahasa"):
            overrides.pop("bahasa")
        brief = sosmed.build_brief(profile, overrides)
        return sosmed.run(brief, outdir, llm=llm, concurrency=args.concurrency, timpa=args.timpa)
    if args.perintah == "artikel":
        from konten import artikel

        jobs = artikel.load_jobs(args.topik, args.keywords, audiens=args.audiens,
                                 panjang=args.panjang, bahasa=args.bahasa)
        return artikel.run(jobs, outdir, llm=llm, concurrency=args.concurrency, timpa=args.timpa)
    if args.perintah == "produk":
        from konten import produk

        return produk.run(args.input, outdir, llm=llm, concurrency=args.concurrency,
                          timpa=args.timpa, lang=args.bahasa, tone=args.tone.lower())
    if args.perintah == "kuis":
        from konten import kuis

        tipe = kuis.parse_types(args.tipe)
        if args.jumlah < 1 or args.jumlah > 200:
            raise KontenError("--jumlah soal harus antara 1 dan 200")
        common = {"jumlah": args.jumlah, "kesulitan": args.kesulitan, "tipe": tipe,
                  "mapel": args.mapel, "jenjang": args.jenjang, "bahasa": args.bahasa,
                  "topik": args.topik or ""}
        jobs = []
        for path in args.materi or []:
            jobs.append(kuis.QuizJob(nama=Path(path).stem, materi=read_text_file(path), **common))
        if not jobs:
            if not args.topik:
                raise KontenError("Isi --materi file.txt/.md/.pdf atau --topik \"...\".")
            jobs.append(kuis.QuizJob(nama=args.topik, **common))
        return kuis.run(jobs, outdir, llm=llm, timpa=args.timpa)
    raise KontenError(f"Subperintah tidak dikenal: {args.perintah}")  # pragma: no cover
