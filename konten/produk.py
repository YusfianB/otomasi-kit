"""Marketplace product copy writer (subcommand ``produk``)."""

from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Any

from konten import templates_id as T
from konten.common import (
    Item,
    KontenError,
    format_rupiah,
    info,
    read_table,
    run_batch,
    slugify,
    split_list,
    stable_seed,
    truncate,
    ucfirst,
    write_excel,
)
from konten.llm import STR, LLMClient, obj, str_list

LIMITS = {"judul_shopee": 120, "judul_tokopedia": 70, "deskripsi": 2000, "promo_wa": 500}
TAGS_RANGE = (5, 10)

COLUMN_ALIASES = {
    "nama": ["nama", "nama produk", "produk", "name", "product", "product name", "judul",
             "nama barang"],
    "kategori": ["kategori", "category", "jenis", "kategori produk"],
    "harga": ["harga", "price", "harga jual", "harga (rp)"],
    "fitur": ["fitur", "spesifikasi", "fitur/spesifikasi", "features", "feature", "keunggulan",
              "spec", "specs", "deskripsi singkat", "detail"],
    "bahan": ["bahan", "material", "bahan baku"],
    "ukuran": ["ukuran", "size", "dimensi", "dimension"],
    "warna": ["warna", "color", "colour", "varian warna"],
    "merek": ["merek", "merk", "brand"],
    "berat": ["berat", "weight", "berat (gram)"],
    "isi_paket": ["isi paket", "kelengkapan", "isi"],
}

NEW_COLUMNS = ["judul_shopee", "judul_tokopedia", "poin_fitur", "deskripsi", "tag_seo",
               "promo_wa", "engine"]


def _norm(col: str) -> str:
    return re.sub(r"[\s_]+", " ", col.strip().lower())


def map_columns(columns: list[str]) -> dict[str, list[str]]:
    """Map canonical field -> list of original column names."""
    mapping: dict[str, list[str]] = {}
    for col in columns:
        n = _norm(col)
        for field, aliases in COLUMN_ALIASES.items():
            if n in aliases:
                mapping.setdefault(field, []).append(col)
                break
    return mapping


def row_to_product(row: dict[str, Any], mapping: dict[str, list[str]]) -> dict[str, Any]:
    used = {c for cols in mapping.values() for c in cols}
    prod: dict[str, Any] = {}
    for field, cols in mapping.items():
        vals = [str(row[c]).strip() for c in cols if str(row.get(c, "")).strip()]
        if field == "fitur":
            prod[field] = [f for v in vals for f in split_list(v)]
        else:
            prod[field] = "; ".join(vals)
    prod["lainnya"] = {c: str(v).strip() for c, v in row.items()
                       if c not in used and str(v).strip()}
    return prod


# --------------------------------------------------------------------------- template engine


def _cap_words(text: str) -> str:
    return " ".join(w[:1].upper() + w[1:] if w.islower() else w for w in text.split())


def _join_unique(parts: list[str], limit: int) -> str:
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        all_words = part.split()
        words = [w for w in all_words if w.lower().strip(",") not in seen]
        # Skip parts that are mostly repeats (avoids leftovers like "Pilihan" or "Steel").
        if not words or (out and len(words) * 2 <= len(all_words)):
            continue
        squashed = "".join(out).lower().replace(" ", "")
        if out and "".join(words).lower() in squashed:  # "500 ml" vs "500ml"
            continue
        candidate = " ".join(out + words)
        if len(candidate) > limit:
            if not out:
                return truncate(candidate, limit)
            continue
        out.extend(words)
        seen.update(w.lower().strip(",") for w in words)
    return " ".join(out)


def _size_summary(ukuran: str) -> str:
    sizes = split_list(ukuran.replace("/", ","))
    if len(sizes) > 1 and all(len(s) <= 4 for s in sizes):
        return f"{sizes[0]}-{sizes[-1]}"
    return ukuran if len(ukuran) <= 20 else ""


CLOTHING_WORDS = ("kaos", "baju", "kemeja", "hijab", "kerudung", "celana", "dress", "gamis",
                  "pakaian", "jaket", "hoodie", "piyama", "kaus", "shirt", "sweater", "rok")
APPAREL_ONLY_KEYS = ("katun", "cotton", "elastis", "stretch")


