# scraper — scraper web berbasis config YAML

Ambil data dari situs web (daftar produk, lowongan kerja, katalog buku, dll.) **tanpa menulis
kode**: cukup tulis file YAML kecil berisi *di mana* datanya, lalu jalankan.

Fitur utama:

- Selector CSS untuk item & field, plus `regex`, `map`, `default`, `required`, `multiple`.
- Tipe data: `text`, `int`, `float`, `price_idr` (paham `Rp1.250.000`, `Rp 89.900`,
  `Rp1,2 jt`, `15rb`), `url` (link relatif jadi lengkap), `date` (`5 November 2026`,
  `02/11/2026`, `2 hari yang lalu` → `2026-11-03`).
- Pagination: link "Berikutnya" **atau** pola URL `?page={page}`.
- Ikuti halaman detail tiap item untuk mengambil data tambahan.
- Data dari JSON tersembunyi: JSON-LD (`<script type="application/ld+json">`) dan
  `__NEXT_DATA__` / `window.__STATE__`.
- Sopan: patuh `robots.txt` (termasuk `Crawl-delay`), jeda acak, timeout, retry dengan backoff
  (menghormati `Retry-After`), deduplikasi, batas item/halaman, bisa dilanjutkan bila terputus.
- Output CSV / XLSX / JSON / JSONL (ditentukan dari ekstensi `--output`) + ringkasan run.
- `monitor`: bandingkan dengan run sebelumnya → item baru, item hilang, harga naik/turun.
- `uji`: lihat apa yang diambil setiap selector, untuk memperbaiki config dengan cepat.

---

## 1. Mulai cepat (tanpa internet)

Di folder `tests/fixtures/toko` ada **Toko Contoh**, toko online palsu untuk latihan.
Dari folder root repo:

```bash
# Terminal 1: nyalakan "situs" lokal
python -m http.server 8000 -d scraper/tests/fixtures/toko

# Terminal 2:
python -m scraper uji scraper/contoh/produk_toko.yaml --detail      # cek selector
python -m scraper jalankan scraper/contoh/produk_toko.yaml -o hasil.xlsx
python -m scraper monitor scraper/contoh/produk_toko.yaml --laporan perubahan.md
```

Ubah salah satu harga di `scraper/tests/fixtures/toko/halaman-2.html` (sebaiknya di salinan
folder), jalankan `monitor` lagi, dan lihat perubahan harganya di `perubahan.md`.

`uji` juga bisa langsung membaca file HTML lokal tanpa server:

```bash
python -m scraper uji scraper/contoh/lowongan_kerja.yaml scraper/tests/fixtures/lain/lowongan.html
```

Contoh config yang tersedia di `scraper/contoh/`:

