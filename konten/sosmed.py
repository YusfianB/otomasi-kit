"""Social media content calendar + caption generator (subcommand ``sosmed``)."""

from __future__ import annotations

import datetime as dt
import json
import random
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from konten import templates_id as T
from konten.common import (
    Item,
    KontenError,
    info,
    ordered,
    run_batch,
    split_list,
    stable_seed,
    truncate,
    write_excel,
)
from konten.llm import STR, LLMClient, obj, str_list

PLATFORMS: dict[str, dict[str, Any]] = {
    # max_caption = characters incl. hashtags; hashtag = (min, max) count
    "instagram": {"nama": "Instagram", "max_caption": 2200, "hashtag": (5, 10), "jam": "19.00"},
    "tiktok": {"nama": "TikTok", "max_caption": 2200, "hashtag": (3, 5), "jam": "20.00"},
    "linkedin": {"nama": "LinkedIn", "max_caption": 3000, "hashtag": (3, 5), "jam": "08.00"},
    "x": {"nama": "X (Twitter)", "max_caption": 280, "hashtag": (1, 2), "jam": "12.00"},
    "facebook": {"nama": "Facebook", "max_caption": 2000, "hashtag": (1, 3), "jam": "18.30"},
}
PLATFORM_ALIASES = {"ig": "instagram", "tt": "tiktok", "twitter": "x", "fb": "facebook",
                    "li": "linkedin"}

PILAR_ROTATION = ["edukasi", "promosi", "interaksi", "bts", "testimoni", "cerita", "hiburan"]

FORMATS = {
    "instagram": {"edukasi": "carousel", "promosi": "foto tunggal", "interaksi": "foto tunggal",
                  "bts": "reels", "testimoni": "carousel", "cerita": "reels", "hiburan": "reels"},
    "tiktok": {},  # always video
    "linkedin": {"edukasi": "dokumen (carousel PDF)"},
    "x": {"edukasi": "thread"},
    "facebook": {"bts": "video", "hiburan": "video", "cerita": "video"},
}
DEFAULT_FORMAT = {"instagram": "foto tunggal", "tiktok": "video pendek",
                  "linkedin": "teks + gambar", "x": "tweet", "facebook": "foto"}

FREQ_WEEKDAYS = {
    "harian": None, "daily": None,
    "2x-seminggu": {1, 4}, "2x-week": {1, 4},
    "3x-seminggu": {0, 2, 4}, "3x-week": {0, 2, 4},
    "5x-seminggu": {0, 1, 2, 3, 4}, "weekdays": {0, 1, 2, 3, 4},
}
FREQ_DAYS = {"harian": 1, "daily": 1, "mingguan": 7, "weekly": 7, "2-mingguan": 14}

HARI = {"id": ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"],
        "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]}
BULAN = {"id": ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus",
                "September", "Oktober", "November", "Desember"],
         "en": ["January", "February", "March", "April", "May", "June", "July", "August",
                "September", "October", "November", "December"]}


@dataclass
class Brief:
    brand: str
    niche: str
    audiens: str
    tone: str = "santai"
    platforms: list[str] = field(default_factory=lambda: ["instagram"])
    jumlah: int = 7
    mulai: str = ""
    frekuensi: str = "harian"
    produk: list[str] = field(default_factory=list)
    keunggulan: list[str] = field(default_factory=list)
    masalah: list[str] = field(default_factory=list)
    topik: list[str] = field(default_factory=list)
    hashtag: list[str] = field(default_factory=list)
    lokasi: str = ""
    promo: str = ""
    link: str = ""
    catatan: str = ""
    bahasa: str = "id"


# --------------------------------------------------------------------------- input


def load_profile(path: str | Path) -> dict[str, Any]:
    import yaml

    p = Path(path)
    if not p.exists():
        raise KontenError(f"File profil tidak ditemukan: {p}")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise KontenError(f"Format YAML profil tidak valid ({p.name}): {exc}") from exc
    if not isinstance(data, dict):
        raise KontenError(f"Profil {p.name} harus berupa pasangan kunci: nilai")
    return data


