# devtools: tools developer otomasi-kit

Isi modul ini:

| Bagian | Perintah | Fungsi |
|---|---|---|
| Backup | `backup`, `verify`, `restore`, `daftar-backup`, `rotasi` | Arsip `.zip`/`.tar.gz` bertimestamp, manifest SHA-256, rotasi & retensi GFS, dump database |
| Sync | `sync` | Mirror satu arah sebuah folder ke folder lain (mis. drive cloud yang di-mount) |
| Template proyek | `buat-proyek`, `daftar-template` | Membuat proyek baru siap pakai: API FastAPI, web statis, bot Telegram, CLI Python |
| GitHub Actions | `.github/workflows/*.yml` | CI, konten terjadwal, monitor scraper, backup mingguan, audit dependency |

Dependency yang dibutuhkan hanya `pyyaml` dan `jinja2` (sudah ada di `requirements.txt`).

```bash
python -m devtools --help
python -m devtools backup --help     # bantuan per perintah
```

Opsi `-v` (log detail) dan `-q` (hanya peringatan/error) bisa ditaruh sebelum atau sesudah
nama perintah.

---

## 1. GitHub Actions

Semua workflow ada di `.github/workflows/`. Setiap workflow terjadwal juga bisa dijalankan manual
dari tab **Actions** -> pilih workflow -> **Run workflow**.

| Workflow | Jadwal (WIB) | Fungsi | Hasil |
|---|---|---|---|
| `ci.yml` | setiap push/PR ke `main` | `ruff check .` + `pytest` di Python 3.11 & 3.13 (dengan cache pip) | status centang di PR |
| `jadwal-konten.yml` | Selasa 06:17 | `python -m konten sosmed --profil ... --jumlah 14 --output output/sosmed` | artifact `konten-sosmed-<n>` (30 hari) |
| `jadwal-scraper.yml` | setiap hari 05:43 | `python -m scraper monitor <config>` lalu bandingkan dengan run sebelumnya | artifact `laporan-scraper-<n>` + ringkasan di Job Summary |
| `backup.yml` | Senin 03:29 | `python -m devtools backup` pada isi repo, lalu `verify` | artifact `backup-otomasi-kit-<n>` (90 hari) |
| `kualitas-kode.yml` | Rabu 08:11 | `pip-audit` (kerentanan dependency) + ruff | Job Summary; run gagal bila ada temuan |
| `dependabot.yml` | Senin 06:00 | PR otomatis untuk update paket pip & versi GitHub Actions | Pull request |

Catatan:

- Jadwal cron di GitHub memakai UTC. Menitnya sengaja tidak `:00` karena antrean GitHub
  paling padat di awal jam. Jadwal bisa terlambat beberapa menit.
- Workflow terjadwal hanya berjalan di branch default, dan GitHub menonaktifkannya bila repo
  tidak ada aktivitas selama 60 hari (aktifkan lagi dari tab Actions).
- Runner GitHub-hosted punya akses internet, jadi monitor scraper bisa mengakses situs
  sungguhan. Riwayat SQLite scraper disimpan antar-run dengan `actions/cache`.

### Secret

| Secret | Wajib? | Dipakai oleh | Keterangan |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | opsional | `jadwal-konten.yml` | Bila ada, konten dibuat dengan Claude. Bila tidak ada, `--engine auto` memakai template engine offline. |

Cara menambah secret: **Settings -> Secrets and variables -> Actions -> New repository secret**.
`ci.yml` sengaja tidak memakai secret apa pun (tes konten memakai mock API), jadi PR dari fork
tetap bisa dites.

### Menyesuaikan

- Konten: ubah input default `profil`/`jumlah`/`engine` di `jadwal-konten.yml`, atau isi saat
  *Run workflow*. Perintahnya diberi komentar agar mudah diubah bila opsi CLI `konten` berubah.
- Scraper: input `config` (default `scraper/contoh/produk_toko.yaml`). Tambahkan
  `--gagal-jika-berubah` agar run gagal (dan GitHub mengirim email) setiap kali ada perubahan.
