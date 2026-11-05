"""Quiz / question bank generator (subcommand ``kuis``).

Template engine: extraction-based questions from the material (cloze/fill-in, multiple
choice with distractors taken from the same material, true/false by swapping key terms,
essay prompts from definitions and cause-effect sentences). AI engine: Claude writes the
questions from the material (or from ``--topik`` alone).
"""

from __future__ import annotations

import json
import random
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any

from konten import templates_id as T
from konten.common import (
    Item,
    KontenError,
    info,
    read_json,
    run_batch,
    slugify,
    stable_seed,
    warn,
    write_excel,
)
from konten.llm import STR, LLMClient, obj

TYPES = ("pilihan_ganda", "benar_salah", "isian", "esai")
LEVELS = ("mudah", "sedang", "sulit")
TYPE_LABEL = {
    "id": {"pilihan_ganda": "Pilihan Ganda", "benar_salah": "Benar/Salah", "isian": "Isian",
           "esai": "Esai"},
    "en": {"pilihan_ganda": "Multiple Choice", "benar_salah": "True/False",
           "isian": "Fill in the Blank", "esai": "Essay"},
}
INSTRUKSI = {
    "id": {"pilihan_ganda": "Pilihlah satu jawaban yang paling tepat!",
           "benar_salah": "Tentukan apakah pernyataan berikut benar atau salah!",
           "isian": "Isilah titik-titik dengan jawaban yang tepat!",
           "esai": "Jawablah pertanyaan berikut dengan jelas dan lengkap!"},
    "en": {"pilihan_ganda": "Choose the best answer!",
           "benar_salah": "Decide whether each statement is true or false!",
           "isian": "Fill in the blanks!", "esai": "Answer the following questions clearly!"},
}
CHUNK = 10
LETTERS = "abcde"


@dataclass
class QuizJob:
    nama: str
    materi: str = ""
    topik: str = ""
    jumlah: int = 10
    kesulitan: str = "sedang"
    tipe: list[str] = field(default_factory=lambda: ["pilihan_ganda"])
    mapel: str = ""
    jenjang: str = ""
    bahasa: str = "id"


def parse_types(value: str | list[str] | None) -> list[str]:
    raw = value if isinstance(value, list) else re.split(r"[,\s]+", value or "pilihan_ganda")
    aliases = {"pg": "pilihan_ganda", "mc": "pilihan_ganda", "bs": "benar_salah",
               "tf": "benar_salah", "isian_singkat": "isian", "essay": "esai"}
    out = []
    for t in raw:
        t = aliases.get(t.strip().lower(), t.strip().lower())
        if not t:
            continue
        if t not in TYPES:
            raise KontenError(f"Tipe soal tidak dikenal: {t}. Pilih dari: {', '.join(TYPES)}")
        if t not in out:
            out.append(t)
    return out or ["pilihan_ganda"]


def type_plan(tipe: list[str], jumlah: int) -> list[str]:
    return [tipe[i % len(tipe)] for i in range(jumlah)]


# --------------------------------------------------------------------------- text analysis

_ABBR = r"(?:dll|dsb|dst|dkk|yth|no|hlm|dr|ir|prof|drs|tn|ny|sdr|mis|kab|kec|jl|st|vs|e\.g|i\.e)"


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line or re.fullmatch(r"[-=*_#\s]+", line):
            continue
        if line.startswith("#"):  # headings are context, not statements
            continue
        line = re.sub(r"^([-*•]|\d+[.)])\s+", "", line)
        line = re.sub(r"[*_`]{1,3}([^*_`]+)[*_`]{1,3}", r"\1", line)
        lines.append(line)
    joined = "\n".join(lines)
    protected = re.sub(rf"\b({_ABBR})\.", r"\1<DOT>", joined, flags=re.IGNORECASE)
    protected = re.sub(r"(\d)\.(\d)", r"\1<DOT>\2", protected)
    parts = re.split(r"(?<=[.!?])\s+|\n+", protected)
    out = []
    for p in parts:
        s = p.replace("<DOT>", ".").strip()
        if s:
            out.append(s)
    return out


@dataclass
class Target:
    text: str  # exact substring in the sentence
    kind: str  # istilah | angka | nama


DEF_RE = re.compile(
    r"^(?P<term>[A-Za-zÀ-ÿ0-9][^,;:()]{1,60}?)\s+(?:adalah|merupakan|ialah|yaitu|is|are|"
    r"refers to)\s+(?P<rest>.{8,})$", re.IGNORECASE)