def build_brief(profile: dict[str, Any], overrides: dict[str, Any]) -> Brief:
    data = {**profile, **{k: v for k, v in overrides.items() if v not in (None, "", [])}}
    if "platform" in data and "platforms" not in data:
        data["platforms"] = data.pop("platform")
    missing = [k for k in ("brand", "niche") if not data.get(k)]
    if missing:
        raise KontenError(
            "Data brand belum lengkap: " + ", ".join(missing)
            + ". Isi lewat --brand/--niche atau file --profil brand.yaml."
        )
    platforms = []
    for p in split_list(data.get("platforms") or ["instagram"]):
        key = PLATFORM_ALIASES.get(p.lower().strip(), p.lower().strip())
        if key not in PLATFORMS:
            raise KontenError(
                f"Platform tidak dikenal: {p}. Pilih dari: {', '.join(PLATFORMS)}"
            )
        if key not in platforms:
            platforms.append(key)
    tone = str(data.get("tone") or "santai").lower()
    if tone not in T.TONES:
        raise KontenError(f"Tone tidak dikenal: {tone}. Pilih dari: {', '.join(T.TONES)}")
    try:
        jumlah = int(data.get("jumlah") or 7)
    except (TypeError, ValueError) as exc:
        raise KontenError("--jumlah harus berupa angka") from exc
    if not 1 <= jumlah <= 366:
        raise KontenError("--jumlah harus antara 1 dan 366 posting per platform")
    mulai = str(data.get("mulai") or dt.date.today().isoformat())
    try:
        dt.date.fromisoformat(mulai)
    except ValueError as exc:
        raise KontenError(f"Tanggal mulai tidak valid: {mulai} (format YYYY-MM-DD)") from exc
    return Brief(
        brand=str(data["brand"]).strip(),
        niche=str(data["niche"]).strip(),
        audiens=str(data.get("audiens") or data.get("audience") or "pelanggan").strip(),
        tone=tone,
        platforms=platforms,
        jumlah=jumlah,
        mulai=mulai,
        frekuensi=str(data.get("frekuensi") or "harian").lower(),
        produk=split_list(data.get("produk")),
        keunggulan=split_list(data.get("keunggulan")),
        masalah=split_list(data.get("masalah") or data.get("masalah_audiens")),
        topik=split_list(data.get("topik")),
        hashtag=split_list(data.get("hashtag")),
        lokasi=str(data.get("lokasi") or "").strip(),
        promo=str(data.get("promo") or "").strip(),
        link=str(data.get("link") or "").strip(),
        catatan=str(data.get("catatan") or "").strip(),
        bahasa=str(data.get("bahasa") or "id"),
    )


def schedule(mulai: str, n: int, frekuensi: str) -> list[dt.date]:
    start = dt.date.fromisoformat(mulai)
    freq = frekuensi.lower().strip()
    if freq in FREQ_WEEKDAYS and FREQ_WEEKDAYS[freq] is not None:
        days, d = [], start
        while len(days) < n:
            if d.weekday() in FREQ_WEEKDAYS[freq]:
                days.append(d)
            d += dt.timedelta(days=1)
        return days
    if freq in FREQ_DAYS:
        step = FREQ_DAYS[freq]
    else:
        m = re.fullmatch(r"(?:setiap-)?(\d+)(?:-?hari|d)?", freq)
        if not m or int(m.group(1)) < 1:
            raise KontenError(
                f"Frekuensi tidak dikenal: {frekuensi}. Contoh: harian, 2x-seminggu, "
                "3x-seminggu, 5x-seminggu, mingguan, 2-mingguan, atau angka hari (mis. 2)."
            )
        step = int(m.group(1))
    return [start + dt.timedelta(days=step * i) for i in range(n)]


def format_date(d: dt.date, lang: str) -> str:
    return f"{HARI[lang][d.weekday()]}, {d.day} {BULAN[lang][d.month - 1]} {d.year}"


# --------------------------------------------------------------------------- planning


def _pools(brief: Brief, lang: str) -> tuple[list[str], list[str], list[str]]:
    bank = T.SOSMED[lang]
    service = re.search(r"\b(jasa|layanan|kursus|les|service|services|class)\b", brief.niche.lower())
    if lang == "id":
        default = f"layanan {brief.brand}" if service else f"produk {brief.brand}"
    else:
        default = f"{brief.brand} services" if service else f"{brief.brand} products"
    produk = brief.produk or [default]
    keunggulan = brief.keunggulan or bank["default_keunggulan"]
    masalah = brief.masalah or [m.replace("{niche}", brief.niche)
                                for m in bank["default_masalah"]]
    return produk, keunggulan, masalah


