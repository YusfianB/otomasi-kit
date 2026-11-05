# data_tools — Otomasi Data & Dokumen untuk Kantor dan UMKM

Kumpulan perintah untuk pekerjaan kantor yang berulang:

| Perintah | Kegunaan |
|---|---|
| `sample` | Membuat data contoh (penjualan, pelanggan, data dokumen, foto) untuk langsung dicoba |
| `clean` | Membersihkan file Excel/CSV yang berantakan |
| `merge` | Menggabungkan banyak file jadi satu, atau menggabung berdasarkan kolom kunci (seperti VLOOKUP) |
| `report` | Membuat laporan Excel + grafik PNG + halaman HTML; ada preset **laporan penjualan** |
| `docs` | Membuat dokumen massal (invoice, sertifikat, surat, slip gaji, atau template Word sendiri) |
| `files` | Ganti nama file massal (dengan pratinjau & undo), konversi file, olah gambar |

Semua perintah dijalankan dari folder utama repo:

```bash
python -m data_tools <perintah> [opsi]
python -m data_tools --help              # daftar perintah
python -m data_tools report --help       # bantuan satu perintah
```

Setiap perintah punya nama alias berbahasa Indonesia: `contoh`, `bersihkan`, `gabung`,
`laporan`, `dokumen`, `file` (`ganti-nama`, `batal`, `konversi`, `gambar`).

## Instalasi

Butuh Python 3.10 atau lebih baru.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt
```

Opsional:

- **xlrd** (`pip install xlrd`) jika perlu membaca file Excel lama `.xls`.
  File `.xlsx` tidak membutuhkannya.
- **LibreOffice** jika template Word buatan sendiri (`.docx`) ingin langsung dijadikan PDF.

## Mulai cepat (5 menit)

```bash
# 1. Buat data contoh di output/contoh
python -m data_tools sample

# 2. Bersihkan semua file penjualan
python -m data_tools clean output/contoh/penjualan -o output/bersih

# 3. Laporan penjualan lengkap (Excel + grafik + HTML)
python -m data_tools report output/contoh/penjualan --preset penjualan -o output/laporan

# 4. Cetak invoice massal + gabungkan jadi satu PDF
python -m data_tools docs invoice output/contoh/dokumen/invoice.xlsx \
    --profil output/contoh/dokumen/profil.yaml -o output/dokumen/invoice \
    --gabung output/dokumen/semua_invoice.pdf

# 5. Kecilkan foto produk + beri watermark
python -m data_tools files images output/contoh/foto -o output/foto_kecil \
    --lebar-maks 1200 --watermark "Toko Sumber Rejeki"