- Backup: artifact GitHub bersifat sementara. Untuk arsip jangka panjang, unduh artifact atau
  jalankan backup di server sendiri (lihat bagian 2) lalu sinkronkan ke cloud.

---

## 2. Backup & restore

### Pemakaian cepat

```bash
# Backup folder proyek ke ./backups, simpan 7 arsip terakhir
python -m devtools backup ./proyek --tujuan ./backups --nama proyek --simpan 7

# Beberapa sumber sekaligus, format tar.gz, kecualikan file log dan folder output
python -m devtools backup ./src ./dokumen catatan.txt --format tar.gz \
    --exclude "*.log" --exclude output --tujuan /mnt/backup --nama kantor

# Hanya file tertentu
python -m devtools backup ./proyek --include "*.py" --include "*.yaml"

# Lihat dulu apa yang akan di-backup
python -m devtools backup ./proyek --dry-run
```

Hasilnya: `backups/proyek-20260105-020000.zip`. Di dalam arsip, setiap sumber disimpan di
folder dengan nama sumbernya (`proyek/...`), plus file `MANIFEST-BACKUP.json` yang berisi
SHA-256 dan ukuran setiap file.

**Pengecualian bawaan:** `.git`, `.venv`, `venv`, `__pycache__`, `node_modules`,
`.pytest_cache`, `.ruff_cache`, `.mypy_cache`, `*.pyc`, `.DS_Store`, `Thumbs.db`.
Matikan dengan `--tanpa-default-exclude`. Folder tujuan backup otomatis dikecualikan bila
berada di dalam folder sumber.

**Aturan pola glob:** pola tanpa `/` (mis. `*.log`, `output`) dicocokkan ke setiap bagian
path di kedalaman mana pun. Pola dengan `/` (mis. `data/mentah/*`) dicocokkan ke path relatif
dari folder sumber.

### Verifikasi & restore

```bash
python -m devtools verify backups/proyek-20260105-020000.zip        # cek checksum
python -m devtools restore backups/proyek-20260105-020000.zip --ke ./pulih
python -m devtools restore arsip.zip --ke ./pulih --hanya "proyek/data/*"   # sebagian
python -m devtools daftar-backup backups --nama proyek
```

- `restore` selalu memverifikasi checksum dulu, dan membatalkan bila ada file rusak/hilang.
  Lewati dengan `--tanpa-verifikasi` (tidak disarankan).
- Folder tujuan harus kosong, kecuali memakai `--paksa`.
- Path berbahaya di dalam arsip (`../`, path absolut) dan symlink ditolak.

### Rotasi & retensi GFS

- `--simpan N`: simpan N arsip terbaru saja.
- GFS (*grandfather-father-son*): `--harian 7 --mingguan 4 --bulanan 6 [--tahunan 2]`
  menyimpan arsip terbaru untuk masing-masing 7 hari, 4 minggu, dan 6 bulan terakhir.
- Bila keduanya dipakai, arsip yang lolos salah satu aturan tetap disimpan.
- Rotasi hanya menyentuh arsip dengan awalan `--nama` yang sama.

```bash
python -m devtools rotasi backups --nama proyek --harian 7 --mingguan 4 --dry-run
```

### Dump database (opsional)

Database di-dump ke folder `_database/` di dalam arsip sebelum diarsipkan:

```bash
python -m devtools backup ./proyek --sqlite data/app.db               # API backup sqlite3 bawaan
python -m devtools backup ./proyek --pg-dsn postgresql://user@localhost/toko   # butuh pg_dump
python -m devtools backup ./proyek --mysql-db toko                     # butuh mysqldump
```

- SQLite memakai API `backup` dari modul `sqlite3`, jadi aman walau database sedang dipakai.
- PostgreSQL/MySQL memanggil `pg_dump`/`mysqldump`. Bila programnya tidak ada di PATH,
  backup dibatalkan dengan pesan yang jelas. Password: pakai `PGPASSWORD` atau `~/.pgpass`
  untuk PostgreSQL, dan `~/.my.cnf` atau `password_env` di config untuk MySQL.

### File config (untuk cron / Task Scheduler)

