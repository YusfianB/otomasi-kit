"""Command line interface: ``python -m data_tools <perintah>``."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from data_tools import DataToolsError, __version__

log = logging.getLogger("data_tools")

EPILOG = """\
Contoh cepat:
  python -m data_tools sample -o output/contoh
  python -m data_tools clean output/contoh/penjualan -o output/bersih
  python -m data_tools report output/contoh/penjualan --preset penjualan -o output/laporan
  python -m data_tools docs invoice output/contoh/dokumen/invoice.xlsx \\
      --profil output/contoh/dokumen/profil.yaml -o output/dokumen/invoice
  python -m data_tools files rename output/contoh/foto --pola "produk_{no}" --spasi _

Ketik 'python -m data_tools <perintah> -h' untuk bantuan tiap perintah.
"""


class _Formatter(argparse.RawDescriptionHelpFormatter):
    def __init__(self, prog):
        super().__init__(prog, max_help_position=34, width=100)


def _indonesian_argparse() -> None:
    """Translate argparse's built-in English messages."""
    import gettext

    table = {
        "usage: ": "cara pakai: ",
        "positional arguments": "argumen wajib",
        "options": "opsi",
        "show this help message and exit": "tampilkan bantuan ini lalu keluar",
        "the following arguments are required: %s": "argumen berikut wajib diisi: %s",
        "%(prog)s: error: %(message)s\n": "%(prog)s: error: %(message)s\n",
        "unrecognized arguments: %s": "argumen tidak dikenal: %s",
        "invalid choice: %(value)r (choose from %(choices)s)":
            "pilihan tidak valid: %(value)r (pilih dari %(choices)s)",
        "expected one argument": "butuh satu nilai",
        "invalid %(type)s value: %(value)r": "nilai %(type)s tidak valid: %(value)r",
        "show program's version number and exit": "tampilkan versi lalu keluar",
    }
    argparse._ = lambda s: table.get(s, gettext.gettext(s))  # type: ignore[attr-defined]


def print_table(headers: list[str], rows: list[list[str]], max_width: int = 45) -> None:
    def cut(text: str) -> str:
        text = str(text)
        return text if len(text) <= max_width else text[: max_width - 1] + "…"

    rows = [[cut(c) for c in r] for r in rows]
    widths = [max([len(h)] + [len(r[i]) for r in rows]) for i, h in enumerate(headers)]
    line = "  ".join("-" * w for w in widths)
    print("  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print(line)
    for r in rows:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)))


def _human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024
    return f"{n} B"


# --------------------------------------------------------------------------- shared options


def _add_read_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--semua-sheet", "--all-sheets", action="store_true", dest="all_sheets",
                   help="baca semua sheet Excel (default: sheet pertama saja)")
    p.add_argument("--sheet", help="nama sheet Excel yang dibaca")
    p.add_argument("--baris-header", type=int, dest="header_row", metavar="N",
                   help="nomor baris judul kolom (1 = baris pertama). Default: deteksi otomatis")
    p.add_argument("--rekursif", action="store_true",
                   help="ikut baca file di dalam subfolder")


def _add_clean_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--teks", action="append", default=[], metavar="KOLOM",
                   help="paksa kolom tetap teks (pisahkan koma), mis. --teks kode_pos,no_hp")
    p.add_argument("--angka", action="append", default=[], metavar="KOLOM",
                   help="paksa kolom jadi angka")
    p.add_argument("--tanggal", action="append", default=[], metavar="KOLOM",
                   help="paksa kolom jadi tanggal")
    p.add_argument("--pertahankan-duplikat", action="store_true",
                   help="jangan buang baris duplikat")


def _clean_options(args):
    from data_tools.cleaning import CleanOptions

    return CleanOptions(
        drop_duplicates=not getattr(args, "pertahankan_duplikat", False),
        text_columns=getattr(args, "teks", []),
        number_columns=getattr(args, "angka", []),
        date_columns=getattr(args, "tanggal", []),
    )