def _benefit(feature: str, lang: str, kategori: str = "") -> str:
    low = feature.lower()
    apparel = any(w in kategori.lower() for w in CLOTHING_WORDS)
    for keys, benefit in (T.BENEFIT_ID if lang == "id" else T.BENEFIT_EN):
        if any(k in low for k in keys) and benefit not in low:
            if not apparel and any(k in APPAREL_ONLY_KEYS for k in keys):
                continue
            return benefit
    return ""


FOOD_WORDS = ("camilan", "makanan", "minuman", "keripik", "kue", "snack", "sambal", "kopi",
              "teh", "frozen", "bumbu", "cokelat", "coklat", "madu")


def _is_food(p: dict[str, Any], kategori: str) -> bool:
    text = f"{kategori} {p.get('nama', '')}".lower()
    return any(w in text for w in FOOD_WORDS)


def make_titles(p: dict[str, Any]) -> tuple[str, str]:
    nama = _cap_words(p.get("nama", ""))
    merek = _cap_words(p.get("merek", ""))
    bahan = _cap_words(p.get("bahan", ""))
    kategori = _cap_words(p.get("kategori", ""))
    short_feats = [_cap_words(f) for f in p.get("fitur", []) if len(f.split()) <= 3][:2]
    size = _size_summary(p.get("ukuran", ""))
    brand = [merek] if merek and merek.lower() not in nama.lower() else []
    # Key search terms first (product name + material + features), brand last.
    shopee_parts = [nama, bahan] + short_feats + ([size] if size else []) + [kategori] + brand
    tokped_parts = [nama, bahan] + short_feats[:1] + brand
    shopee = _join_unique([x for x in shopee_parts if x], LIMITS["judul_shopee"])
    tokped = _join_unique([x for x in tokped_parts if x], LIMITS["judul_tokopedia"])
    return shopee, tokped


def generate_template(p: dict[str, Any], lang: str = "id", tone: str = "santai") -> dict:
    if not p.get("nama"):
        raise KontenError("kolom nama produk kosong")
    bank = T.PRODUK_TEXT[lang]
    rng = random.Random(stable_seed("produk", p, lang, tone))
    reg = T.register_tokens(tone, lang)
    kategori = p.get("kategori") or ("produk" if lang == "id" else "product")
    values = {"nama": p["nama"], "kategori": kategori.lower(), "kamu": reg["kamu"]}
    shopee, tokped = make_titles(p)

    bullets: list[str] = []
    feats = p.get("fitur", [])[:6]
    said = " ".join(f.lower() for f in feats)
    used_benefits: set[str] = set()

    def benefit_for(text: str) -> str:
        b = _benefit(text, lang, f"{kategori} {p['nama']}")
        # Skip benefits already stated by another feature or used before.
        if not b or b in used_benefits or any(f.lower() in b for f in feats):
            return ""
        used_benefits.add(b)
        return b

    for f in feats:
        b = benefit_for(f)
        bullets.append(f"{ucfirst(f)} — {b}" if b else ucfirst(f))
    if p.get("bahan") and p["bahan"].lower() not in said:
        bahan = p["bahan"]
        if sum(w[:1].isupper() for w in bahan.split()) <= 1:
            bahan = bahan[:1].lower() + bahan[1:]
        b = benefit_for(p["bahan"])
        label = "Bahan" if lang == "id" else "Material"
        bullets.append(f"{label} {bahan}" + (f" — {b}" if b else ""))
    if not bullets:
        bullets.append(ucfirst(kategori) + (" berkualitas" if lang == "id" else " of quality"))

    h = bank["headings"]
    labels = bank["spec_labels"]
    desc = [T.fill(rng.choice(bank["intro"]), values), "",
            f"{h['keunggulan']}:"] + [f"✔ {b}" for b in bullets] + ["", f"{h['spesifikasi']}:"]
    for key in ("merek", "kategori", "bahan", "ukuran", "warna", "berat"):
        if p.get(key) and not (key == "berat" and p[key] == p.get("ukuran")):
            desc.append(f"- {labels[key]}: {p[key]}")
    for col, val in p.get("lainnya", {}).items():
        desc.append(f"- {ucfirst(col)}: {val}")
    if p.get("isi_paket"):
        desc += ["", ("Isi paket" if lang == "id" else "In the box") + f": {p['isi_paket']}"]
    if lang == "id":
        haystack = f"{p.get('bahan', '')} {kategori} {p['nama']}".lower()
        care = next((txt for key, txt in T.CARE_ID.items() if key in haystack), "")
        if not care and _is_food(p, kategori):
            care = T.CARE_FOOD_ID
        if care:
            desc += ["", f"{h['perawatan']}:", care]
    food = lang == "id" and _is_food(p, kategori)
    notes = [n for n in bank["notes"][:2] if not (food and "arna" in n)]
    desc += ["", f"{h['catatan']}:"] + [f"- {n}" for n in notes]
    desc += ["", rng.choice(bank["closing"]) if reg["yuk"] == "yuk" or lang == "en"
             else bank["closing"][1]]
    deskripsi = truncate("\n".join(desc), LIMITS["deskripsi"])

    tags = []
    tag_vals = {
        "kategori": kategori.lower(), "bahan": p.get("bahan", "").lower(),
        "nama_pendek": " ".join(p["nama"].lower().split()[:3]),
        "warna": (split_list(p.get("warna", "")) or [""])[0].lower(),
        "ukuran": "", "merek": p.get("merek", "").lower(),
        "nama_lengkap": p["nama"].lower() if len(p["nama"].split()) <= 5 else "",
        "fitur1": next((f.lower() for f in p.get("fitur", []) if len(f.split()) <= 2), ""),
    }
    for pat in bank["tag_patterns"]:
        if all(tag_vals.get(f) for f in T.fields_of(pat)):
            tags.append(re.sub(r"\s+", " ", pat.format(**tag_vals)).strip())
    tags = list(dict.fromkeys(tags))[: TAGS_RANGE[1]]

    harga = format_rupiah(p.get("harga"))
    short = [b.split(" — ")[0] for b in bullets]
    f1 = short[0]
    f2 = short[1] if len(short) > 1 else (p.get("bahan") or kategori)
    f2_lc = f2 if f2[:2].isupper() else f2[:1].lower() + f2[1:]
    promo_vals = {
        "nama": p["nama"],
        "f1": f1, "f2": f2, "f2_lc": f2_lc,
        "harga_txt": (f" cuma {harga}" if lang == "id" else f" for only {harga}") if harga else "",
        "harga_line": f"💰 {bank['harga']}: {harga}\n" if harga else "",
    }
    promo = T.fill(rng.choice(bank["promo"]), promo_vals)
    return _finalize({"judul_shopee": shopee, "judul_tokopedia": tokped, "poin_fitur": bullets,
                      "deskripsi": deskripsi, "tag_seo": tags, "promo_wa": promo},
                     engine="template")