Lihat contoh lengkap di [`contoh/backup.yaml`](contoh/backup.yaml):

```bash
python -m devtools backup --config devtools/contoh/backup.yaml
python -m devtools sync   --config devtools/contoh/backup.yaml
```

Path relatif di config dihitung **dari folder file config**, bukan dari folder kerja saat ini.
Argumen command line menimpa nilai di config (mis. `--tujuan`).

#### Linux / macOS (cron)

`crontab -e`, lalu tambahkan (backup setiap hari 02:30, sync 03:00):

```cron
30 2 * * * cd /home/budi/otomasi-kit && .venv/bin/python -m devtools -q backup --config devtools/contoh/backup.yaml >> /home/budi/backup.log 2>&1
0 3 * * *  cd /home/budi/otomasi-kit && .venv/bin/python -m devtools -q sync --config devtools/contoh/backup.yaml >> /home/budi/backup.log 2>&1
```

Pakai path absolut. Di macOS, beri izin *Full Disk Access* ke `cron` bila mem-backup folder
seperti Documents/Desktop.

#### Windows (Task Scheduler)

Lewat PowerShell (sebagai user biasa):

```powershell
$aksi = New-ScheduledTaskAction -Execute "C:\otomasi-kit\.venv\Scripts\python.exe" `
    -Argument "-m devtools -q backup --config devtools\contoh\backup.yaml" `
    -WorkingDirectory "C:\otomasi-kit"
$pemicu = New-ScheduledTaskTrigger -Daily -At 2:30am
Register-ScheduledTask -TaskName "Backup otomasi-kit" -Action $aksi -Trigger $pemicu
```

Atau dengan `schtasks`:

```bat
schtasks /Create /SC DAILY /ST 02:30 /TN "Backup otomasi-kit" ^
  /TR "C:\otomasi-kit\.venv\Scripts\python.exe -m devtools -q backup --config C:\otomasi-kit\devtools\contoh\backup.yaml"
```

Atau lewat GUI: Task Scheduler -> *Create Basic Task* -> Program: `python.exe` dari venv,
Arguments: `-m devtools backup --config ...`, *Start in*: folder repo.

Exit code: `0` sukses, `1` gagal (pesan error di stderr), cocok untuk notifikasi monitoring.

---

## 3. Sync folder (mirror satu arah)

```bash
python -m devtools sync ./dokumen /media/hdd/dokumen --dry-run   # simulasi dulu
python -m devtools sync ./dokumen /media/hdd/dokumen             # salin baru/berubah
python -m devtools sync ./dokumen /media/hdd/dokumen --hapus     # + hapus file yang sudah tidak ada di sumber
python -m devtools sync ./foto /mnt/nas/foto --mode hash --exclude "*.tmp"
```

- Perubahan dideteksi dari ukuran + waktu ubah (toleransi 2 detik untuk drive FAT/cloud), atau
  SHA-256 dengan `--mode hash` (lebih lambat tapi teliti).
- File disalin lewat file sementara lalu di-rename, sehingga tujuan tidak pernah berisi file
  setengah jadi. Waktu ubah file ikut disalin.
- Dengan `--hapus`, file yang cocok dengan pola exclude **tidak** dihapus dari tujuan.
  Selalu coba `--dry-run` dulu.
- Hasil akhirnya berupa ringkasan, mis.
  `Ringkasan sync: 3 baru, 1 berubah, 120 sama, 0 dihapus, 2.4 MB disalin`.

### Ke cloud (Google Drive, OneDrive, Dropbox, S3)

devtools tidak memanggil API cloud secara langsung. Ada dua cara:

1. **Drive yang di-mount sebagai folder** (paling mudah): Google Drive for desktop
   (`G:\My Drive\Backup` di Windows, `~/Library/CloudStorage/GoogleDrive-.../My Drive` di macOS),
   OneDrive, atau Dropbox. Jadikan folder itu sebagai `tujuan`:

   ```bash
   python -m devtools sync ./backups "G:/My Drive/Backup/otomasi-kit"
   ```