DISEBUT_RE = re.compile(r"\b(?:disebut(?: juga)?|dinamakan|dikenal sebagai|called|known as)\s+"
                        r"(?:dengan\s+)?(?P<term>[A-Za-zÀ-ÿ][\w-]*(?:\s+[A-Za-zÀ-ÿ][\w-]*){0,2})",
                        re.IGNORECASE)
NUM_RE = re.compile(r"(?<![\w.,])\d{1,4}(?:[.,]\d+)?(?:\s?(?:%|persen|derajat|°C|km|kg|meter|"
                    r"juta|ribu|miliar|tahun|abad))?(?![\w])")
PROPER_RE = re.compile(r"(?<=\s)([A-Z][a-zà-ÿ]+(?:\s+[A-Z][a-zà-ÿ]+){0,3})")
CAUSE_RE = re.compile(r"^(?P<a>.{12,}?),?\s+(?P<conn>karena|sebab|sehingga|because|so that)\s+"
                      r"(?P<b>.{8,})$", re.IGNORECASE)


def _stop(lang: str) -> set[str]:
    return T.STOPWORDS_ID | T.STOPWORDS_EN if lang == "en" else T.STOPWORDS_ID


@dataclass
class Analysis:
    sentences: list[str]
    keywords: list[str]  # by importance
    pools: dict[str, list[str]]  # kind -> candidate answers found in the material
    definitions: dict[int, tuple[str, str]]  # sentence index -> (term, rest)


VERB_PREFIXES = ("meng", "meny", "mem", "men", "me", "ber", "ter", "di", "per")


def _verbish(word: str) -> bool:
    """Rough Indonesian verb filter (me-/ber-/di-/ter- forms, -kan suffix)."""
    w = word.lower()
    return w.endswith(("kan", "nya")) or (w.startswith(VERB_PREFIXES) and len(w) >= 7)


def _proper_nouns(sentence: str) -> list[str]:
    """Capitalised word groups that are not simply the first word of the sentence."""
    return [m.group(1) for m in PROPER_RE.finditer(sentence)]


def analyse(text: str, lang: str = "id") -> Analysis:
    sents = [s for s in split_sentences(text) if 5 <= len(s.split()) <= 45
             and not s.endswith((":", "?"))]
    stop = _stop(lang)
    words: Counter[str] = Counter()
    doc_freq: Counter[str] = Counter()
    bigrams: Counter[str] = Counter()
    heads: Counter[str] = Counter()
    mods: Counter[str] = Counter()
    surface: dict[str, str] = {}
    for s in sents:
        raw = re.findall(r"[A-Za-zÀ-ÿ][\w-]+", s)
        toks = [w.lower() for w in raw]
        content = [w for w in toks if w not in stop and len(w) >= 4]
        words.update(content)
        doc_freq.update(set(content))
        for (r1, w1), (r2, w2) in pairwise(zip(raw, toks, strict=True)):
            if w1 not in stop and w2 not in stop and len(w1) >= 3 and len(w2) >= 3:
                key = f"{w1} {w2}"
                bigrams[key] += 1
                heads[w1] += 1
                mods[w2] += 1
                # Keep the original casing of names inside phrases ("siklus Calvin").
                if r2[:1].isupper() and key not in surface:
                    surface[key] = f"{w1} {r2}"
    n = max(1, len(sents))
    definitions: dict[int, tuple[str, str]] = {}
    pools: dict[str, list[str]] = {"frasa": [], "istilah": [], "angka": [], "nama": [],
                                   "konsep": []}
    protected: set[str] = set()
    for i, s in enumerate(sents):
        m = DEF_RE.match(s)
        if m and len(m.group("term").split()) <= 6:
            term = m.group("term").strip()
            definitions[i] = (term, m.group("rest").rstrip(". "))
            pools["istilah"].append(term)
            protected.update(term.lower().split())
        for m in DISEBUT_RE.finditer(s):
            term = m.group("term").strip(" .,")
            pools["istilah"].append(term)
            protected.update(term.lower().split())
        pools["angka"].extend(m.group(0) for m in NUM_RE.finditer(s))
        pools["nama"].extend(_proper_nouns(s))
    phrases = [surface.get(bg, bg) for bg, c in bigrams.most_common() if c >= 2
               and not (_verbish(bg.split()[0]) and bg.split()[0] not in protected)]
    pools["frasa"] = phrases[:40]
    pools["konsep"] = list(pools["istilah"])  # defined / named terms: the strongest options
    # Single words: keep likely nouns (phrase heads such as "reaksi", "cadangan"), drop
    # modifiers ("hijau", "utama") and verb forms.
    name_words = {w.lower() for name in pools["nama"] for w in name.split()}
    keywords = [w for w, c in words.most_common()
                if doc_freq[w] <= max(2, int(n * 0.5)) and len(w) >= 5
                and w not in name_words
                and (w in protected or (not _verbish(w) and (
                    heads[w] > mods[w] or (heads[w] == mods[w] == 0 and doc_freq[w] >= 2))))]
    pools["istilah"].extend(keywords[:40])
    for k, values in pools.items():
        seen, uniq = set(), []
        for v in values:
            if v.lower() not in seen and v.lower() not in stop:
                seen.add(v.lower())
                uniq.append(v)
        pools[k] = uniq
    return Analysis(sents, keywords, pools, definitions)


