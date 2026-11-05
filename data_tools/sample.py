"""Generate realistic (deliberately messy) demo data so every command can be tried at once."""

from __future__ import annotations

import csv
import datetime as dt
import logging
import random
from pathlib import Path

from data_tools.formatting import BULAN, format_number

log = logging.getLogger("data_tools")

START = dt.date(2026, 8, 1)
END = dt.date(2026, 10, 31)

PRODUCTS = [
    # name, category, price
    ("Kopi Bubuk Robusta 250g", "Minuman", 35000),
    ("Kopi Arabika Gayo 200g", "Minuman", 68000),
    ("Teh Melati Celup isi 25", "Minuman", 9500),
    ("Gula Aren Cair 500ml", "Bahan Pokok", 42000),
    ("Beras Pandan Wangi 5kg", "Bahan Pokok", 78000),
    ("Minyak Goreng 2L", "Bahan Pokok", 36500),
    ("Keripik Singkong Balado", "Makanan Ringan", 15000),
    ("Kerupuk Udang 500g", "Makanan Ringan", 48000),
    ("Emping Melinjo 250g", "Makanan Ringan", 32000),
    ("Kacang Mete Panggang 250g", "Makanan Ringan", 85000),
    ("Sambal Bawang Botol", "Bumbu", 25000),
    ("Bumbu Rendang Instan", "Bumbu", 12500),
    ("Rendang Daging Kemasan", "Oleh-oleh", 95000),
    ("Dodol Garut Original", "Oleh-oleh", 22000),
    ("Bakpia Kacang Hijau isi 20", "Oleh-oleh", 45000),
    ("Madu Hutan 250ml", "Minuman", 120000),
    ("Kue Bolu Pandan – Premium", "Oleh-oleh", 55000),
]
WEIGHTS = [9, 4, 8, 5, 6, 7, 10, 4, 3, 2, 8, 6, 2, 5, 4, 2, 3]
KASIR = ["Rina", "Dewi", "Agus", "Fajar"]
FIRST = ["Budi", "Siti", "Agus", "Dewi", "Rudi", "Ani", "Joko", "Sri", "Andi", "Rina", "Bayu",
         "Putri", "Hendra", "Lestari", "Wahyu", "Fitri", "Eko", "Maya", "Dimas", "Indah"]
LAST = ["Santoso", "Wijaya", "Pratama", "Lestari", "Saputra", "Hidayat", "Nugroho", "Kusuma",
        "Rahmawati", "Setiawan", "Siregar", "Harahap", "Purnomo", "Wibowo", "Utami"]
CITIES = ["Bandung", "Jakarta Selatan", "Surabaya", "Yogyakarta", "Semarang", "Medan", "Bekasi",
          "Depok", "Bogor", "Malang"]
STREETS = ["Jl. Merdeka", "Jl. Sudirman", "Jl. Diponegoro", "Jl. Ahmad Yani", "Jl. Gatot Subroto",
           "Jl. Pahlawan", "Jl. Siliwangi", "Jl. Gajah Mada", "Jl. Veteran", "Jl. Cendana"]


def _rupiah_text(rng: random.Random, value: int) -> str:
    style = rng.random()
    if style < 0.4:
        return f"Rp {format_number(value)}"
    if style < 0.6:
        return f"Rp{format_number(value)},-"
    if style < 0.8:
        return format_number(value)
    return str(value)


def _date_text(rng: random.Random, d: dt.date) -> str:
    style = rng.random()
    if style < 0.45:
        return d.strftime("%d/%m/%Y")
    if style < 0.6:
        return f"{d.day} {BULAN[d.month - 1]} {d.year}"
    if style < 0.75:
        return f"{d.day} {BULAN[d.month - 1][:3]} {d.year}"
    if style < 0.9:
        return d.strftime("%d-%m-%Y")
    return d.isoformat()


def _name(rng: random.Random) -> str:
    return f"{rng.choice(FIRST)} {rng.choice(LAST)}"