def allowed_variants(pb: dict[str, Any], kind: str, body_i: int) -> list[int]:
    """Indexes of ``kind`` (hook/visual/topik) that fit body ``body_i``: the ones paired with
    that body in ``pasangan`` plus the generic ones not paired with any body."""
    pairs = pb.get("pasangan", {})
    paired = {i for spec in pairs.values() for i in spec.get(kind, [])}
    own = list(pairs.get(body_i, {}).get(kind, []))
    generic = [i for i in range(len(pb[kind])) if i not in paired]
    return (own + generic) or list(range(len(pb[kind])))


def plan(brief: Brief) -> list[dict[str, Any]]:
    """Deterministic content plan: date, platform, pillar, format and the variant choices that
    keep posts distinct (body first, then a hook/visual/topic that fits that body, always
    preferring the least-used option so nothing repeats before the pool is exhausted)."""
    lang = brief.bahasa
    bank = T.SOSMED[lang]
    rng = random.Random(stable_seed("sosmed-plan", asdict(brief)))
    dates = schedule(brief.mulai, brief.jumlah, brief.frekuensi)
    produk, keunggulan, masalah = _pools(brief, lang)
    formal_tone = brief.tone in T.FORMAL_TONES
    # Which templates can be filled with the data we have (promo / second product optional).
    probe = {"brand": "x", "niche": "x", "audiens": "x", "produk": "x", "keunggulan": "x",
             "keunggulan2": "x" if len(keunggulan) > 1 else "", "masalah": "x",
             "produk2": "x" if len(produk) > 1 else "", "promo": brief.promo,
             "lokasi": brief.lokasi, "link_cta": "x"}
    slots: list[dict[str, Any]] = []
    for p_index, platform in enumerate(brief.platforms):
        pillars = [p for p in PILAR_ROTATION
                   if not (p == "hiburan" and (formal_tone or platform == "linkedin"))]
        offset = (p_index * 2) % len(pillars)
        cyc: dict[str, T.Cycler] = {}
        used: dict[str, dict[int, int]] = {}
        edu_count = 0

        def pick(name: str, items: list[Any], _cyc: dict = cyc) -> Any:
            if name not in _cyc:
                _cyc[name] = T.Cycler(rng, items)
            return _cyc[name].next()

        def least_used(name: str, options: list[int], _used: dict = used) -> int:
            counts = _used.setdefault(name, {})
            best = min(counts.get(o, 0) for o in options)
            choice = rng.choice([o for o in options if counts.get(o, 0) == best])
            counts[choice] = counts.get(choice, 0) + 1
            return choice

        def fillable(pb: dict, kind: str, idxs: list[int]) -> list[int]:
            ok = [i for i in idxs if T.usable([pb[kind][i]], probe)]
            return ok or idxs

        for i, day in enumerate(dates):
            pilar = pillars[(i + offset) % len(pillars)]
            pb = bank["pilar"][pilar]
            fmt = FORMATS[platform].get(pilar, DEFAULT_FORMAT[platform])
            p1 = pick("produk", produk)
            others = [p for p in produk if p != p1]
            k1 = pick("keunggulan", keunggulan)
            k_others = [k for k in keunggulan if k != k1]
            body_i = least_used(f"body-{pilar}",
                                fillable(pb, "body", list(range(len(pb["body"])))))
            user_topik = ""
            if pilar == "edukasi" and brief.topik:
                if edu_count % 2 == 0:  # every other education post covers a client topic
                    user_topik = pick("user-topik", brief.topik)
                edu_count += 1
            slots.append({
                "no": len(slots) + 1,
                "tanggal": day.isoformat(),
                "platform": platform,
                "pilar": pilar,
                "format": fmt,
                "produk": p1,
                "produk2": rng.choice(others) if others else "",
                "keunggulan": k1,
                "keunggulan2": rng.choice(k_others) if k_others else "",
                "masalah": pick("masalah", masalah),
                "body_i": body_i,
                "hook_i": least_used(f"hook-{pilar}", fillable(
                    pb, "hook", allowed_variants(pb, "hook", body_i))),
                "visual_i": least_used(f"visual-{pilar}", allowed_variants(pb, "visual", body_i)),
                "topik_i": least_used(f"topik-{pilar}", fillable(
                    pb, "topik", allowed_variants(pb, "topik", body_i))),
                "pendek_i": least_used(f"pendek-{pilar}", fillable(
                    pb, "pendek", list(range(len(pb["pendek"]))))),
                "cta_i": least_used(f"cta-{pilar}", list(range(len(pb["cta"])))),
                "user_topik": user_topik,
                "emoji_seed": rng.randrange(1_000_000),
            })
    return slots


