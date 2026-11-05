"""File conversions: Excel <-> CSV, table -> PDF, images -> PDF, PDF merge/split."""

from __future__ import annotations

import datetime as dt
import logging
import re
from pathlib import Path

import pandas as pd

from data_tools import DataToolsError
from data_tools.cleaning import CleanOptions, clean_dataframe
from data_tools.formatting import format_number, format_rupiah, format_tanggal, is_money_column
from data_tools.io import (
    collect_files,
    excel_sheet_names,
    read_csv_file,
    read_excel_file,
    read_tables,
    write_csv,
    write_excel,
)

log = logging.getLogger("data_tools")

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}


def _safe(text: str) -> str:
    return re.sub(r"[^\w\-]+", "_", text).strip("_") or "sheet"


# --------------------------------------------------------------------------- Excel <-> CSV


def excel_to_csv(inputs: list[str | Path], out_dir: str | Path, delimiter: str = ",",
                 sheet: str | None = None) -> list[Path]:
    """Every sheet of every workbook -> ``<file>_<sheet>.csv`` (UTF-8 with BOM).

    Cell values are exported as they are (no cleaning) so nothing gets reformatted.
    """
    out_dir = Path(out_dir)
    written = []
    for path in collect_files(inputs, extensions={".xlsx", ".xlsm", ".xls"}):
        sheets = [sheet] if sheet else excel_sheet_names(path)
        for name in sheets:
            tables = read_excel_file(path, sheet=name, header_row=0)
            if not tables:
                continue
            df = tables[0].df
            stem = path.stem if len(sheets) == 1 else f"{path.stem}_{_safe(name)}"
            target = write_csv(_plain_frame(df), out_dir / f"{stem}.csv", delimiter)
            log.info("%s [%s] -> %s (%d baris)", path.name, name, target.name, len(df))
            written.append(target)
    return written


