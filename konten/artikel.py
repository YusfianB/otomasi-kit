"""SEO article draft generator (subcommand ``artikel``).

Keyword "research" here is model/template based (related terms, long-tail variations and an
estimated search intent). It is NOT real search-volume data.
"""

from __future__ import annotations

import datetime as dt
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from konten import templates_id as T
from konten.common import (
    Item,
    KontenError,
    info,
    ordered,
    read_json,
    read_table,
    run_batch,
    slugify,
    stable_seed,
    title_case_id,
    truncate,
)
from konten.llm import STR, LLMClient, obj, str_list

INTENTS = ("informasional", "komersial", "transaksional", "navigasional")
KEYWORD_COLUMNS = ("keyword", "kata_kunci", "kata kunci", "keyword_utama", "topik", "topic")


@dataclass
class ArticleJob:
    keyword: str
    audiens: str = ""
    panjang: int = 1200
    catatan: str = ""
    bahasa: str = "id"

    @property
    def slug(self) -> str:
        return slugify(self.keyword)


def load_jobs(topik: str | None, keywords_file: str | None, *, audiens: str, panjang: int,
              bahasa: str) -> list[ArticleJob]:
    jobs: list[ArticleJob] = []
    if topik:
        jobs.append(ArticleJob(topik.strip(), audiens, panjang, "", bahasa))
    if keywords_file:
        df = read_table(keywords_file)
        cols = {c.lower(): c for c in df.columns}
        kw_col = next((cols[c] for c in KEYWORD_COLUMNS if c in cols), df.columns[0])
        for _, row in df.iterrows():
            kw = str(row[kw_col]).strip()
            if not kw:
                continue
            aud = str(row[cols["audiens"]]).strip() if "audiens" in cols else ""
            try:
                pj = int(float(row[cols["panjang"]])) if "panjang" in cols and \
                    str(row[cols["panjang"]]).strip() else panjang
            except ValueError:
                pj = panjang
            cat = str(row[cols["catatan"]]).strip() if "catatan" in cols else ""
            jobs.append(ArticleJob(kw, aud or audiens, pj, cat, bahasa))
    if not jobs:
        raise KontenError("Isi --topik \"...\" atau --keywords file.csv/xlsx (kolom 'keyword').")
    seen, unique = set(), []
    for j in jobs:
        if j.slug not in seen:
            seen.add(j.slug)
            unique.append(j)
    return unique


# --------------------------------------------------------------------------- template engine


def classify_intent(keyword: str, lang: str = "id") -> str:
    words = keyword.lower()
    bank = T.ARTIKEL[lang]["intent_words"]
    for intent in ("transaksional", "komersial", "informasional"):
        for w in bank[intent]:
            if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", words):
                return intent
    return "informasional"


def base_keyword(keyword: str, lang: str = "id") -> str:
    """Strip intent words so modifiers can be applied (``cara budidaya lele`` -> ``budidaya
    lele``, ``harga iphone 15 terbaru`` -> ``iphone 15``)."""
    strip = {"cara", "tips", "panduan", "harga", "beli", "jual", "terbaik", "rekomendasi",
             "review", "terbaru", "murah", "apa", "itu", "adalah", "how", "to", "best", "buy",
             "price", "guide", "what", "is"}
    strip_year = re.compile(r"^(19|20)\d\d$")
    words = [w for w in keyword.split() if w.lower() not in strip and not strip_year.match(w)]
    return " ".join(words).strip() or keyword.strip()


def expand_keywords(keyword: str, intent: str, audiens: str, lang: str,
                    year: int) -> tuple[list[str], list[str]]:
    base = base_keyword(keyword, lang).lower()
    mods = T.ARTIKEL[lang]["modifiers"][intent]
    rng = random.Random(stable_seed("kw", keyword))
    candidates = [m.format(kw=base, tahun=year) for m in mods]
    rng.shuffle(candidates)
    norm = keyword.lower().strip()
    candidates = [c for c in dict.fromkeys(candidates) if c != norm]
    related = candidates[:5]
    long_tail = candidates[5:]
    if audiens:
        long_tail.insert(0, f"{base} untuk {audiens}" if lang == "id" else f"{base} for {audiens}")
    long_tail.append(f"{keyword.lower()} {year}")
    return related, list(dict.fromkeys(long_tail))[:6]