```

> Di Windows (Command Prompt), ganti `\` di akhir baris dengan `^`, atau tulis perintah dalam
> satu baris.

---

## 1. `clean` — membersihkan Excel/CSV

```bash
python -m data_tools clean data_penjualan.xlsx -o output/bersih
python -m data_tools clean folder_data/ -o output/bersih --format csv
python -m data_tools clean laporan.xlsx --semua-sheet -o output/bersih
python -m data_tools clean data.csv -o output/data_bersih.xlsx --teks kode_pos --tanggal tgl_kirim
```

Yang dikerjakan otomatis:

- **Membaca** satu file atau satu folder berisi `.csv`, `.xlsx`, `.xls`. Pemisah CSV
  (`,` `;` tab `|`) dan encoding (UTF-8, UTF-8 BOM, Windows-1252/Latin-1) dideteksi sendiri.
- **Baris judul** di atas tabel (mis. "LAPORAN PENJUALAN CABANG BANDUNG") dilewati; baris
  header dicari otomatis. Bisa ditentukan manual dengan `--baris-header 4`.
- **Nama kolom** dirapikan jadi `snake_case`: `" Harga Satuan (Rp) "` → `harga_satuan_rp`.
- **Teks** dipangkas dan spasi ganda dihapus; baris/kolom yang kosong total dibuang;
  **baris duplikat** dibuang (matikan dengan `--pertahankan-duplikat`).
- **Tanggal** dikenali dari berbagai format: `05/11/2026`, `5-11-26`, `2026-11-05`,
  `5 Nov 2026`, `5 Agustus 2026`, `Kamis, 5 November 2026`, `05-Nov-2026 14:30`.
  Format hari/bulan/tahun (Indonesia) diutamakan.
- **Angka** format Indonesia: `Rp 1.234.567,50` → 1234567,5 · `1.234` → 1234 ·
  `12,5%` → 12,5 · `15rb` → 15000 · `1,5jt` → 1500000 · `(5.000)` → -5000.
- **Kode/ID tidak dirusak**: nomor HP `0812...`, kode pos `01234`, NIK 16 digit, kolom
  bernama `id_...`, `kode_...`, `no_...`, `nik`, `npwp`, `telp`, `hp`, `rekening`, `sku`
  tetap teks sehingga angka nol di depan tidak hilang.

Opsi penting: `--teks KOLOM`, `--angka KOLOM`, `--tanggal KOLOM` untuk memaksa tipe kolom
(boleh dipisah koma), `-v` untuk melihat tipe yang terdeteksi tiap kolom.

## 2. `merge` — menggabungkan file

```bash
# Bertumpuk: semua baris dari banyak file, kolom diselaraskan menurut nama
python -m data_tools merge output/contoh/penjualan -o output/gabungan.xlsx

# Join berdasarkan kolom kunci (seperti VLOOKUP)
python -m data_tools merge penjualan.csv pelanggan.csv --kunci id_pelanggan -o output/lengkap.xlsx
python -m data_tools merge a.xlsx b.xlsx --kunci kode_barang --cara inner -o output/hasil.csv
```

- Mode bertumpuk menambah kolom `source_file` (dan `source_sheet` bila banyak sheet) agar
  asal tiap baris tetap diketahui. Kolom yang tidak ada di salah satu file dibiarkan kosong.
- `--cara`: `left` (default, semua baris file pertama), `inner` (hanya yang cocok), `outer`,
  `right`.
- Data dibersihkan dulu (sama seperti `clean`); pakai `--tanpa-bersih` untuk apa adanya.

## 3. `report` — laporan Excel + grafik + HTML

```bash
# Laporan umum: ringkasan statistik + pivot
python -m data_tools report data.xlsx --group-by kategori --value total,qty -o output/laporan

# Preset penjualan
python -m data_tools report output/contoh/penjualan --preset penjualan -o output/laporan \
    --judul "Laporan Penjualan Q3 2026"