def _finalize(d: dict[str, Any], engine: str) -> dict[str, Any]:
    tags = [re.sub(r"\s+", " ", t.strip().lstrip("#")).lower() for t in d["tag_seo"]]
    return {
        "judul_shopee": truncate(d["judul_shopee"], LIMITS["judul_shopee"]),
        "judul_tokopedia": truncate(d["judul_tokopedia"], LIMITS["judul_tokopedia"]),
        "poin_fitur": "\n".join(f"• {b.strip().lstrip('•-✔ ')}" for b in d["poin_fitur"]),
        "deskripsi": truncate(d["deskripsi"], LIMITS["deskripsi"]),
        "tag_seo": ", ".join(list(dict.fromkeys(t for t in tags if t))[: TAGS_RANGE[1]]),
        "promo_wa": truncate(d["promo_wa"], LIMITS["promo_wa"]),
        "engine": engine,
    }


# --------------------------------------------------------------------------- AI engine

TASK_SYSTEM = """\
Task: write marketplace listing copy for ONE product sold by an Indonesian online seller on
Shopee and Tokopedia, using only the facts in the product data (do not invent specifications,
certifications, warranty or "original" claims). Fields:
- judul_shopee: max 120 characters. Most-searched key terms first (product type + key
  attribute + material/size), then brand/benefit. No ALL CAPS, no emoji, no symbols spam.
- judul_tokopedia: max 70 characters, same principle, tighter.
- poin_fitur: 4-7 short bullet points "feature — customer benefit", without bullet symbols.
- deskripsi: full plain-text description (marketplaces do not render Markdown) max 2000
  characters: opening hook, key benefits, specifications list, care/usage tips if relevant,
  notes (colour variance, unboxing video for claims), closing call to action.
- tag_seo: 5-10 lowercase search keywords/phrases buyers would type.
- promo_wa: short WhatsApp broadcast message (max 500 characters) with a few emoji, the price
  if known, and a reply-to-order CTA. WhatsApp bold uses *asterisks*.
"""


