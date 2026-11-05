"""Bulk image processing: resize, compress, convert format, text watermark."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from data_tools import DataToolsError
from data_tools.io import collect_files

log = logging.getLogger("data_tools")

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
FORMATS = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "webp": "WEBP"}
POSITIONS = ("kanan-bawah", "kiri-bawah", "kanan-atas", "kiri-atas", "tengah")
FONT_CANDIDATES = (
    "DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "arialbd.ttf", "Arial Bold.ttf", "arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
)


@dataclass
class ImageOptions:
    max_width: int | None = None
    max_height: int | None = None
    quality: int = 85
    fmt: str | None = None  # jpg | png | webp; None keeps the original format
    watermark: str | None = None
    position: str = "kanan-bawah"
    opacity: int = 60  # percent
    suffix: str = ""


@dataclass
class ImageResult:
    source: Path
    target: Path
    size_before: tuple[int, int]
    size_after: tuple[int, int]
    bytes_before: int
    bytes_after: int


def _font(size: int):
    from PIL import ImageFont

    for name in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def add_watermark(img, text: str, position: str = "kanan-bawah", opacity: int = 60):
    """Draw semi-transparent text with a soft shadow; returns an RGBA image."""
    from PIL import Image, ImageDraw

    if position not in POSITIONS:
        raise DataToolsError(f"Posisi watermark harus salah satu dari: {', '.join(POSITIONS)}")
    base = img.convert("RGBA")
    w, h = base.size
    size = max(12, int(min(w, h) * (0.08 if position == "tengah" else 0.045)))
    font = _font(size)
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    tw, th = right - left, bottom - top
    margin = int(min(w, h) * 0.03)
    x = {"kiri": margin, "kanan": w - tw - margin}.get(position.split("-")[0], (w - tw) // 2)
    y = {"atas": margin, "bawah": h - th - margin}.get(
        position.split("-")[-1] if "-" in position else "", (h - th) // 2)
    alpha = int(255 * max(0, min(100, opacity)) / 100)
    shadow = max(1, size // 18)
    draw.text((x - left + shadow, y - top + shadow), text, font=font, fill=(0, 0, 0, alpha // 2))
    draw.text((x - left, y - top), text, font=font, fill=(255, 255, 255, alpha))
    return Image.alpha_composite(base, layer)


def process_image(src: Path, out_dir: Path, opts: ImageOptions) -> ImageResult:
    from PIL import Image, ImageOps

    with Image.open(src) as opened:
        before = opened.size
        img = ImageOps.exif_transpose(opened)
        img.load()
    fmt_key = (opts.fmt or src.suffix.lstrip(".")).lower()
    pil_format = FORMATS.get(fmt_key)
    if pil_format is None:  # e.g. .bmp kept as png
        fmt_key, pil_format = "png", "PNG"
    if opts.max_width or opts.max_height:
        limit = (opts.max_width or 10**6, opts.max_height or 10**6)
        if img.width > limit[0] or img.height > limit[1]:
            img.thumbnail(limit, Image.Resampling.LANCZOS)
    if opts.watermark:
        had_alpha = "A" in img.getbands() or "transparency" in img.info
        img = add_watermark(img, opts.watermark, opts.position, opts.opacity)
        if not had_alpha:
            img = img.convert("RGB")
    if pil_format == "JPEG" and img.mode not in ("RGB", "L"):
        rgba = img.convert("RGBA")
        background = Image.new("RGB", img.size, "white")
        background.paste(rgba, mask=rgba.split()[-1])
        img = background
    elif pil_format == "PNG" and img.mode == "CMYK":
        img = img.convert("RGB")
    if opts.fmt:
        ext = "jpg" if fmt_key in ("jpg", "jpeg") else fmt_key
    else:  # keep the original extension (lower-cased); exotic formats become png
        ext = fmt_key if fmt_key in FORMATS else "png"
    target = out_dir / f"{src.stem}{opts.suffix}.{ext}"
    if target.resolve() == src.resolve():
        raise DataToolsError("Folder output sama dengan folder input; pilih folder lain dengan -o")
    save_args: dict = {}
    if pil_format == "JPEG":
        save_args = {"quality": opts.quality, "optimize": True, "progressive": True}
    elif pil_format == "WEBP":
        save_args = {"quality": opts.quality, "method": 6}
    elif pil_format == "PNG":
        save_args = {"optimize": True}
    img.save(target, pil_format, **save_args)
    return ImageResult(src, target, before, img.size, src.stat().st_size, target.stat().st_size)


def process_images(inputs: list[str | Path], out_dir: str | Path, opts: ImageOptions,
                   recursive: bool = False) -> list[ImageResult]:
    files = collect_files(inputs, extensions=IMAGE_EXTENSIONS, recursive=recursive)
    if not files:
        raise DataToolsError("Tidak ada file gambar yang ditemukan.")
    if opts.fmt and opts.fmt.lower() not in FORMATS:
        raise DataToolsError("Format gambar harus jpg, png, atau webp.")
    if not 1 <= opts.quality <= 100:
        raise DataToolsError("Kualitas harus antara 1 dan 100.")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for f in files:
        try:
            results.append(process_image(f, out_dir, opts))
        except OSError as exc:
            log.warning("Lewati %s: %s", f.name, exc)
    return results