def _transactions(rng: random.Random, prefix: str, per_day: tuple[int, int], customers: int):
    """Yield dicts: one row per item; a transaction has 1-3 items."""
    day = START
    counter = 0
    while day <= END:
        weekend = day.weekday() >= 5
        n = rng.randint(*per_day) + (3 if weekend else 0)
        if day.month == 10:
            n += 2  # growth in October
        for _ in range(n):
            counter += 1
            trx = f"{prefix}-{day.strftime('%y%m%d')}-{counter:04d}"
            cust = f"{rng.randint(1, customers):04d}"
            kasir = rng.choice(KASIR)
            k = rng.choice([1, 1, 1, 2, 2, 3])
            chosen: list[tuple[str, str, int]] = []
            while len(chosen) < k:
                pick = rng.choices(PRODUCTS, weights=WEIGHTS)[0]
                if pick not in chosen:
                    chosen.append(pick)
            for product, category, price in chosen:
                qty = rng.choice([1, 1, 1, 2, 2, 3, 4, 5]) if price < 50000 else rng.choice([1, 1, 2])
                yield {
                    "trx": trx, "date": day, "product": product, "category": category,
                    "qty": qty, "price": price, "total": qty * price, "customer": cust,
                    "kasir": kasir,
                }
        day += dt.timedelta(days=1)