def find_targets(sentence: str, idx: int, an: Analysis) -> list[Target]:
    """Candidate blanks in priority order."""
    out: list[Target] = []
    low = sentence.lower()
    if idx in an.definitions:
        out.append(Target(an.definitions[idx][0], "istilah"))
    for m in DISEBUT_RE.finditer(sentence):
        out.append(Target(m.group("term").strip(" .,"), "istilah"))
    for ph in an.pools["frasa"]:
        m = re.search(rf"(?<!\w){re.escape(ph.lower())}(?!\w)", low)
        if m:
            out.append(Target(sentence[m.start():m.end()], "frasa"))
    for name in _proper_nouns(sentence):
        out.append(Target(name, "nama"))
    for m in NUM_RE.finditer(sentence):
        out.append(Target(m.group(0), "angka"))
    for kw in an.keywords[:30]:
        m = re.search(rf"(?<!\w){re.escape(kw)}(?!\w)", low)
        if m:
            out.append(Target(sentence[m.start():m.end()], "istilah"))
    seen, uniq = set(), []
    for t in out:
        if t.text.lower() not in seen and len(t.text) >= 2:
            seen.add(t.text.lower())
            uniq.append(t)
    return uniq


def _numeric_variants(value: str, rng: random.Random, k: int) -> list[str]:
    m = re.match(r"(\d+(?:[.,]\d+)?)(.*)", value)
    if not m:
        return []
    num_s, unit = m.groups()
    is_float = any(c in num_s for c in ".,")
    num = float(num_s.replace(",", "."))
    year_like = 1000 <= num <= 2100 and not is_float
    out: set[str] = set()
    tries = 0
    while len(out) < k and tries < 50:
        tries += 1
        if year_like:
            cand = num + rng.choice([-1, 1]) * rng.randint(2, 30)
        elif num < 10 and not is_float:
            cand = num + rng.choice([-3, -2, -1, 1, 2, 3])
        else:
            cand = num * rng.choice([0.5, 0.75, 1.25, 1.5, 2])
        if cand <= 0 or cand == num:
            continue
        txt = (f"{cand:.1f}".replace(".", "," if "," in num_s else ".") if is_float
               else str(round(cand)))
        out.add(txt + unit)
    return sorted(out)


def distractors(target: Target, sentence: str, an: Analysis, rng: random.Random, k: int = 3,
                level: str = "sedang", strict: bool = False) -> list[str]:
    """Wrong options of the same kind as the answer, taken from the material itself.

    ``strict`` (true/false swaps) only uses defined terms, phrases, names and numbers, so a
    false statement is plausible rather than nonsensical."""
    low_sentence = sentence.lower()
    t_low = target.text.lower()

    def ok(v: str) -> bool:
        v_low = v.lower()
        return (v_low != t_low and v_low not in low_sentence and t_low not in v_low
                and v_low not in t_low)

    if target.kind == "angka":
        unit = bool(re.search(r"[a-z%°]", t_low))
        pool = [v for v in an.pools["angka"] if ok(v) and bool(re.search(r"[a-z%°]", v)) == unit]
        rng.shuffle(pool)
        if len(pool) < k:
            pool += _numeric_variants(target.text, rng, k - len(pool))
        chosen = pool[:k]
    else:
        n_words = len(target.text.split())
        if target.kind == "frasa":
            # Siblings sharing a word ("reaksi terang" -> "reaksi gelap") are the best traps.
            sib = [v for v in an.pools["frasa"]
                   if ok(v) and set(v.lower().split()) & set(t_low.split())]
            rest = [v for v in an.pools["frasa"] + an.pools["konsep"] + (
                [] if strict else an.pools["istilah"]) if ok(v) and v not in sib]
        elif target.kind == "nama":
            sib = [v for v in an.pools["nama"] if ok(v)]
            rest = [v for v in an.pools["istilah"] if ok(v) and v[:1].isupper()]
        else:
            sib = [v for v in an.pools["konsep"] if ok(v)]
            rest = [] if strict else [v for v in an.pools["istilah"] + an.pools["frasa"]
                                      if ok(v) and v not in sib]
        rng.shuffle(sib)
        rng.shuffle(rest)
        pool = sib + rest
        # Prefer options of similar shape (word count) so the answer does not stand out.
        pool.sort(key=lambda v: abs(len(v.split()) - n_words) > 1)
        if level == "sulit":
            pool.sort(key=lambda v: abs(len(v) - len(target.text)))
        chosen = []
        for v in pool:
            if v.lower() not in {c.lower() for c in chosen}:
                chosen.append(v)
            if len(chosen) == k:
                break
    return chosen if len(chosen) == k else []