def _meta_title(h1: str, base_title: str, lang: str) -> str:
    if len(h1) <= 60:
        return h1
    alt = f"{base_title}: {'Panduan Lengkap' if lang == 'id' else 'Complete Guide'}"
    return alt if len(alt) <= 60 else truncate(base_title, 60)


def generate_template(job: ArticleJob) -> dict[str, Any]:
    lang = job.bahasa
    bank = T.ARTIKEL[lang]
    year = dt.date.today().year
    intent = classify_intent(job.keyword, lang)
    base = base_keyword(job.keyword, lang)
    audiens = job.audiens or bank["default_audiens"]
    related, long_tail = expand_keywords(job.keyword, intent, job.audiens, lang, year)
    rng = random.Random(stable_seed("artikel", job.keyword, lang))
    values = {"kw": base.lower(), "Kw": title_case_id(base), "audiens": audiens,
              "tahun": str(year)}
    full_title = title_case_id(job.keyword)
    h1_pattern = rng.choice(bank["h1"][intent])
    if intent == "informasional" and job.keyword.lower().startswith(("cara ", "tips ", "how ")):
        h1 = f"{full_title}: {'Panduan Lengkap untuk Pemula' if lang == 'id' else 'A Complete Guide'}"
    else:
        h1 = h1_pattern.format(Kw=title_case_id(base), tahun=year)
    outline = []
    body_parts = [rng.choice(bank["intro"]).format(**values), ""]
    for h2_t, h3s, paras in bank["sections"][intent]:
        h2 = h2_t.format(**values)
        outline.append({"h2": h2, "h3": [h.format(**values) for h in h3s]})
        body_parts += [f"## {h2}", ""]
        for p in paras:
            body_parts += [p.format(**values), ""]
        for h3 in h3s:
            body_parts += [f"### {h3.format(**values)}", ""]
            text = T.ARTIKEL_H3[lang].get(h3)
            if text:
                body_parts += [text.format(**values), ""]
    faq = [{"pertanyaan": q.format(**values), "jawaban": a.format(**values)}
           for q, a in bank["faq"][intent]]
    conclusion = rng.choice(bank["conclusion"]).format(**values)
    outline += [{"h2": bank["faq_title"], "h3": [f["pertanyaan"] for f in faq]},
                {"h2": bank["conclusion_title"], "h3": []}]
    meta_desc = truncate(rng.choice(bank["meta_desc"]).format(**values), 158)
    return _assemble(job, {
        "intent": intent,
        "kata_kunci_terkait": related,
        "long_tail": long_tail,
        "judul_h1": h1,
        "meta_title": _meta_title(h1, title_case_id(base), lang),
        "meta_description": meta_desc,
        "slug": job.slug,
        "outline": outline,
        "artikel_markdown": "\n".join(body_parts).strip() + "\n\n## "
        + bank["conclusion_title"] + "\n\n" + conclusion,
        "faq": faq,
        "anchor_internal": [{"anchor": k, "topik_tujuan": k} for k in related[:4]],
    }, engine="template")


# --------------------------------------------------------------------------- AI engine

TASK_RESEARCH = """\
Task (step 1 of 2, SEO article): given a main keyword, do keyword expansion and plan the
article. Return: estimated search intent (informasional / komersial / transaksional /
navigasional), 5-8 related keywords, 4-8 long-tail keywords that real Indonesian users would
type, an H1, meta_title (max 60 characters, contains the main keyword near the start),
meta_description (120-155 characters, contains the keyword, ends with a soft CTA), a short
lowercase ASCII slug with hyphens, and an outline of 5-8 H2 sections (each with 0-4 H3) that
follows the search intent. Do not include FAQ or conclusion in the outline; they are added in
step 2. Keyword suggestions are estimates from your knowledge, not search-volume data.
"""

TASK_WRITE = """\
Task (step 2 of 2, SEO article): write the full article draft in Markdown from the approved
outline. artikel_markdown starts with a 2-3 paragraph introduction (no H1), then every H2/H3
from the outline in order, and ends with a "## Kesimpulan" (or "## Conclusion") section.
Use the main keyword naturally in the first 100 words and in at least one H2; use related and
long-tail keywords where they fit. Short paragraphs, lists and tables where helpful. Where a
fact needs a source or current data (prices, statistics, regulations), write a placeholder
like [cek data terbaru] instead of inventing numbers. Also return 4-6 FAQ items (concise,
self-contained answers) and 3-5 internal-link anchor suggestions (anchor text + the topic of
the page it should link to).
"""