def slot_key(slot: dict[str, Any]) -> str:
    return f"{slot['no']:03d}_{slot['tanggal']}_{slot['platform']}"


# --------------------------------------------------------------------------- template engine


def _pick_variant(options: list[str], index: int, values: dict[str, str]) -> str:
    ok = T.usable(options, values)
    if not ok:
        ok = [o for o in options if "{promo}" not in o.lower() and "{produk2}" not in o.lower()]
    if not ok:
        return ""
    preferred = options[index % len(options)]
    return preferred if preferred in ok else ok[index % len(ok)]


def build_hashtags(brief: Brief, platform: str, pilar: str, seed: int) -> list[str]:
    lang = brief.bahasa
    lo, hi = PLATFORMS[platform]["hashtag"]
    stop = T.STOPWORDS_ID | T.STOPWORDS_EN | {"kekinian", "online", "murah"}
    generic_heads = {"jasa", "layanan", "toko", "produk", "usaha", "bisnis", "warung", "kedai",
                     "service", "services", "shop", "store"}
    words = [w for w in re.findall(r"[a-z0-9]+", brief.niche.lower()) if w not in stop]
    if len(words) > 1 and words[0] in generic_heads:
        words = words[1:]
    camel = platform == "linkedin"
    acronyms = {"umkm": "UMKM", "ukm": "UKM", "seo": "SEO", "ai": "AI"}

    def tag(*parts: str) -> str:
        parts = tuple(p for p in parts if p)
        if camel:
            return "".join(acronyms.get(p, p[:1].upper() + p[1:]) for p in parts)
        return "".join(parts).lower()

    brand_tag = re.sub(r"[^A-Za-z0-9]", "", brief.brand)
    lokasi = re.sub(r"[^a-z0-9]", "", brief.lokasi.lower())
    niche_tags: list[str] = []
    if words:
        niche_tags.append(tag(*words[:2]))
        if len(words) > 2 and len("".join(words)) <= 24:
            niche_tags.append(tag(*words))
        niche_tags.append(tag(words[0]))
        if lokasi:
            niche_tags.append(tag(words[0], lokasi))
            niche_tags.append(tag("umkm" if lang == "id" else "local", lokasi))
        pillar_word = {"edukasi": "tips", "promosi": "promo", "testimoni": "review",
                       }.get(pilar)
        if pillar_word:
            niche_tags.append(tag(pillar_word, words[0]) if lang == "id"
                              else tag(words[0], pillar_word))
    user = [h.lstrip("#").replace(" ", "") for h in brief.hashtag]
    generic = T.SOSMED[lang]["generic_tags"][platform]
    ordered_tags = user[:2] + niche_tags[:2] + [tag(brand_tag)] + niche_tags[2:] + user[2:]
    rng = random.Random(seed)
    extra = list(generic)
    rng.shuffle(extra)
    if platform == "tiktok" and "fyp" in extra:  # keep #fyp first on TikTok
        extra.remove("fyp")
        ordered_tags = ["fyp"] + ordered_tags
    ordered_tags += extra
    seen, out = set(), []
    for t in ordered_tags:
        t = t.strip()
        if t and t.lower() not in seen and len(t) <= 30:
            seen.add(t.lower())
            out.append("#" + t)
    return out[: max(lo, min(hi, len(out)))]


def _video_script(brief: Brief, slot: dict, values: dict[str, str], hook: str, cta: str) -> str:
    bank = T.SOSMED[brief.bahasa]
    rng = random.Random(slot["emoji_seed"])
    points = [T.fill(p, values) for p in bank["video_point"][slot["pilar"]]]
    rng.shuffle(points)
    v = {**values, "hook": hook, "cta": cta,
         "visual_open": T.fill(rng.choice(bank["video_open"]), values),
         "point1": points[0], "point2": points[1 % len(points)],
         "audio": rng.choice(bank["audio"])}
    return "\n".join(f"- {T.fill(line, v)}" for line in bank["video"])