def make_sales_files(folder: Path, rng: random.Random) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    files = []

    # 1) Semicolon CSV, Windows-1252 encoding, Indonesian number formats, duplicates
    header = ["No. Transaksi", "Tanggal", "Nama Produk", "Kategori", "Qty", "Harga Satuan",
              "Total", "ID Pelanggan", "Kasir"]
    rows = []
    for t in _transactions(rng, "PST", (4, 8), 120):
        rows.append([
            t["trx"], _date_text(rng, t["date"]),
            ("  " + t["product"] + " ") if rng.random() < 0.1 else t["product"],
            t["category"], str(t["qty"]), _rupiah_text(rng, t["price"]),
            _rupiah_text(rng, t["total"]), t["customer"], t["kasir"],
        ])
    for i in rng.sample(range(len(rows)), 12):
        rows.insert(i, list(rows[i]))  # exact duplicates
    for i in rng.sample(range(len(rows)), 4):
        rows.insert(i, [""] * len(header))  # empty rows
    path = folder / "penjualan_toko_pusat.csv"
    with path.open("w", encoding="cp1252", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(header)
        w.writerows(rows)
    files.append(path)

    # 2) Excel with a title block, mixed real dates/text dates, an empty column
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Penjualan"
    ws["A1"] = "LAPORAN PENJUALAN CABANG BANDUNG"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = "Periode: Agustus - Oktober 2026"
    ws.append([])
    ws.append(["NO. TRANSAKSI", "TANGGAL", "NAMA PRODUK", "KATEGORI", "QTY", "HARGA SATUAN",
               "TOTAL", "Catatan", "ID PELANGGAN", "KASIR"])
    for cell in ws[4]:
        cell.font = Font(bold=True)
    for count, t in enumerate(_transactions(rng, "BDG", (2, 6), 80), start=1):
        date_value = t["date"] if rng.random() < 0.7 else _date_text(rng, t["date"])
        if isinstance(date_value, dt.date):
            date_value = dt.datetime(t["date"].year, t["date"].month, t["date"].day)
        price = t["price"] if rng.random() < 0.7 else _rupiah_text(rng, t["price"])
        row = [t["trx"], date_value, t["product"].upper() if rng.random() < 0.05 else t["product"],
               t["category"], t["qty"], price, t["total"], None, t["customer"], t["kasir"]]
        ws.append(row)
        if count % 97 == 0:
            ws.append(row)  # duplicate
        if count % 151 == 0:
            ws.append([None] * 10)
    notes = wb.create_sheet("Catatan")
    notes.append(["Keterangan"])
    notes.append(["Data diekspor dari mesin kasir cabang Bandung."])
    path = folder / "penjualan_cabang_bandung.xlsx"
    wb.save(path)
    files.append(path)

    # 3) Comma CSV, UTF-8 with BOM, "rb/jt" style numbers, ISO and text dates
    header = ["no_transaksi", "tanggal", "nama produk", "kategori", "qty", "harga satuan", "total",
              "id pelanggan", "kasir"]
    path = folder / "penjualan_online.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for t in _transactions(rng, "ONL", (1, 4), 150):
            price = t["price"]
            price_text = f"{price // 1000}rb" if price % 1000 == 0 and rng.random() < 0.3 else (
                f"Rp {format_number(price)}")
            w.writerow([t["trx"], _date_text(rng, t["date"]), t["product"], t["category"],
                        t["qty"], price_text, f"Rp {format_number(t['total'])}", t["customer"],
                        "Online"])
    files.append(path)
    return files


def make_customers(path: Path, rng: random.Random, n: int = 150) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ID Pelanggan", "Nama Pelanggan", "No HP", "Kota", "Member Sejak"])
        for i in range(1, n + 1):
            joined = dt.date(2024, 1, 1) + dt.timedelta(days=rng.randint(0, 900))
            w.writerow([f"{i:04d}", _name(rng), (f"08{rng.choice([12, 13, 21, 52, 57, 77])}"
                        f"{rng.randint(10000000, 99999999)}"), rng.choice(CITIES),
                        joined.strftime("%d/%m/%Y")])
    return path


# --------------------------------------------------------------------------- documents


def make_document_files(folder: Path, rng: random.Random) -> dict[str, Path]:
    from openpyxl import Workbook

    folder.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}

    # Invoice: several rows (line items) per invoice number
    wb = Workbook()
    ws = wb.active
    ws.title = "Invoice"
    ws.append(["Nomor Invoice", "Tanggal", "Jatuh Tempo", "Nama Pelanggan", "Alamat Pelanggan",
               "Telepon", "Item", "Qty", "Harga", "Diskon", "Catatan"])
    for i in range(1, 9):
        date = dt.datetime(2026, 11, rng.randint(1, 5))
        customer = rng.choice(["CV Maju Jaya", "PT Sinar Abadi", "Toko Berkah", "UD Sejahtera",
                               "Warung Bu Tini", "Koperasi Karyawan Sentosa"])
        address = f"{rng.choice(STREETS)} No. {rng.randint(1, 200)}, {rng.choice(CITIES)}"
        phone = f"08{rng.randint(1100000000, 9999999999)}"
        for product, _cat, price in rng.sample(PRODUCTS, k=rng.randint(2, 5)):
            ws.append([f"INV/2026/11/{i:03d}", date, date + dt.timedelta(days=14), customer,
                       address, phone, product, rng.randint(2, 40), price,
                       rng.choice([0, 0, 0, 5000, 10000]),
                       "Terima kasih atas kepercayaan Anda." if i % 3 == 0 else None])
    out["invoice"] = folder / "invoice.xlsx"
    wb.save(out["invoice"])

    # Certificates
    path = folder / "sertifikat.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Nomor Sertifikat", "Nama", "Peran", "Acara", "Tanggal", "Tempat"])
        for i in range(1, 11):
            w.writerow([f"{i:03d}/PELATIHAN/XI/2026", _name(rng),
                        "Pemateri" if i == 1 else "Peserta",
                        "Pelatihan Digital Marketing untuk UMKM", "5 November 2026", "Bandung"])
    out["sertifikat"] = path

    # Letters + body text with placeholders
    wb = Workbook()
    ws = wb.active
    ws.append(["Nomor Surat", "Tanggal", "Nama", "Jabatan", "Instansi", "Alamat", "Perihal",
               "Tanggal Acara", "Waktu", "Tempat"])
    for i in range(1, 7):
        ws.append([f"{i:03d}/SR/XI/2026", dt.datetime(2026, 11, 5), _name(rng),
                   rng.choice(["Pemilik", "Manajer", "Kepala Toko", "Direktur"]),
                   rng.choice(["Toko Berkah", "CV Maju Jaya", "UD Sejahtera", "PT Sinar Abadi"]),
                   f"{rng.choice(STREETS)} No. {rng.randint(1, 120)}, {rng.choice(CITIES)}",
                   "Undangan Temu Mitra 2026", dt.datetime(2026, 11, 20),
                   "09.00 – 12.00 WIB", "Hotel Savoy Homann, Bandung"])
    out["surat"] = folder / "surat.xlsx"
    wb.save(out["surat"])
    body = (
        "Dengan hormat,\n\n"
        "Bersama surat ini kami mengundang Bapak/Ibu {{nama}} selaku {{jabatan}} {{instansi}} "
        "untuk hadir dalam acara Temu Mitra 2026 yang akan diselenggarakan pada:\n\n"
        "Hari/tanggal: {{tanggal_acara|hari}}\n"
        "Waktu: {{waktu}}\n"
        "Tempat: {{tempat}}\n\n"
        "Acara ini membahas rencana kerja sama tahun depan serta program diskon khusus mitra. "
        "Mengingat pentingnya acara tersebut, kami sangat mengharapkan kehadiran Bapak/Ibu.\n\n"
        "Demikian undangan ini kami sampaikan. Atas perhatian dan kehadirannya kami ucapkan "
        "terima kasih."
    )
    out["isi_surat"] = folder / "isi_surat.txt"
    out["isi_surat"].write_text(body, encoding="utf-8")

    # Payslips
    wb = Workbook()
    ws = wb.active
    ws.append(["NIK", "Nama", "Jabatan", "Departemen", "Periode", "Tanggal Masuk", "Gaji Pokok",
               "Tunjangan Jabatan", "Tunjangan Transport", "Tunjangan Makan", "Lembur",
               "Potongan BPJS Kesehatan", "Potongan BPJS Ketenagakerjaan", "Potongan PPh21",
               "Potongan Kasbon", "Bank", "No Rekening"])
    jobs = [("Kasir", "Operasional", 3800000), ("Staf Gudang", "Logistik", 3900000),
            ("Admin Keuangan", "Keuangan", 5200000), ("Supervisor Toko", "Operasional", 6500000),
            ("Kurir", "Logistik", 3600000), ("Staf Pemasaran", "Pemasaran", 5000000)]
    for i in range(1, 9):
        job, dept, base = rng.choice(jobs)
        ws.append([f"0{rng.randint(10000, 99999)}{i:02d}", _name(rng), job, dept, "November 2026",
                   dt.datetime(rng.randint(2019, 2025), rng.randint(1, 12), rng.randint(1, 28)),
                   base, 750000 if "Supervisor" in job else 0, 400000, 25000 * 22,
                   rng.choice([0, 150000, 275000, 410000]), round(base * 0.01),
                   round(base * 0.02), rng.choice([0, 0, 45000, 87500]),
                   rng.choice([0, 0, 0, 250000]), rng.choice(["BCA", "BRI", "Mandiri", "BNI"]),
                   f"0{rng.randint(100000000, 999999999)}"])
    out["slip-gaji"] = folder / "slip_gaji.xlsx"
    wb.save(out["slip-gaji"])

    out["profil"] = folder / "profil.yaml"
    out["profil"].write_text(
        "# Data perusahaan untuk kop surat, invoice, sertifikat dan slip gaji\n"
        "nama: Toko Sumber Rejeki\n"
        "alamat: Jl. Merdeka No. 45, Bandung 40111\n"
        "telepon: 022-1234567\n"
        "email: halo@sumberrejeki.co.id\n"
        "npwp: 01.234.567.8-901.000\n"
        "kota: Bandung\n"
        "logo: logo.png\n"
        "rekening: BCA 1234567890 a.n. Toko Sumber Rejeki\n"
        "penandatangan: Budi Santoso\n"
        "jabatan: Direktur\n"
        "ppn: 11            # persen; 0 jika tidak memungut PPN\n"
        "warna: '#1F4E78'\n",
        encoding="utf-8",
    )
    out["logo"] = make_logo(folder / "logo.png")
    out["template_docx"] = make_custom_docx(folder / "template_surat_keterangan.docx")
    return out


