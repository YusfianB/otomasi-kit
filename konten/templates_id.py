"""Phrase banks and helpers for the offline ``template`` engine.

Templates use ``{placeholder}`` fields. Common ones:
  brand, niche, audiens, produk, produk2, keunggulan, keunggulan2, masalah, lokasi, promo,
  link_cta, e (emoji; empty for formal tone)
Register tokens (resolved per tone, see ``register_tokens``):
  kamu/Kamu, yuk/Yuk, nih, bikin, nggak, aja, udah
Every placeholder also has a capitalised variant (``{Produk}``) filled automatically.
A template is only used when every placeholder it needs has a value (so optional data such
as ``promo``/``lokasi``/``produk2`` never leaves holes).
"""

from __future__ import annotations

import random
import re
import string
from typing import Any

# --------------------------------------------------------------------------- helpers


class Cycler:
    """Draw items in shuffled order without repetition until the pool is exhausted."""

    def __init__(self, rng: random.Random, items: list[Any]) -> None:
        self.rng = rng
        self.items = list(items)
        self.pool: list[Any] = []
        self.last: Any = None

    def next(self) -> Any:
        if not self.items:
            raise ValueError("Cycler kosong")
        if not self.pool:
            self.pool = list(self.items)
            self.rng.shuffle(self.pool)
            if len(self.pool) > 1 and self.pool[-1] == self.last:
                self.pool[0], self.pool[-1] = self.pool[-1], self.pool[0]
        self.last = self.pool.pop()
        return self.last


_FORMATTER = string.Formatter()


def fields_of(template: str) -> set[str]:
    return {f for _, f, _, _ in _FORMATTER.parse(template) if f}


ALWAYS_OK = {"e", "kamu", "yuk", "nih", "bikin", "nggak", "aja", "udah"}


def usable(templates: list[str], values: dict[str, str]) -> list[str]:
    ok = []
    for t in templates:
        need = {f for f in fields_of(t) if f.lower() not in ALWAYS_OK}
        if all(values.get(f) or values.get(f[:1].lower() + f[1:]) for f in need):
            ok.append(t)
    return ok


def fill(template: str, values: dict[str, str]) -> str:
    full = dict(values)
    for k, v in values.items():
        if v and k[:1].islower():
            full.setdefault(k[:1].upper() + k[1:], v[:1].upper() + v[1:])
    text = template.format_map(_Default(full))
    return tidy(text)


class _Default(dict):
    def __missing__(self, key: str) -> str:
        return ""