def generate_template(brief: Brief, slot: dict[str, Any]) -> dict[str, Any]:
    lang = brief.bahasa
    platform = slot["platform"]
    bank = T.SOSMED[lang]
    pb = bank["pilar"][slot["pilar"]]
    formal_platform = platform == "linkedin"
    values = {
        "brand": brief.brand, "niche": brief.niche, "audiens": brief.audiens,
        "produk": slot["produk"], "produk2": slot["produk2"], "keunggulan": slot["keunggulan"],
        "keunggulan2": slot["keunggulan2"], "masalah": slot["masalah"], "lokasi": brief.lokasi,
        "promo": brief.promo,
        "link_cta": (brief.link if brief.link and platform in ("facebook", "linkedin")
                     else T.LINK_CTA[lang][platform]),
        **T.register_tokens(brief.tone, lang, formal_platform),
    }
    rng = random.Random(slot["emoji_seed"])
    casual = brief.tone in T.CASUAL_TONES or brief.tone == "inspiratif"
    values["e"] = rng.choice(T.EMOJI[slot["pilar"]]) if casual and not formal_platform else ""

    hook = T.fill(_pick_variant(pb["hook"], slot["hook_i"], values), values)
    cta = T.fill(_pick_variant(pb["cta"], slot["cta_i"], values), values)
    if cta[-1:].isalnum():
        cta += "."
    topik = T.fill(_pick_variant(pb["topik"], slot["topik_i"], values), values)
    if platform == "x":
        body = T.fill(_pick_variant(pb["pendek"], slot["pendek_i"], values), values)
        hook = re.split(r"(?<=[.!?])\s", body, maxsplit=1)[0]
    else:
        body = T.fill(_pick_variant(pb["body"], slot["body_i"], values), values)
    if slot["user_topik"]:
        # The offline engine cannot know facts about a client topic: give a ready structure
        # with clearly marked blanks instead of inventing content.
        topik = slot["user_topik"]
        hook = T.fill(T.SOSMED[lang]["topik_hook"], {**values, "topik": topik})
        body = T.fill(T.SOSMED[lang]["topik_body"], {**values, "topik": topik})
        if platform == "x":
            hook = body = T.fill(T.SOSMED[lang]["topik_pendek"], {**values, "topik": topik})
    visual = T.fill(_pick_variant(pb["visual"], slot["visual_i"], values), values)
    hashtags = build_hashtags(brief, platform, slot["pilar"], slot["emoji_seed"])

    if platform == "x":
        caption = _fit_x(body, cta, " ".join(hashtags))
    else:
        caption = f"{hook}\n\n{body}\n\n{cta}"
    naskah = ""
    if _is_video(slot["format"]):
        naskah = _video_script(brief, slot, values, hook, cta)
    return _finalize(slot, brief, {
        "topik": topik, "hook": hook, "caption": caption, "hashtag": hashtags, "cta": cta,
        "ide_visual": visual, "naskah_video": naskah,
    }, engine="template")


def _fit_x(body: str, cta: str, tags: str) -> str:
    limit = PLATFORMS["x"]["max_caption"] - len(tags) - 1
    for cand in (f"{body}\n\n{cta}", body):
        if len(cand) <= limit:
            return cand
    return truncate(body, limit, "…")


def _is_video(fmt: str) -> bool:
    return any(w in fmt for w in ("video", "reels"))


def _finalize(slot: dict, brief: Brief, data: dict, engine: str) -> dict[str, Any]:
    platform = slot["platform"]
    cfg = PLATFORMS[platform]
    tags = [_clean_tag(t) for t in data["hashtag"]]
    tags = [t for t in dict.fromkeys(tags) if t][: cfg["hashtag"][1]]
    tag_text = " ".join(tags)
    room = cfg["max_caption"] - (len(tag_text) + 1 if tag_text else 0)
    caption = data["caption"].strip()
    if len(caption) > room:
        caption = truncate(caption, room, "…")
    day = dt.date.fromisoformat(slot["tanggal"])
    return {
        "no": slot["no"],
        "tanggal": slot["tanggal"],
        "hari": HARI[brief.bahasa][day.weekday()],
        "jam": cfg["jam"],
        "platform": cfg["nama"],
        "format": slot["format"],
        "pilar": T.SOSMED[brief.bahasa]["pilar"][slot["pilar"]]["label"],
        "topik": data.get("topik", ""),
        "hook": data["hook"].strip(),
        "caption": caption,
        "hashtag": tag_text,
        "cta": data["cta"].strip(),
        "ide_visual": data["ide_visual"].strip(),
        "naskah_video": data.get("naskah_video", "").strip(),
        "jumlah_karakter": len(caption) + (len(tag_text) + 1 if tag_text else 0),
        "engine": engine,
    }