def _read(args):
    from data_tools.io import read_tables

    header = args.header_row - 1 if args.header_row else None
    return read_tables(args.input, all_sheets=args.all_sheets, sheet=args.sheet,
                       header_row=header, recursive=args.rekursif)


def _load_clean_concat(args, clean: bool = True):
    from data_tools.merging import clean_tables, concat_tables

    tables = _read(args)
    if clean:
        tables = clean_tables(tables, _clean_options(args))
    if len(tables) == 1:
        return tables[0].df
    log.info("Menggabungkan %d tabel", len(tables))
    return concat_tables(tables)


# --------------------------------------------------------------------------- commands


def cmd_sample(args) -> int:
    from data_tools.sample import generate_samples

    created = generate_samples(args.output, seed=args.seed)
    out = Path(args.output)
    print(f"Data contoh dibuat di: {out}")
    for files in created.values():
        folder = files[0].parent.relative_to(out).as_posix()
        prefix = f"{folder}/" if folder != "." else ""
        print(f"  {prefix}" + (", ".join(f.name for f in files) if prefix else files[0].name))
    print("\nCoba langkah berikut:")
    d = out.as_posix()
    print(f"  python -m data_tools clean {d}/penjualan -o output/bersih")
    print(f"  python -m data_tools merge {d}/penjualan -o output/gabungan.xlsx")
    print(f"  python -m data_tools report {d}/penjualan --preset penjualan -o output/laporan")
    print(f"  python -m data_tools docs invoice {d}/dokumen/invoice.xlsx "
          f"--profil {d}/dokumen/profil.yaml -o output/dokumen/invoice")
    print(f"  python -m data_tools files images {d}/foto -o output/foto_kecil --lebar-maks 1200 "
          "--watermark \"Toko Sumber Rejeki\"")
    return 0


def cmd_clean(args) -> int:
    from data_tools.cleaning import clean_dataframe, describe_types
    from data_tools.io import write_table

    tables = _read(args)
    out = Path(args.output)
    single_file = out.suffix.lower() in (".xlsx", ".csv", ".tsv") and len(tables) == 1
    if out.suffix and not single_file:
        raise DataToolsError("Untuk banyak file, -o harus berupa folder.")
    written = []
    for t in tables:
        df, report = clean_dataframe(t.df, _clean_options(args))
        log.info("%s: %s", t.label, report.summary())
        if args.verbose:
            log.info("  Tipe kolom: %s", describe_types(report))
        if single_file:
            target = out
        else:
            stem = t.source.stem + (f"_{t.sheet}" if args.all_sheets and t.sheet else "")
            target = out / f"{stem}_bersih.{args.format}"
        written.append(write_table(df, target))
    print(f"Selesai: {len(written)} file bersih disimpan:")
    for w in written:
        print(f"  {w}")
    return 0


def cmd_merge(args) -> int:
    from data_tools.io import write_table
    from data_tools.merging import clean_tables, concat_tables, join_tables

    tables = _read(args)
    if not args.tanpa_bersih:
        tables = clean_tables(tables, _clean_options(args))
    if args.kunci:
        df = join_tables(tables, args.kunci, args.cara)
        how = f"join '{args.kunci}' ({args.cara})"
    else:
        df = concat_tables(tables)
        if not args.pertahankan_duplikat:
            data_cols = [c for c in df.columns if c not in ("source_file", "source_sheet")]
            before = len(df)
            df = df.drop_duplicates(subset=data_cols, ignore_index=True)
            if before - len(df):
                log.info("%d baris duplikat antar-file dibuang", before - len(df))
        how = "digabung bertumpuk"
    path = write_table(df, args.output)
    print(f"Selesai: {len(tables)} tabel {how} -> {path} ({len(df)} baris, "
          f"{len(df.columns)} kolom)")
    return 0


