# otomasi-kit

Kumpulan otomasi siap pakai untuk bisnis kecil, pekerja kantor, dan kreator konten di Indonesia.
Semua alat dijalankan dari terminal dengan Python.

| Paket | Fungsi | Dokumentasi |
|---|---|---|
| `data_tools` | Bersihkan & gabung Excel/CSV, laporan penjualan + grafik, dokumen massal (invoice, sertifikat, surat, slip gaji), rename/konversi file & gambar | [data_tools/README.md](data_tools/README.md) |
| `konten` | Generator konten sosmed, artikel SEO, deskripsi produk marketplace, soal/kuis — pakai Claude API atau mode template offline | [konten/README.md](konten/README.md) |
| `scraper` | Scraper web berbasis file YAML (tanpa coding) + pemantau perubahan harga | [scraper/README.md](scraper/README.md) |
| `devtools` | Backup & sinkronisasi folder, pembuat template proyek (API, web, bot Telegram, CLI) | [devtools/README.md](devtools/README.md) |
| `.github/workflows` | CI otomatis, jadwal konten mingguan, jadwal scraper harian, backup, cek keamanan dependensi | [devtools/README.md](devtools/README.md) |

## Instalasi

Butuh Python 3.10 atau lebih baru.

```bash
git clone https://github.com/YusfianB/otomasi-kit.git
cd otomasi-kit
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Coba cepat

```bash
python -m data_tools --help
python -m konten --help
python -m scraper --help
python -m devtools --help
```

Fitur AI di paket `konten` memakai Claude API. Isi `ANTHROPIC_API_KEY` (dari
https://console.anthropic.com) agar hasilnya ditulis AI; tanpa key, otomatis memakai mode template.

```bash
export ANTHROPIC_API_KEY="sk-ant-..."   # Windows PowerShell: $env:ANTHROPIC_API_KEY="sk-ant-..."
```

## Pengembangan

```bash
pip install -r requirements-dev.txt
ruff check .
pytest
```