def _clean_tag(tag: str) -> str:
    t = re.sub(r"[^\w]", "", tag.strip().lstrip("#"), flags=re.UNICODE)
    return f"#{t}" if t else ""


# --------------------------------------------------------------------------- AI engine

TASK_SYSTEM = """\
Task: write ONE social media post for an Indonesian small business, following a content plan
slot that fixes the date, platform, content pillar, format and focus. Fields:
- topik: the specific angle of this post in a few words.
- hook: the first line that stops the scroll (max ~100 characters, no hashtags).
- caption: the full ready-to-post caption (hook as the first line, body, CTA at the end),
  WITHOUT hashtags. Use line breaks for readability. Respect the character budget given.
- hashtag: hashtags for this platform, each starting with '#', count within the given range.
- cta: the call to action sentence used at the end of the caption.
- ide_visual: concrete visual direction (shot list / slide plan / design notes).
- naskah_video: for video formats (TikTok, Reels, Facebook video) a short timed script as
  lines like "0–3 dtk: ...", otherwise an empty string.
Platform conventions: Instagram = storytelling + line breaks; TikTok = short punchy caption,
the script carries the content; LinkedIn = professional, insight-led, uses "Anda", no slang,
CamelCase hashtags; X = one tight message; Facebook = warm, conversational, community tone.
"""


def _schema() -> dict:
    return obj({
        "topik": STR, "hook": STR, "caption": STR, "hashtag": str_list(1), "cta": STR,
        "ide_visual": STR, "naskah_video": {"type": "string", "x-nonEmpty": False},
    })


def generate_ai(llm: LLMClient, brief: Brief, slot: dict[str, Any]) -> dict[str, Any]:
    platform = slot["platform"]
    cfg = PLATFORMS[platform]
    lo, hi = cfg["hashtag"]
    lang_name = "Bahasa Indonesia" if brief.bahasa == "id" else "English"
    brief_data = {k: v for k, v in asdict(brief).items()
                  if k not in ("platforms", "jumlah", "mulai", "frekuensi") and v}
    budget = cfg["max_caption"] - 15 * hi
    prompt = (
        f"Output language: {lang_name}. Tone: {brief.tone}.\n\n"
        f"Brand brief (JSON):\n{json.dumps(brief_data, ensure_ascii=False, indent=1)}\n\n"
        "This post's plan slot:\n"
        f"- tanggal: {slot['tanggal']} ({format_date(dt.date.fromisoformat(slot['tanggal']), 'id')})\n"
        f"- platform: {cfg['nama']}\n- format: {slot['format']}\n"
        f"- pilar konten: {T.SOSMED['id']['pilar'][slot['pilar']]['label']}\n"
        f"- produk fokus: {slot['produk']}\n- keunggulan yang ditonjolkan: {slot['keunggulan']}\n"
        f"- masalah audiens yang disentuh: {slot['masalah']}\n"
        + (f"- topik dari klien: {slot['user_topik']}\n" if slot["user_topik"] else "")
        + f"- posting ke-{slot['no']} dari kalender ini: pakai sudut dan pembuka yang berbeda "
        "dari posting lain.\n\n"
        f"Limits: caption max {budget} characters; hashtags {lo}-{hi}."
        + (" Video format: fill naskah_video." if _is_video(slot["format"]) else
           " Not a video: naskah_video must be an empty string.")
    )

    def check(data: dict) -> list[str]:
        problems = []
        if len(data["caption"]) > budget:
            problems.append(f"caption {len(data['caption'])} karakter, maksimal {budget}")
        if not lo <= len(data["hashtag"]) <= hi:
            problems.append(f"jumlah hashtag {len(data['hashtag'])}, harus {lo}-{hi}")
        if any(" " in h.strip() for h in data["hashtag"]):
            problems.append("hashtag tidak boleh mengandung spasi")
        if _is_video(slot["format"]) and not data["naskah_video"].strip():
            problems.append("naskah_video wajib diisi untuk format video")
        return problems

    data = llm.generate_json(task_system=TASK_SYSTEM, prompt=prompt, schema=_schema(),
                             check=check, max_tokens=8000)
    return _finalize(slot, brief, data, engine=f"ai:{llm.model}")


