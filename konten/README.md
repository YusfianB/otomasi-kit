# konten — generator konten untuk UMKM, seller online & kreator

Paket `konten` membantu membuat konten secara massal (batch) untuk usaha kecil, penjual di
Shopee/Tokopedia/TikTok Shop, dan kreator konten di Indonesia:

| Subperintah | Fungsi | Hasil |
|---|---|---|
| `sosmed` | Kalender konten + caption media sosial (Instagram, TikTok, LinkedIn, X, Facebook) | Excel (1 sheet per platform + sheet Kalender), CSV, Markdown |
| `artikel` | Draf artikel SEO: riset kata kunci (estimasi), outline H1/H2/H3, artikel, FAQ, saran tautan internal | File `.md` dengan front-matter + `index_artikel.csv` |
| `produk` | Judul Shopee/Tokopedia, poin fitur, deskripsi lengkap, tag SEO, copy promo WhatsApp | Excel/CSV: data asli + kolom baru di sebelahnya |
| `kuis` | Soal pilihan ganda, benar/salah, isian, esai + kunci jawaban | `soal.md/.docx`, `kunci_jawaban.md/.docx`, export Excel/CSV (sheet Quizizz) |

Ada **dua mesin**:

- **`ai`** — memakai Claude lewat Anthropic API (butuh `ANTHROPIC_API_KEY`). Hasil paling
  bagus dan spesifik.
- **`template`** — offline, gratis, deterministik (input sama → hasil sama). Memakai bank
  frasa Bahasa Indonesia. Cocok untuk mencoba alur kerja atau saat tidak ada API key. Setiap
  hasil diberi label `engine: template` dan log menulis `Mesin: TEMPLATE`.

`--engine auto` (default) memakai AI bila `ANTHROPIC_API_KEY` tersedia, selain itu template.

---

## 1. Instalasi

Dari folder root repo `otomasi-kit`:

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m konten --help
```

Paket yang dipakai: `anthropic`, `pandas`, `openpyxl`, `pyyaml`. Opsional: `pypdf` (materi kuis
dari PDF) dan `python-docx` (soal dalam format Word). Jika keduanya tidak terpasang, `kuis`
tetap jalan dengan materi `.txt/.md` dan hasil Markdown saja.

## 2. Mengatur API key (mesin AI)

1. Buat API key di Claude Console: <https://platform.claude.com/settings/keys>
2. Set sebagai variabel lingkungan (jangan ditulis di kode / jangan di-commit):

```bash
# Linux / macOS
export ANTHROPIC_API_KEY="sk-ant-..."