def cmd_report(args) -> int:
    from data_tools.report import build_report, build_sales_report

    df = _load_clean_concat(args, clean=not args.tanpa_bersih)
    if args.preset == "penjualan":
        overrides = {
            "tanggal": args.kolom_tanggal, "produk": args.kolom_produk, "qty": args.kolom_qty,
            "harga": args.kolom_harga, "total": args.kolom_total,
        }
        result = build_sales_report(df, args.output, title=args.judul or "Laporan Penjualan",
                                    overrides=overrides, name=args.nama or "laporan_penjualan",
                                    top_n=args.top)
    else:
        result = build_report(df, args.output, group_by=args.group_by, values=args.value,
                              title=args.judul or "Laporan Data", name=args.nama or "laporan")
    print("Laporan selesai:")
    print(f"  Excel : {result.excel}")
    print(f"  HTML  : {result.html}")
    print(f"  Grafik: {len(result.charts)} file PNG di {Path(args.output) / 'grafik'}")
    for label, value in result.kpis:
        print(f"  - {label}: {value}")
    return 0


def cmd_docs(args) -> int:
    from data_tools.cleaning import CleanOptions, clean_dataframe
    from data_tools.documents import generate_documents, load_profile
    from data_tools.io import read_tables

    tables = read_tables([args.data], sheet=args.sheet)
    df, _ = clean_dataframe(tables[0].df, CleanOptions(drop_duplicates=False,
                                                       text_columns=args.teks))
    formats = ("pdf", "docx") if args.format == "semua" else (args.format,)
    body = Path(args.isi).read_text(encoding="utf-8") if args.isi else None
    result = generate_documents(
        args.template, df, args.output, formats=formats, name_pattern=args.nama_file,
        profile=load_profile(args.profil), body=body, merge_to=args.gabung,
        number_column=args.kolom_nomor, ppn=args.ppn, limit=args.batas,
    )
    print(f"Selesai: {len(result.files)} dokumen dibuat di {args.output}")
    for f in result.files[:5]:
        print(f"  {f.name}")
    if len(result.files) > 5:
        print(f"  … dan {len(result.files) - 5} file lainnya")
    if result.merged:
        print(f"PDF gabungan: {result.merged}")
    return 0


def cmd_rename(args) -> int:
    from data_tools.files import (
        STATUS_CONFLICT,
        STATUS_OK,
        RenameOptions,
        apply_renames,
        plan_renames,
    )

    opts = RenameOptions(
        pattern=args.pola, prefix=args.awalan, suffix=args.akhiran, find=args.cari,
        replace=args.ganti, regex=args.regex, ignore_case=args.abaikan_besar_kecil,
        start=args.mulai, digits=args.digit, case=args.huruf, spaces=args.spasi,
        date_format=args.format_tanggal, lower_ext=args.ext_kecil, match=args.filter,
        sort=args.urut, recursive=args.rekursif,
    )
    plans = plan_renames(args.folder, opts)
    if not plans:
        print("Tidak ada file yang cocok.")
        return 0
    status_text = {"ok": "akan diubah" if not args.jalankan else "diubah", "tetap": "tetap",
                   "bentrok": "BENTROK"}
    rows = [[str(i), p.source.name, p.target.name,
             status_text[p.status] + (f" ({p.note})" if p.note else "")]
            for i, p in enumerate(plans, 1)]
    print_table(["No", "Nama lama", "Nama baru", "Status"], rows)
    n_ok = sum(p.status == STATUS_OK for p in plans)
    n_conflict = sum(p.status == STATUS_CONFLICT for p in plans)
    print(f"\n{n_ok} file akan diganti namanya, {n_conflict} bentrok, "
          f"{len(plans) - n_ok - n_conflict} tetap.")
    if not args.jalankan:
        print("Ini hanya PRATINJAU (dry-run). Tambahkan --jalankan untuk benar-benar mengganti nama.")
        return 0
    if n_conflict and not args.lewati_bentrok:
        raise DataToolsError("Ada nama yang bentrok. Ubah pola, atau tambahkan --lewati-bentrok "
                             "untuk melewati file yang bentrok.")
    result = apply_renames(plans, args.folder, args.log)
    if result is None:
        print("Tidak ada yang diubah.")
        return 0
    print(f"Selesai: {len(result.entries)} file diganti namanya.")
    print(f"Log untuk membatalkan: {result.path}")
    print(f"Batalkan dengan: python -m data_tools files undo \"{result.path}\"")
    return 0