# --------------------------------------------------------------------------- output

COLUMNS = ["no", "tanggal", "hari", "jam", "platform", "format", "pilar", "topik", "hook",
           "caption", "hashtag", "cta", "ide_visual", "naskah_video", "jumlah_karakter", "engine"]


def write_outputs(posts: list[dict[str, Any]], outdir: Path, brief: Brief) -> list[Path]:
    import pandas as pd

    posts = sorted(posts, key=lambda p: (p["tanggal"], p["no"]))
    df = pd.DataFrame(posts, columns=COLUMNS)
    sheets = {"Kalender": df[["tanggal", "hari", "jam", "platform", "format", "pilar", "topik",
                              "hook"]]}
    for platform in brief.platforms:
        name = PLATFORMS[platform]["nama"]
        sheets[name] = df[df["platform"] == name]
    xlsx = outdir / "kalender_konten.xlsx"
    csv = outdir / "kalender_konten.csv"
    md = outdir / "kalender_konten.md"
    write_excel(xlsx, sheets)
    df.to_csv(csv, index=False, encoding="utf-8-sig")
    md.write_text(_markdown(posts, brief), encoding="utf-8")
    return [xlsx, csv, md]


def _markdown(posts: list[dict[str, Any]], brief: Brief) -> str:
    lang = brief.bahasa
    engines = sorted({p["engine"] for p in posts})
    lines = [f"# Kalender Konten {brief.brand}", "",
             f"- Niche: {brief.niche}", f"- Audiens: {brief.audiens}", f"- Tone: {brief.tone}",
             f"- Platform: {', '.join(PLATFORMS[p]['nama'] for p in brief.platforms)}",
             f"- Mesin: {', '.join(engines)}", ""]
    if any(e == "template" for e in engines):
        lines += [("> Dibuat dengan mesin **template** (offline, tanpa AI). Sesuaikan detail, "
                  "ganti placeholder [..] dengan data asli sebelum diposting."), ""]
    current = None
    for p in posts:
        if p["tanggal"] != current:
            current = p["tanggal"]
            lines += [f"## {format_date(dt.date.fromisoformat(current), lang)}", ""]
        lines += [f"### {p['platform']} — {p['pilar']} ({p['format']}, {p['jam']})", "",
                  f"**Topik:** {p['topik']}  ", f"**Hook:** {p['hook']}", "", "**Caption:**", "",
                  "\n".join("> " + line if line else ">" for line in p["caption"].split("\n")),
                  "", f"**Hashtag:** {p['hashtag']}  ", f"**CTA:** {p['cta']}  ",
                  f"**Ide visual:** {p['ide_visual']}", ""]
        if p["naskah_video"]:
            lines += ["**Naskah video:**", "", p["naskah_video"], ""]
    return "\n".join(lines).rstrip() + "\n"


def run(brief: Brief, outdir: Path, *, llm: LLMClient | None, concurrency: int,
        timpa: bool) -> list[Path]:
    slots = plan(brief)
    total = len(slots)
    info(f"Rencana: {brief.jumlah} posting x {len(brief.platforms)} platform = {total} posting, "
         f"mulai {brief.mulai} ({brief.frekuensi}).")
    if llm is None:
        info("Mesin: TEMPLATE (offline, tanpa AI) — hasil berbasis template, cek & sesuaikan.")

        def worker(slot: dict) -> dict:
            return generate_template(brief, slot)
    else:
        def worker(slot: dict) -> dict:
            return generate_ai(llm, brief, slot)
    items = [Item(slot_key(s), s, f"{s['tanggal']} {PLATFORMS[s['platform']]['nama']}")
             for s in slots]
    res = run_batch(items, worker, items_dir=outdir / "_items" / "sosmed",
                    concurrency=concurrency if llm else 1, timpa=timpa, label="posting")
    posts = ordered(res.results, [i.key for i in items])
    if not posts:
        raise KontenError("Tidak ada posting yang berhasil dibuat.")
    files = write_outputs(posts, outdir, brief)
    if res.failed:
        info(f"{len(res.failed)} posting gagal; jalankan ulang perintah yang sama untuk "
             "melanjutkan (yang sudah jadi tidak dibuat ulang).")
    return files