# Windows PowerShell
$env:ANTHROPIC_API_KEY="sk-ant-..."
```

3. Opsional: pilih model & effort default lewat env (atau per perintah dengan `--model` /
   `--effort`):

```bash
export OTOMASI_MODEL="claude-opus-5-5"   # default
export OTOMASI_EFFORT="medium"           # low | medium | high | xhigh | max
```

Pesan error dibuat jelas dalam Bahasa Indonesia, misalnya: key belum diset / tidak valid, model
tidak ditemukan, kena rate limit (otomatis dicoba ulang dengan jeda), server sibuk, saldo
habis, atau permintaan ditolak oleh filter keamanan model.

## 3. Pilihan model & catatan biaya

**Default: `claude-opus-5-5`, effort `medium`.**

Alasannya: mengikuti panduan resmi Anthropic SDK, model default yang dipakai adalah Claude Opus
terbaru (Opus 5.5), dan biaya untuk pekerjaan massal diatur terutama lewat **effort**, bukan
dengan diam-diam menurunkan model. Effort `medium` sudah cukup untuk copywriting dan
artikel, dan lebih hemat dibanding `high`. Opus 5.5 juga lebih murah daripada Opus generasi
sebelumnya, dan system prompt bersama di-cache sehingga biaya input berulang kecil.

Kalau ingin lebih hemat, keputusan ada di tangan Anda:

| Pilihan | Harga per 1 juta token (input / output) | Kapan dipakai |
|---|---|---|
| `claude-opus-5-5` (default) | $4 / $20 (cache baca $0,20) | Kualitas terbaik, artikel panjang, kuis dari materi |
| `--effort low` | sama, tapi token output (termasuk "berpikir") jauh lebih sedikit | Caption & deskripsi produk massal |
| `--model claude-sonnet-5-5` | $2 / $10 | Kira-kira setengah biaya Opus, kualitas tetap baik |
| `--model claude-haiku-5-5` | $0,10 / $0,50 | Paling murah untuk copy pendek; tanpa *fallback* otomatis bila ditolak |

Perkiraan kasar dengan default (Opus 5.5, effort medium) — angka nyata tergantung panjang input
dan berapa banyak model "berpikir":

- 1 posting sosmed ≈ $0,02–0,04 → kalender 30 posting ≈ $0,6–1,2
- 1 deskripsi produk ≈ $0,02–0,04
- 1 artikel ~1.200 kata (2 langkah) ≈ $0,10–0,20
- 10 soal kuis dari materi 2 halaman ≈ $0,05–0,10

Di akhir setiap run AI, program mencetak **pemakaian token nyata dan perkiraan biaya**
(termasuk token yang dibaca dari cache). Selalu cek tagihan sebenarnya di Console.

Detail teknis mesin AI (untuk developer):

- Output memakai *structured outputs* (`output_config.format` dengan JSON schema), lalu
  divalidasi lagi secara lokal (tipe, field wajib, batas karakter, jumlah hashtag, dll.). Jika
  tidak sesuai, model diminta memperbaiki (maks. 3 percobaan); jika masih melebihi batas,
  teks dipotong rapi di batas kata.
- System prompt bersama + instruksi per tugas dikirim sebagai blok stabil dengan
  `cache_control` (prompt caching). Materi kuis juga di-cache antar bagian soal.
- Server-side fallback (`fallbacks: "default"`) aktif untuk model yang mendukung, jadi
  permintaan yang ditolak filter keamanan dicoba di model pengganti secara otomatis.
- SDK otomatis mengulang 429/5xx; di atasnya ada backoff tambahan. Request dikirim via
  streaming agar tidak kena timeout untuk output panjang.

## 4. Contoh pemakaian (copy-paste)

Semua contoh dijalankan dari root repo. Hasil default ke `output/<subperintah>/` (folder
`output/` sudah di-*gitignore*). Tambahkan `--engine template` untuk mode offline.

### 4.1 `sosmed` — kalender & caption media sosial

```bash
# Dari profil brand (lihat konten/examples/brand.yaml)
python -m konten sosmed --profil konten/examples/brand.yaml

# Tanpa file profil
python -m konten sosmed --brand "Kopi Senja" --niche "kopi susu kekinian" \
  --audiens "mahasiswa di Bandung" --tone santai --platform instagram,tiktok,x \
  --jumlah 12 --mulai 2026-12-01 --frekuensi 3x-seminggu \
  --produk "Es Kopi Susu Gula Aren; Kopi Pandan Latte" --promo "Gratis ongkir 12.12"

# Profil + override, bahasa Inggris, AI hemat
python -m konten sosmed --profil konten/examples/brand.yaml --platform linkedin \
  --tone profesional --bahasa en --effort low -o output/sosmed_linkedin
```

- `--jumlah` = jumlah posting **per platform**.
- `--frekuensi`: `harian`, `2x-seminggu`, `3x-seminggu` (Sen/Rab/Jum), `5x-seminggu`,
  `mingguan`, `2-mingguan`, atau angka hari (mis. `2`).
- `--tone`: `santai`, `profesional`, `lucu`, `inspiratif`, `edukatif`. LinkedIn otomatis
  memakai bahasa formal ("Anda", tanpa slang, hashtag CamelCase).
- Batas platform yang dijaga: caption+hashtag maks. 280 karakter (X), 2.200 (Instagram,
  TikTok), 3.000 (LinkedIn), 2.000 (Facebook, disarankan tetap singkat). Jumlah hashtag:
  Instagram 5–10, TikTok 3–5, LinkedIn 3–5, X 1–2, Facebook 1–3.
- Tiap posting berisi: tanggal, hari, jam posting (saran), platform, format, pilar konten,
  topik, hook, caption, hashtag, CTA, ide visual, dan **naskah video** (TikTok, Reels,
  video Facebook).
- Pilar konten bergiliran: Edukasi & Tips, Promo & Produk, Interaksi, Behind the Scenes,
  Bukti Sosial, Cerita Brand, Hiburan. Tidak ada testimoni palsu — pilar Bukti Sosial memakai
  placeholder `[tempel ulasan asli]`.

### 4.2 `artikel` — draf artikel SEO

```bash
# Satu topik
python -m konten artikel --topik "cara budidaya lele di ember" --audiens "pemula di rumah"