def _style_options(options: list[str], target: Target, sentence: str,
                   proper: set[str]) -> list[str]:
    """Same capitalisation for every option so case does not reveal the answer."""
    at_start = sentence.startswith(target.text)
    out = []
    for o in options:
        if o in proper or o.isupper() or target.kind == "angka" or re.search(r"\s[A-Z]", o):
            out.append(o[:1].upper() + o[1:] if at_start else o)
        elif at_start:
            out.append(o[:1].upper() + o[1:])
        else:
            out.append(o[:1].lower() + o[1:])
    return out


def _blank(sentence: str, target: str, blank: str) -> str:
    return sentence.replace(target, blank, 1)


def cause_effect(sentence: str) -> tuple[str, str] | None:
    """``"A karena B."`` -> (effect A, cause B); ``"A sehingga B."`` -> (B, A)."""
    m = CAUSE_RE.match(sentence.rstrip("."))
    if not m:
        return None
    a, conn, b = m.group("a").strip(" ,"), m.group("conn").lower(), m.group("b").strip(" .")
    effect, cause = (b, a) if conn in ("sehingga", "so that") else (a, b)
    effect = re.sub(r"^(selain itu|oleh karena itu|namun|dengan demikian|akibatnya|"
                    r"however|therefore),?\s+", "", effect, flags=re.IGNORECASE)
    if not effect[:2].isupper():
        effect = effect[:1].lower() + effect[1:]
    return effect, cause[:1].upper() + cause[1:] + "."


# --------------------------------------------------------------------------- template engine


def _rank_sentences(an: Analysis, level: str, rng: random.Random) -> list[int]:
    def score(i: int) -> float:
        s = an.sentences[i]
        n = len(s.split())
        sc = 3.0 if i in an.definitions else 0.0
        sc += 1.0 if NUM_RE.search(s) else 0.0
        sc += sum(1 for kw in an.keywords[:15] if kw in s.lower()) * 0.7
        ideal = {"mudah": 12, "sedang": 18, "sulit": 26}[level]
        sc -= abs(n - ideal) / 8
        return sc + rng.random() * 0.5

    return sorted(range(len(an.sentences)), key=score, reverse=True)


