"""konten - generator konten untuk UMKM, seller online, dan kreator di Indonesia.

Subperintah:
- sosmed : kalender & caption media sosial (Instagram, TikTok, LinkedIn, X, Facebook)
- artikel: draf artikel SEO (riset kata kunci, outline, artikel, FAQ, front-matter)
- produk : deskripsi produk marketplace (Shopee/Tokopedia) + copy promo WhatsApp
- kuis   : bank soal dari materi (pilihan ganda, benar/salah, isian, esai)

Dua mesin: ``ai`` (Anthropic API, butuh ANTHROPIC_API_KEY) dan ``template`` (offline,
deterministik). Jalankan ``python -m konten --help``.
"""

__version__ = "0.1.0"