# Batch dari CSV/XLSX (kolom: keyword, opsional audiens, panjang, catatan)
python -m konten artikel --keywords konten/examples/keywords_contoh.csv --panjang 1500 \
  --concurrency 3
```

Setiap artikel menjadi `<slug>.md` dengan front-matter YAML (title, meta_title ≤ 60 karakter,
meta_description ≤ 160 karakter, slug, kata kunci terkait, long-tail, intent pencarian, saran
tautan internal), lalu H1, isi, FAQ, dan kesimpulan. `index_artikel.csv` merangkum semua
artikel di folder.

> **Jujur soal "riset kata kunci":** kata kunci terkait, long-tail, dan intent di sini
> adalah **perkiraan dari model AI atau template**, *bukan* data volume pencarian sungguhan.
> Validasi dengan Google Keyword Planner, Google Search Console, atau tools SEO lain sebelum
> menentukan prioritas. Draf dari mesin template berupa kerangka dengan `> Catatan editor`
> yang harus dilengkapi fakta dan contoh nyata.

### 4.3 `produk` — deskripsi marketplace

```bash
python -m konten produk --input konten/examples/produk_contoh.csv
python -m konten produk --input katalog.xlsx --tone profesional -o output/katalog
```

Kolom fleksibel; nama kolom dikenali otomatis (huruf besar/kecil & spasi/underscore bebas):

| Data | Nama kolom yang dikenali |
|---|---|
| nama (wajib) | `nama`, `nama produk`, `produk`, `name`, `product`, `judul` |
| kategori | `kategori`, `category`, `jenis` |
| harga | `harga`, `price` (angka, `Rp 125.000`, `25rb`, `1,5jt`) |
| fitur | `fitur`, `spesifikasi`, `keunggulan`, `features`, `detail` (pisahkan dengan `;`) |
| bahan / ukuran / warna / merek / berat / isi paket | `bahan`, `material`, `ukuran`, `size`, `warna`, `color`, `merek`, `merk`, `brand`, `berat`, `isi paket` |

Kolom lain tetap dipakai sebagai spesifikasi tambahan. Hasil: `<nama file>_konten.xlsx` dan
`.csv` berisi semua kolom asli + `judul_shopee` (≤ 120 karakter), `judul_tokopedia`
(≤ 70 karakter), `poin_fitur`, `deskripsi` (≤ 2.000 karakter, teks polos), `tag_seo`,
`promo_wa`, `engine`. Judul menaruh kata kunci utama di depan, tanpa huruf kapital semua,
tanpa klaim seperti "original/termurah" yang tidak ada di data.

### 4.4 `kuis` — bank soal

```bash
# Dari materi (txt/md/pdf), campuran tipe soal
python -m konten kuis --materi konten/examples/materi_contoh.txt --jumlah 12 \
  --tipe pilihan_ganda,benar_salah,isian,esai --kesulitan sedang \
  --mapel IPA --jenjang "SMP kelas 8"

# Beberapa file sekaligus (batch), masing-masing jadi satu set soal
python -m konten kuis --materi bab1.pdf bab2.md --jumlah 20 --tipe pg,bs

# Hanya dari topik (khusus mesin AI)
python -m konten kuis --topik "Proklamasi Kemerdekaan Indonesia" --jumlah 10 \
  --jenjang "SD kelas 5" --engine ai
