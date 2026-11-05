"""Command-line interface: ``python -m devtools <perintah> ...``."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from devtools import __version__
from devtools.backup import (
    GFS_KEYS,
    BackupConfig,
    BackupError,
    DatabaseConfig,
    config_from_mapping,
    create_backup,
    describe_verify,
    list_archives,
    load_yaml,
    restore_archive,
    rotate,
    verify_archive,
)
from devtools.filters import DEFAULT_EXCLUDES, human_size

log = logging.getLogger("devtools")

EPILOG = """\
Contoh:
  python -m devtools backup ./proyek --tujuan ./backups --simpan 7
  python -m devtools backup --config devtools/contoh/backup.yaml
  python -m devtools verify backups/proyek-20260101-020000.zip
  python -m devtools restore backups/proyek-20260101-020000.zip --ke ./pulih
  python -m devtools sync ./dokumen /mnt/gdrive/dokumen --hapus --dry-run
  python -m devtools buat-proyek api-fastapi toko-online --penulis "Budi"
  python -m devtools daftar-template
"""


def _add_filter_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--include", action="append", metavar="POLA", default=None,
        help="Hanya ambil file yang cocok dengan pola glob (boleh diulang), mis. '*.py'",
    )
    p.add_argument(
        "--exclude", action="append", metavar="POLA", default=None,
        help="Kecualikan file/folder yang cocok dengan pola glob (boleh diulang), mis. '*.log'",
    )
    p.add_argument(
        "--tanpa-default-exclude", action="store_true", default=None,
        help=f"Jangan pakai pengecualian bawaan ({', '.join(DEFAULT_EXCLUDES[:5])}, ...)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m devtools",
        description="Tools developer: backup & restore, sync folder, dan pembuat proyek dari template.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"devtools {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="Tampilkan log detail")
    parser.add_argument("-q", "--quiet", action="store_true", help="Hanya tampilkan peringatan/error")
    # -v/-q are also accepted after the sub-command (SUPPRESS keeps the global value otherwise).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS,
                        help="Tampilkan log detail")
    common.add_argument("-q", "--quiet", action="store_true", default=argparse.SUPPRESS,
                        help="Hanya tampilkan peringatan/error")
    sub = parser.add_subparsers(dest="perintah", metavar="PERINTAH")

    def add(name: str, **kw) -> argparse.ArgumentParser:
        return sub.add_parser(name, parents=[common], **kw)

    # backup
    p = add(
        "backup", help="Buat arsip backup bertimestamp (.zip/.tar.gz) + manifest SHA-256",
        description="Backup folder/file ke arsip bertimestamp dengan manifest SHA-256 dan rotasi.",
    )
    p.add_argument("sumber", nargs="*", type=Path, help="Folder/file yang di-backup")
    p.add_argument("--config", type=Path, help="File YAML (bagian 'backup:'), mis. devtools/contoh/backup.yaml")
    p.add_argument("--tujuan", type=Path, help="Folder tempat menyimpan arsip (default: ./backups)")
    p.add_argument("--nama", help="Awalan nama arsip (default: 'backup')")
    p.add_argument("--format", choices=["zip", "tar.gz"], help="Format arsip (default: zip)")
    _add_filter_args(p)
    p.add_argument("--simpan", type=int, metavar="N", help="Rotasi: simpan N arsip terbaru saja")
    for key in GFS_KEYS:
        p.add_argument(
            f"--{key}", type=int, metavar="N",
            help=f"Retensi GFS: simpan arsip terbaru untuk N periode {key}",
        )
    p.add_argument("--sqlite", action="append", type=Path, metavar="FILE",
                   help="Dump database SQLite ini ke dalam arsip (boleh diulang)")
    p.add_argument("--pg-dsn", action="append", metavar="DSN",
                   help="Dump PostgreSQL via pg_dump, mis. postgresql://user@localhost/db")
    p.add_argument("--mysql-db", action="append", metavar="DB",
                   help="Dump database MySQL/MariaDB via mysqldump (user dari ~/.my.cnf)")
    p.add_argument("--dry-run", action="store_true", help="Tampilkan apa yang akan di-backup saja")

    # verify
    p = add("verify", help="Cek checksum arsip terhadap manifest-nya")
    p.add_argument("arsip", nargs="+", type=Path)

    # restore
    p = add("restore", help="Pulihkan arsip (diverifikasi dulu checksum-nya)")
    p.add_argument("arsip", type=Path)
    p.add_argument("--ke", type=Path, required=True, metavar="FOLDER", help="Folder tujuan restore")
    p.add_argument("--paksa", action="store_true", help="Izinkan restore ke folder yang tidak kosong")
    p.add_argument("--hanya", action="append", metavar="POLA",
                   help="Hanya pulihkan path arsip yang cocok pola glob, mis. 'proyek/data/*'")
    p.add_argument("--tanpa-verifikasi", action="store_true", help="Lewati verifikasi checksum")

    # daftar-backup
    p = add("daftar-backup", help="Tampilkan arsip backup di sebuah folder")
    p.add_argument("tujuan", type=Path)
    p.add_argument("--nama", default="backup")

    # rotasi
    p = add("rotasi", help="Jalankan rotasi arsip lama tanpa membuat backup baru")
    p.add_argument("tujuan", type=Path)
    p.add_argument("--nama", default="backup")
    p.add_argument("--simpan", type=int, metavar="N")
    for key in GFS_KEYS:
        p.add_argument(f"--{key}", type=int, metavar="N")
    p.add_argument("--dry-run", action="store_true")

    # sync
    p = add(
        "sync", help="Mirror satu arah folder ke folder lain (mis. drive cloud yang di-mount)",
        description="Salin file baru/berubah dari SUMBER ke TUJUAN. Dengan --hapus, file di "
        "TUJUAN yang tidak ada di SUMBER ikut dihapus.",
    )
    p.add_argument("sumber", nargs="?", type=Path)
    p.add_argument("tujuan", nargs="?", type=Path)
    p.add_argument("--config", type=Path, help="File YAML (bagian 'sync:' berisi daftar job)")
    p.add_argument("--hapus", action="store_true", default=None,
                   help="Hapus file di tujuan yang tidak ada di sumber")
    p.add_argument("--mode", choices=["ukuran", "hash"], default=None,
                   help="Pembanding perubahan: ukuran+waktu ubah (cepat, default) atau hash SHA-256")
    _add_filter_args(p)
    p.add_argument("--dry-run", action="store_true", help="Simulasi tanpa mengubah apa pun")

    # buat-proyek
    p = add("buat-proyek", help="Buat proyek baru dari template")
    p.add_argument("template", help="Nama template (lihat: daftar-template)")
    p.add_argument("nama", help="Nama proyek, mis. toko-online")
    p.add_argument("--folder", type=Path, help="Folder proyek (default: ./<nama>)")
    p.add_argument("--deskripsi", help="Deskripsi singkat proyek")
    p.add_argument("--penulis", help="Nama penulis/pemilik proyek")
    p.add_argument("--paksa", action="store_true", help="Izinkan menulis ke folder yang tidak kosong")

    add("daftar-template", help="Tampilkan template proyek yang tersedia")
    return parser


def _setup_logging(verbose: bool, quiet: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING if quiet else logging.INFO
    root = logging.getLogger("devtools")
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False


def _gfs_from_args(args) -> dict[str, int]:
    return {k: getattr(args, k) for k in GFS_KEYS if getattr(args, k, None)}


# --------------------------------------------------------------------------- commands


def cmd_backup(args) -> int:
    if args.config:
        data = load_yaml(args.config)
        section = data.get("backup", data if "sync" not in data else None)
        if not section:
            raise BackupError(f"Config {args.config} tidak punya bagian 'backup:'.")
        cfg = config_from_mapping(section, args.config.resolve().parent)
    else:
        cfg = BackupConfig(sources=[], destination=Path("backups"))
    # Command-line arguments override the config file.
    if args.sumber:
        cfg.sources = list(args.sumber)
    if args.tujuan:
        cfg.destination = args.tujuan
    if args.nama:
        cfg.name = args.nama
    if args.format:
        cfg.fmt = args.format
    if args.include:
        cfg.include = args.include
    if args.exclude:
        cfg.exclude = cfg.exclude + args.exclude
    if args.tanpa_default_exclude:
        cfg.use_default_excludes = False
    if args.simpan:
        cfg.keep_last = args.simpan
    cfg.gfs.update(_gfs_from_args(args))
    if args.sqlite or args.pg_dsn or args.mysql_db:
        cfg.databases = DatabaseConfig(
            sqlite=cfg.databases.sqlite + list(args.sqlite or []),
            postgres=cfg.databases.postgres + [{"dsn": d} for d in args.pg_dsn or []],
            mysql=cfg.databases.mysql + [{"database": d} for d in args.mysql_db or []],
        )
    if not cfg.sources and cfg.databases.is_empty():
        raise BackupError("Sebutkan folder/file sumber, atau gunakan --config.")
    result = create_backup(cfg, dry_run=args.dry_run)
    if not args.dry_run:
        print(result.archive)
    return 0


def cmd_verify(args) -> int:
    bad = 0
    for path in args.arsip:
        res = verify_archive(path)
        status = "OK   " if res.ok else "GAGAL"
        print(f"{status} {path}: {describe_verify(res)}")
        bad += not res.ok
    return 1 if bad else 0


def cmd_restore(args) -> int:
    restore_archive(
        args.arsip, args.ke, force=args.paksa, verify=not args.tanpa_verifikasi, only=args.hanya
    )
    return 0


def cmd_list_backups(args) -> int:
    archives = list_archives(args.tujuan, args.nama)
    if not archives:
        print(f"Tidak ada arsip '{args.nama}-*' di {args.tujuan}")
        return 0
    for when, path in archives:
        print(f"{when:%Y-%m-%d %H:%M:%S}  {human_size(path.stat().st_size):>9}  {path.name}")
    return 0


def cmd_rotate(args) -> int:
    gfs = _gfs_from_args(args)
    if not args.simpan and not gfs:
        raise BackupError("Tentukan --simpan dan/atau --harian/--mingguan/--bulanan/--tahunan.")
    deleted = rotate(args.tujuan, args.nama, args.simpan, gfs, dry_run=args.dry_run)
    print(f"{'Akan dihapus' if args.dry_run else 'Dihapus'}: {len(deleted)} arsip")
    return 0


def cmd_sync(args) -> int:
    from devtools.sync import SyncConfig, SyncError, configs_from_mapping, sync_folders

    jobs: list[SyncConfig] = []
    if args.config:
        data = load_yaml(args.config)
        if "sync" not in data:
            raise SyncError(f"Config {args.config} tidak punya bagian 'sync:'.")
        jobs = configs_from_mapping(data["sync"], args.config.resolve().parent)
    if args.sumber or args.tujuan:
        if not (args.sumber and args.tujuan):
            raise SyncError("Sebutkan SUMBER dan TUJUAN sekaligus.")
        jobs = [SyncConfig(source=args.sumber, destination=args.tujuan)]
    if not jobs:
        raise SyncError("Sebutkan SUMBER TUJUAN, atau gunakan --config.")
    for job in jobs:
        if args.hapus:
            job.delete_extra = True
        if args.mode:
            job.mode = args.mode
        if args.include:
            job.include = args.include
        if args.exclude:
            job.exclude = job.exclude + args.exclude
        if args.tanpa_default_exclude:
            job.use_default_excludes = False
        log.info("Sync %s -> %s%s", job.source, job.destination, " (--hapus)" if job.delete_extra else "")
        summary = sync_folders(job, dry_run=args.dry_run)
        print(summary.text())
    return 0


def cmd_create_project(args) -> int:
    from devtools.scaffold import create_project, next_steps

    project_dir, files, ctx = create_project(
        args.template, args.nama, folder=args.folder, description=args.deskripsi,
        author=args.penulis, force=args.paksa,
    )
    print(f"Proyek '{ctx['judul']}' ({args.template}) dibuat di {project_dir} - {len(files)} file.")
    steps = next_steps(args.template, project_dir, ctx)
    if steps:
        print("\nLangkah berikutnya:")
        for s in steps:
            print(f"  {s}")
    return 0


def cmd_list_templates(args) -> int:
    from devtools.scaffold import list_templates

    templates = list_templates()
    width = max(len(t.name) for t in templates)
    print("Template yang tersedia:\n")
    for t in templates:
        print(f"  {t.name:<{width}}  {t.title} - {t.description}")
    print("\nPakai: python -m devtools buat-proyek <template> <nama> [--folder F] "
          "[--deskripsi D] [--penulis P]")
    return 0


COMMANDS = {
    "backup": cmd_backup,
    "verify": cmd_verify,
    "restore": cmd_restore,
    "daftar-backup": cmd_list_backups,
    "rotasi": cmd_rotate,
    "sync": cmd_sync,
    "buat-proyek": cmd_create_project,
    "daftar-template": cmd_list_templates,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.perintah:
        parser.print_help()
        return 0
    _setup_logging(args.verbose, args.quiet)
    try:
        return COMMANDS[args.perintah](args)
    except BackupError as exc:
        log.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        log.error("Dibatalkan oleh pengguna.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