def tidy(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" +([,.!?:;])", r"\1", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


CASUAL_TONES = {"santai", "lucu", "casual", "fun", "playful"}
FORMAL_TONES = {"profesional", "edukatif", "formal", "professional", "educational"}
TONES = ("santai", "profesional", "lucu", "inspiratif", "edukatif")


def register_tokens(tone: str, lang: str, formal_platform: bool = False) -> dict[str, str]:
    if lang == "en":
        return {"kamu": "you", "yuk": "let's", "nih": "", "bikin": "make", "nggak": "don't",
                "aja": "", "udah": "already"}
    formal = formal_platform or tone in FORMAL_TONES
    casual = (tone in CASUAL_TONES) and not formal_platform
    return {
        "kamu": "Anda" if formal else "kamu",
        "yuk": "yuk" if casual else "mari",
        "nih": "nih" if casual else "",
        "bikin": "bikin" if casual else "membuat",
        "nggak": "nggak" if casual else "tidak",
        "aja": "aja" if casual else "saja",
        "udah": "udah" if casual else "sudah",
    }


# --------------------------------------------------------------------------- sosmed

EMOJI = {
    "edukasi": ["💡", "📌", "✅", "📝"],
    "promosi": ["🔥", "🛒", "🎉", "✨"],
    "bts": ["🎬", "👀", "🛠️", "☕"],
    "testimoni": ["⭐", "💬", "🙌", "❤️"],
    "interaksi": ["👇", "🤔", "💬", "🗳️"],
    "cerita": ["🌱", "💪", "✨", "🤍"],
    "hiburan": ["😂", "🤭", "😅", "🙈"],
}

LINK_CTA = {
    "id": {
        "instagram": "klik link di bio",
        "tiktok": "cek keranjang kuning atau link di bio",
        "linkedin": "kunjungi tautan di kolom komentar",
        "x": "cek link di bio",
        "facebook": "klik tautan di postingan ini",
    },
    "en": {
        "instagram": "tap the link in bio",
        "tiktok": "check the link in bio",
        "linkedin": "visit the link in the comments",
        "x": "check the link in bio",
        "facebook": "click the link in this post",
    },
}

SOSMED: dict[str, dict[str, Any]] = {
    "id": {
        "pilar": {
            "edukasi": {
                "label": "Edukasi & Tips",
                "topik": ["Tips memilih {niche}", "Mitos vs fakta {niche}",
                          "Kesalahan umum soal {niche}", "Panduan singkat {niche}"],
                "hook": [
                    "Sebelum memilih {niche}, perhatikan 3 hal ini dulu {e}",
                    "Simpan dulu postingan ini, nanti pasti kepakai! {e}",
                    "Mitos atau fakta: {niche} yang bagus pasti mahal?",
                    "Masih {masalah}? Coba cara sederhana ini.",
                    "Banyak yang belum tahu hal ini tentang {niche} {e}",
                    "Panduan singkat buat {audiens} yang baru kenal {niche}.",
                    "3 kesalahan yang sering terjadi saat memilih {niche}.",
                ],
                "body": [
                    ("Memilih {niche} itu gampang-gampang susah. Ini tiga hal yang kami sarankan:\n\n"
                    "1. Cek kualitas bahan dan prosesnya.\n"
                    "2. Baca ulasan pembeli lain dengan teliti.\n"
                    "3. Sesuaikan dengan kebutuhan dan budget {kamu}.\n\n"
                    "Nilai plus dari {brand}: {keunggulan}."),
                    ("Mitos: {niche} berkualitas pasti mahal.\n"
                    "Fakta: yang paling penting adalah kualitas dan kecocokan dengan kebutuhan "
                    "{kamu}.\n\nDi {brand}, {produk} hadir dengan nilai plus: {keunggulan}."),
                    ("Kalau {kamu} sering {masalah}, mulai dari langkah kecil:\n\n"
                    "✔ Kenali dulu kebutuhan utama {kamu}.\n"
                    "✔ Pilih {niche} yang sudah jelas kualitasnya.\n"
                    "✔ Konsisten, karena hasil terbaik datang dari kebiasaan.\n\n"
                    "{Produk} bisa jadi teman yang pas untuk memulai."),
                    ("Kesalahan yang sering terjadi:\n\n"
                    "✖ Tergiur harga murah tanpa cek kualitas.\n"
                    "✖ Asal ikut tren tanpa tahu kebutuhan sendiri.\n"
                    "✖ Malas bertanya sebelum membeli.\n\n"
                    "Jangan ragu tanya-tanya dulu ke tim {brand}, kami bantu pilihkan yang paling "
                    "cocok untuk {kamu}."),
                ],
                "pendek": [
                    ("Tips memilih {niche}: cek kualitas, baca ulasan, sesuaikan budget. "
                    "Di {brand}: {keunggulan}."),
                    ("Mitos: {niche} bagus pasti mahal. Fakta: yang penting kualitas & cocok "
                    "dengan kebutuhan {kamu}."),
                ],
                "visual": [
                    ("Carousel 5 slide: slide 1 judul besar berisi hook, slide 2–4 satu tips per "
                    "slide dengan ikon sederhana, slide 5 logo {brand} + ajakan simpan postingan."),
                    ("Infografis 'Mitos vs Fakta' dua kolom (merah vs hijau) dengan foto {produk} "
                    "di tengah."),
                    "Foto flat-lay {produk} dengan teks overlay berisi 3 poin tips.",
                ],
                "pasangan": {0: {"hook": [0], "visual": [0], "topik": [0]},
                             1: {"hook": [2], "visual": [1], "topik": [1]},
                             2: {"hook": [3], "topik": [3]},
                             3: {"hook": [6], "topik": [2]}},
                "cta": ["Simpan postingan ini dan bagikan ke teman yang butuh {e}",
                        "Ada pertanyaan? Tulis di kolom komentar, kami jawab satu per satu.",
                        "Follow {brand} untuk tips {niche} lainnya."],
            },
            "promosi": {
                "label": "Promo & Produk",
                "topik": ["Highlight produk: {produk}", "Promo: {promo}",
                          "Keunggulan: {keunggulan}"],
                "hook": [
                    "{Promo}! Jangan sampai kelewatan {e}",
                    "Kenalan dulu sama {produk}, favorit pelanggan {brand} {e}",
                    "Cari {niche} yang bisa diandalkan? Ini jawabannya.",
                    "Kenapa {produk} selalu jadi incaran? {e}",
                    "Stok terbatas! {Produk} siap menemani hari {kamu}.",
                    "Buat {audiens}, ini rekomendasi dari {brand}.",
                ],
                "body": [
                    ("{Produk} hadir buat {kamu} yang ingin {niche} berkualitas tanpa ribet.\n\n"
                    "Kenapa harus {produk}?\n✔ {Keunggulan}\n✔ {Keunggulan2}\n\n{Promo}."),
                    ("Kabar baik buat {audiens}! {Promo}.\n\n"
                    "Momen yang pas untuk mencoba {produk} — {keunggulan}."),
                    ("Satu alasan kenapa pelanggan balik lagi ke {brand}: {keunggulan}.\n\n"
                    "Kalau {kamu} belum pernah coba {produk}, sekarang waktu yang tepat."),
                    ("Masih {masalah}? {Produk} dari {brand} bisa jadi solusinya.\n\n"
                    "✔ {Keunggulan}\n✔ {Keunggulan2}"),
                ],
                "pendek": [
                    "{Promo}! {Produk} dari {brand}: {keunggulan}.",
                    "Cari {niche} yang bisa diandalkan? Coba {produk} — {keunggulan}.",
                    "Alasan {produk} jadi favorit: {keunggulan}.",
                ],
                "visual": [
                    ("Foto produk {produk} close-up dengan latar warna brand, teks harga/promo "
                    "besar di pojok."),
                    "Video 10 detik unboxing {produk} dengan transisi cepat dan teks promo.",
                    "Foto {produk} dipakai/dinikmati pelanggan dalam suasana sehari-hari.",
                ],
                "pasangan": {0: {"topik": [1]}, 1: {"topik": [1]}, 2: {"hook": [0]},
                             3: {"hook": [0]}},
                "cta": ["Order sekarang, {link_cta} {e}", "Chat admin untuk pesan, {link_cta}.",
                        "Checkout sebelum promonya berakhir, {link_cta}."],
            },
            "bts": {
                "label": "Behind the Scenes",
                "topik": ["Proses pembuatan {produk}", "Keseharian tim {brand}",
                          "Proses packing pesanan"],
                "hook": [
                    "Intip proses di balik {produk} yang jarang orang lihat {e}",
                    "Begini suasana {brand} sebelum pesanan {kamu} dikirim.",
                    "Dari bahan sampai siap dikirim: perjalanan {produk} {e}",
                    "Ini alasan kami {nggak} pernah buru-buru saat membuat {produk}.",
                    "Satu hari bersama tim {brand} {e}",
                ],
                "body": [
                    ("Setiap {produk} melewati beberapa tahap sebelum sampai di tangan {kamu}: "
                    "pemilihan bahan, proses pembuatan, pengecekan kualitas, lalu packing yang "
                    "rapi.\n\nSemua demi kualitas yang konsisten untuk {kamu}."),
                    ("Di balik layar {brand}, ada tim kecil yang memastikan setiap pesanan dikemas "
                    "dengan teliti. Prinsip kami sederhana: setiap pesanan harus sampai dalam "
                    "kondisi terbaik."),
                    ("Banyak yang tanya, kenapa {produk} bisa konsisten? Jawabannya ada di proses. "
                    "Kami cek ulang setiap tahap supaya {kamu} dapat yang terbaik."),
                ],
                "pendek": [
                    ("Di balik setiap {produk}: pilih bahan, proses, cek kualitas, packing rapi. "
                    "Semua demi kualitas yang konsisten."),
                    "Tim kecil {brand} mengecek setiap pesanan sebelum dikirim ke {kamu}.",
                ],
                "visual": [
                    "Video time-lapse proses pembuatan/packing {produk}, tanpa wajah juga oke.",
                    "Foto candid tim {brand} sedang bekerja, teks overlay 'Di balik layar'.",
                    "Carousel urutan proses: bahan → produksi → quality control → siap kirim.",
                ],
                "pasangan": {0: {"hook": [0, 2, 3], "visual": [0, 2], "topik": [0]},
                             1: {"hook": [1, 4], "visual": [1], "topik": [1, 2]},
                             2: {"hook": [0, 3], "visual": [0, 2], "topik": [0]}},
                "cta": ["Mau lihat proses lainnya? Tulis 'MAU' di komentar {e}",
                        "Follow {brand} biar {nggak} ketinggalan cerita di balik layar.",
                        "Penasaran hasilnya? {Link_cta}."],
            },
            "testimoni": {
                "label": "Bukti Sosial",
                "topik": ["Ulasan pelanggan {produk}", "Repost konten pelanggan",
                          "Rangkuman ulasan bulan ini", "Ajakan berbagi ulasan"],
                "hook": [
                    "Kata mereka yang sudah mencoba {produk} {e}",
                    "Ulasan jujur pelanggan {brand} minggu ini.",
                    "Ini yang {bikin} kami semangat setiap hari {e}",
                    "Dari pelanggan, untuk calon pelanggan {e}",
                    "Sudah coba {produk}? Kami mau dengar cerita {kamu} {e}",
                ],
                "body": [
                    ("“[Tempel kutipan ulasan asli pelanggan di sini]”\n— [nama/inisial pelanggan, "
                    "dengan izin]\n\nTerima kasih sudah percaya pada {brand}! Ulasan seperti ini "
                    "yang membuat kami terus semangat melayani."),
                    ("Kami kumpulkan beberapa ulasan pelanggan tentang {produk} "
                    "[tempel screenshot ulasan asli]. Terima kasih untuk setiap masukan, baik "
                    "pujian maupun kritik!"),
                    ("Sudah pakai {produk}? Ceritakan pengalaman {kamu} di kolom komentar — ulasan "
                    "terbaik akan kami tampilkan di sini (dengan izin)."),
                ],
                "pendek": [
                    "Ulasan pelanggan {brand}: “[kutip ulasan asli]”. Terima kasih sudah percaya!",
                    "Sudah coba {produk}? Ceritakan pengalaman {kamu}, ulasan terbaik kami repost.",
                ],
                "visual": [
                    "Screenshot ulasan asli (sensor data pribadi) di atas latar warna brand.",
                    ("Repost foto/video pelanggan (minta izin dulu) dengan bingkai bertuliskan "
                    "'Kata Mereka'."),
                    ("Foto {produk} dengan teks besar 'Ceritakan pengalamanmu!' + stiker "
                    "pertanyaan di Story."),
                ],
                "pasangan": {0: {"hook": [0, 1, 2, 3], "visual": [0, 1], "topik": [0, 1]},
                             1: {"hook": [0, 1, 2, 3], "visual": [0], "topik": [0, 2]},
                             2: {"hook": [4], "visual": [2], "topik": [3]}},
                "cta": ["Bagikan pengalamanmu dan tag {brand} {e}",
                        "Mau merasakan sendiri? {Link_cta}.",
                        "Tulis ulasan {kamu} di komentar, kami tunggu!"],
            },
            "interaksi": {
                "label": "Interaksi & Engagement",
                "topik": ["Polling pilihan produk", "Pertanyaan untuk audiens",
                          "Isi titik-titik bareng audiens"],
                "hook": [
                    "Tim mana {nih}: {produk} atau {produk2}? {e}",
                    "Jujur-jujuran: kapan terakhir kali {kamu} {masalah}?",
                    "Isi titik-titik ini: hari terasa lengkap kalau ada ... {e}",
                    "Kami mau dengar pendapat {kamu}! {e}",
                    "Kalau cuma boleh pilih satu {niche}, {kamu} pilih yang mana?",
                ],
                "body": [
                    ("Dua-duanya favorit, tapi kami penasaran pilihan {kamu}.\n\n"
                    "🅰️ {Produk}\n🅱️ {Produk2}\n\nJawab A atau B di kolom komentar ya!"),
                    ("Kami lagi menyiapkan varian/konten baru dan ingin tahu kebutuhan "
                    "{audiens}. Apa satu hal yang paling {kamu} cari dari {niche}? "
                    "Tulis di komentar, semua masukan kami baca."),
                    ("Hampir semua orang pernah {masalah}. Ceritakan pengalaman {kamu} di komentar, "
                    "siapa tahu cerita {kamu} membantu orang lain."),
                    ("Jawaban kami jelas: {produk} {e}\n\nKalau {kamu}? Tulis jawaban {kamu} di "
                    "kolom komentar ya!"),
                ],
                "pendek": [
                    "{Produk} atau {produk2}? Balas dengan A atau B!",
                    "Satu hal yang paling {kamu} cari dari {niche}? Balas di sini.",
                ],
                "visual": [
                    "Gambar split-screen dua produk dengan huruf A dan B besar.",
                    "Story Instagram dengan stiker polling/kuis, lalu repost hasilnya ke feed.",
                    "Teks besar di latar polos berisi pertanyaan, font tebal warna brand.",
                ],
                "pasangan": {0: {"hook": [0, 4], "visual": [0], "topik": [0]},
                             1: {"hook": [3], "topik": [1]}, 2: {"hook": [1], "topik": [1]},
                             3: {"hook": [2], "topik": [2]}},
                "cta": ["Jawab di kolom komentar {e}", "Tag teman yang pilihannya pasti beda!",
                        "Vote sekarang, hasilnya kami umumkan minggu depan."],
            },
            "cerita": {
                "label": "Cerita Brand & Inspirasi",
                "topik": ["Cerita awal {brand}", "Nilai yang dipegang {brand}",
                          "Misi {brand} untuk {audiens}"],
                "hook": [
                    "Awalnya {brand} cuma ide kecil. Ini ceritanya {e}",
                    "Kenapa kami memulai {brand}?",
                    "Satu prinsip yang kami pegang sejak hari pertama {e}",
                    "Untuk {kamu} yang sedang berjuang, cerita ini untuk {kamu}.",
                ],
                "body": [
                    ("{Brand} lahir dari keinginan sederhana: menghadirkan {niche} yang bisa "
                    "diandalkan untuk {audiens}. Dari situ kami belajar, jatuh-bangun, dan terus "
                    "memperbaiki diri.\n\nSalah satu hal yang terus kami jaga: {keunggulan}."),
                    ("Kami percaya usaha kecil bisa memberi dampak besar. Setiap pesanan dari "
                    "{kamu} membantu tim lokal kami terus berkarya.\n\nTerima kasih sudah menjadi "
                    "bagian dari perjalanan {brand}."),
                    ("Kami melihat banyak {audiens} yang {masalah}. Itulah alasan {brand} ada: "
                    "membantu dengan cara yang sederhana dan jujur."),
                ],
                "pendek": [
                    ("{Brand} lahir dari ide sederhana: {niche} yang bisa diandalkan. "
                    "Yang terus kami jaga: {keunggulan}."),
                    ("Setiap pesanan dari {kamu} membantu tim lokal {brand} terus berkarya. "
                    "Terima kasih!"),
                ],
                "visual": [
                    "Foto lama vs sekarang (awal usaha vs kondisi saat ini) berdampingan.",
                    "Video pendek founder bercerita 30 detik, latar tempat usaha.",
                    "Kutipan inspiratif dengan tipografi bersih dan logo {brand}.",
                ],
                "pasangan": {0: {"hook": [0, 1, 2], "visual": [0], "topik": [0, 1]},
                             1: {"hook": [3], "topik": [1]}, 2: {"hook": [1, 3], "topik": [2]}},
                "cta": ["Dukung produk lokal dengan follow {brand} {e}",
                        "Ceritakan perjalanan usaha {kamu} di komentar!",
                        "Bagikan jika cerita ini relate dengan {kamu}."],
            },
            "hiburan": {
                "label": "Hiburan & Relatable",
                "topik": ["Konten relatable", "POV pelanggan", "Meme seputar {niche}"],
                "hook": [
                    "POV: akhirnya nemu {niche} yang pas di hati {e}",
                    "Tanda-tanda {kamu} butuh {produk} sekarang juga {e}",
                    "Ekspektasi vs realita {e}",
                    "Siapa di sini yang {masalah}? Ngaku aja {e}",
                ],
                "body": [
                    ("Tanda-tanda {kamu} butuh {produk}:\n\n1. Sering {masalah}.\n"
                    "2. Udah coba macam-macam tapi belum cocok.\n3. Lagi baca postingan ini 😅\n\n"
                    "Tenang, {brand} siap bantu."),
                    ("Ekspektasi: semuanya lancar.\nRealita: {masalah}.\n\nUntung ada {produk} "
                    "dari {brand} {e}"),
                    ("Kalau {kamu} senyum-senyum baca ini, berarti {kamu} juga pernah {masalah}. "
                    "Tag teman yang paling relate!"),
                ],
                "pendek": [
                    "Ekspektasi: lancar jaya. Realita: {masalah}. Untung ada {produk} {e}",
                    "POV: akhirnya nemu {niche} yang pas. Namanya {produk} {e}",
                ],
                "visual": [
                    "Meme format 'ekspektasi vs realita' dengan foto {produk} di panel akhir.",
                    "Video POV pakai sound yang sedang tren, teks di layar mengikuti ketukan.",
                ],
                "pasangan": {0: {"hook": [1]}, 1: {"hook": [2], "visual": [0]},
                             2: {"hook": [3]}},
                "cta": ["Tag teman yang paling relate {e}",
                        "Share ke grup bestie kamu!", "Follow buat konten receh lainnya {e}"],
            },
        },
        "topik_hook": "{Topik} {e}",
        "topik_body": (
            "[Tulis 2–3 kalimat penjelasan singkat tentang topik ini dengan bahasa sehari-hari.]"
            "\n\nPoin penting:\n1. [poin pertama]\n2. [poin kedua]\n3. [poin ketiga]\n\n"
            "Masih ada pertanyaan soal {niche}? Tulis di komentar, tim {brand} siap jawab."
        ),
        "topik_pendek": "{Topik}: [tulis 1 fakta/tips singkat di sini] {e}",
        "video": [
            "0–3 dtk (hook): teks besar di layar \"{hook}\" + {visual_open}",
            "3–12 dtk: {point1}",
            "12–22 dtk: {point2}",
            "22–30 dtk (CTA): tampilkan logo/akun {brand} dan ucapkan \"{cta}\"",
            "Audio: {audio}. Tambahkan subtitle karena banyak orang menonton tanpa suara.",
        ],
        "video_open": ["close-up {produk}", "wajah kaget/penasaran ke kamera",
                       "tangan menunjukkan {produk}", "transisi cepat dari masalah ke solusi"],
        "video_point": {
            "edukasi": ["jelaskan tips pertama sambil menunjukkan contoh nyata",
                        "tunjukkan perbandingan cara yang salah vs benar",
                        "tutup dengan ringkasan 3 poin di layar"],
            "promosi": ["tunjukkan {produk} dari berbagai sudut",
                        "sebutkan keunggulan: {keunggulan}",
                        "tampilkan info promo/harga dengan teks besar"],
            "bts": ["rekam proses kerja dengan potongan cepat",
                    "tunjukkan detail kecil yang menjaga kualitas",
                    "tampilkan pesanan siap kirim"],
            "testimoni": ["tampilkan ulasan asli pelanggan (screenshot/video, dengan izin)",
                          "tunjukkan {produk} yang diulas",
                          "ucapkan terima kasih ke pelanggan"],
            "interaksi": ["tampilkan pertanyaan dengan teks besar di layar",
                          "ajak penonton menjawab di komentar",
                          "janjikan jawaban terbaik dibahas di video berikutnya"],
            "cerita": ["ceritakan momen awal {brand} dengan foto lama",
                       "tunjukkan kondisi sekarang",
                       "sampaikan nilai yang dipegang: {keunggulan}"],
            "hiburan": ["peragakan situasi lucu saat {masalah}",
                        "munculkan {produk} sebagai penyelamat",
                        "akhiri dengan ekspresi lega/lucu"],
        },
        "audio": ["sound yang sedang tren di For You Page", "voice over santai",
                  "musik latar ringan tanpa lirik"],
        "default_keunggulan": ["kualitas yang terjaga", "pelayanan yang ramah dan cepat",
                               "harga yang bersahabat"],
        "default_masalah": ["bingung memilih {niche} yang pas", "takut salah beli"],
        "generic_tags": {
            "instagram": ["umkmindonesia", "produklokal", "banggabuatanindonesia"],
            "tiktok": ["fyp", "tiktokshop"],
            "linkedin": ["UMKM", "BisnisIndonesia", "Pemasaran"],
            "x": [],
            "facebook": ["umkmindonesia"],
        },
    },
    "en": {
        "pilar": {
            "edukasi": {
                "label": "Education & Tips",
                "topik": ["How to choose {niche}", "{niche} myths vs facts"],
                "hook": ["Before you choose {niche}, check these 3 things {e}",
                         "Save this post, you'll need it later! {e}",
                         "Myth or fact: good {niche} is always expensive?",
                         "A quick guide for {audiens} who are new to {niche}."],
                "body": [
                    ("Choosing {niche} doesn't have to be hard. Here's what we recommend:\n\n"
                    "1. Check the quality of materials and process.\n2. Read other buyers' "
                    "reviews.\n3. Match it to your needs and budget.\n\nAt {brand}, we focus on "
                    "one thing: {keunggulan}."),
                    ("Myth: quality {niche} is always expensive.\nFact: what matters is quality "
                    "and fit for your needs.\n\n{Produk} by {brand} comes with {keunggulan}."),
                ],
                "pendek": [("Choosing {niche}: check quality, read reviews, fit your budget. "
                           "At {brand}: {keunggulan}.")],
                "visual": [("5-slide carousel: hook on slide 1, one tip per slide, CTA on the "
                           "last slide."), "Flat-lay photo of {produk} with 3 tips as overlay."],
                "pasangan": {0: {"hook": [0]}, 1: {"hook": [2]}},
                "cta": ["Save this post and share it with a friend {e}",
                        "Questions? Drop them in the comments."],
            },
            "promosi": {
                "label": "Promo & Product",
                "topik": ["Product highlight: {produk}", "Promo: {promo}"],
                "hook": ["{Promo}! Don't miss it {e}", "Meet {produk}, a {brand} favourite {e}",
                         "Looking for {niche} you can rely on? Here it is."],
                "body": [("{Produk} is made for people who want quality {niche} without the "
                         "hassle.\n\nWhy {produk}?\n✔ {Keunggulan}\n✔ {Keunggulan2}"),
                         "Good news for {audiens}! {Promo}. The perfect time to try {produk}."],
                "pendek": ["{Promo}! {Produk} by {brand}: {keunggulan}."],
                "visual": ["Close-up product shot of {produk} with the promo in large text."],
                "cta": ["Order now, {link_cta} {e}", "Message us to order, {link_cta}."],
            },
            "bts": {
                "label": "Behind the Scenes",
                "topik": ["How {produk} is made"],
                "hook": ["A peek behind the scenes of {produk} {e}",
                         "From raw materials to your door: the journey of {produk}."],
                "body": [("Every {produk} goes through careful steps: choosing materials, "
                         "making, quality check and neat packing. That's how we keep "
                         "{keunggulan}.")],
                "pendek": [("Behind every {produk}: materials, making, QC, packing. "
                           "That's how we keep {keunggulan}.")],
                "visual": ["Time-lapse video of the packing process."],
                "cta": ["Want to see more? Comment 'MORE' {e}"],
            },
            "testimoni": {
                "label": "Social Proof",
                "topik": ["Customer review"],
                "hook": ["What customers say about {produk} {e}"],
                "body": [("“[Paste a real customer review here]”\n— [customer name, with "
                         "permission]\n\nThank you for trusting {brand}!")],
                "pendek": ["Customer review: “[paste a real review]”. Thank you!"],
                "visual": ["Screenshot of a real review on a brand-colour background."],
                "cta": ["Share your experience and tag {brand} {e}"],
            },
            "interaksi": {
                "label": "Engagement",
                "topik": ["Product poll"],
                "hook": ["Team {produk} or team {produk2}? {e}",
                         "We want to hear from you! {e}"],
                "body": [("Both are favourites, but we're curious.\n\n🅰️ {Produk}\n🅱️ {Produk2}"
                         "\n\nReply A or B in the comments!"),
                         ("What's the one thing you look for in {niche}? Tell us in the "
                         "comments.")],
                "pendek": ["{Produk} or {produk2}? Reply A or B!"],
                "visual": ["Split-screen image of two products with big A and B letters."],
                "pasangan": {0: {"hook": [0], "visual": [0]}, 1: {"hook": [1]}},
                "cta": ["Answer in the comments {e}"],
            },
            "cerita": {
                "label": "Brand Story",
                "topik": ["How {brand} started"],
                "hook": ["{Brand} started as a small idea. Here's the story {e}",
                         "Why did we start {brand}?"],
                "body": [("{Brand} was born from a simple wish: reliable {niche} for {audiens}. "
                         "Our promise stays the same: {keunggulan}.")],
                "pendek": [("{Brand} started with a simple idea: reliable {niche}. "
                           "Our promise: {keunggulan}.")],
                "visual": ["Then-vs-now photos side by side."],
                "cta": ["Support local by following {brand} {e}"],
            },
            "hiburan": {
                "label": "Fun & Relatable",
                "topik": ["Relatable content"],
                "hook": ["POV: you finally found {niche} you love {e}",
                         "Expectation vs reality {e}"],
                "body": [("Expectation: everything goes smoothly.\nReality: {masalah}.\n\n"
                         "Good thing {produk} exists {e}")],
                "pendek": [("Expectation: smooth. Reality: {masalah}. Thank goodness for "
                           "{produk} {e}")],
                "visual": ["'Expectation vs reality' meme with {produk} in the last panel."],
                "cta": ["Tag a friend who can relate {e}"],
            },
        },
        "topik_hook": "{Topik} {e}",
        "topik_body": ("[Write 2–3 sentences explaining this topic in plain words.]\n\n"
                       "Key points:\n1. [point one]\n2. [point two]\n3. [point three]\n\n"
                       "Questions about {niche}? Ask in the comments, {brand} will answer."),
        "topik_pendek": "{Topik}: [write one short fact/tip here] {e}",
        "video": [
            "0–3s (hook): big on-screen text \"{hook}\" + {visual_open}",
            "3–12s: {point1}",
            "12–22s: {point2}",
            "22–30s (CTA): show the {brand} logo/handle and say \"{cta}\"",
            "Audio: {audio}. Add subtitles, many people watch without sound.",
        ],
        "video_open": ["close-up of {produk}", "surprised face to camera"],
        "video_point": {
            "edukasi": ["explain the first tip with a real example", "show wrong vs right"],
            "promosi": ["show {produk} from several angles", "mention: {keunggulan}"],
            "bts": ["quick cuts of the work process", "show orders ready to ship"],
            "testimoni": ["show a real customer review (with permission)", "show {produk}"],
            "interaksi": ["show options A and B", "ask viewers to answer in comments"],
            "cerita": ["tell how {brand} started", "show where you are now"],
            "hiburan": ["act out the funny situation", "reveal {produk} as the fix"],
        },
        "audio": ["a trending sound", "casual voice over"],
        "default_keunggulan": ["consistent quality", "friendly, fast service", "fair prices"],
        "default_masalah": ["not sure which {niche} to choose"],
        "generic_tags": {
            "instagram": ["smallbusiness", "shoplocal", "madeinindonesia"],
            "tiktok": ["fyp", "tiktokshop"],
            "linkedin": ["SmallBusiness", "Marketing"],
            "x": [],
            "facebook": ["smallbusiness"],
        },
    },
}

STOPWORDS_ID = frozenset({
    "ada", "adalah", "agar", "akan", "aku", "anda", "antara", "apa", "apabila", "atau",
    "bagai", "bagaimana", "bagi", "bahkan", "bahwa", "banyak", "baru", "beberapa", "begitu",
    "belum", "benar", "berbagai", "bersama", "besar", "biasanya", "bila", "bisa", "buat",
    "bukan", "cara", "contohnya", "cukup", "dalam", "dan", "dapat", "dari", "daripada",
    "dengan", "di", "dia", "dikenal", "disebut", "dll", "dsb", "hal", "hanya", "harus",
    "hingga", "ia", "ini", "itu", "jadi", "jika", "juga", "jumlah", "kalau", "kali", "kami",
    "kamu", "karena", "ke", "kebanyakan", "kecil", "kembali", "kemudian", "kepada", "ketika",
    "kita", "lagi", "lain", "lalu", "lebih", "maka", "masih", "melalui", "memang", "mereka",
    "merupakan", "misalnya", "mulai", "namun", "nya", "oleh", "pada", "paling", "para",
    "pernah", "pula", "pun", "saat", "saja", "salah", "sama", "sambil", "sampai", "sangat",
    "satu", "sebagai", "sebelum", "sebuah", "sebut", "secara", "sedang", "sehingga", "sejak",
    "sekali", "selain", "selalu", "seluruh", "semua", "sendiri", "seperti", "serta",
    "setelah", "setiap", "sudah", "supaya", "tanpa", "tapi", "telah", "tentang", "terhadap",
    "termasuk", "tersebut", "tetapi", "tidak", "umumnya", "untuk", "yaitu", "yakni", "yang",
})

STOPWORDS_EN = frozenset({
    "a", "about", "after", "all", "also", "an", "and", "any", "are", "as", "at", "be", "been",
    "before", "being", "but", "by", "called", "can", "could", "did", "do", "does", "each",
    "few", "for", "from", "he", "her", "his", "how", "if", "in", "into", "is", "it", "its",
    "just", "known", "may", "might", "more", "most", "must", "no", "not", "of", "often", "on",
    "only", "or", "other", "our", "over", "own", "same", "she", "should", "so", "some",
    "such", "than", "that", "the", "their", "them", "then", "there", "these", "they", "this",
    "those", "to", "too", "usually", "very", "was", "we", "were", "what", "when", "where",
    "which", "who", "whom", "whose", "why", "will", "with", "would", "yes", "you", "your",
})


# --------------------------------------------------------------------------- artikel

ARTIKEL: dict[str, dict[str, Any]] = {
    "id": {
        "intent_words": {
            "transaksional": ["beli", "harga", "jual", "promo", "diskon", "murah", "order",
                              "pesan", "sewa", "jasa", "toko", "terdekat"],
            "komersial": ["terbaik", "rekomendasi", "review", "ulasan", "vs", "perbandingan",
                          "bagus", "merek", "top", "pilihan"],
            "informasional": ["cara", "apa", "tips", "panduan", "adalah", "manfaat", "pengertian",
                              "contoh", "kenapa", "mengapa", "bagaimana", "tutorial", "langkah"],
        },
        "modifiers": {
            "informasional": ["cara {kw}", "{kw} untuk pemula", "tips {kw}", "manfaat {kw}",
                              "kesalahan saat {kw}", "panduan {kw} lengkap", "{kw} adalah",
                              "contoh {kw}", "langkah {kw}", "biaya {kw}"],
            "komersial": ["{kw} terbaik", "rekomendasi {kw}", "{kw} terbaik {tahun}",
                          "review {kw}", "kelebihan dan kekurangan {kw}", "{kw} murah tapi bagus",
                          "perbandingan {kw}", "cara memilih {kw}"],
            "transaksional": ["harga {kw} terbaru", "{kw} murah", "beli {kw} online",
                              "{kw} terdekat", "promo {kw}", "tips membeli {kw}",
                              "{kw} original", "{kw} COD"],
        },
        "h1": {
            "informasional": ["{Kw}: Panduan Lengkap untuk Pemula",
                              "Panduan {Kw} Langkah demi Langkah ({tahun})",
                              "{Kw}: Cara, Tips, dan Kesalahan yang Perlu Dihindari"],
            "komersial": ["Rekomendasi {Kw} Terbaik ({tahun}) dan Cara Memilihnya",
                          "{Kw}: Panduan Memilih yang Tepat Sesuai Kebutuhan"],
            "transaksional": ["Harga {Kw} Terbaru {tahun} dan Tips Membeli",
                              "{Kw}: Daftar Harga, Tempat Beli, dan Tips Agar Tidak Tertipu"],
        },
        "sections": {
            "informasional": [
                ("Apa Itu {Kw}?", [], [
                    ("Sebelum masuk ke langkah praktis, mari pahami dulu dasar-dasar {kw}. "
                    "Banyak {audiens} mencari informasi ini karena ingin hasil yang lebih baik "
                    "tanpa harus coba-coba terlalu lama."),
                    ("> **Catatan editor:** tambahkan definisi singkat {kw} dalam 2–3 kalimat, "
                    "idealnya dengan rujukan sumber tepercaya."),
                ]),
                ("Manfaat {Kw}", ["Manfaat untuk Pemula", "Manfaat Jangka Panjang"], [
                    ("Memahami {kw} dengan benar membantu Anda menghemat waktu, biaya, dan tenaga. "
                    "Manfaatnya biasanya terasa sejak awal dan semakin besar jika dilakukan "
                    "secara konsisten."),
                ]),
                ("Cara {Kw} Langkah demi Langkah",
                 ["Langkah 1: Siapkan Kebutuhan Dasar", "Langkah 2: Mulai dari Skala Kecil",
                  "Langkah 3: Evaluasi dan Perbaiki"], [
                    ("Berikut alur sederhana yang bisa langsung Anda praktikkan. Sesuaikan setiap "
                    "langkah dengan kondisi dan sumber daya yang Anda miliki."),
                ]),
                ("Tips Agar Hasil {Kw} Maksimal", [], [
                    "Beberapa kebiasaan kecil berikut sering membuat perbedaan besar:",
                    ("- Catat setiap percobaan supaya Anda tahu apa yang berhasil.\n"
                    "- Belajar dari pengalaman orang lain melalui komunitas atau forum.\n"
                    "- Jangan terburu-buru; kualitas lebih penting daripada kecepatan.\n"
                    "- Lakukan evaluasi rutin, misalnya setiap minggu."),
                ]),
                ("Kesalahan yang Sering Terjadi", [], [
                    "Banyak pemula mengalami kendala yang sebenarnya bisa dihindari:",
                    ("1. Melewatkan persiapan karena ingin cepat melihat hasil.\n"
                    "2. Mengikuti saran tanpa menyesuaikan dengan kondisi sendiri.\n"
                    "3. Berhenti terlalu cepat sebelum hasilnya terlihat."),
                ]),
            ],
            "komersial": [
                ("Kenapa Memilih {Kw} yang Tepat Itu Penting?", [], [
                    ("Pilihan yang tepat menentukan kepuasan dan nilai uang yang Anda keluarkan. "
                    "Salah pilih bisa berarti biaya tambahan dan waktu yang terbuang."),
                ]),
                ("Kriteria Memilih {Kw}",
                 ["Kualitas dan Daya Tahan", "Harga dan Nilai yang Didapat",
                  "Ulasan dan Reputasi Penjual", "Layanan Purnajual"], [
                    "Gunakan kriteria berikut sebagai daftar periksa sebelum memutuskan.",
                ]),
                ("Rekomendasi {Kw}", [], [
                    ("> **Catatan editor:** isi daftar 3–5 rekomendasi nyata beserta kelebihan, "
                    "kekurangan, dan kisaran harga. Jangan menulis klaim yang tidak bisa "
                    "dibuktikan."),
                    ("| Pilihan | Cocok untuk | Kelebihan | Kekurangan |\n|---|---|---|---|\n"
                    "| [Nama produk 1] | [kebutuhan] | [kelebihan] | [kekurangan] |\n"
                    "| [Nama produk 2] | [kebutuhan] | [kelebihan] | [kekurangan] |\n"
                    "| [Nama produk 3] | [kebutuhan] | [kelebihan] | [kekurangan] |"),
                ]),
                ("Kelebihan dan Kekurangan {Kw}", [], [
                    ("Tidak ada pilihan yang sempurna untuk semua orang. Timbang kelebihan dan "
                    "kekurangannya berdasarkan prioritas Anda: harga, kualitas, atau kepraktisan."),
                ]),
            ],
            "transaksional": [
                ("Kisaran Harga {Kw}", [], [
                    ("Harga {kw} bisa berbeda tergantung kualitas, merek, lokasi, dan musim promo. "
                    "Bandingkan beberapa penjual sebelum membeli."),
                    ("> **Catatan editor:** tambahkan tabel harga terbaru dari sumber yang bisa "
                    "dicek, lengkap dengan tanggal pengecekan."),
                ]),
                ("Tempat Membeli {Kw}", ["Toko Offline", "Marketplace", "Toko Resmi/Website"], [
                    ("Setiap tempat membeli punya kelebihan masing-masing. Toko offline "
                    "memungkinkan Anda melihat langsung, sedangkan marketplace sering punya "
                    "promo dan pilihan lebih banyak."),
                ]),
                ("Tips Membeli {Kw} Agar Tidak Kecewa", [], [
                    ("- Cek rating dan ulasan pembeli lain, terutama yang menyertakan foto.\n"
                    "- Tanyakan detail spesifikasi ke penjual sebelum checkout.\n"
                    "- Manfaatkan voucher gratis ongkir dan momen promo tanggal kembar.\n"
                    "- Simpan bukti transaksi dan rekam video saat membuka paket."),
                ]),
                ("Cara Menghindari Penipuan", [], [
                    ("Waspadai harga yang terlalu murah dibanding pasaran, penjual yang meminta "
                    "transfer di luar aplikasi, dan akun tanpa riwayat penjualan."),
                ]),
            ],
        },
        "intro": [
            ("Mencari informasi tentang {kw}? Anda tidak sendirian. Topik ini banyak dicari oleh "
            "{audiens} yang ingin hasil terbaik tanpa membuang waktu. Di artikel ini, kami "
            "merangkum hal-hal penting yang perlu Anda ketahui, mulai dari dasar hingga tips "
            "praktis."),
            ("{Kw} sering terdengar rumit, padahal bisa dipahami dengan langkah yang tepat. "
            "Panduan ini disusun untuk {audiens} agar bisa langsung dipraktikkan."),
        ],
        "conclusion": [
            ("Itulah panduan seputar {kw}. Kuncinya adalah memahami kebutuhan Anda, mulai dari "
            "langkah kecil, dan terus mengevaluasi hasilnya. Simpan artikel ini sebagai "
            "referensi dan bagikan kepada teman yang membutuhkan."),
        ],
        "faq": {
            "informasional": [
                ("Apakah {kw} cocok untuk pemula?",
                 ("Ya. Mulailah dari langkah dasar, pelajari pelan-pelan, dan tingkatkan secara "
                 "bertahap sesuai kemampuan.")),
                ("Berapa lama hasil {kw} bisa terlihat?",
                 ("Tergantung kondisi masing-masing. Konsistensi dan evaluasi rutin biasanya "
                 "mempercepat hasil.")),
                ("Apa kesalahan paling umum saat {kw}?",
                 ("Melewatkan persiapan dan tidak mencatat hasil percobaan, sehingga sulit "
                 "mengetahui apa yang perlu diperbaiki.")),
                ("Apakah {kw} membutuhkan biaya besar?",
                 ("Tidak selalu. Anda bisa memulai dengan modal kecil lalu menambah sesuai "
                 "kebutuhan.")),
            ],
            "komersial": [
                ("Bagaimana cara memilih {kw} yang bagus?",
                 "Perhatikan kualitas, harga, ulasan pembeli, dan layanan purnajual."),
                ("Apakah {kw} yang mahal selalu lebih bagus?",
                 "Tidak selalu. Pilih yang paling sesuai dengan kebutuhan dan anggaran Anda."),
                ("Di mana membaca ulasan {kw} yang tepercaya?",
                 "Ulasan pembeli dengan foto di marketplace, forum, dan video review independen."),
            ],
            "transaksional": [
                ("Berapa harga {kw} saat ini?",
                 ("Harga bervariasi tergantung kualitas dan penjual. Bandingkan minimal tiga "
                 "penjual sebelum membeli.")),
                ("Apakah aman membeli {kw} secara online?",
                 ("Aman jika membeli di penjual dengan rating tinggi dan transaksi tetap di dalam "
                 "aplikasi marketplace.")),
                ("Kapan waktu terbaik membeli {kw}?",
                 "Saat promo tanggal kembar, Harbolnas, atau awal bulan ketika banyak voucher."),
            ],
        },
        "meta_desc": [
            ("Panduan {kw} untuk {audiens}: cara, tips, dan kesalahan yang perlu dihindari. "
            "Baca selengkapnya!"),
            ("Pelajari {kw} dengan langkah praktis dan tips yang mudah diikuti. Cocok untuk "
            "pemula."),
            "Semua yang perlu Anda tahu tentang {kw}, lengkap dengan tips dan FAQ.",
        ],
        "faq_title": "Pertanyaan yang Sering Diajukan (FAQ)",
        "conclusion_title": "Kesimpulan",
        "intro_title": "Pendahuluan",
        "default_audiens": "pembaca",
    },
    "en": {
        "intent_words": {
            "transaksional": ["buy", "price", "cheap", "deal", "discount", "order", "near me"],
            "komersial": ["best", "review", "vs", "comparison", "top", "recommended"],
            "informasional": ["how", "what", "tips", "guide", "benefits", "why", "tutorial"],
        },
        "modifiers": {
            "informasional": ["how to {kw}", "{kw} for beginners", "{kw} tips",
                              "benefits of {kw}", "{kw} mistakes", "{kw} guide",
                              "what is {kw}", "{kw} examples"],
            "komersial": ["best {kw}", "{kw} review", "best {kw} {tahun}",
                          "{kw} pros and cons", "how to choose {kw}", "{kw} comparison"],
            "transaksional": ["{kw} price", "buy {kw} online", "cheap {kw}", "{kw} deals",
                              "{kw} near me"],
        },
        "h1": {
            "informasional": ["{Kw}: A Complete Beginner's Guide",
                              "{Kw}: Steps, Tips, and Mistakes to Avoid"],
            "komersial": ["Best {Kw} ({tahun}) and How to Choose"],
            "transaksional": ["{Kw} Prices ({tahun}) and Buying Tips"],
        },
        "sections": {
            "informasional": [
                ("What Is {Kw}?", [], [
                    "Before the practical steps, let's cover the basics of {kw}.",
                    "> **Editor's note:** add a 2–3 sentence definition with a trusted source.",
                ]),
                ("Benefits of {Kw}", [], [
                    "Understanding {kw} properly saves time, money, and effort.",
                ]),
                ("How to {Kw}: Step by Step",
                 ["Step 1: Prepare the Basics", "Step 2: Start Small", "Step 3: Review"], [
                    "Here is a simple flow you can follow right away.",
                ]),
                ("Common Mistakes", [], [
                    ("1. Skipping preparation.\n2. Copying advice without adapting it.\n"
                    "3. Giving up too early."),
                ]),
            ],
            "komersial": [
                ("How to Choose {Kw}", ["Quality", "Price and Value", "Reviews"], [
                    "Use these criteria as a checklist before deciding.",
                ]),
                ("Recommended {Kw}", [], [
                    "> **Editor's note:** list 3–5 real options with pros, cons and prices.",
                ]),
            ],
            "transaksional": [
                ("{Kw} Price Range", [], [
                    "Prices vary by quality, brand, location, and season.",
                ]),
                ("Buying Tips", [], [
                    ("- Check ratings and photo reviews.\n- Ask the seller for specs.\n"
                    "- Keep proof of purchase."),
                ]),
            ],
        },
        "intro": [("Looking for information about {kw}? This guide for {audiens} covers the "
                  "basics through practical tips.")],
        "conclusion": [("That's our guide to {kw}. Understand your needs, start small, and "
                       "keep evaluating. Bookmark this article and share it.")],
        "faq": {
            "informasional": [("Is {kw} suitable for beginners?",
                               "Yes. Start with the basics and improve step by step."),
                              ("How long until {kw} shows results?",
                               "It depends; consistency usually speeds things up.")],
            "komersial": [("How do I choose good {kw}?",
                           "Look at quality, price, reviews, and after-sales service.")],
            "transaksional": [("Is it safe to buy {kw} online?",
                               "Yes, from highly rated sellers inside the marketplace app.")],
        },
        "meta_desc": [("A practical guide to {kw} for {audiens}: steps, tips, and mistakes to "
                      "avoid. Read more!")],
        "faq_title": "Frequently Asked Questions (FAQ)",
        "conclusion_title": "Conclusion",
        "intro_title": "Introduction",
        "default_audiens": "readers",
    },
}


# --------------------------------------------------------------------------- produk

# keyword in a feature -> customer benefit phrase
BENEFIT_ID = [
    (("anti air", "waterproof", "tahan air"), "tetap aman saat terkena air atau hujan"),
    (("anti slip", "antislip", "tidak licin"), "lebih aman dan stabil saat dipakai"),
    (("ringan", "lightweight"), "nyaman dibawa ke mana saja"),
    (("lipat", "foldable"), "hemat tempat saat disimpan"),
    (("katun", "cotton"), "adem dan menyerap keringat"),
    (("premium",), "kualitas terasa sejak pertama dipakai"),
    (("stainless", "anti karat"), "awet dan tidak mudah berkarat"),
    (("bpa free", "food grade"), "aman untuk makanan dan minuman"),
    (("garansi",), "belanja lebih tenang"),
    (("handmade", "buatan tangan"), "dibuat teliti satu per satu"),
    (("tahan panas",), "aman digunakan untuk suhu tinggi"),
    (("elastis", "stretch", "melar"), "mengikuti gerak tubuh dengan nyaman"),
    (("usb", "rechargeable", "cas"), "praktis diisi ulang tanpa ganti baterai"),
    (("tanpa pengawet", "alami", "natural"), "lebih tenang dikonsumsi sehari-hari"),
    (("halal",), "sesuai kebutuhan konsumen muslim"),
    (("adjustable", "bisa diatur"), "bisa disesuaikan dengan kebutuhan"),
    (("tebal",), "terasa kokoh dan tidak menerawang"),
    (("serbaguna", "multifungsi"), "satu produk untuk banyak kebutuhan"),
    (("hemat", "irit"), "lebih hemat dalam pemakaian jangka panjang"),
]

BENEFIT_EN = [
    (("waterproof", "water resistant"), "stays safe in rain or splashes"),
    (("lightweight", "light"), "easy to carry anywhere"),
    (("cotton",), "breathable and comfortable"),
    (("stainless",), "durable and rust resistant"),
    (("warranty",), "shop with peace of mind"),
    (("handmade",), "carefully made one by one"),
]

CARE_ID = {
    "katun": "Cuci dengan air dingin, hindari pemutih, jemur di tempat teduh.",
    "kulit": "Bersihkan dengan kain lembap, hindari terkena air terlalu lama.",
    "stainless": "Cuci dengan sabun biasa dan keringkan agar tetap mengilap.",
    "kayu": "Lap dengan kain kering, hindari tempat lembap.",
    "rajut": "Cuci dengan tangan, jangan diperas terlalu keras, jemur mendatar.",
    "plastik": "Cuci dengan sabun lembut, hindari benda tajam dan suhu terlalu panas.",
    "keramik": "Cuci dengan spons lembut; hindari perubahan suhu ekstrem.",
}

PRODUK_TEXT = {
    "id": {
        "intro": [
            ("Cari {kategori} yang bisa diandalkan? {Nama} hadir untuk {kamu} yang ingin "
            "kualitas tanpa ribet."),
            ("{Nama} adalah pilihan tepat untuk {kamu} yang butuh {kategori} praktis dan "
            "berkualitas."),
            ("Hadirkan kenyamanan baru bersama {nama} — {kategori} yang dirancang untuk "
            "kebutuhan sehari-hari."),
        ],
        "feature_fallback": "{fitur} — bikin pemakaian lebih nyaman",
        "headings": {"keunggulan": "Keunggulan Produk", "spesifikasi": "Spesifikasi",
                     "perawatan": "Cara Perawatan", "catatan": "Catatan"},
        "notes": [
            "Warna produk bisa sedikit berbeda karena pencahayaan foto dan layar.",
            "Mohon rekam video saat membuka paket sebagai syarat klaim.",
            "Pesanan sebelum pukul 15.00 dikirim di hari yang sama (sesuaikan dengan toko).",
        ],
        "closing": [
            "Yuk, masukkan ke keranjang sekarang sebelum kehabisan!",
            "Klik 'Beli Sekarang' dan nikmati kemudahannya!",
            "Jangan sampai kehabisan, checkout sekarang juga!",
        ],
        "promo": [
            ("Halo Kak! 👋\n{Nama} sekarang tersedia{harga_txt}.\n✅ {f1}\n✅ {f2}\n"
            "Stok terbatas! Balas *MAU* untuk order ya 🙏"),
            "✨ *{Nama}* ✨\n{f1} & {f2}.\n{harga_line}Order sekarang, tinggal balas chat ini! 🛒",
            ("Kak, sudah lihat {nama}? 😍\n👉 {f1}\n👉 {f2}\n{harga_line}"
            "Balas *ORDER* untuk pesan, bisa COD (sesuaikan) 🚚"),
        ],
        "tag_patterns": ["{kategori}", "{kategori} {bahan}", "{nama_pendek}", "{kategori} murah",
                         "{kategori} {warna}", "{kategori} {ukuran}", "{merek} {kategori}",
                         "{kategori} kekinian", "{kategori} premium", "{kategori} original"],
        "title_benefit": ["Premium", "Kekinian", "Serbaguna", "Original"],
        "harga": "Harga",
        "spec_labels": {"bahan": "Bahan", "ukuran": "Ukuran", "warna": "Warna",
                        "berat": "Berat", "merek": "Merek", "kategori": "Kategori"},
    },
    "en": {
        "intro": [
            ("Looking for reliable {kategori}? {Nama} is made for people who want quality "
            "without the hassle."),
            "{Nama} is the right choice if you need practical, quality {kategori}.",
        ],
        "feature_fallback": "{fitur} for a better everyday experience",
        "headings": {"keunggulan": "Key Features", "spesifikasi": "Specifications",
                     "perawatan": "Care Instructions", "catatan": "Notes"},
        "notes": ["Colours may vary slightly due to lighting and screens.",
                  "Please record a video when unboxing for any claims."],
        "closing": ["Add to cart now before it's gone!", "Click 'Buy Now' today!"],
        "promo": [
            ("Hi! 👋\n{Nama} is now available{harga_txt}.\n✅ {f1}\n✅ {f2}\n"
            "Limited stock! Reply *YES* to order 🙏"),
            "✨ *{Nama}* ✨\n{f1} & {f2}.\n{harga_line}Reply to this chat to order! 🛒",
        ],
        "tag_patterns": ["{kategori}", "{kategori} {bahan}", "{nama_pendek}", "cheap {kategori}",
                         "{kategori} {warna}", "{merek} {kategori}", "premium {kategori}"],
        "title_benefit": ["Premium", "Multipurpose"],
        "harga": "Price",
        "spec_labels": {"bahan": "Material", "ukuran": "Size", "warna": "Colour",
                        "berat": "Weight", "merek": "Brand", "kategori": "Category"},
    },
}


# --------------------------------------------------------------------------- kuis

KUIS_TEXT = {
    "id": {
        "cloze_pg": "Lengkapi pernyataan berikut: \"{kalimat}\"",
        "cloze_isian": "Isilah bagian yang kosong: \"{kalimat}\"",
        "def_q": "Apa yang dimaksud dengan {istilah}?",
        "def_pg": "Berdasarkan materi, {istilah} adalah ...",
        "why_q": "Mengapa {akibat}?",
        "esai_def": "Jelaskan dengan kata-kata sendiri apa yang dimaksud dengan {istilah}!",
        "esai_why": "Jelaskan mengapa {akibat}!",
        "esai_general": "Jelaskan kembali isi pernyataan berikut dan berikan contohnya: "
                        "\"{kalimat}\"",
        "bs_q": "Benar atau salah: \"{kalimat}\"",
        "true": "Benar",
        "false": "Salah",
        "expl_cloze": "Sesuai materi: \"{kalimat}\"",
        "expl_false": "Pernyataan ini salah. Yang benar: \"{kalimat}\"",
        "expl_true": "Pernyataan ini sesuai dengan materi.",
        "expl_esai": "Poin jawaban: {kalimat}",
        "blank": "_____",
    },
    "en": {
        "cloze_pg": "Complete the statement: \"{kalimat}\"",
        "cloze_isian": "Fill in the blank: \"{kalimat}\"",
        "def_q": "What is meant by {istilah}?",
        "def_pg": "According to the material, {istilah} is ...",
        "why_q": "Why {akibat}?",
        "esai_def": "Explain in your own words what {istilah} means!",
        "esai_why": "Explain why {akibat}!",
        "esai_general": "Explain the following statement and give an example: \"{kalimat}\"",
        "bs_q": "True or false: \"{kalimat}\"",
        "true": "True",
        "false": "False",
        "expl_cloze": "From the material: \"{kalimat}\"",
        "expl_false": "This is false. Correct statement: \"{kalimat}\"",
        "expl_true": "This statement matches the material.",
        "expl_esai": "Key points: {kalimat}",
        "blank": "_____",
    },
}


# Paragraph for each H3 used in ARTIKEL["sections"] (keyed by the H3 text).
ARTIKEL_H3: dict[str, dict[str, str]] = {
    "id": {
        "Manfaat untuk Pemula": "Bagi pemula, memahami {kw} membantu membangun dasar yang kuat "
        "sehingga langkah berikutnya terasa lebih mudah dan terarah.",
        "Manfaat Jangka Panjang": "Dalam jangka panjang, kebiasaan yang baik dalam {kw} "
        "biasanya berdampak pada hasil yang lebih stabil dan biaya yang lebih efisien.",
        "Langkah 1: Siapkan Kebutuhan Dasar": "Tentukan tujuan, siapkan alat dan bahan yang "
        "diperlukan, lalu susun rencana sederhana. Persiapan yang matang mengurangi risiko "
        "kesalahan di tengah jalan.",
        "Langkah 2: Mulai dari Skala Kecil": "Lakukan uji coba dalam skala kecil agar Anda bisa "
        "belajar tanpa risiko besar. Catat apa yang berhasil dan apa yang perlu diperbaiki.",
        "Langkah 3: Evaluasi dan Perbaiki": "Setelah beberapa waktu, bandingkan hasil dengan "
        "target awal. Perbaiki bagian yang kurang, lalu tingkatkan skala secara bertahap.",
        "Kualitas dan Daya Tahan": "Periksa bahan, cara pembuatan, dan garansi. Pilihan yang "
        "awet biasanya lebih hemat dalam jangka panjang.",
        "Harga dan Nilai yang Didapat": "Bandingkan harga dengan fitur yang Anda dapatkan. "
        "Harga termurah belum tentu paling hemat.",
        "Ulasan dan Reputasi Penjual": "Baca ulasan yang menyertakan foto atau video, dan "
        "perhatikan cara penjual menanggapi keluhan pembeli.",
        "Layanan Purnajual": "Pastikan ada garansi, layanan servis, atau kebijakan retur yang "
        "jelas sebelum membeli.",
        "Toko Offline": "Cocok jika Anda ingin melihat dan mencoba langsung sebelum membeli.",
        "Marketplace": "Pilihan lebih banyak dan sering ada promo. Utamakan toko dengan rating "
        "tinggi atau berlabel toko resmi.",
        "Toko Resmi/Website": "Biasanya lebih terjamin keasliannya dan punya layanan purnajual "
        "yang jelas.",
    },
    "en": {
        "Step 1: Prepare the Basics": "Set a goal, prepare the tools you need, and make a "
        "simple plan.",
        "Step 2: Start Small": "Test on a small scale so you can learn without big risks.",
        "Step 3: Review": "Compare results with your goal, fix what's lacking, then scale up.",
        "Quality": "Check materials, build quality, and warranty.",
        "Price and Value": "Compare the price with what you actually get.",
        "Reviews": "Read reviews with photos and see how sellers handle complaints.",
    },
}