| File | Isi |
|---|---|
| `produk_toko.yaml` | Toko online umum: kartu produk, harga Rupiah, pagination, halaman detail, monitor harga |
| `lowongan_kerja.yaml` | Lowongan kerja: pola URL `?page={page}`, gaji min/maks, tanggal, tag |
| `buku_toscrape.yaml` | [books.toscrape.com](https://books.toscrape.com) (situs latihan scraping) |
| `produk_json_ld.yaml` | Data produk dari JSON-LD schema.org |
| `produk_next_data.yaml` | Data dari `<script id="__NEXT_DATA__">` (situs Next.js) |

---

## 2. Perintah

```bash
python -m scraper --help
python -m scraper <perintah> --help
```

### `jalankan` — scraping dan simpan hasil

```bash
python -m scraper jalankan CONFIG.yaml -o hasil.xlsx
python -m scraper jalankan CONFIG.yaml -o hasil.csv --max-items 100 --ringkasan ringkasan.json
python -m scraper jalankan CONFIG.yaml -o hasil.csv --lanjut     # lanjutkan run yang terputus
```

| Opsi | Arti |
|---|---|
| `-o, --output` | `.csv` (UTF-8 BOM, aman dibuka di Excel), `.xlsx`, `.json`, `.jsonl`. Default `output/<nama>.csv` |
| `--ringkasan FILE.json` | simpan ringkasan: jumlah item, halaman, duplikat, error, durasi |
| `--max-items N`, `--max-halaman N` | batasi run (menimpa nilai di config) |
| `--lanjut` | lanjutkan dari checkpoint `<output>.progress.jsonl` (lihat bagian 7) |
| `--jeda DETIK` | jeda antar-request (menimpa `delay` di config) |
| `--abaikan-robots` | abaikan robots.txt — **hanya** jika Anda punya izin pemilik situs |
| `--render js` | render JavaScript dengan Playwright (opsional, lihat bagian 9) |
| `-v` / `-q` | log lebih detail / hanya peringatan |

Kode keluar: `0` sukses, `1` error (config salah, tidak ada item sama sekali, dll.),
`130` dihentikan dengan Ctrl+C (progres tersimpan).

### `uji` — debug selector

```bash
python -m scraper uji CONFIG.yaml                      # pakai start_urls pertama
python -m scraper uji CONFIG.yaml https://situs/kategori?page=2 -n 5
python -m scraper uji CONFIG.yaml halaman_tersimpan.html --detail
python -m scraper uji CONFIG.yaml --simpan-html cek.html   # simpan HTML yang diterima
```

Contoh keluaran:

```
Sumber   : http://localhost:8000/halaman-1.html
Item     : 5 cocok dengan CSS 'div.produk-card', 1 dibuang karena field wajib kosong
Halaman berikut: http://localhost:8000/halaman-2.html

Item #1
  nama        : 'Sepatu Lari Ringan'
  harga       : 1250000   (mentah: 'Rp1.250.000')
  rating      : 4.8   (mentah: '★ 4,8')
  terjual     : 1200   (mentah: '1,2rb terjual')
```

"mentah" = teks asli sebelum diubah ke tipe data. Jika tidak ada item yang cocok, `uji`
memberi petunjuk (mis. halaman punya JSON-LD, `__NEXT_DATA__`, atau tampak dirender
JavaScript). `--simpan-html` berguna untuk melihat HTML yang *benar-benar* diterima scraper —
sering berbeda dengan yang tampil di browser.

### `monitor` — pantau harga / kompetitor

```bash
python -m scraper monitor CONFIG.yaml
python -m scraper monitor CONFIG.yaml --riwayat data/riwayat.sqlite --laporan laporan.md
python -m scraper monitor CONFIG.yaml --min-persen 2 --gagal-jika-berubah
```

Setiap run disimpan ke riwayat SQLite (default `output/riwayat_scraper.sqlite`) lalu dibandingkan
dengan run sebelumnya untuk config yang sama. Laporan berisi:

| `jenis` | Arti |
|---|---|
| `baru` | item yang sebelumnya tidak ada |
| `hilang` | item yang sekarang tidak ada |
| `harga_naik` / `harga_turun` | harga berubah (dengan `selisih` dan `persen`) |
| `berubah` | field lain di `monitor.track` berubah (mis. stok) |

| Opsi | Arti |
|---|---|
| `--laporan FILE` | `.csv`, `.xlsx` (sheet *perubahan* + *ringkasan*), `.json`, atau `.md`. Default `output/laporan/perubahan_<nama>_<waktu>.csv` |
| `--riwayat FILE.sqlite` | lokasi riwayat |
| `-o FILE` | simpan juga hasil lengkap run ini |
| `--min-persen X` | abaikan perubahan harga di bawah X% |
| `--simpan-run N` | simpan hanya N run terakhir di riwayat |
| `--gagal-jika-berubah` | keluar dengan kode `3` bila ada perubahan (untuk notifikasi CI) |

Run pertama hanya menyimpan acuan. Jika sebuah run gagal total (0 item), riwayat **tidak**
diperbarui agar run berikutnya tidak menganggap semua item "hilang". Jika ada error halaman
atau run dibatasi `max_items`, laporan memberi peringatan bahwa item "hilang" mungkin palsu.

Kunci item, field harga dan nama diatur di bagian `monitor:` config (lihat bagian 4). Jika tidak
diisi: kunci = field bertipe `url` pertama, harga = field bertipe `price_idr` pertama.

---

## 3. Menulis config langkah demi langkah

### Langkah 0 — cek izin

Buka `https://situs-target.com/robots.txt` dan baca Syarat & Ketentuan situs. Jika scraping
dilarang, berhenti di sini (lihat bagian 10). Cek juga apakah situs menyediakan API resmi
atau file ekspor — biasanya lebih mudah dan lebih sah.

### Langkah 1 — temukan selector item dengan DevTools

1. Buka halaman daftar (mis. hasil pencarian / kategori) di Chrome, Edge, atau Firefox.
2. Klik kanan pada **satu kartu produk** → **Inspect / Periksa** (atau tekan `F12`).
3. Di panel *Elements*, arahkan kursor ke baris-baris HTML; bagian halaman yang sesuai akan
   tersorot. Naik ke elemen yang membungkus **seluruh** kartu (biasanya `<div>`, `<li>`, atau
   `<article>` dengan class seperti `product-card`).
4. Catat nama tag dan class-nya, mis. `<div class="produk-card" data-sku="...">` → selector
   `div.produk-card`.
5. Uji selector: di panel *Elements* tekan `Ctrl+F` (Mac: `Cmd+F`) dan ketik `div.produk-card`.
   Di pojok terlihat jumlahnya, mis. `1 of 24` — harus sama dengan jumlah kartu di halaman.
   Alternatif: di tab *Console* ketik `document.querySelectorAll("div.produk-card").length`.

Cara menulis selector CSS yang paling sering dipakai:

| Selector | Artinya |
|---|---|
| `div.produk-card` | `<div>` dengan class `produk-card` |
| `.harga .jual` | elemen class `jual` di dalam elemen class `harga` |
| `h3 a` | `<a>` di dalam `<h3>` |
| `#judul` | elemen dengan `id="judul"` |
| `[data-testid="price"]` | elemen dengan atribut tersebut (sering paling stabil) |
| `li:nth-of-type(2)` | `<li>` kedua |
| `div#deskripsi + p` | `<p>` tepat setelah `div#deskripsi` |

Tips: pilih class yang bermakna (`product-price`, `data-testid=...`). Hindari class acak
hasil generator seperti `css-1x2y3z` atau `sc-aXZVg` — biasanya berubah tiap situs di-*deploy*.
Menu klik kanan → *Copy → Copy selector* bisa membantu, tetapi hasilnya sering terlalu
panjang/rapuh; sederhanakan.

### Langkah 2 — tentukan field

Di dalam kartu, lakukan hal yang sama untuk tiap data (nama, harga, link…). Selector field
ditulis **relatif terhadap kartu**.

```yaml
items: div.produk-card
fields:
  nama: h3.produk-nama                 # bentuk singkat = ambil teks
  harga:
    selector: span.harga-jual
    type: price_idr                    # "Rp1.250.000" -> 1250000
    required: true                     # kartu tanpa harga (iklan) dibuang
  link:
    selector: a.produk-link
    attr: href                         # ambil atribut, bukan teks
    type: url                          # relatif -> URL lengkap
  sku:
    attr: data-sku                     # tanpa selector = atribut kartu itu sendiri
```

### Langkah 3 — pagination

Cari tombol "Berikutnya / Next" dengan DevTools:

```yaml
pagination:
  next: nav.paginasi a.next      # selector link halaman berikutnya
  max_pages: 20
```

Atau, bila URL halaman berpola (`...?page=2`, `.../page/3`):

```yaml
pagination:
  url_pattern: https://situs.com/kategori?page={page}
  start: 1
  end: 30                  # opsional
  stop_when_empty: true    # berhenti saat halaman tidak punya item (atau 404)
```

### Langkah 4 — (opsional) halaman detail

```yaml
detail:
  link: link               # nama field yang berisi URL detail
  fields:
    deskripsi: div.deskripsi
    stok: {selector: td.stok, type: int, default: 0}
```

Setiap item = 1 request tambahan, jadi run lebih lama. Hanya pakai bila perlu.

### Langkah 5 — uji, lalu jalankan

```bash
python -m scraper uji toko_saya.yaml --detail
python -m scraper jalankan toko_saya.yaml -o hasil.xlsx --max-items 20   # coba kecil dulu
python -m scraper jalankan toko_saya.yaml -o hasil.xlsx
```

---

## 4. Referensi config

| Kunci | Keterangan |
|---|---|
| `name` | nama config (dipakai di riwayat monitor & nama file default). Default: nama file |
| `start_urls` | daftar URL awal (boleh path file HTML lokal) |
| `items` | selector CSS satu item |
| `json_ld`, `json_ld_type` | ambil item dari JSON-LD (default `@type` `Product`) |
| `json_script`, `json_items_path` | ambil item dari JSON di `<script>` (mis. `script#__NEXT_DATA__`) |
| `fields` | field yang diambil (lihat di bawah) |
| `pagination` | `next` **atau** `url_pattern` (+ `start`, `end`, `max_pages`, `stop_when_empty`) |
| `detail` | `link` (nama field URL), `fields`, opsional `json_ld: true` |
| `dedupe_by` | daftar field penentu duplikat. Default: field `url` pertama, atau semua field |
| `monitor` | `key` (kunci item), `price` (field harga), `label` (nama tampilan), `track` (field lain yang dipantau) |
| `headers`, `user_agent` | header HTTP tambahan / User-Agent |
| `delay` | jeda antar-request, angka atau `[min, maks]` acak. Default `[1, 3]` detik |
| `timeout`, `retries`, `backoff` | default 20 detik, 3 kali, 1 detik (jeda retry: 1, 2, 4, …) |
| `max_items`, `max_pages` | batas item / halaman daftar |
| `respect_robots` | default `true` |
| `render`, `render_wait` | `html` (default) atau `js` (Playwright) + selector yang ditunggu |

Kunci field:

| Kunci | Keterangan |
|---|---|
| `selector` | CSS relatif terhadap item. Kosong = elemen item itu sendiri |
| `attr` | `text` (default), `html`, atau nama atribut (`href`, `src`, `content`, `data-…`) |
| `path` | jalur JSON bertitik, mis. `offers.price` (lihat bagian 5) |
| `regex` | ambil bagian teks; grup pertama `( )` yang dipakai |
| `map` | ganti nilai, mis. `{One: 1, Two: 2}` atau `{Tersedia: ya}` |
| `type` | `text`, `int`, `float`, `price_idr`, `url`, `date` |
| `default` | nilai bila kosong |
| `required` | `true` = buang item bila field ini kosong |
| `multiple`, `join` | ambil semua elemen yang cocok lalu gabungkan (default `", "`) |
| `decimal` | paksa pemisah desimal `","` atau `"."` untuk `int`/`float` |
| `date_format` | format tanggal eksplisit, mis. `"%d/%m/%Y"` |

Contoh konversi tipe:

| Teks | `type` | Hasil |
|---|---|---|
| `Rp1.250.000` / `Rp 1.250.000,00` / `IDR 1,250,000` | `price_idr` | `1250000` |
| `Rp1,2 jt` / `Rp 15rb` | `price_idr` | `1200000` / `15000` |
| `1,2rb terjual` / `Stok: 25` | `int` | `1200` / `25` |
| `★ 4,8` / `£51.77` | `float` | `4.8` / `51.77` |
| `Diperbarui 3 November 2026` / `02/11/2026` | `date` | `2026-11-03` / `2026-11-02` |
| `kemarin` / `2 hari yang lalu` | `date` | dihitung dari tanggal hari ini |

Tanggal `dd/mm/yyyy` dibaca format Indonesia (hari dulu). Untuk format lain gunakan `date_format`.

---

## 5. Data JSON tersembunyi (JSON-LD & `__NEXT_DATA__`)

Banyak marketplace dan toko modern menaruh data lengkap dalam JSON di dalam HTML — biasanya
lebih rapi dan lebih stabil daripada tampilan. Cek dengan **View Page Source** (`Ctrl+U`) lalu
cari `ld+json` atau `__NEXT_DATA__`.

**JSON-LD** (schema.org):

```yaml
json_ld: true
json_ld_type: Product        # dicari di mana saja: @graph, ItemList, dll.
fields:
  nama: name                 # di mode JSON, teks singkat = jalur JSON
  harga: {path: offers.price, type: price_idr}
  rating: {path: aggregateRating.ratingValue, type: float}
```

**`__NEXT_DATA__` / state JSON lain**:

```yaml
json_script: 'script#__NEXT_DATA__'
json_items_path: props.pageProps.initialState.search.products
fields:
  nama: name
  harga: {path: price.value, type: price_idr}
  kota: shop.city
```

`json_script` juga menangani bentuk `window.__INITIAL_STATE__ = {...};`.

Aturan jalur (*dotted path*): `a.b.c` masuk ke kunci bertingkat; `items.0.name` atau
`items[0].name` = elemen pertama list; `items.*.name` = ambil `name` dari **semua** elemen;
bila bertemu list tanpa nomor, elemen pertama yang dipakai (mis. `offers.price` saat `offers`
berupa list).

Di mode HTML biasa, satu field juga bisa membaca JSON dari `<script>` tertentu:

```yaml
fields:
  merek: {selector: 'script[type="application/ld+json"]', path: brand.name}
```

Dan halaman detail bisa memakai JSON-LD: `detail: {link: link, json_ld: true, fields: {...}}`.

---

## 6. Penjadwalan (cron & GitHub Actions)

`monitor` dirancang dijalankan berkala oleh penjadwal — scraper ini sendiri tidak berjalan terus.

### cron (Linux / macOS)

`crontab -e`, lalu tambahkan (setiap hari pukul 07:15):

```cron
15 7 * * * cd /path/ke/otomasi-kit && .venv/bin/python -m scraper -q monitor scraper/contoh/produk_toko.yaml --riwayat data/riwayat.sqlite --laporan data/laporan/harga_$(date +\%Y\%m\%d).md >> data/monitor.log 2>&1
```

Ganti `scraper/contoh/produk_toko.yaml` dengan config Anda sendiri (contoh itu menunjuk ke
server lokal). Di crontab, `%` harus ditulis `\%`. Di Windows gunakan **Task Scheduler** dengan perintah
yang sama.

### GitHub Actions

Simpan sebagai `.github/workflows/monitor-harga.yml`. Riwayat SQLite di-commit kembali ke repo
agar run berikutnya bisa membandingkan, dan laporan Markdown tampil di ringkasan job:

```yaml
name: Monitor harga
on:
  schedule:
    - cron: "15 0 * * *"      # 00:15 UTC = 07:15 WIB
  workflow_dispatch: {}
permissions:
  contents: write
jobs:
  monitor:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
      - name: Jalankan monitor
        run: |
          python -m scraper monitor configs/toko_kompetitor.yaml \
            --riwayat data/riwayat.sqlite --laporan laporan.md --simpan-run 60
          cat laporan.md >> "$GITHUB_STEP_SUMMARY"
      - uses: actions/upload-artifact@v4
        with:
          name: laporan-harga
          path: laporan.md
      - name: Simpan riwayat
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add -f data/riwayat.sqlite
          git commit -m "Update riwayat monitor harga" || echo "Tidak ada perubahan"
          git push
```

Catatan: IP GitHub Actions (data center) lebih sering diblokir situs dibanding koneksi rumah.
Untuk notifikasi, tambahkan `--gagal-jika-berubah` — job akan "gagal" (kode 3) saat ada
perubahan sehingga GitHub mengirim email.

---

## 7. Melanjutkan run yang terputus, duplikat, dan batas

- Selama `jalankan` berjalan, setiap halaman daftar yang selesai dicatat di
  `<output>.progress.jsonl`. Jika run terhenti (Ctrl+C, listrik mati, koneksi putus),
  jalankan perintah yang sama dengan `--lanjut`: halaman yang sudah selesai tidak diambil ulang.
  Checkpoint dihapus otomatis setelah run selesai.
- Item yang sama (menurut `dedupe_by`) hanya disimpan sekali, termasuk "produk unggulan" yang
  muncul di banyak halaman. Halaman detail duplikat tidak diambil.
- Halaman yang gagal (404, timeout setelah retry) dicatat sebagai error di ringkasan; scraping
  tetap lanjut ke halaman lain.

---

## 8. Masalah umum

| Gejala | Kemungkinan & solusi |
|---|---|
| `0 cocok dengan CSS ...` | selector salah, atau konten dibuat JavaScript. Jalankan `uji --simpan-html cek.html` dan cari teks produk di file itu. Jika tidak ada → coba JSON-LD / `__NEXT_DATA__` / `--render js` |
| field selalu kosong | selector salah atau datanya di atribut (`attr: content`, `attr: data-price`) |
| `HTTP 403` | situs memblokir bot. Hormati keputusan itu; jangan memaksa dengan mengakali proteksi |
| `HTTP 429` | terlalu cepat. Naikkan `delay`, mis. `[5, 10]` |
| `Diblokir robots.txt` | pemilik situs tidak mengizinkan halaman tersebut di-scrape |
| harga salah baca | pastikan `type: price_idr`; untuk format khusus pakai `regex` atau `decimal` |
| angka `1.234` terbaca 1234 padahal maksudnya 1,234 | tambahkan `decimal: "."` |

---

## 9. Situs berbasis JavaScript, Tokopedia/Shopee, dan Playwright

Marketplace besar seperti **Tokopedia, Shopee, Lazada, Bukalapak** umumnya menampilkan produk
dengan JavaScript, memakai proteksi anti-bot (captcha, rate limit, fingerprinting), dan dalam
Syarat & Ketentuannya **melarang** pengambilan data otomatis. Scraper berbasis HTML seperti ini
biasanya hanya akan menerima halaman kosong atau diblokir. Pilihan yang lebih tepat: API/program
afiliasi/mitra resmi, fitur ekspor di dashboard penjual (untuk data toko Anda sendiri), atau
penyedia data berlisensi.

Untuk situs lain yang dirender JavaScript dan **mengizinkan** scraping, ada mode opsional
`--render js` (atau `render: js` di config) yang membuka halaman di Chromium tanpa tampilan
melalui [Playwright](https://playwright.dev/python/):

```bash
pip install playwright
playwright install chromium
python -m scraper jalankan config.yaml --render js -o hasil.csv
```

`render_wait: "div.produk-card"` membuat scraper menunggu elemen tersebut muncul. Mode ini jauh
lebih lambat dan berat; coba dulu JSON-LD/`__NEXT_DATA__` (bagian 5). Playwright **tidak**
termasuk di `requirements.txt` — pasang hanya jika butuh.

---

## 10. Etika & hukum — wajib dibaca

Scraper ini alat; tanggung jawab penggunaannya ada pada Anda.

1. **Patuhi Syarat & Ketentuan (ToS) situs.** Jika ToS melarang scraping, jangan lakukan.
   `robots.txt` dihormati secara default; `--abaikan-robots` hanya untuk situs milik sendiri
   atau dengan izin tertulis pemilik situs.
2. **Jangan mengambil data pribadi.** UU No. 27 Tahun 2022 tentang **Pelindungan Data Pribadi
   (UU PDP)** mengatur pemrosesan data pribadi, termasuk yang tampil publik — nama, nomor HP,
   email, alamat, foto, NIK, data pelamar kerja, ulasan yang mengidentifikasi orang, dsb.
   Pengumpulan tanpa dasar hukum dapat dikenai sanksi administratif hingga pidana. Batasi diri
   pada data non-pribadi seperti nama produk, harga, stok, dan informasi lowongan.
3. **Jangan membebani server.** Gunakan `delay` yang wajar (default 1–3 detik), batasi
   `max_pages`, dan jadwalkan di luar jam sibuk. Scraping agresif dapat dianggap gangguan
   sistem elektronik (UU ITE).
4. **Jangan menembus proteksi**: login orang lain, paywall, captcha, atau pemblokiran IP.
5. **Hak cipta & database**: deskripsi, foto, dan ulasan bisa dilindungi hak cipta. Gunakan
   data untuk analisis internal; jangan menerbitkan ulang konten orang lain begitu saja.
6. Identifikasi diri dengan jujur lewat `user_agent` (default:
   `otomasi-kit-scraper/0.1`) dan sediakan kontak bila scraping rutin.

Dokumen ini bukan nasihat hukum. Untuk penggunaan komersial, konsultasikan dengan ahli hukum.

---

## 11. Untuk pengembang

Struktur modul:

| File | Isi |
|---|---|
| `config.py` | baca & validasi YAML (pesan error bahasa Indonesia, saran salah ketik) |
| `fetch.py` | HTTP: jeda sopan, retry/backoff, `Retry-After`, robots.txt, file lokal, Playwright opsional |
| `extract.py` | selector CSS, JSON-LD, JSON script, dotted path, konversi tipe |
| `paginate.py` | link "next" & pola URL |
| `runner.py` | orkestrasi crawl, detail, dedupe, batas, checkpoint |
| `output.py` | CSV / XLSX / JSON / JSONL |
| `monitor.py` | riwayat SQLite, perbandingan, laporan |
| `cli.py` | perintah `jalankan`, `monitor`, `uji` |

Tes memakai toko palsu di `tests/fixtures/` dan `http.server` lokal (tanpa internet):

```bash
.venv/bin/pytest scraper
.venv/bin/ruff check scraper
```