def _schema() -> dict:
    return obj({"judul_shopee": STR, "judul_tokopedia": STR, "poin_fitur": str_list(3),
                "deskripsi": STR, "tag_seo": str_list(3), "promo_wa": STR})


def generate_ai(llm: LLMClient, p: dict[str, Any], raw_row: dict[str, Any], lang: str,
                tone: str) -> dict[str, Any]:
    if not p.get("nama"):
        raise KontenError("kolom nama produk kosong")
    lang_name = "Bahasa Indonesia" if lang == "id" else "English"
    harga = format_rupiah(p.get("harga"))
    prompt = (f"Output language: {lang_name}. Tone: {tone}.\n"
              f"Product data (original spreadsheet row):\n"
              f"{json.dumps(raw_row, ensure_ascii=False, indent=1)}\n"
              + (f"Formatted price: {harga}\n" if harga else "Price unknown: do not mention one.\n"))

    def check(d: dict) -> list[str]:
        problems = []
        for key in ("judul_shopee", "judul_tokopedia", "deskripsi", "promo_wa"):
            if len(d[key]) > LIMITS[key]:
                problems.append(f"{key} {len(d[key])} karakter, maksimal {LIMITS[key]}")
        if not TAGS_RANGE[0] <= len(d["tag_seo"]) <= TAGS_RANGE[1]:
            problems.append(f"tag_seo harus {TAGS_RANGE[0]}-{TAGS_RANGE[1]} item")
        if d["judul_shopee"].isupper():
            problems.append("judul jangan huruf kapital semua")
        return problems

    data = llm.generate_json(task_system=TASK_SYSTEM, prompt=prompt, schema=_schema(),
                             check=check, max_tokens=8000)
    return _finalize(data, engine=f"ai:{llm.model}")


# --------------------------------------------------------------------------- run


def run(input_path: str, outdir: Path, *, llm: LLMClient | None, concurrency: int, timpa: bool,
        lang: str = "id", tone: str = "santai") -> list[Path]:
    import pandas as pd

    df = read_table(input_path)
    if df.empty:
        raise KontenError(f"File {input_path} tidak berisi data produk.")
    mapping = map_columns(list(df.columns))
    if "nama" not in mapping:
        raise KontenError(
            "Kolom nama produk tidak ditemukan. Beri judul kolom 'nama' (atau 'nama produk', "
            f"'name', 'product'). Kolom yang ada: {', '.join(df.columns)}"
        )
    info(f"{len(df)} produk dibaca. Kolom dikenali: "
         + ", ".join(f"{k}={'/'.join(v)}" for k, v in mapping.items()))
    if llm is None:
        info("Mesin: TEMPLATE (offline, tanpa AI) — copy berbasis template dari data kolom.")
    rows = df.to_dict(orient="records")
    items = []
    for i, row in enumerate(rows, start=1):
        prod = row_to_product(row, mapping)
        key = f"{i:04d}_{slugify(prod.get('nama', 'produk'), 30)}_" \
              f"{stable_seed(row, lang, tone, llm.model if llm else 'template') % 16**6:06x}"
        items.append(Item(key, (prod, row), f"{prod.get('nama', '(tanpa nama)')}"))

    def worker(payload: tuple[dict, dict]) -> dict:
        prod, row = payload
        if llm is None:
            return generate_template(prod, lang, tone)
        return generate_ai(llm, prod, row, lang, tone)

    res = run_batch(items, worker, items_dir=outdir / "_items" / "produk",
                    concurrency=concurrency if llm else 1, timpa=timpa, label="produk")
    new_rows = []
    for item, row in zip(items, rows, strict=True):
        result = res.results.get(item.key)
        extra = {c: (result or {}).get(c, "") for c in NEW_COLUMNS}
        if result is None:
            extra["engine"] = f"GAGAL: {res.failed.get(item.key, '')}"
        new_rows.append({**row, **extra})
    out_df = pd.DataFrame(new_rows, columns=list(df.columns) + NEW_COLUMNS)
    stem = Path(input_path).stem
    xlsx = outdir / f"{stem}_konten.xlsx"
    csv = outdir / f"{stem}_konten.csv"
    write_excel(xlsx, {"Produk": out_df})
    out_df.to_csv(csv, index=False, encoding="utf-8-sig")
    return [xlsx, csv]