def _plain_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Write Excel datetimes as ISO dates and integral floats without ``.0``."""
    def conv(v):
        if isinstance(v, dt.datetime):
            return v.strftime("%Y-%m-%d") if v.time() == dt.time() else v.isoformat(sep=" ")
        if isinstance(v, float) and v.is_integer():
            return int(v)
        return v

    return df.map(conv)


def csv_to_excel(inputs: list[str | Path], output: str | Path, raw: bool = False,
                 separate: bool = False) -> list[Path]:
    """CSV files -> one workbook (one sheet per file) or one workbook per file.

    By default numbers/dates are converted to real Excel values (leading-zero codes stay text);
    ``raw=True`` keeps every cell as text.
    """
    files = collect_files(inputs, extensions={".csv", ".tsv", ".txt"})
    if not files:
        raise DataToolsError("Tidak ada file CSV yang ditemukan.")
    frames: dict[str, pd.DataFrame] = {}
    for f in files:
        df = read_csv_file(f).df
        if not raw:
            df, _ = clean_dataframe(df, CleanOptions(normalize_columns=False, drop_duplicates=False,
                                                     drop_empty=False))
        frames[f.stem] = df
    output = Path(output)
    if separate or len(files) > 1 and output.suffix.lower() not in (".xlsx", ".xlsm"):
        out_dir = output if output.suffix == "" else output.parent
        return [write_excel(out_dir / f"{stem}.xlsx", {"Data": df}) for stem, df in frames.items()]
    if output.suffix.lower() not in (".xlsx", ".xlsm"):
        output = output / f"{files[0].stem}.xlsx" if output.suffix == "" else \
            output.with_suffix(".xlsx")
    return [write_excel(output, frames)]


# --------------------------------------------------------------------------- table -> PDF


def _cell_text(col: str, value) -> str:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ""
    if isinstance(value, (pd.Timestamp, dt.date)):
        return format_tanggal(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return format_rupiah(value) if is_money_column(col) else format_number(value)
    return str(value)


def table_to_pdf(inputs: list[str | Path], output: str | Path, title: str | None = None,
                 all_sheets: bool = True, clean: bool = True) -> Path:
    """Print CSV/Excel tables to a paginated PDF (header repeated on every page)."""
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        LongTable,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        TableStyle,
    )

    tables = read_tables(inputs, all_sheets=all_sheets)
    max_cols = max(len(t.df.columns) for t in tables)
    pagesize = landscape(A4) if max_cols > 6 else A4
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(output), pagesize=pagesize, leftMargin=12 * mm,
                            rightMargin=12 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
                            title=title or output.stem, author="otomasi-kit")

    def footer(canvas, d):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.grey)
        canvas.drawRightString(pagesize[0] - 12 * mm, 8 * mm, f"Halaman {d.page}")
        canvas.drawString(12 * mm, 8 * mm, format_tanggal(dt.date.today()))
        canvas.restoreState()

    title_style = ParagraphStyle("judul", fontName="Helvetica-Bold", fontSize=13, leading=16,
                                 spaceAfter=6, textColor=colors.HexColor("#1F4E78"))
    story = []
    for n, t in enumerate(tables):
        df = t.df
        if clean:
            df, _ = clean_dataframe(df, CleanOptions(normalize_columns=False,
                                                     drop_duplicates=False))
        cols = [str(c) for c in df.columns]
        size = 8.5 if len(cols) <= 6 else (7 if len(cols) <= 10 else 5.5)
        cell = ParagraphStyle("sel", fontName="Helvetica", fontSize=size, leading=size * 1.2)
        num_cell = ParagraphStyle("angka", parent=cell, alignment=2)
        head = ParagraphStyle("kepala", parent=cell, fontName="Helvetica-Bold",
                              textColor=colors.white)
        numeric = [pd.api.types.is_numeric_dtype(df[c]) for c in df.columns]
        data = [[Paragraph(escape(c), head) for c in cols]]
        for row in df.itertuples(index=False, name=None):
            data.append([
                Paragraph(escape(_cell_text(c, v)), num_cell if numeric[i] else cell)
                for i, (c, v) in enumerate(zip(cols, row))
            ])
        lengths = [
            max([len(c)] + [len(_cell_text(c, v)) for v in df[c].head(200).tolist()])
            for c in df.columns
        ]
        lengths = [min(max(n_, 3), 40) for n_ in lengths] or [1]
        widths = [doc.width * n_ / sum(lengths) for n_ in lengths]
        table = LongTable(data, colWidths=widths, repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E78")),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#B7C3D0")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F6FA")]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        if n:
            story.append(PageBreak())
        heading = title or t.source.stem
        if len(tables) > 1:
            heading = f"{heading} – {t.label}" if title else t.label
        story += [Paragraph(escape(heading), title_style),
                  Paragraph(f"{format_number(len(df))} baris", cell), Spacer(1, 4), table]
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return output


# --------------------------------------------------------------------------- images -> PDF


def images_to_pdf(inputs: list[str | Path], output: str | Path, page: str = "a4",
                  margin_mm: float = 10) -> Path:
    """One image per page. ``page="a4"`` fits images on A4 (auto orientation),
    ``page="asli"`` uses the image size as page size."""
    from PIL import Image as PILImage
    from PIL import ImageOps
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    files = collect_files(inputs, extensions=IMAGE_EXTENSIONS)
    if not files:
        raise DataToolsError("Tidak ada file gambar (jpg/png/webp/...) yang ditemukan.")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(output))
    for f in files:
        with PILImage.open(f) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode not in ("RGB", "L"):
                background = PILImage.new("RGB", im.size, "white")
                rgba = im.convert("RGBA")
                background.paste(rgba, mask=rgba.split()[-1])
                im = background
            iw, ih = im.size
            reader = ImageReader(im)
            if page == "asli":
                pw, ph = iw * 0.75, ih * 0.75  # 96 dpi -> points
                c.setPageSize((pw, ph))
                c.drawImage(reader, 0, 0, pw, ph)
            else:
                pw, ph = landscape(A4) if iw > ih else A4
                c.setPageSize((pw, ph))
                m = margin_mm * mm
                scale = min((pw - 2 * m) / iw, (ph - 2 * m) / ih)
                w, h = iw * scale, ih * scale
                c.drawImage(reader, (pw - w) / 2, (ph - h) / 2, w, h)
        c.showPage()
        log.info("Tambah halaman: %s", f.name)
    c.save()
    return output


# --------------------------------------------------------------------------- PDF merge / split


def merge_pdfs(inputs: list[str | Path], output: str | Path) -> Path:
    from pypdf import PdfWriter

    files = collect_files(inputs, extensions={".pdf"})
    output = Path(output)
    files = [f for f in files if f.resolve() != output.resolve()]
    if not files:
        raise DataToolsError("Tidak ada file PDF untuk digabung.")
    output.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    for f in files:
        writer.append(str(f))
    with output.open("wb") as fh:
        writer.write(fh)
    writer.close()
    log.info("Gabung %d PDF -> %s", len(files), output)
    return output


def parse_ranges(spec: str, total: int) -> list[tuple[int, int]]:
    """``"1-3,5,7-"`` -> [(1,3), (5,5), (7,total)] (1-based, inclusive)."""
    ranges = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d*)\s*-\s*(\d*)|(\d+)", part)
        if not m:
            raise DataToolsError(f"Format halaman salah: '{part}' (contoh: 1-3,5,7-)")
        if m.group(3):
            start = end = int(m.group(3))
        else:
            start = int(m.group(1) or 1)
            end = int(m.group(2) or total)
        if not 1 <= start <= end <= total:
            raise DataToolsError(f"Rentang halaman {part} di luar 1-{total}")
        ranges.append((start, end))
    return ranges


def split_pdf(input_file: str | Path, out_dir: str | Path, pages: str | None = None,
              every: int = 1) -> list[Path]:
    """Split per page (default), per ``every`` pages, or by explicit ranges ``"1-3,4-6"``."""
    from pypdf import PdfReader, PdfWriter

    src = Path(input_file)
    if not src.is_file():
        raise DataToolsError(f"File PDF tidak ditemukan: {src}")
    reader = PdfReader(str(src))
    total = len(reader.pages)
    if pages:
        ranges = parse_ranges(pages, total)
    else:
        step = max(1, every)
        ranges = [(s, min(s + step - 1, total)) for s in range(1, total + 1, step)]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    width = len(str(total))
    written = []
    for start, end in ranges:
        writer = PdfWriter()
        for i in range(start - 1, end):
            writer.add_page(reader.pages[i])
        label = f"{start:0{width}d}" if start == end else f"{start:0{width}d}-{end:0{width}d}"
        target = out_dir / f"{src.stem}_hal_{label}.pdf"
        with target.open("wb") as fh:
            writer.write(fh)
        written.append(target)
    log.info("Pisah %s (%d halaman) -> %d file", src.name, total, len(written))
    return written