def _research_schema() -> dict:
    return obj({
        "intent": {"type": "string", "enum": list(INTENTS)},
        "kata_kunci_terkait": str_list(3), "long_tail": str_list(3), "judul_h1": STR,
        "meta_title": STR, "meta_description": STR, "slug": STR,
        "outline": {"type": "array", "x-minItems": 3,
                    "items": obj({"h2": STR, "h3": {"type": "array", "items": STR}})},
    })


def _write_schema() -> dict:
    return obj({
        "artikel_markdown": STR,
        "faq": {"type": "array", "x-minItems": 3,
                "items": obj({"pertanyaan": STR, "jawaban": STR})},
        "anchor_internal": {"type": "array",
                            "items": obj({"anchor": STR, "topik_tujuan": STR})},
    })


def generate_ai(llm: LLMClient, job: ArticleJob) -> dict[str, Any]:
    lang_name = "Bahasa Indonesia" if job.bahasa == "id" else "English"
    ctx = (f"Output language: {lang_name}.\nMain keyword: {job.keyword}\n"
           f"Target audience: {job.audiens or 'umum'}\nTarget length: ~{job.panjang} words\n"
           + (f"Client notes: {job.catatan}\n" if job.catatan else ""))

    def check_research(d: dict) -> list[str]:
        p = []
        if len(d["meta_title"]) > 60:
            p.append(f"meta_title {len(d['meta_title'])} karakter, maksimal 60")
        if len(d["meta_description"]) > 160:
            p.append(f"meta_description {len(d['meta_description'])} karakter, maksimal 160")
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", d["slug"]):
            p.append("slug harus huruf kecil ASCII dipisah tanda hubung")
        return p

    research = llm.generate_json(task_system=TASK_RESEARCH, prompt=ctx,
                                 schema=_research_schema(), check=check_research, max_tokens=8000)
    outline_md = "\n".join(
        f"## {s['h2']}" + "".join(f"\n### {h}" for h in s["h3"]) for s in research["outline"]
    )
    prompt = (ctx + f"Intent: {research['intent']}\nH1: {research['judul_h1']}\n"
              f"Related keywords: {', '.join(research['kata_kunci_terkait'])}\n"
              f"Long-tail: {', '.join(research['long_tail'])}\n\nOutline:\n{outline_md}")
    min_words = int(job.panjang * 0.6)

    def check_write(d: dict) -> list[str]:
        p = []
        words = len(d["artikel_markdown"].split())
        if words < min_words:
            p.append(f"artikel baru {words} kata, target minimal {min_words}")
        if "## " not in d["artikel_markdown"]:
            p.append("artikel harus memakai heading H2 (## )")
        if re.search(r"^# ", d["artikel_markdown"], flags=re.MULTILINE):
            p.append("jangan tulis H1 (# ) di artikel_markdown")
        return p

    written = llm.generate_json(task_system=TASK_WRITE, prompt=prompt, schema=_write_schema(),
                                check=check_write, max_tokens=32000)
    outline = research["outline"] + [
        {"h2": T.ARTIKEL[job.bahasa]["faq_title"],
         "h3": [f["pertanyaan"] for f in written["faq"]]}]
    data = {**research, **written, "outline": outline}
    return _assemble(job, data, engine=f"ai:{llm.model}")


# --------------------------------------------------------------------------- assemble/output