```

Hasil per set soal di `output/kuis/<nama-materi>/`:

- `soal.md` (+ `soal.docx`) — lembar soal siap cetak, dikelompokkan A. Pilihan Ganda,
  B. Benar/Salah, C. Isian, D. Esai.
- `kunci_jawaban.md` (+ `.docx`) — jawaban + penjelasan singkat.
- `kuis_export.xlsx` — sheet **Soal** (format umum: pertanyaan, opsi_a–d, kunci, penjelasan)
  dan sheet **Quizizz** (kolom template impor spreadsheet Quizizz: Question Text, Question
  Type, Option 1–5, Correct Answer, Time in seconds, …). `kuis_export.csv` berisi format
  umum. Google Forms tidak punya impor spreadsheet bawaan; pakai add-on (mis. *Form
  Builder*) dengan sheet **Soal**. Cek kembali template impor terbaru di masing-masing tools.

Mesin template untuk kuis bekerja dengan **ekstraksi dari materi**: kalimat definisi
("X adalah …") jadi soal isian/pilihan ganda, kalimat sebab-akibat ("… karena …",
"… sehingga …") jadi soal esai "Jelaskan mengapa …", dan pengecoh diambil dari istilah lain di
materi yang sama (mis. "reaksi terang" ↔ "reaksi gelap"). Hasilnya bisa langsung dipakai,
tetapi tetap periksa sebelum diberikan ke siswa. Tanpa materi, mesin template tidak bisa
membuat soal — gunakan `--engine ai`.

## 5. Batch, paralel, dan melanjutkan (resume)

- Progres dicetak per item: `[3/10] OK ...`, `LEWAT` (sudah ada), `GAGAL: alasan`.
- Satu item gagal tidak menghentikan batch. Exit code `2` jika ada item gagal, `1` untuk
  error fatal (mis. key tidak valid), `0` jika semua sukses.
- **Resume:** hasil per item disimpan di `<output>/_items/`. Jalankan ulang perintah yang sama
  untuk melanjutkan — item yang sudah jadi dilewati (artikel `.md` yang sudah ada juga tidak
  ditimpa, jadi editan manual aman). Pakai `--timpa` untuk membuat ulang.
- `--concurrency N` (default 3) mengatur jumlah request AI paralel. Turunkan ke 1–2 jika
  sering kena rate limit. Mesin template selalu berjalan berurutan (sudah sangat cepat).

## 6. Keterbatasan

- Mesin template tidak "tahu" fakta tentang produk/topik Anda: ia merangkai frasa dari data
  yang Anda berikan. Topik khusus dari profil brand diberi kerangka dengan placeholder
  `[...]`, bukan isi karangan. Artikel template perlu dilengkapi editor.
- Dengan `--bahasa en`, mesin template memakai frasa bahasa Inggris, tetapi data dari Anda
  (nama produk, fitur, keunggulan) tetap seperti yang ditulis — tidak diterjemahkan.
- Riset kata kunci bukan data volume pencarian (lihat 4.2).
- Kualitas soal template bergantung pada materi: materi yang berisi definisi dan kalimat
  sebab-akibat menghasilkan soal terbaik. Materi PDF hasil scan (gambar) tidak bisa dibaca.
- Perkiraan biaya bersifat kasar; cek Console untuk angka resmi.
- Batas karakter platform/marketplace bisa berubah; sesuaikan konstanta `PLATFORMS`
  (`konten/sosmed.py`) dan `LIMITS` (`konten/produk.py`) bila perlu.

## 7. Struktur & pengembangan

```
konten/
├── cli.py            # argparse, pemilihan mesin, exit code
├── llm.py            # wrapper Anthropic SDK: structured output, validasi, retry, cache, error ID
├── common.py         # util teks, baca CSV/XLSX, tulis Excel, batch runner resumable
├── templates_id.py   # bank frasa template (id/en)
├── sosmed.py  artikel.py  produk.py  kuis.py
├── examples/         # brand.yaml, produk_contoh.csv, keywords_contoh.csv, materi_contoh.txt
└── tests/            # test_konten_*.py (client Anthropic di-mock, tanpa jaringan)
```

```bash
.venv/bin/pytest konten
.venv/bin/ruff check konten
```