def cmd_undo(args) -> int:
    from data_tools.files import undo_renames

    log_file, count, problems = undo_renames(args.log)
    for p in problems:
        log.warning("Peringatan: %s", p)
    print(f"Selesai: {count} file dikembalikan ke nama semula (log: {log_file.name}).")
    return 0


def cmd_convert(args) -> int:
    from data_tools import convert

    mode = args.mode
    if mode == "excel-ke-csv":
        files = convert.excel_to_csv(args.input, args.output, delimiter=args.pemisah,
                                     sheet=args.sheet)
    elif mode == "csv-ke-excel":
        files = convert.csv_to_excel(args.input, args.output, raw=args.mentah,
                                     separate=args.terpisah)
    elif mode == "tabel-ke-pdf":
        files = [convert.table_to_pdf(args.input, args.output, title=args.judul,
                                      clean=not args.tanpa_bersih)]
    elif mode == "gambar-ke-pdf":
        files = [convert.images_to_pdf(args.input, args.output, page=args.ukuran)]
    elif mode == "gabung-pdf":
        files = [convert.merge_pdfs(args.input, args.output)]
    elif mode == "pisah-pdf":
        if len(args.input) != 1:
            raise DataToolsError("pisah-pdf hanya menerima satu file PDF.")
        files = convert.split_pdf(args.input[0], args.output, pages=args.halaman,
                                  every=args.setiap)
    else:  # pragma: no cover - argparse restricts choices
        raise DataToolsError(f"Mode tidak dikenal: {mode}")
    print(f"Selesai: {len(files)} file dibuat")
    for f in files[:10]:
        print(f"  {f}")
    if len(files) > 10:
        print(f"  … dan {len(files) - 10} file lainnya")
    return 0


def cmd_images(args) -> int:
    from data_tools.images import ImageOptions, process_images

    opts = ImageOptions(
        max_width=args.lebar_maks, max_height=args.tinggi_maks, quality=args.kualitas,
        fmt=args.format, watermark=args.watermark, position=args.posisi,
        opacity=args.opasitas, suffix=args.akhiran,
    )
    results = process_images(args.input, args.output, opts, recursive=args.rekursif)
    rows = [[r.source.name, r.target.name, f"{r.size_before[0]}x{r.size_before[1]}",
             f"{r.size_after[0]}x{r.size_after[1]}", _human_size(r.bytes_before),
             _human_size(r.bytes_after)] for r in results]
    print_table(["File", "Hasil", "Ukuran awal", "Ukuran akhir", "Size awal", "Size akhir"], rows)
    before = sum(r.bytes_before for r in results)
    after = sum(r.bytes_after for r in results)
    saved = (1 - after / before) * 100 if before else 0
    print(f"\nSelesai: {len(results)} gambar disimpan di {args.output} "
          f"({_human_size(before)} -> {_human_size(after)}, hemat {saved:.0f}%)")
    return 0