def _assemble(job: ArticleJob, d: dict[str, Any], engine: str) -> dict[str, Any]:
    lang = job.bahasa
    slug = slugify(d.get("slug") or job.keyword)
    meta_title = truncate(d["meta_title"], 60)
    meta_desc = truncate(d["meta_description"], 160)
    links = [{"anchor": a["anchor"], "tujuan": "/" + slugify(a["topik_tujuan"])}
             for a in d.get("anchor_internal", [])]
    front = {
        "title": d["judul_h1"],
        "meta_title": meta_title,
        "meta_description": meta_desc,
        "slug": slug,
        "keyword_utama": job.keyword,
        "kata_kunci_terkait": d["kata_kunci_terkait"],
        "long_tail": d["long_tail"],
        "intent": d["intent"],
        "tanggal": dt.date.today().isoformat(),
        "bahasa": lang,
        "engine": engine,
        "saran_tautan_internal": links,
        "catatan": ("Riset kata kunci berbasis template/model, bukan data volume pencarian. "
                    "Validasi dengan Google Keyword Planner/Search Console."),
    }
    faq_title = T.ARTIKEL[lang]["faq_title"]
    faq_md = "\n\n".join(f"### {f['pertanyaan']}\n\n{f['jawaban']}" for f in d["faq"])
    body = d["artikel_markdown"].strip()
    conclusion_title = T.ARTIKEL[lang]["conclusion_title"]
    # Place FAQ before the conclusion when the body ends with one.
    m = re.search(rf"^## {re.escape(conclusion_title)}\b.*\Z", body, flags=re.MULTILINE | re.DOTALL)
    if m:
        body = body[: m.start()].rstrip() + f"\n\n## {faq_title}\n\n{faq_md}\n\n" + m.group(0)
    else:
        body = body + f"\n\n## {faq_title}\n\n{faq_md}"
    fm = yaml.safe_dump(front, allow_unicode=True, sort_keys=False, width=1000).strip()
    markdown = f"---\n{fm}\n---\n\n# {d['judul_h1']}\n\n{body}\n"
    return {
        "keyword": job.keyword,
        "slug": slug,
        "file": f"{job.slug}.md",
        "judul": d["judul_h1"],
        "intent": d["intent"],
        "meta_title": meta_title,
        "meta_description": meta_desc,
        "kata_kunci_terkait": d["kata_kunci_terkait"],
        "long_tail": d["long_tail"],
        "outline": d["outline"],
        "jumlah_kata": len(re.findall(r"\w+", body)),
        "engine": engine,
        "markdown": markdown,
    }


def run(jobs: list[ArticleJob], outdir: Path, *, llm: LLMClient | None, concurrency: int,
        timpa: bool) -> list[Path]:
    import pandas as pd

    outdir.mkdir(parents=True, exist_ok=True)
    info(f"{len(jobs)} kata kunci akan diproses.")
    if llm is None:
        info("Mesin: TEMPLATE (offline, tanpa AI) — hasil berupa draf kerangka dengan catatan "
             "editor yang perlu dilengkapi.")
    items_dir = outdir / "_items" / "artikel"
    items = []
    for job in jobs:
        # An existing .md for this slug also counts as done (user may have edited it).
        md = outdir / f"{job.slug}.md"
        if md.exists() and not timpa and not (items_dir / f"{job.slug}.json").exists():
            info(f"LEWAT  {job.keyword} ({md.name} sudah ada, pakai --timpa)")
            continue
        items.append(Item(job.slug, job, job.keyword))

    def worker(job: ArticleJob) -> dict:
        result = generate_ai(llm, job) if llm else generate_template(job)
        (outdir / f"{job.slug}.md").write_text(result["markdown"], encoding="utf-8")
        return result

    res = run_batch(items, worker, items_dir=items_dir, concurrency=concurrency if llm else 1,
                    timpa=timpa, label="artikel")
    # Rebuild the index from every cached article in the folder (incl. earlier runs).
    rows = []
    all_results = {p.stem: p for p in sorted(items_dir.glob("*.json"))}
    results = ordered(res.results, [j.slug for j in jobs])
    known = {r["file"] for r in results}
    for stem, path in all_results.items():
        if f"{stem}.md" not in known:
            try:
                results.append(read_json(path))
            except (OSError, ValueError):
                continue
    for i, r in enumerate(results, start=1):
        rows.append({"no": i, "keyword": r["keyword"], "judul": r["judul"], "slug": r["slug"],
                     "intent": r["intent"], "meta_title": r["meta_title"],
                     "meta_description": r["meta_description"],
                     "kata_kunci_terkait": "; ".join(r["kata_kunci_terkait"]),
                     "jumlah_kata": r["jumlah_kata"], "file": r["file"],
                     "engine": r["engine"]})
    index = outdir / "index_artikel.csv"
    pd.DataFrame(rows).to_csv(index, index=False, encoding="utf-8-sig")
    return [index] + [outdir / r["file"] for r in results]