def make_logo(path: Path) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((10, 10, 290, 290), fill=(31, 78, 120, 255))
    draw.ellipse((40, 40, 260, 260), outline=(255, 255, 255, 255), width=8)
    font = ImageFont.load_default(size=110)
    draw.text((150, 150), "SR", fill=(255, 255, 255, 255), font=font, anchor="mm")
    img.save(path)
    return path


def make_custom_docx(path: Path) -> Path:
    """A user-style DOCX template with ``{{placeholders}}`` (some split across runs)."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    doc = Document()
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run("SURAT KETERANGAN KERJA")
    run.bold, run.font.size = True, Pt(16)
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.add_run("Nomor: SK/{{nik}}/XI/2026")
    doc.add_paragraph("Yang bertanda tangan di bawah ini menerangkan bahwa:")
    table = doc.add_table(rows=0, cols=2)
    table.style = "Table Grid"
    for label, key in (("Nama", "{{nama}}"), ("NIK", "{{nik}}"), ("Jabatan", "{{jabatan}}"),
                       ("Departemen", "{{departemen}}")):
        cells = table.add_row().cells
        cells[0].text = label
        cells[1].paragraphs[0].add_run(key).bold = True
    p = doc.add_paragraph()
    p.add_run("adalah benar karyawan kami sejak tanggal ")
    p.add_run("{{tanggal_").bold = True  # placeholder deliberately split across runs
    p.add_run("masuk}}").bold = True
    p.add_run(" sampai dengan saat ini, dengan gaji pokok sebesar ")
    p.add_run("{{gaji_pokok|rupiah}}").italic = True
    p.add_run(" ({{gaji_pokok|terbilang}} rupiah).")
    doc.add_paragraph(
        "Demikian surat keterangan ini dibuat untuk dipergunakan sebagaimana mestinya."
    )
    p = doc.add_paragraph("Bandung, {{hari_ini}}")
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p = doc.add_paragraph("\n\n\nBudi Santoso\nDirektur")
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    doc.save(path)
    return path


# --------------------------------------------------------------------------- images


def make_images(folder: Path, rng: random.Random) -> list[Path]:
    from PIL import Image, ImageDraw, ImageFont

    folder.mkdir(parents=True, exist_ok=True)
    names = ["IMG_20260805_093011.jpg", "IMG_20260805_093047.jpg", "DSC 0012.JPG",
             "foto produk (1).png", "foto produk (2).png", "WhatsApp Image 2026-10-01.jpeg"]
    paths = []
    for i, name in enumerate(names):
        w, h = rng.choice([(2400, 1600), (1600, 2400), (3000, 2000), (1200, 1200)])
        top = (rng.randint(30, 120), rng.randint(60, 160), rng.randint(120, 220))
        bottom = (rng.randint(180, 255), rng.randint(150, 230), rng.randint(60, 140))
        img = Image.new("RGB", (w, h))
        draw = ImageDraw.Draw(img)
        for y in range(0, h, 4):
            f = y / h
            color = tuple(int(top[c] + (bottom[c] - top[c]) * f) for c in range(3))
            draw.rectangle((0, y, w, y + 4), fill=color)
        for _ in range(6):
            r = rng.randint(min(w, h) // 12, min(w, h) // 5)
            x, y = rng.randint(0, w), rng.randint(0, h)
            draw.ellipse((x - r, y - r, x + r, y + r),
                         fill=(rng.randint(200, 255), rng.randint(200, 255), rng.randint(200, 255)))
        font = ImageFont.load_default(size=max(40, w // 14))
        draw.text((w // 2, h // 2), f"Produk {i + 1}", fill=(20, 20, 20), font=font, anchor="mm")
        path = folder / name
        if path.suffix.lower() == ".png":
            img.convert("RGBA").save(path)
        else:
            img.save(path, quality=95)
        paths.append(path)
    return paths


def generate_samples(folder: str | Path, seed: int = 2026) -> dict[str, list[Path]]:
    """Create ``penjualan/``, ``pelanggan.csv``, ``dokumen/`` and ``foto/`` under ``folder``."""
    folder = Path(folder)
    rng = random.Random(seed)
    created = {
        "penjualan": make_sales_files(folder / "penjualan", rng),
        "pelanggan": [make_customers(folder / "pelanggan.csv", rng)],
        "dokumen": list(make_document_files(folder / "dokumen", rng).values()),
        "foto": make_images(folder / "foto", rng),
    }
    return created