# --------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    _indonesian_argparse()
    parser = argparse.ArgumentParser(
        prog="python -m data_tools",
        description="Alat bantu data & dokumen untuk kantor dan UMKM: bersihkan Excel/CSV, "
                    "gabung file, buat laporan, cetak dokumen massal, dan olah file.",
        epilog=EPILOG, formatter_class=_Formatter,
    )
    parser.add_argument("--version", action="version", version=f"data_tools {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="tampilkan detail proses")
    sub = parser.add_subparsers(dest="command", metavar="<perintah>", title="perintah")

    def add(name, aliases, help_text, func):
        p = sub.add_parser(name, aliases=aliases, help=help_text, description=help_text,
                           formatter_class=_Formatter)
        p.set_defaults(func=func)
        p.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS,
                       help="tampilkan detail proses")
        return p

    # sample
    p = add("sample", ["contoh"], "buat data contoh (penjualan, dokumen, foto) untuk dicoba",
            cmd_sample)
    p.add_argument("-o", "--output", default="output/contoh", help="folder tujuan "
                   "(default: output/contoh)")
    p.add_argument("--seed", type=int, default=2026, help="angka acak agar hasil sama tiap kali")

    # clean
    p = add("clean", ["bersihkan"], "bersihkan file Excel/CSV (satu file atau satu folder)",
            cmd_clean)
    p.add_argument("input", nargs="+", help="file .csv/.xlsx/.xls atau folder")
    p.add_argument("-o", "--output", default="output/bersih",
                   help="folder hasil, atau nama file jika input hanya satu (default: "
                        "output/bersih)")
    p.add_argument("--format", choices=["xlsx", "csv"], default="xlsx",
                   help="format file hasil (default: xlsx)")
    _add_read_options(p)
    _add_clean_options(p)

    # merge
    p = add("merge", ["gabung"], "gabungkan banyak file jadi satu (bertumpuk atau join kolom "
            "kunci)", cmd_merge)
    p.add_argument("input", nargs="+", help="file dan/atau folder")
    p.add_argument("-o", "--output", default="output/gabungan.xlsx",
                   help="file hasil .xlsx/.csv (default: output/gabungan.xlsx)")
    p.add_argument("--kunci", "--key", help="kolom kunci untuk join (seperti VLOOKUP), "
                   "mis. id_pelanggan")
    p.add_argument("--cara", "--how", choices=["left", "inner", "outer", "right"],
                   default="left", help="jenis join (default: left = semua baris file pertama)")
    p.add_argument("--tanpa-bersih", action="store_true", help="jangan bersihkan data dulu")
    _add_read_options(p)
    _add_clean_options(p)

    # report
    p = add("report", ["laporan"], "buat laporan Excel + grafik PNG + HTML", cmd_report)
    p.add_argument("input", nargs="+", help="file dan/atau folder data")
    p.add_argument("-o", "--output", default="output/laporan",
                   help="folder hasil (default: output/laporan)")
    p.add_argument("--preset", choices=["penjualan"],
                   help="laporan siap pakai; 'penjualan' = omzet harian/bulanan, produk teratas")
    p.add_argument("--group-by", "--kelompok", action="append", dest="group_by",
                   metavar="KOLOM", help="kolom pengelompokan untuk pivot (boleh dipisah koma)")
    p.add_argument("--value", "--nilai", action="append", dest="value", metavar="KOLOM",
                   help="kolom angka yang dijumlah/dirata-rata (default: semua kolom angka)")
    p.add_argument("--judul", help="judul laporan")
    p.add_argument("--nama", help="nama file laporan tanpa ekstensi")
    p.add_argument("--top", type=int, default=10, help="jumlah produk teratas (default: 10)")
    p.add_argument("--tanpa-bersih", action="store_true", help="jangan bersihkan data dulu")
    for role in ("tanggal", "produk", "qty", "harga", "total"):
        p.add_argument(f"--kolom-{role}", metavar="KOLOM",
                       help=f"nama kolom {role} (preset penjualan; default: deteksi otomatis)")
    _add_read_options(p)
    _add_clean_options(p)

    # docs
    p = add("docs", ["dokumen"], "buat dokumen massal (PDF/DOCX) dari spreadsheet", cmd_docs)
    p.add_argument("template", help="invoice | sertifikat | surat | slip-gaji | path template "
                   ".docx sendiri (berisi {{nama_kolom}})")
    p.add_argument("data", help="file .xlsx/.csv: satu baris = satu dokumen "
                   "(invoice: baris dengan nomor sama digabung)")
    p.add_argument("-o", "--output", default="output/dokumen",
                   help="folder hasil (default: output/dokumen)")
    p.add_argument("--format", choices=["pdf", "docx", "semua"], default="pdf",
                   help="format dokumen (default: pdf; template .docx -> pdf butuh LibreOffice)")
    p.add_argument("--nama-file", metavar="POLA",
                   help='pola nama file, mis. "{nomor}_{nama}" (nama kolom dalam kurung kurawal)')
    p.add_argument("--gabung", metavar="FILE.pdf", help="gabungkan semua PDF jadi satu file")
    p.add_argument("--profil", metavar="FILE.yaml",
                   help="data perusahaan (nama, alamat, logo, penandatangan, ppn, ...)")
    p.add_argument("--isi", metavar="FILE.txt",
                   help="isi surat dengan {{placeholder}} (template surat)")
    p.add_argument("--kolom-nomor", metavar="KOLOM",
                   help="kolom nomor invoice untuk mengelompokkan item")
    p.add_argument("--ppn", type=float, help="tarif PPN persen (menimpa nilai di profil)")
    p.add_argument("--sheet", help="nama sheet Excel")
    p.add_argument("--teks", action="append", default=[], metavar="KOLOM",
                   help="paksa kolom tetap teks")
    p.add_argument("--batas", type=int, metavar="N", help="buat N dokumen pertama saja (uji coba)")

    # files
    pf = add("files", ["file"], "utilitas file: rename massal, undo, konversi, olah gambar",
             lambda a: (pf.print_help(), 0)[1])
    fsub = pf.add_subparsers(dest="files_command", metavar="<aksi>", title="aksi")

    def fadd(name, aliases, help_text, func):
        q = fsub.add_parser(name, aliases=aliases, help=help_text, description=help_text,
                            formatter_class=_Formatter)
        q.set_defaults(func=func)
        q.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS,
                       help="tampilkan detail proses")
        return q

    q = fadd("rename", ["ganti-nama"], "ganti nama banyak file sekaligus (pratinjau dulu)",
             cmd_rename)
    q.add_argument("folder", help="folder berisi file")
    q.add_argument("--pola", default="{nama}",
                   help="pola nama baru. Token: {nama} {no} {no:4} {tanggal} {tanggal:%%d%%m%%Y} "
                        "{hari_ini} {folder} {ext}. Default: {nama}")
    q.add_argument("--awalan", "--prefix", default="", help="teks di depan nama")
    q.add_argument("--akhiran", "--suffix", default="", help="teks di belakang nama")
    q.add_argument("--cari", "--find", help="teks yang dicari di nama asli")
    q.add_argument("--ganti", "--replace", default="", help="pengganti teks --cari")
    q.add_argument("--regex", action="store_true", help="--cari berupa regular expression")
    q.add_argument("--abaikan-besar-kecil", action="store_true",
                   help="--cari tidak membedakan huruf besar/kecil")
    q.add_argument("--mulai", type=int, default=1, help="nomor awal untuk {no} (default: 1)")
    q.add_argument("--digit", type=int, default=3, help="jumlah digit {no} (default: 3 -> 001)")
    q.add_argument("--huruf", choices=["kecil", "besar", "judul"], help="ubah huruf nama")
    q.add_argument("--spasi", metavar="KARAKTER", help='ganti spasi, mis. --spasi _')
    q.add_argument("--format-tanggal", default="%Y-%m-%d",
                   help="format {tanggal} (default: %%Y-%%m-%%d)")
    q.add_argument("--ext-kecil", action="store_true", help="ubah ekstensi jadi huruf kecil")
    q.add_argument("--filter", default="*", help='hanya file yang cocok, mis. "*.jpg,*.png"')
    q.add_argument("--urut", choices=["nama", "tanggal", "ukuran"], default="nama",
                   help="urutan penomoran (default: nama)")
    q.add_argument("--rekursif", action="store_true", help="ikut file di subfolder")
    q.add_argument("--dry-run", "--pratinjau", action="store_true",
                   help="hanya pratinjau (ini perilaku default)")
    q.add_argument("--jalankan", "--apply", action="store_true",
                   help="benar-benar ganti nama (tanpa ini hanya pratinjau)")
    q.add_argument("--lewati-bentrok", action="store_true",
                   help="tetap jalankan dan lewati file yang namanya bentrok")
    q.add_argument("--log", help="lokasi file log undo (default: di folder tersebut)")

    q = fadd("undo", ["batal"], "batalkan rename berdasarkan file log", cmd_undo)
    q.add_argument("log", help="file log _log_rename_*.json, atau folder (pakai log terbaru)")

    q = fadd("convert", ["konversi"], "konversi file", cmd_convert)
    q.add_argument("mode", choices=["excel-ke-csv", "csv-ke-excel", "tabel-ke-pdf",
                                    "gambar-ke-pdf", "gabung-pdf", "pisah-pdf"],
                   help="excel-ke-csv | csv-ke-excel | tabel-ke-pdf | gambar-ke-pdf | "
                        "gabung-pdf | pisah-pdf")
    q.add_argument("input", nargs="+", help="file dan/atau folder")
    q.add_argument("-o", "--output", required=True, help="file atau folder hasil")
    q.add_argument("--pemisah", default=",", help="pemisah CSV hasil (default: koma; "
                   "Excel Indonesia sering memakai ;)")
    q.add_argument("--sheet", help="excel-ke-csv: hanya sheet ini")
    q.add_argument("--mentah", action="store_true",
                   help="csv-ke-excel: simpan semua sel sebagai teks apa adanya")
    q.add_argument("--terpisah", action="store_true",
                   help="csv-ke-excel: satu workbook per file CSV")
    q.add_argument("--judul", help="tabel-ke-pdf: judul di atas tabel")
    q.add_argument("--tanpa-bersih", action="store_true",
                   help="tabel-ke-pdf: tampilkan data apa adanya")
    q.add_argument("--ukuran", choices=["a4", "asli"], default="a4",
                   help="gambar-ke-pdf: ukuran halaman (default: a4)")
    q.add_argument("--halaman", help='pisah-pdf: rentang, mis. "1-3,4,5-" (default: per halaman)')
    q.add_argument("--setiap", type=int, default=1, help="pisah-pdf: jumlah halaman per file")

    q = fadd("images", ["gambar"], "olah banyak gambar: resize, kompres, ubah format, watermark",
             cmd_images)
    q.add_argument("input", nargs="+", help="file gambar dan/atau folder")
    q.add_argument("-o", "--output", default="output/gambar",
                   help="folder hasil (default: output/gambar)")
    q.add_argument("--lebar-maks", "--max-width", type=int, metavar="PX",
                   help="lebar maksimum (rasio tetap)")
    q.add_argument("--tinggi-maks", "--max-height", type=int, metavar="PX",
                   help="tinggi maksimum (rasio tetap)")
    q.add_argument("--kualitas", "--quality", type=int, default=85,
                   help="kualitas JPG/WEBP 1-100 (default: 85)")
    q.add_argument("--format", choices=["jpg", "png", "webp"], help="ubah format gambar")
    q.add_argument("--watermark", metavar="TEKS", help="tambahkan teks watermark")
    q.add_argument("--posisi", default="kanan-bawah",
                   choices=["kanan-bawah", "kiri-bawah", "kanan-atas", "kiri-atas", "tengah"],
                   help="posisi watermark (default: kanan-bawah)")
    q.add_argument("--opasitas", type=int, default=60, help="opasitas watermark 0-100 "
                   "(default: 60)")
    q.add_argument("--akhiran", default="", help="tambahan di nama file hasil, mis. _kecil")
    q.add_argument("--rekursif", action="store_true", help="ikut gambar di subfolder")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s", stream=sys.stdout, force=True,
    )
    for noisy in ("matplotlib", "PIL", "fontTools"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return int(args.func(args) or 0)
    except DataToolsError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Dibatalkan.", file=sys.stderr)
        return 130
    except PermissionError as exc:
        print(f"Error: tidak punya izin atau file sedang dibuka program lain: {exc.filename}",
              file=sys.stderr)
        return 1