def generate_template(job: QuizJob) -> list[dict[str, Any]]:
    if not job.materi.strip():
        raise KontenError(
            "Mesin template butuh teks materi (--materi file.txt/.md/.pdf). Untuk membuat soal "
            "hanya dari --topik, gunakan --engine ai (butuh ANTHROPIC_API_KEY)."
        )
    lang = job.bahasa
    txt = T.KUIS_TEXT[lang]
    an = analyse(job.materi, lang)
    if len(an.sentences) < 3:
        raise KontenError("Materi terlalu pendek untuk dibuat soal (minimal beberapa kalimat).")
    rng = random.Random(stable_seed("kuis", job.nama, job.materi[:5000], job.kesulitan,
                                    job.tipe, job.jumlah))
    order = _rank_sentences(an, job.kesulitan, rng)
    proper = set(an.pools["nama"])
    used: set[int] = set()
    questions: list[dict[str, Any]] = []
    # Essays first: they need the few definition / cause-effect sentences most.
    priority = {"esai": 0, "pilihan_ganda": 1, "benar_salah": 2, "isian": 3}
    plan = sorted(type_plan(job.tipe, job.jumlah), key=priority.__getitem__)
    bs_truth = [True, False] * (job.jumlah // 2 + 1)
    rng.shuffle(bs_truth)

    def take(pred) -> tuple[int, Any] | None:
        for i in order:
            if i in used:
                continue
            res = pred(i)
            if res:
                used.add(i)
                return i, res
        return None

    def essay(i: int, mode: str):
        s = an.sentences[i]
        if mode == "sebab":
            ce = cause_effect(s)
            return (txt["esai_why"].format(akibat=ce[0]), ce[1]) if ce else None
        if mode == "definisi":
            if i in an.definitions:
                return txt["esai_def"].format(istilah=an.definitions[i][0]), s
            return None
        if job.kesulitan != "mudah" and len(s.split()) >= 10:
            return txt["esai_general"].format(kalimat=s), s
        return None

    def pg(i: int):
        s = an.sentences[i]
        for t in find_targets(s, i, an):
            ds = distractors(t, s, an, rng, 3, job.kesulitan)
            if ds:
                return t, ds
        return None

    def bs_false(i: int):
        s = an.sentences[i]
        for t in find_targets(s, i, an):
            ds = distractors(t, s, an, rng, 1, job.kesulitan, strict=True)
            if ds:
                return t, _style_options(ds, t, s, proper)[0]
        return None

    for qtype in plan:
        q = None
        if qtype == "esai":
            got = None
            for mode in ("sebab", "definisi", "umum"):
                got = take(lambda i, m=mode: essay(i, m))
                if got:
                    break
            if got:
                i, (stem, key) = got
                nxt = an.sentences[i + 1] if i + 1 < len(an.sentences) else ""
                # Definitions/general statements: the next sentence usually elaborates.
                elaborate = mode != "sebab" and nxt and i + 1 not in used
                points = key + (f" {nxt}" if elaborate else "")
                q = {"tipe": qtype, "pertanyaan": stem, "opsi": [], "jawaban": points,
                     "penjelasan": txt["expl_esai"].format(kalimat=points)}
        elif qtype == "pilihan_ganda":
            got = take(pg)
            if got:
                i, (t, ds) = got
                s = an.sentences[i]
                styled = _style_options(ds + [t.text], t, s, proper)
                answer = styled[-1]
                options = styled[:]
                rng.shuffle(options)
                other_defs = [d[1] for j, d in an.definitions.items() if j != i]
                if (i in an.definitions and t.text == an.definitions[i][0]
                        and len(other_defs) >= 3 and rng.random() < 0.5):
                    # Definition question: the answer is the definition itself.
                    term, rest = an.definitions[i]
                    rng.shuffle(other_defs)
                    options = other_defs[:3] + [rest]
                    rng.shuffle(options)
                    q = {"tipe": qtype, "pertanyaan": txt["def_pg"].format(istilah=term),
                         "opsi": options, "jawaban": rest,
                         "penjelasan": txt["expl_cloze"].format(kalimat=s)}
                else:
                    q = {"tipe": qtype,
                         "pertanyaan": txt["cloze_pg"].format(
                             kalimat=_blank(s, t.text, txt["blank"])),
                         "opsi": options, "jawaban": answer,
                         "penjelasan": txt["expl_cloze"].format(kalimat=s)}
        elif qtype == "isian":
            got = take(lambda i: (find_targets(an.sentences[i], i, an) or [None])[0])
            if got:
                i, t = got
                s = an.sentences[i]
                q = {"tipe": qtype,
                     "pertanyaan": txt["cloze_isian"].format(
                         kalimat=_blank(s, t.text, txt["blank"])),
                     "opsi": [], "jawaban": t.text,
                     "penjelasan": txt["expl_cloze"].format(kalimat=s)}
        elif qtype == "benar_salah":
            truth = bs_truth.pop()
            if truth:
                got = take(lambda i: ("benar", None))
            else:
                got = take(lambda i: ("salah", bs_false(i)) if bs_false(i) else None)
            if got:
                i, (kind, swap) = got
                s = an.sentences[i]
                if kind == "benar":
                    q = {"tipe": qtype, "pertanyaan": txt["bs_q"].format(kalimat=s),
                         "opsi": [txt["true"], txt["false"]], "jawaban": txt["true"],
                         "penjelasan": txt["expl_true"]}
                else:
                    t, wrong = swap
                    q = {"tipe": qtype,
                         "pertanyaan": txt["bs_q"].format(kalimat=s.replace(t.text, wrong, 1)),
                         "opsi": [txt["true"], txt["false"]], "jawaban": txt["false"],
                         "penjelasan": txt["expl_false"].format(kalimat=s)}
        if q is not None:
            questions.append(q)
    if len(questions) < job.jumlah:
        warn(f"Materi '{job.nama}' hanya cukup untuk {len(questions)} dari {job.jumlah} soal "
             "dengan mesin template. Tambah materi atau gunakan --engine ai.")
    return _number(questions, engine="template")


def display_title(job: QuizJob) -> str:
    """Topic, else the material's first short heading-like line, else the file name."""
    if job.topik:
        return job.topik
    for line in job.materi.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            if len(line.split()) <= 8 and not line.endswith((".", "!", "?")):
                return line
            break
    return job.nama.replace("_", " ").replace("-", " ").strip().title()


def _number(questions: list[dict[str, Any]], engine: str) -> list[dict[str, Any]]:
    # Group by type in the canonical order so the document has sections A, B, C, D.
    questions = sorted(questions, key=lambda q: TYPES.index(q["tipe"]))
    for n, q in enumerate(questions, start=1):
        q["no"] = n
        q["engine"] = engine
        q["kunci"] = (LETTERS[q["opsi"].index(q["jawaban"])]
                      if q["tipe"] == "pilihan_ganda" and q["jawaban"] in q["opsi"] else "")
    return questions


# --------------------------------------------------------------------------- AI engine

TASK_SYSTEM = """\
Task: write quiz questions for Indonesian learners (school, course, or training). Every
question must be answerable from the provided material (or, if no material is given, from
well-established knowledge of the topic and curriculum level). Fields per question:
- tipe: one of pilihan_ganda, benar_salah, isian, esai (follow the requested sequence).
- pertanyaan: the question text (for isian, include a blank "_____").
- opsi: pilihan_ganda = exactly 4 options without letter prefixes, one correct, distractors
  plausible and similar in length; benar_salah = exactly ["Benar", "Salah"] (or
  ["True", "False"] in English); isian and esai = empty list.
- jawaban: for pilihan_ganda/benar_salah the exact text of the correct option; for isian the
  short answer; for esai the key points of a complete answer.
- penjelasan: 1-2 sentences explaining why, referring to the material.
Difficulty: mudah = recall of explicit facts; sedang = understanding and application;
sulit = analysis, comparison, cause-effect, multi-step reasoning. Never repeat a question
already listed as existing.
"""


def _schema() -> dict:
    return obj({"soal": {"type": "array", "x-minItems": 1, "items": obj({
        "tipe": {"type": "string", "enum": list(TYPES)}, "pertanyaan": STR,
        "opsi": {"type": "array", "items": STR}, "jawaban": STR, "penjelasan": STR})}})


def generate_ai_chunk(llm: LLMClient, job: QuizJob, types: list[str],
                      existing: list[str]) -> list[dict[str, Any]]:
    lang = job.bahasa
    txt = T.KUIS_TEXT[lang]
    lang_name = "Bahasa Indonesia" if lang == "id" else "English"
    content: list[dict] = []
    if job.materi:
        # Material first with its own cache breakpoint so later chunks reuse it.
        content.append({"type": "text", "text": f"<materi>\n{job.materi}\n</materi>",
                        "cache_control": {"type": "ephemeral"}})
    instr = (
        f"Output language: {lang_name}.\n"
        + (f"Topic: {job.topik}\n" if job.topik else "")
        + (f"Subject: {job.mapel}\n" if job.mapel else "")
        + (f"Level/grade: {job.jenjang}\n" if job.jenjang else "")
        + f"Difficulty: {job.kesulitan}\n"
        f"Write exactly {len(types)} questions with this type sequence: {json.dumps(types)}.\n"
        + ("Existing questions (do not repeat):\n- " + "\n- ".join(existing) if existing else "")
    )
    content.append({"type": "text", "text": instr})

    def check(d: dict) -> list[str]:
        p = []
        got = [q["tipe"] for q in d["soal"]]
        if got != types:
            p.append(f"urutan/jumlah tipe soal harus {types}, didapat {got}")
        for n, q in enumerate(d["soal"], start=1):
            if q["tipe"] == "pilihan_ganda":
                if len(q["opsi"]) != 4:
                    p.append(f"soal {n}: pilihan ganda harus 4 opsi")
                if q["jawaban"] not in q["opsi"]:
                    p.append(f"soal {n}: jawaban harus sama persis dengan salah satu opsi")
            elif q["tipe"] == "benar_salah":
                if q["jawaban"] not in (txt["true"], txt["false"]):
                    p.append(f"soal {n}: jawaban harus '{txt['true']}' atau '{txt['false']}'")
            elif q["opsi"]:
                p.append(f"soal {n}: opsi harus kosong untuk {q['tipe']}")
        return p

    data = llm.generate_json(task_system=TASK_SYSTEM, prompt=content, schema=_schema(),
                             check=check, max_tokens=16000)
    out = []
    for q in data["soal"]:
        q = {k: q[k] for k in ("tipe", "pertanyaan", "opsi", "jawaban", "penjelasan")}
        if q["tipe"] == "benar_salah":
            q["opsi"] = [txt["true"], txt["false"]]
        if q["tipe"] == "pilihan_ganda" and q["jawaban"] not in q["opsi"]:
            continue  # unrecoverable after retries; drop rather than ship a wrong key
        if q["tipe"] in ("isian", "esai"):
            q["opsi"] = []
        q["opsi"] = [re.sub(r"^[A-Ea-e][.)]\s+", "", o) for o in q["opsi"]]
        out.append(q)
    return out


# --------------------------------------------------------------------------- output


def _md_soal(job: QuizJob, qs: list[dict], engine: str) -> str:
    lang = job.bahasa
    title = "Soal" if lang == "id" else "Quiz"
    head = " — ".join(x for x in (job.mapel, job.jenjang) if x)
    lines = [f"# {title}: {display_title(job)}" + (f" ({head})" if head else ""), ""]
    lines += (["Nama: ____________________  Kelas: ________  Tanggal: __________", ""]
              if lang == "id" else ["Name: ____________________  Class: ________  Date: ______",
                                    ""])
    if engine == "template":
        lines += [("<!-- Dibuat dengan mesin template (ekstraksi kalimat dari materi). Periksa "
                  "ulang sebelum dipakai. -->"), ""]
    current = None
    section = 0
    for q in qs:
        if q["tipe"] != current:
            current = q["tipe"]
            lines += [f"## {chr(65 + section)}. {TYPE_LABEL[lang][current]}", "",
                      f"*{INSTRUKSI[lang][current]}*", ""]
            section += 1
        lines.append(f"{q['no']}. {q['pertanyaan']}")
        if q["tipe"] == "pilihan_ganda":
            lines += [f"   {LETTERS[i]}. {o}" for i, o in enumerate(q["opsi"])]
        elif q["tipe"] == "benar_salah":
            lines.append(f"   ( ) {q['opsi'][0]}   ( ) {q['opsi'][1]}")
        elif q["tipe"] == "esai":
            lines += ["", "   " + "_" * 60, "   " + "_" * 60]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _md_kunci(job: QuizJob, qs: list[dict]) -> str:
    lang = job.bahasa
    title = "Kunci Jawaban" if lang == "id" else "Answer Key"
    lines = [f"# {title}: {display_title(job)}", ""]
    for q in qs:
        ans = f"{q['kunci']}. {q['jawaban']}" if q["kunci"] else q["jawaban"]
        label = "Penjelasan" if lang == "id" else "Explanation"
        lines += [f"**{q['no']}. ({TYPE_LABEL[lang][q['tipe']]})** {ans}  ",
                  f"*{label}:* {q['penjelasan']}", ""]
    return "\n".join(lines).rstrip() + "\n"


def _docx(md_text: str, path: Path) -> bool:
    try:
        from docx import Document
    except ImportError:
        return False
    doc = Document()
    for line in md_text.splitlines():
        if line.startswith("<!--"):
            continue
        if line.startswith("# "):
            doc.add_heading(line[2:], level=1)
        elif line.startswith("## "):
            doc.add_heading(line[3:], level=2)
        elif line.strip():
            text = re.sub(r"\*\*(.+?)\*\*", r"\1", line.strip())
            text = re.sub(r"\*(.+?)\*", r"\1", text).rstrip()
            para = doc.add_paragraph(text)
            if line.startswith("   "):
                para.paragraph_format.left_indent = 360000  # EMU (~1 cm)
    doc.save(str(path))
    return True


def write_outputs(job: QuizJob, qs: list[dict], outdir: Path) -> list[Path]:
    import pandas as pd

    outdir.mkdir(parents=True, exist_ok=True)
    engine = qs[0]["engine"] if qs else "template"
    soal_md, kunci_md = _md_soal(job, qs, engine), _md_kunci(job, qs)
    files = [outdir / "soal.md", outdir / "kunci_jawaban.md"]
    files[0].write_text(soal_md, encoding="utf-8")
    files[1].write_text(kunci_md, encoding="utf-8")
    if _docx(soal_md, outdir / "soal.docx") and _docx(kunci_md, outdir / "kunci_jawaban.docx"):
        files += [outdir / "soal.docx", outdir / "kunci_jawaban.docx"]
    else:
        warn("python-docx belum terpasang; hanya Markdown yang dibuat (pip install python-docx).")
    generic = []
    for q in qs:
        opsi = q["opsi"] + [""] * (4 - len(q["opsi"]))
        generic.append({"no": q["no"], "tipe": q["tipe"], "pertanyaan": q["pertanyaan"],
                        "opsi_a": opsi[0], "opsi_b": opsi[1], "opsi_c": opsi[2],
                        "opsi_d": opsi[3], "kunci": q["kunci"], "jawaban": q["jawaban"],
                        "penjelasan": q["penjelasan"], "tingkat": job.kesulitan,
                        "mapel": job.mapel, "jenjang": job.jenjang, "engine": q["engine"]})
    seconds = {"mudah": 30, "sedang": 45, "sulit": 60}[job.kesulitan]
    quizizz = []
    qtype = {"pilihan_ganda": "Multiple Choice", "benar_salah": "Multiple Choice",
             "isian": "Fill-in-the-Blank", "esai": "Open-Ended"}
    for q in qs:
        opts = q["opsi"] if q["opsi"] else ([q["jawaban"]] if q["tipe"] == "isian" else [])
        opts = opts + [""] * (5 - len(opts))
        correct = (str(q["opsi"].index(q["jawaban"]) + 1) if q["jawaban"] in q["opsi"]
                   else ("1" if q["tipe"] == "isian" else ""))
        quizizz.append({"Question Text": q["pertanyaan"], "Question Type": qtype[q["tipe"]],
                        "Option 1": opts[0], "Option 2": opts[1], "Option 3": opts[2],
                        "Option 4": opts[3], "Option 5": opts[4], "Correct Answer": correct,
                        "Time in seconds": 120 if q["tipe"] == "esai" else seconds,
                        "Image Link": "", "Answer explanation": q["penjelasan"]})
    xlsx, csv = outdir / "kuis_export.xlsx", outdir / "kuis_export.csv"
    write_excel(xlsx, {"Soal": pd.DataFrame(generic), "Quizizz": pd.DataFrame(quizizz)})
    pd.DataFrame(generic).to_csv(csv, index=False, encoding="utf-8-sig")
    return files + [xlsx, csv]


def run(jobs: list[QuizJob], outdir: Path, *, llm: LLMClient | None, timpa: bool) -> list[Path]:
    files: list[Path] = []
    if llm is None:
        info("Mesin: TEMPLATE (offline, tanpa AI) — soal dibuat dari kalimat materi "
             "(isian/cloze, pengecoh dari materi). Periksa ulang sebelum dipakai.")
    for job in jobs:
        params = {k: v for k, v in asdict(job).items() if k != "materi"}
        tag = f"{stable_seed(params, job.materi, llm.model if llm else 'template') % 16**8:08x}"
        job_dir = outdir / slugify(job.nama, 40)
        items_dir = job_dir / "_items" / f"kuis-{tag}"
        info(f"== Kuis '{job.nama}': {job.jumlah} soal, {job.kesulitan}, "
             f"tipe {', '.join(job.tipe)}")
        if llm is None:
            res = run_batch([Item("semua", job, job.nama)], generate_template,
                            items_dir=items_dir, timpa=timpa, label="kuis")
            qs = res.results.get("semua", [])
        else:
            if len(job.materi) > 400_000:
                warn("Materi sangat panjang; biaya token input akan besar (materi dikirim "
                     "utuh, di-cache antar bagian).")
            plan = type_plan(job.tipe, job.jumlah)
            chunks = [plan[i:i + CHUNK] for i in range(0, len(plan), CHUNK)]
            keys = [f"bagian_{n:02d}" for n in range(1, len(chunks) + 1)]

            def worker(payload: tuple[int, list[str]], _dir: Path = items_dir,
                       _keys: list[str] = keys, _job: QuizJob = job) -> list[dict]:
                n, types = payload
                existing = []
                for k in _keys[:n]:
                    path = _dir / f"{k}.json"
                    if path.exists():
                        existing += [q["pertanyaan"] for q in read_json(path)]
                return generate_ai_chunk(llm, _job, types, existing)

            items = [Item(k, (n, c), f"{job.nama} bagian {n + 1}/{len(chunks)}")
                     for n, (k, c) in enumerate(zip(keys, chunks, strict=True))]
            res = run_batch(items, worker, items_dir=items_dir, concurrency=1, timpa=timpa,
                            label="bagian kuis")
            qs = [q for k in keys for q in res.results.get(k, [])]
            qs = _number(qs, engine=f"ai:{llm.model}")
        if not qs:
            warn(f"Tidak ada soal untuk '{job.nama}'.")
            continue
        files += write_outputs(job, qs, job_dir)
    if not files:
        raise KontenError("Tidak ada kuis yang berhasil dibuat.")
    return files