```

Hasil di folder output:

- `laporan.xlsx` dengan sheet **Data**, **Ringkasan** (statistik: jumlah data, total,
  rata-rata, median, min/maks, dll. + info kolom), **Pivot** (total, jumlah data, rata-rata
  per kelompok) dan **Grafik**. Format Rupiah dan tanggal sudah diatur.
- `grafik/*.png` — grafik batang per kelompok, total per bulan, sebaran nilai.
- `laporan.html` — satu file HTML mandiri (grafik tertanam) yang bisa dibuka di browser
  atau dikirim lewat email/WhatsApp.

Tanpa `--group-by`, kolom teks pertama dengan 2–50 nilai berbeda dipakai otomatis.
Tanpa `--value`, semua kolom angka dipakai.

**Preset `penjualan`** mencari kolom berikut (nama kolom setelah dirapikan):

| Peran | Nama kolom yang dikenali | Wajib |
|---|---|---|
| tanggal | `tanggal`, `tgl`, `tanggal_transaksi`, `date`, `waktu`, ... | ya |
| produk | `produk`, `nama_produk`, `barang`, `item`, `menu`, ... | ya |
| total | `total`, `subtotal`, `total_harga`, `omzet`, `pendapatan`, ... | ya, atau qty + harga |
| qty | `qty`, `jumlah`, `kuantitas`, `jml`, `pcs`, ... | tidak (default 1 per baris) |
| harga | `harga`, `harga_satuan`, `harga_jual`, `price` | tidak |
| kategori, cabang/toko | `kategori`, `jenis` / `cabang`, `toko`, `outlet`, `channel` | tidak |
| nomor transaksi | `no_transaksi`, `no_invoice`, `no_nota`, `order_id`, ... | tidak (dipakai menghitung jumlah transaksi) |

Jika nama kolom berbeda, tentukan sendiri: `--kolom-tanggal tgl_jual --kolom-total nilai`.
Hasil preset: sheet **Ringkasan** (total omzet, jumlah transaksi, rata-rata per transaksi,
produk terlaris, hari terbaik), **Harian**, **Bulanan** (dengan pertumbuhan %), **Produk
Teratas** (porsi omzet), **Per Kategori/Cabang/Sumber**, **Pivot** produk × bulan, plus grafik
tren omzet harian (dengan rata-rata 7 hari), omzet bulanan, dan produk teratas. Penulisan nama
produk yang hanya beda huruf besar/kecil ("MINYAK GORENG 2L" vs "Minyak Goreng 2L") otomatis
disatukan.

## 4. `docs` — dokumen massal

```bash
python -m data_tools docs <template> <data.xlsx|data.csv> -o <folder> [opsi]
```

Satu baris spreadsheet = satu dokumen (khusus invoice: baris dengan nomor invoice sama
digabung menjadi satu invoice berisi beberapa item).

| Template | Kolom yang dibaca (selain itu diabaikan) |
|---|---|
| `invoice` | `nomor_invoice`, `tanggal`, `jatuh_tempo`, `nama_pelanggan`, `alamat_pelanggan`, `telepon`, `item`, `qty`, `harga`, `diskon` (Rupiah per baris), `catatan` |
| `sertifikat` | `nama`, `nomor_sertifikat`, `peran` (Peserta/Pemateri), `acara`, `tanggal`, `tempat`, `keterangan` |
| `surat` | `nomor_surat`, `tanggal`, `nama`, `jabatan`, `instansi`, `alamat`, `perihal`, `lampiran` + kolom apa pun yang dipakai di isi surat |
| `slip-gaji` | `nik`/`nip`, `nama`, `jabatan`, `departemen`, `periode`, `gaji_pokok`, kolom `tunjangan_*`, `lembur`, `bonus`, `insentif` (pendapatan), kolom `potongan_*`, `bpjs_*`, `pph21`, `kasbon` (potongan), `bank`, `no_rekening` |
| `file.docx` | template Word buatan sendiri berisi `{{nama_kolom}}` |

Contoh:

```bash
D=output/contoh/dokumen
python -m data_tools docs invoice $D/invoice.xlsx --profil $D/profil.yaml -o output/invoice \
    --nama-file "{nomor_invoice}_{nama_pelanggan}" --gabung output/semua_invoice.pdf
python -m data_tools docs sertifikat $D/sertifikat.csv --profil $D/profil.yaml -o output/sertifikat
python -m data_tools docs surat $D/surat.xlsx --isi $D/isi_surat.txt --profil $D/profil.yaml \
    -o output/surat --format semua
python -m data_tools docs slip-gaji $D/slip_gaji.xlsx --profil $D/profil.yaml -o output/slip
python -m data_tools docs $D/template_surat_keterangan.docx $D/slip_gaji.xlsx -o output/sk \
    --format docx --nama-file "SK_{nama}"
```

Opsi:

- `--format pdf|docx|semua` — default PDF. Template bawaan bisa langsung PDF dan DOCX.
- `--nama-file "{nomor}_{nama}"` — pola nama file dari kolom data. Karakter terlarang
  (`/ \ : * ? " < > |`) diganti otomatis dan spasi jadi `_`. `{urut}` = nomor urut 001, 002, ...
- `--gabung semua.pdf` — gabungkan semua PDF hasil menjadi satu file (siap cetak).
- `--profil profil.yaml` — data usaha untuk kop surat (lihat contoh di bawah).
- `--isi isi_surat.txt` — isi surat dengan placeholder (template `surat`). Baris berbentuk
  `Label: nilai` yang berurutan dicetak rapi sebagai daftar.
- `--ppn 11` — tarif PPN invoice (menimpa nilai di profil; `0` = tanpa PPN).
- `--batas 3` — buat 3 dokumen pertama saja untuk uji coba.

**Placeholder** di isi surat dan template `.docx`: `{{nama_kolom}}`. Nama kolom ditulis
seperti aslinya atau versi `snake_case` (`{{Nama Lengkap}}` = `{{nama_lengkap}}`). Format
otomatis: tanggal → `5 November 2026`, kolom uang (harga, total, gaji, tunjangan, ...) →
`Rp 1.250.000`. Filter tersedia: `{{gaji|rupiah}}`, `{{gaji|terbilang}}` (satu juta dua ratus
lima puluh ribu), `{{tanggal|hari}}` (Kamis, 5 November 2026), `{{tanggal|bulan}}`,
`{{qty|angka}}`, `{{nama|besar}}`, `{{nama|kecil}}`, `{{nama|judul}}`, `{{kode|teks}}`.
`{{hari_ini}}` berisi tanggal hari ini.

Template `.docx` sendiri: placeholder diganti di paragraf, tabel, header, footer, dan text box;
format huruf (tebal, miring, ukuran) dari awal placeholder dipertahankan, meskipun Word
memecah placeholder menjadi beberapa bagian.

Contoh `profil.yaml`:

```yaml
nama: Toko Sumber Rejeki
alamat: Jl. Merdeka No. 45, Bandung 40111
telepon: 022-1234567
email: halo@sumberrejeki.co.id
npwp: 01.234.567.8-901.000
kota: Bandung            # dipakai untuk "Bandung, 5 November 2026"
logo: logo.png           # relatif terhadap lokasi file profil
rekening: BCA 1234567890 a.n. Toko Sumber Rejeki
penandatangan: Budi Santoso
jabatan: Direktur
ppn: 11
warna: '#1F4E78'
```

## 5. `files` — utilitas file

### Ganti nama massal (`files rename`) — aman dengan pratinjau & undo

```bash
# Pratinjau saja (default, tidak ada file yang diubah)
python -m data_tools files rename foto/ --pola "produk_{no}" --ext-kecil

# Jalankan sungguhan
python -m data_tools files rename foto/ --pola "produk_{no}" --ext-kecil --jalankan

# Batalkan (pakai log terbaru di folder tsb, atau sebutkan file lognya)
python -m data_tools files undo foto/
```

Token pola: `{nama}` (nama asli), `{no}` (nomor urut, `--mulai 1 --digit 3` → 001),
`{no:4}`, `{tanggal}` (tanggal ubah file, `{tanggal:%d-%m-%Y}`), `{hari_ini}`, `{folder}`,
`{ext}`. Opsi lain: `--awalan`, `--akhiran`, `--cari X --ganti Y` (berlaku pada nama asli,
`--regex` untuk regular expression), `--huruf kecil|besar|judul`, `--spasi _`,
`--filter "*.jpg,*.png"`, `--urut nama|tanggal|ukuran`, `--rekursif`.

Nama yang bentrok (dua file jadi nama sama, atau nama tujuan sudah dipakai) ditandai
`BENTROK` dan proses dihentikan kecuali memakai `--lewati-bentrok`. Setiap rename menulis log
`_log_rename_YYYYMMDD_HHMMSS.json` di folder tersebut untuk `undo`.

### Konversi (`files convert <mode>`)

```bash
python -m data_tools files convert excel-ke-csv laporan.xlsx -o output/csv --pemisah ";"
python -m data_tools files convert csv-ke-excel data1.csv data2.csv -o output/gabung.xlsx
python -m data_tools files convert tabel-ke-pdf pelanggan.xlsx -o output/pelanggan.pdf --judul "Daftar Pelanggan"
python -m data_tools files convert gambar-ke-pdf scan/ -o output/scan.pdf
python -m data_tools files convert gabung-pdf a.pdf b.pdf folder_pdf/ -o output/gabungan.pdf
python -m data_tools files convert pisah-pdf dokumen.pdf -o output/pisah --halaman "1-3,4,5-"
```

- `excel-ke-csv`: setiap sheet jadi satu CSV (UTF-8 BOM agar terbaca benar di Excel).
  Pakai `--pemisah ";"` jika Excel di komputer memakai pengaturan regional Indonesia.
- `csv-ke-excel`: setiap CSV jadi satu sheet; angka/tanggal diubah ke nilai Excel asli
  (kode dengan nol di depan tetap teks). `--mentah` = semua sel teks; `--terpisah` = satu
  workbook per CSV.
- `tabel-ke-pdf`: tabel rapi dengan header berulang di setiap halaman dan nomor halaman;
  otomatis landscape jika kolom banyak.
- `gambar-ke-pdf`: satu gambar per halaman A4 (orientasi otomatis), `--ukuran asli` untuk
  ukuran halaman sesuai gambar.
- `pisah-pdf`: default per halaman; `--setiap 2` per 2 halaman; `--halaman "1-3,5"` rentang.

### Olah gambar (`files images`)

```bash
python -m data_tools files images foto/ -o output/foto_web --lebar-maks 1200 --kualitas 80
python -m data_tools files images foto/ -o output/foto_webp --format webp --lebar-maks 800 --akhiran _web
python -m data_tools files images foto/ -o output/foto_wm --watermark "© Toko Sumber Rejeki" --posisi tengah --opasitas 40
```

Rasio gambar selalu dipertahankan dan gambar kecil tidak diperbesar. Rotasi foto dari HP (EXIF)
diperbaiki otomatis. Format: `jpg`, `png`, `webp`. Posisi watermark: `kanan-bawah` (default),
`kiri-bawah`, `kanan-atas`, `kiri-atas`, `tengah`. Hasil selalu ditulis ke folder lain; file
asli tidak diubah.

---

## Format input yang diharapkan

- Baris pertama tabel berisi **judul kolom** (baris judul laporan di atasnya boleh ada).
- Satu baris = satu data (satu transaksi, satu peserta, satu karyawan). Hindari sel gabungan
  (merge cells) di area data.
- Tanggal boleh berupa tanggal Excel asli atau teks (`05/11/2026`, `5 November 2026`, ...).
  Format bulan/hari/tahun (AS) dikenali jika ada tanggal di atas 12 (mis. `11/25/2026`).
- Angka boleh berformat Indonesia (`Rp 1.250.000`, `12,5%`). Bila kolom berisi angka bergaya
  AS (`1,234.50`), pemisah dideteksi per kolom.
- Kolom kode (HP, NIK, kode pos) sebaiknya disimpan sebagai teks di Excel. Jika sudah
  tersimpan sebagai angka di Excel, nol di depannya sudah hilang dari file aslinya.

## Menjalankan tes

```bash
pip install -r requirements-dev.txt
pytest data_tools
ruff check data_tools
```

## Keterbatasan

- File `.xls` butuh `xlrd`; rumus Excel dibaca sebagai hasil terakhir yang tersimpan di file.
- Template Word sendiri → PDF butuh LibreOffice (`soffice`) terpasang.
- PDF bawaan memakai font Helvetica standar: huruf Latin (termasuk é, –, ©) aman, tetapi
  huruf non-Latin (Arab, Mandarin, emoji) belum didukung.
- Placeholder di template `.docx` tidak bisa mengulang baris tabel (mis. daftar item invoice);
  gunakan template `invoice` bawaan untuk itu.
- Persen disimpan sebagai angka yang tertulis (`12,5%` → 12,5), bukan 0,125.