2. **[rclone](https://rclone.org/)** untuk S3, Google Drive, Backblaze B2, dan lain-lain.
   Jalankan `rclone config` sekali, lalu pilih salah satu:

   ```bash
   # a) mount sebagai folder, lalu pakai devtools sync
   rclone mount gdrive:Backup ~/gdrive --daemon --vfs-cache-mode writes
   python -m devtools sync ./backups ~/gdrive/otomasi-kit

   # b) atau biarkan rclone yang menyalin hasil backup devtools
   python -m devtools backup --config devtools/contoh/backup.yaml
   rclone copy ./backups s3:nama-bucket/otomasi-kit --max-age 24h
   ```

---

## 4. Template proyek

```bash
python -m devtools daftar-template
python -m devtools buat-proyek <template> <nama> [--folder F] [--deskripsi D] [--penulis P] [--paksa]
```

| Template | Isi |
|---|---|
| `api-fastapi` | FastAPI + pydantic, CRUD `/produk` dengan SQLite (`sqlite3` bawaan), tes pytest + TestClient, Dockerfile, `.env.example`, CI |
| `web-statis` | Landing page responsif usaha kecil (HTML/CSS/JS murni, mode terang/gelap, form ke WhatsApp), workflow deploy GitHub Pages |
| `bot-telegram` | Bot long polling dengan `requests`: `/start`, `/help`, FAQ dari `faq.yaml`, `/ingatkan` (pengingat tersimpan di JSON), token dari env |
| `cli-python` | Kerangka CLI: argparse + subcommand, logging, config TOML, tes pytest, packaging `pyproject.toml` (`pip install -e .`), CI |

Contoh:

```bash
python -m devtools buat-proyek api-fastapi toko-online --penulis "Budi" --deskripsi "API toko online"
python -m devtools buat-proyek web-statis "Kopi Senja" --folder ~/situs/kopi-senja
python -m devtools buat-proyek bot-telegram bot-cs
python -m devtools buat-proyek cli-python rapikan-file
```

- Folder default: `./<nama-dalam-huruf-kecil-dengan-strip>`. Nama paket Python diturunkan dari
  nama itu (`toko-online` -> `toko_online`).
- Bila folder sudah ada dan tidak kosong, proses ditolak kecuali memakai `--paksa`. Dengan
  `--paksa`, file template menimpa file bernama sama, sedangkan file lain tidak dihapus.
- Setelah selesai, langkah berikutnya (install, tes, jalankan) ditampilkan.

### Menambah template sendiri

Buat folder `devtools/templates/<nama>/` berisi:

- `template.yaml`: berisi `judul`, `deskripsi`, `deskripsi_default`, dan `langkah`
  (boleh memakai `{folder}`, `{slug}`, `{paket}`, `{judul}`).
- `files/`: isi proyek. Aturannya:
  - File berakhiran `.tmpl` dirender dengan Jinja2, lalu akhiran itu dibuang. Variabel yang
    tersedia: `nama`, `slug`, `paket`, `judul`, `deskripsi`, `penulis`, `tahun`, `tanggal`.
  - File lain disalin apa adanya. Gunakan cara ini untuk workflow GitHub yang berisi `${{ }}`.
  - Segmen path `__paket__` diganti nama paket, `__nama__` diganti slug.
  - Awalan `dot-` diganti `.`, jadi `dot-gitignore` menjadi `.gitignore` dan `dot-github`
    menjadi `.github`.
  - Semua file Python di template **wajib** berakhiran `.py.tmpl`, supaya ruff dan pytest repo
    ini tidak ikut memeriksanya (ini dicek oleh tes).

---

## 5. Pengembangan

```bash
.venv/bin/pytest devtools        # tes: backup/restore, rotasi, sync, scaffold, workflow
.venv/bin/ruff check devtools
```

Tes scaffold juga merender setiap template, meng-compile semua file `.py`, mem-parse file
YAML/JSON/TOML, menjalankan tes bawaan proyek `cli-python` dan `bot-telegram`, serta tes
lapisan database `api-fastapi` (FastAPI sendiri tidak di-install di repo ini).
