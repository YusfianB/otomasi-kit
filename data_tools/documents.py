"""Mass document generation: built-in templates (PDF + DOCX) and custom DOCX templates.

Built-in templates are described with a tiny, backend-neutral block model
(:class:`Text`, :class:`Table`, :class:`Image`, ...) that is rendered either to PDF
(reportlab) or DOCX (python-docx), so both outputs always look alike.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from data_tools import DataToolsError
from data_tools.cleaning import normalize_column_name
from data_tools.formatting import (
    format_bulan,
    format_number,
    format_rupiah,
    format_tanggal,
    is_missing,
    is_money_column,
    parse_number,
    terbilang,
)

log = logging.getLogger("data_tools")

TEMPLATES = ("invoice", "sertifikat", "surat", "slip-gaji")
PLACEHOLDER_RE = re.compile(r"\{\{\s*([^{}|]+?)\s*(?:\|\s*([a-z_]+)\s*)?\}\}")
FILENAME_RE = re.compile(r"\{\s*([^{}|]+?)\s*(?:\|\s*([a-z_]+)\s*)?\}")
INVALID_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')

# --------------------------------------------------------------------------- block model


@dataclass
class Text:
    text: str
    size: float = 10
    bold: bool = False
    italic: bool = False
    align: str = "left"  # left | center | right | justify
    color: str | None = None
    space_before: float = 0
    space_after: float = 4


@dataclass
class Image:
    path: Path
    width_mm: float = 25
    align: str = "left"


@dataclass
class Spacer:
    height_mm: float = 5


@dataclass
class Rule:
    color: str = "#1F4E78"
    thickness: float = 1.2


Cell = str | list


@dataclass
class Table:
    rows: list[list[Cell]]
    widths: list[float]  # fractions of the available width
    header: bool = False
    grid: bool = False
    align: list[str] | None = None
    bold_rows: set[int] = field(default_factory=set)
    shade_rows: dict[int, str] = field(default_factory=dict)
    size: float = 9.5
    padding: float = 3
    header_color: str = "#1F4E78"


Block = Text | Image | Spacer | Rule | Table


@dataclass
class DocSpec:
    title: str
    blocks: list[Block]
    landscape: bool = False
    border: str | None = None  # page border color (certificate)
    margin_mm: float = 18


# --------------------------------------------------------------------------- profile & values

DEFAULT_PROFILE = {
    "nama": "Nama Usaha Anda",
    "alamat": "",
    "telepon": "",
    "email": "",
    "npwp": "",
    "kota": "",
    "logo": None,
    "rekening": "",
    "penandatangan": "",
    "jabatan": "",
    "ppn": 0,
    "warna": "#1F4E78",
}


def load_profile(path: str | Path | None) -> dict[str, Any]:
    profile = dict(DEFAULT_PROFILE)
    if not path:
        return profile
    import yaml

    path = Path(path)
    if not path.is_file():
        raise DataToolsError(f"File profil tidak ditemukan: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise DataToolsError("File profil harus berisi pasangan 'kunci: nilai' (format YAML).")
    profile.update({normalize_column_name(k): v for k, v in data.items()})
    if profile.get("logo"):
        logo = Path(str(profile["logo"]))
        if not logo.is_absolute():
            logo = path.parent / logo
        if logo.is_file():
            profile["logo"] = logo
        else:
            log.warning("Peringatan: logo %s tidak ditemukan, dilewati", logo)
            profile["logo"] = None
    return profile


def format_value(key: str, value: Any, filt: str | None = None) -> str:
    """Format a cell for display; ``filt`` is one of the ``{{kolom|filter}}`` names."""
    if filt in ("besar", "kecil", "judul"):
        text = format_value(key, value)
        return {"besar": text.upper(), "kecil": text.lower(), "judul": text.title()}[filt]
    if is_missing(value):
        return ""
    if filt == "teks":
        return str(value)
    if filt in ("tanggal", "hari", "bulan"):
        if filt == "bulan":
            return format_bulan(value)
        return format_tanggal(value, with_day=filt == "hari")
    if filt in ("rupiah", "angka", "terbilang"):
        num = value if isinstance(value, (int, float)) else parse_number(str(value))
        if num is None:
            return str(value)
        if filt == "rupiah":
            return format_rupiah(num)
        if filt == "angka":
            return format_number(num)
        return terbilang(num)
    if filt:
        log.warning("Peringatan: filter '%s' tidak dikenal (pakai: rupiah, angka, tanggal, hari, "
                    "bulan, terbilang, besar, kecil, judul, teks)", filt)
    if isinstance(value, (pd.Timestamp, dt.date)):
        return format_tanggal(value)
    if isinstance(value, bool):
        return "Ya" if value else "Tidak"
    if isinstance(value, (int, float)):
        return format_rupiah(value) if is_money_column(key) else format_number(value)
    return str(value)


class Context(dict):
    """Row values keyed by normalized column name, with a lenient lookup."""

    def lookup(self, key: str) -> tuple[bool, Any]:
        for k in (key, normalize_column_name(key)):
            if k in self:
                return True, self[k]
        return False, None


def make_context(row: dict[str, Any], index: int, extra: dict[str, Any] | None = None) -> Context:
    ctx = Context()
    ctx["hari_ini"] = pd.Timestamp(dt.date.today())
    ctx["urut"] = f"{index:03d}"
    for k, v in (extra or {}).items():
        ctx[k] = v
    for k, v in row.items():
        ctx[normalize_column_name(k) or str(k)] = _plain(v)
    return ctx


def _plain(value: Any) -> Any:
    """numpy scalars -> Python, pandas NA/NaT -> None."""
    if value is None or isinstance(value, str):
        return value
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        return value
    if isinstance(value, pd.Timestamp):
        return value
    if hasattr(value, "item"):
        return value.item()
    return value


def render_text(template: str, ctx: Context, missing: set[str] | None = None) -> str:
    """Replace ``{{kolom}}`` / ``{{kolom|filter}}`` placeholders."""

    def repl(m: re.Match) -> str:
        key, filt = m.group(1).strip(), m.group(2)
        found, value = ctx.lookup(key)
        if not found:
            if missing is not None:
                missing.add(key)
            return m.group(0)
        return format_value(normalize_column_name(key), value, filt)

    return PLACEHOLDER_RE.sub(repl, template)


def sanitize_filename(name: str, max_len: int = 120) -> str:
    name = INVALID_FILENAME.sub("-", name)
    name = re.sub(r"\s+", "_", name.strip())
    name = re.sub(r"[-_]{2,}", lambda m: m.group(0)[0], name).strip("._-")
    return name[:max_len] or "dokumen"


def render_filename(pattern: str, ctx: Context) -> str:
    def repl(m: re.Match) -> str:
        key, filt = m.group(1).strip(), m.group(2)
        found, value = ctx.lookup(key)
        if not found:
            raise DataToolsError(
                f"Kolom '{key}' di pola --nama-file tidak ada. Kolom tersedia: "
                f"{', '.join(k for k in ctx if k != 'hari_ini')}"
            )
        if isinstance(value, (pd.Timestamp, dt.date)) and not filt:
            return pd.Timestamp(value).strftime("%Y-%m-%d")
        return format_value(normalize_column_name(key), value, filt)

    return sanitize_filename(FILENAME_RE.sub(repl, pattern))


def unique_path(path: Path, used: set[Path]) -> Path:
    """Avoid name clashes inside one batch (files from earlier runs are overwritten)."""
    candidate, n = path, 2
    while candidate in used:
        candidate = path.with_name(f"{path.stem}_{n}{path.suffix}")
        n += 1
    used.add(candidate)
    return candidate


# --------------------------------------------------------------------------- PDF backend


def _hex(color: str | None):
    from reportlab.lib import colors

    return colors.HexColor(color) if color else colors.black


def _pdf_font(bold: bool, italic: bool) -> str:
    if bold and italic:
        return "Helvetica-BoldOblique"
    if bold:
        return "Helvetica-Bold"
    if italic:
        return "Helvetica-Oblique"
    return "Helvetica"


def _pdf_flowables(block: Block, width: float, cell: bool = False) -> list:
    from xml.sax.saxutils import escape

    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import HRFlowable, Paragraph, TableStyle
    from reportlab.platypus import Image as RLImage
    from reportlab.platypus import Spacer as RLSpacer
    from reportlab.platypus import Table as RLTable

    aligns = {"left": TA_LEFT, "center": TA_CENTER, "right": TA_RIGHT, "justify": TA_JUSTIFY}
    if isinstance(block, Text):
        style = ParagraphStyle(
            "t", fontName=_pdf_font(block.bold, block.italic), fontSize=block.size,
            leading=block.size * 1.3, alignment=aligns.get(block.align, TA_LEFT),
            textColor=_hex(block.color), spaceBefore=block.space_before,
            spaceAfter=0 if cell else block.space_after,
        )
        content = escape(block.text).replace("\n", "<br/>") if block.text.strip() else "&nbsp;"
        return [Paragraph(content, style)]
    if isinstance(block, Spacer):
        return [RLSpacer(1, block.height_mm * mm)]
    if isinstance(block, Rule):
        return [HRFlowable(width="100%", thickness=block.thickness, color=_hex(block.color),
                           spaceBefore=2, spaceAfter=6)]
    if isinstance(block, Image):
        from reportlab.lib.utils import ImageReader

        iw, ih = ImageReader(str(block.path)).getSize()
        w = block.width_mm * mm
        img = RLImage(str(block.path), width=w, height=w * ih / iw)
        img.hAlign = block.align.upper() if block.align != "justify" else "LEFT"
        return [img]
    if isinstance(block, Table):
        col_widths = [f * width for f in block.widths]
        data = []
        for ri, row in enumerate(block.rows):
            out_row = []
            for ci, value in enumerate(row):
                align = (block.align or ["left"] * len(row))[ci]
                bold = (block.header and ri == 0) or ri in block.bold_rows
                color = "#FFFFFF" if block.header and ri == 0 else None
                if isinstance(value, list):
                    flow = []
                    for b in value:
                        flow.extend(_pdf_flowables(b, col_widths[ci] - 2 * block.padding, True))
                    out_row.append(flow)
                else:
                    out_row.append(_pdf_flowables(
                        Text(str(value), size=block.size, bold=bold, align=align, color=color),
                        col_widths[ci], True)[0])
            data.append(out_row)
        table = RLTable(data, colWidths=col_widths, repeatRows=1 if block.header else 0)
        cmds = [
            ("VALIGN", (0, 0), (-1, -1), "TOP" if not block.grid else "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), block.padding),
            ("RIGHTPADDING", (0, 0), (-1, -1), block.padding),
            ("TOPPADDING", (0, 0), (-1, -1), block.padding),
            ("BOTTOMPADDING", (0, 0), (-1, -1), block.padding),
        ]
        if block.grid:
            cmds.append(("GRID", (0, 0), (-1, -1), 0.5, _hex("#B7C3D0")))
        if block.header:
            cmds.append(("BACKGROUND", (0, 0), (-1, 0), _hex(block.header_color)))
        for ri, color in block.shade_rows.items():
            cmds.append(("BACKGROUND", (0, ri), (-1, ri), _hex(color)))
        table.setStyle(TableStyle(cmds))
        return [table]
    raise TypeError(f"Unknown block {block!r}")


def render_pdf(spec: DocSpec, path: Path) -> Path:
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate

    pagesize = landscape(A4) if spec.landscape else A4
    margin = spec.margin_mm * mm
    doc = SimpleDocTemplate(
        str(path), pagesize=pagesize, leftMargin=margin, rightMargin=margin,
        topMargin=margin, bottomMargin=margin, title=spec.title, author="otomasi-kit",
    )

    def decorate(canvas, _doc):
        if spec.border:
            canvas.saveState()
            canvas.setStrokeColor(_hex(spec.border))
            w, h = pagesize
            canvas.setLineWidth(4)
            canvas.rect(8 * mm, 8 * mm, w - 16 * mm, h - 16 * mm)
            canvas.setLineWidth(1)
            canvas.rect(11 * mm, 11 * mm, w - 22 * mm, h - 22 * mm)
            canvas.restoreState()

    story = []
    for block in spec.blocks:
        story.extend(_pdf_flowables(block, doc.width))
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return path


# --------------------------------------------------------------------------- DOCX backend


def _docx_shade(cell, color: str) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), color.lstrip("#"))
    cell._tc.get_or_add_tcPr().append(shd)


def _docx_rule(paragraph, color: str, size: float) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    p_pr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), str(int(size * 8)))
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), color.lstrip("#"))
    borders.append(bottom)
    p_pr.append(borders)


def _docx_page_border(section, color: str) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    borders = OxmlElement("w:pgBorders")
    borders.set(qn("w:offsetFrom"), "page")
    for side in ("top", "left", "bottom", "right"):
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:val"), "thinThickSmallGap")
        el.set(qn("w:sz"), "24")
        el.set(qn("w:space"), "24")
        el.set(qn("w:color"), color.lstrip("#"))
        borders.append(el)
    section._sectPr.append(borders)


class _DocxWriter:
    def __init__(self, width_mm: float):
        self.width_mm = width_mm

    def paragraph(self, container, fresh: list):
        """Reuse a cell's initial empty paragraph once, otherwise add a new one."""
        if fresh and fresh[0] is not None:
            p, fresh[0] = fresh[0], None
            return p
        return container.add_paragraph()

    def write(self, container, block: Block, width_mm: float, fresh: list) -> None:
        from docx.enum.table import WD_TABLE_ALIGNMENT
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Mm, Pt, RGBColor

        aligns = {"left": WD_ALIGN_PARAGRAPH.LEFT, "center": WD_ALIGN_PARAGRAPH.CENTER,
                  "right": WD_ALIGN_PARAGRAPH.RIGHT, "justify": WD_ALIGN_PARAGRAPH.JUSTIFY}
        if isinstance(block, Text):
            p = self.paragraph(container, fresh)
            p.alignment = aligns.get(block.align)
            p.paragraph_format.space_before = Pt(block.space_before)
            p.paragraph_format.space_after = Pt(block.space_after)
            run = p.add_run(block.text)
            run.bold, run.italic = block.bold, block.italic
            run.font.size = Pt(block.size)
            if block.color:
                run.font.color.rgb = RGBColor.from_string(block.color.lstrip("#").upper())
        elif isinstance(block, Spacer):
            p = self.paragraph(container, fresh)
            p.paragraph_format.space_after = Mm(block.height_mm)
            p.paragraph_format.line_spacing = Pt(1)
        elif isinstance(block, Rule):
            p = self.paragraph(container, fresh)
            p.paragraph_format.space_after = Pt(6)
            _docx_rule(p, block.color, block.thickness)
        elif isinstance(block, Image):
            p = self.paragraph(container, fresh)
            p.alignment = aligns.get(block.align)
            p.add_run().add_picture(str(block.path), width=Mm(min(block.width_mm, width_mm)))
        elif isinstance(block, Table):
            ncols = len(block.widths)
            table = container.add_table(rows=len(block.rows), cols=ncols)
            if fresh and fresh[0] is not None:  # drop the unused empty first paragraph
                fresh[0]._p.getparent().remove(fresh[0]._p)
                fresh[0] = None
            if block.grid:
                table.style = "Table Grid"
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            table.autofit = False
            widths = [Mm(f * width_mm) for f in block.widths]
            for ri, row in enumerate(block.rows):
                for ci, value in enumerate(row):
                    cell = table.cell(ri, ci)
                    cell.width = widths[ci]
                    cell_fresh = [cell.paragraphs[0]]
                    align = (block.align or ["left"] * ncols)[ci]
                    if isinstance(value, list):
                        for b in value:
                            self.write(cell, b, block.widths[ci] * width_mm, cell_fresh)
                    else:
                        header = block.header and ri == 0
                        self.write(cell, Text(
                            str(value), size=block.size, align=align,
                            bold=header or ri in block.bold_rows,
                            color="#FFFFFF" if header else None, space_after=0,
                        ), widths[ci].mm, cell_fresh)
                    if block.header and ri == 0:
                        _docx_shade(cell, block.header_color)
                    elif ri in block.shade_rows:
                        _docx_shade(cell, block.shade_rows[ri])
            if not hasattr(container, "_tc"):  # top level: small gap after tables
                gap = container.add_paragraph()
                gap.paragraph_format.space_after = Pt(2)
        else:
            raise TypeError(f"Unknown block {block!r}")


def render_docx(spec: DocSpec, path: Path) -> Path:
    from docx import Document
    from docx.enum.section import WD_ORIENT
    from docx.shared import Mm, Pt

    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(10)
    normal.paragraph_format.space_after = Pt(0)
    section = doc.sections[0]
    section.page_width, section.page_height = Mm(210), Mm(297)
    if spec.landscape:
        section.orientation = WD_ORIENT.LANDSCAPE
        section.page_width, section.page_height = Mm(297), Mm(210)
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(section, side, Mm(spec.margin_mm))
    if spec.border:
        _docx_page_border(section, spec.border)
    doc.core_properties.title = spec.title
    doc.core_properties.author = "otomasi-kit"
    width_mm = (297 if spec.landscape else 210) - 2 * spec.margin_mm
    writer = _DocxWriter(width_mm)
    # The default document has no paragraphs, so nothing to reuse at top level.
    for block in spec.blocks:
        writer.write(doc, block, width_mm, [None])
    doc.save(path)
    return path


# --------------------------------------------------------------------------- template helpers


def _letterhead(profile: dict, compact: bool = False) -> list[Block]:
    color = profile.get("warna") or "#1F4E78"
    lines = [profile.get("alamat") or ""]
    contact = " | ".join(
        x for x in (
            f"Telp. {profile['telepon']}" if profile.get("telepon") else "",
            profile.get("email") or "",
            f"NPWP {profile['npwp']}" if profile.get("npwp") else "",
        ) if x
    )
    lines.append(contact)
    info: list[Block] = [Text(str(profile.get("nama") or ""), size=15 if not compact else 13,
                              bold=True, color=color, space_after=1)]
    info += [Text(line, size=8.5, color="#444444", space_after=0) for line in lines if line]
    blocks: list[Block]
    if profile.get("logo"):
        blocks = [Table([[[Image(Path(profile["logo"]), width_mm=18 if not compact else 14)],
                          info]], widths=[0.13, 0.87], padding=1)]
    else:
        blocks = list(info)
    return blocks + [Rule(color=color, thickness=1.6)]


def _signature(profile: dict, place_date: str, label: str = "Hormat kami,",
               name: str | None = None, title: str | None = None,
               align: str = "center", side: str = "right") -> Table:
    content = [
        Text(place_date, align=align, space_after=0),
        Text(label, align=align, space_after=0),
        Spacer(18),
        Text(name if name is not None else str(profile.get("penandatangan") or ""), bold=True,
             align=align, space_after=0),
        Text(title if title is not None else str(profile.get("jabatan") or ""), align=align,
             space_after=0),
    ]
    if side == "center":
        return Table([[" ", content, " "]], widths=[0.3, 0.4, 0.3])
    row = [" ", content] if side == "right" else [content, " "]
    return Table([row], widths=[0.58, 0.42] if side == "right" else [0.42, 0.58])


def _place_date(profile: dict, date: Any = None) -> str:
    d = format_tanggal(date if not is_missing(date) else dt.date.today())
    return f"{profile['kota']}, {d}" if profile.get("kota") else d


def _first(ctx: Context, *names: str) -> Any:
    for n in names:
        found, value = ctx.lookup(n)
        if found and not is_missing(value):
            return value
    return None


def _num(value: Any) -> float:
    if is_missing(value):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    parsed = parse_number(str(value))
    return parsed or 0.0


def _text(value: Any, key: str = "") -> str:
    return format_value(key, value)


# --------------------------------------------------------------------------- invoice

INVOICE_NUMBER = ["nomor_invoice", "no_invoice", "invoice", "nomor_faktur", "no_faktur", "nomor",
                  "no"]


def invoice_spec(rows: list[Context], profile: dict, ppn: float | None = None) -> DocSpec:
    head = rows[0]
    color = profile.get("warna") or "#1F4E78"
    number = _text(_first(head, *INVOICE_NUMBER) or head["urut"])
    date = _first(head, "tanggal", "tanggal_invoice", "tgl")
    due = _first(head, "jatuh_tempo", "tanggal_jatuh_tempo", "due_date")
    customer = _first(head, "nama_pelanggan", "pelanggan", "nama", "customer", "kepada")
    address = _first(head, "alamat_pelanggan", "alamat")
    phone = _first(head, "telepon", "telp", "no_hp", "hp")

    blocks: list[Block] = _letterhead(profile)
    meta_rows = [["Nomor", f": {number}"], ["Tanggal", f": {format_tanggal(date) or '-'}"]]
    if due is not None:
        meta_rows.append(["Jatuh tempo", f": {format_tanggal(due)}"])
    bill = [Text("Ditagihkan kepada:", size=9, color="#555555", space_after=1),
            Text(_text(customer) or "-", bold=True, size=11, space_after=1)]
    if address:
        bill.append(Text(_text(address), size=9, space_after=0))
    if phone:
        bill.append(Text(f"Telp. {_text(phone)}", size=9, space_after=0))
    meta = [Text("INVOICE", size=20, bold=True, color=color, align="right", space_after=4),
            Table(meta_rows, widths=[0.4, 0.6], size=9.5, padding=1)]
    blocks.append(Table([[bill, meta]], widths=[0.55, 0.45]))
    blocks.append(Spacer(4))

    has_disc = any(_num(_first(r, "diskon", "potongan")) for r in rows)
    header = ["No", "Deskripsi", "Qty", "Harga", *(["Diskon"] if has_disc else []), "Jumlah"]
    table_rows: list[list[Cell]] = [header]
    subtotal = discount_total = 0.0
    for i, r in enumerate(rows, start=1):
        item = _first(r, "item", "deskripsi", "produk", "nama_produk", "barang", "keterangan")
        qty = _num(_first(r, "qty", "jumlah", "kuantitas", "banyak"))
        price = _num(_first(r, "harga", "harga_satuan"))
        disc = _num(_first(r, "diskon", "potongan"))
        line = _first(r, "total", "subtotal", "jumlah_harga")
        amount = _num(line) if line is not None else qty * price - disc
        subtotal += amount + disc
        discount_total += disc
        table_rows.append([str(i), _text(item) or "-", format_number(qty), format_rupiah(price),
                           *([format_rupiah(disc) if disc else "-"] if has_disc else []),
                           format_rupiah(amount)])
    widths = [0.06, 0.42, 0.08, 0.16, 0.12, 0.16] if has_disc else [0.06, 0.5, 0.08, 0.17, 0.19]
    align = ["center", "left", "right", "right", *(["right"] if has_disc else []), "right"]
    blocks.append(Table(table_rows, widths=widths, header=True, grid=True, align=align,
                        header_color=color,
                        shade_rows={i: "#F2F6FA" for i in range(2, len(table_rows), 2)}))

    rate = float(ppn if ppn is not None else (profile.get("ppn") or 0))
    after_disc = subtotal - discount_total
    tax = round(after_disc * rate / 100)
    total = after_disc + tax
    totals = [["Subtotal", format_rupiah(subtotal)]]
    if discount_total:
        totals.append(["Diskon", f"-{format_rupiah(discount_total)}"])
    if rate:
        totals.append([f"PPN {format_number(rate)}%", format_rupiah(tax)])
    totals.append(["TOTAL", format_rupiah(total)])
    blocks.append(Table(
        [[" ", [Table(totals, widths=[0.5, 0.5], align=["left", "right"],
                      bold_rows={len(totals) - 1}, shade_rows={len(totals) - 1: "#DDEBF7"},
                      size=10, padding=3)]]],
        widths=[0.5, 0.5], padding=0,
    ))
    blocks.append(Text(f"Terbilang: {terbilang(total).capitalize()} rupiah", italic=True, size=9,
                       space_before=6))
    if profile.get("rekening"):
        blocks.append(Text(f"Pembayaran melalui transfer ke: {profile['rekening']}", size=9))
    note = _first(head, "catatan", "keterangan_invoice", "note")
    if note:
        blocks.append(Text(f"Catatan: {_text(note)}", size=9))
    blocks.append(Spacer(6))
    blocks.append(_signature(profile, _place_date(profile, date)))
    return DocSpec(title=f"Invoice {number}", blocks=blocks)


# --------------------------------------------------------------------------- certificate


def certificate_spec(ctx: Context, profile: dict) -> DocSpec:
    color = profile.get("warna") or "#1F4E78"
    name = _text(_first(ctx, "nama", "nama_peserta", "nama_lengkap", "penerima")) or "-"
    number = _first(ctx, "nomor_sertifikat", "nomor", "no_sertifikat", "no")
    role = _text(_first(ctx, "peran", "sebagai", "status")) or "Peserta"
    event = _text(_first(ctx, "acara", "kegiatan", "pelatihan", "event", "judul"))
    date = _first(ctx, "tanggal", "tanggal_acara", "tgl")
    place = _text(_first(ctx, "tempat", "lokasi", "kota")) or profile.get("kota") or ""
    extra = _first(ctx, "keterangan", "predikat", "nilai")

    blocks: list[Block] = []
    if profile.get("logo"):
        blocks.append(Image(Path(profile["logo"]), width_mm=20, align="center"))
    else:
        blocks.append(Spacer(8))
    blocks += [
        Text(str(profile.get("nama") or "").upper(), size=11, bold=True, align="center",
             color="#555555", space_before=2, space_after=8),
        Text("SERTIFIKAT", size=40, bold=True, align="center", color=color, space_after=4),
        Text(f"Nomor: {_text(number)}" if number else " ", size=10, align="center",
             color="#555555", space_after=14),
        Text("diberikan kepada", size=13, italic=True, align="center", space_after=8),
        Text(name, size=30, bold=True, align="center", space_after=4),
        Table([[" ", [Rule(color=color, thickness=1)], " "]], widths=[0.25, 0.5, 0.25],
              padding=0),
        Text(f"atas partisipasinya sebagai {role}" + (" dalam" if event else ""), size=13,
             align="center", space_before=6, space_after=4),
    ]
    if event:
        blocks.append(Text(event, size=17, bold=True, align="center", color=color, space_after=4))
    when = " ".join(x for x in (
        f"yang diselenggarakan pada {format_tanggal(date)}" if date is not None else "",
        f"di {place}" if place else "",
    ) if x)
    if when:
        blocks.append(Text(when + ".", size=12, align="center", space_after=4))
    if extra:
        blocks.append(Text(f"dengan predikat {_text(extra, 'keterangan')}", size=12,
                           align="center", italic=True))
    blocks.append(Spacer(6))
    sign_place = f"{place}, {format_tanggal(date)}" if place and date is not None else \
        _place_date(profile, date)
    blocks.append(_signature(profile, sign_place, label="", side="center"))
    return DocSpec(title=f"Sertifikat {name}", blocks=blocks, landscape=True, border=color,
                   margin_mm=20)


# --------------------------------------------------------------------------- letter

DEFAULT_LETTER = (
    "Dengan hormat,\n\n"
    "Bersama surat ini kami sampaikan informasi kepada Bapak/Ibu {{nama}} mengenai {{perihal}}.\n\n"
    "Demikian surat ini kami sampaikan. Atas perhatian Bapak/Ibu kami ucapkan terima kasih."
)


def letter_spec(ctx: Context, profile: dict, body: str | None = None,
                missing: set[str] | None = None) -> DocSpec:
    template = body or (_text(_first(ctx, "isi", "isi_surat")) or DEFAULT_LETTER)
    number = _text(_first(ctx, "nomor_surat", "nomor", "no_surat", "no")) or "-"
    subject = _text(_first(ctx, "perihal", "hal", "subjek")) or "-"
    attachment = _text(_first(ctx, "lampiran")) or "-"
    date = _first(ctx, "tanggal", "tanggal_surat", "tgl")
    name = _text(_first(ctx, "nama", "kepada", "nama_penerima")) or "-"
    position = _text(_first(ctx, "jabatan"))
    org = _text(_first(ctx, "instansi", "perusahaan", "nama_perusahaan", "toko"))
    address = _text(_first(ctx, "alamat", "alamat_penerima"))

    blocks: list[Block] = _letterhead(profile)
    blocks.append(Text(_place_date(profile, date), align="right", space_after=6))
    blocks.append(Table([["Nomor", f": {number}"], ["Lampiran", f": {attachment}"],
                         ["Perihal", f": {subject}"]], widths=[0.14, 0.86], padding=1, size=10))
    recipient = ["Kepada Yth.", f"Bapak/Ibu {name}"]
    if position or org:
        recipient.append(" ".join(x for x in (position, org) if x))
    if address:
        recipient.append(address)
    recipient.append("di tempat")
    blocks.append(Text("\n".join(recipient), space_before=8, space_after=10))
    for para in re.split(r"\n\s*\n", render_text(template, ctx, missing).strip()):
        lines = para.strip()
        if not lines:
            continue
        split = lines.splitlines()
        is_list = len(split) >= 2 and all(
            ":" in ln and len(ln.split(":", 1)[0]) <= 30 for ln in split
        )
        if is_list:
            rows = [[k.strip(), f": {v.strip()}"] for k, v in
                    (ln.split(":", 1) for ln in lines.splitlines())]
            blocks.append(Table([[" ", [Table(rows, widths=[0.3, 0.7], padding=1, size=10)]]],
                                widths=[0.06, 0.94], padding=0))
            blocks.append(Spacer(2))
        else:
            blocks.append(Text(lines, align="justify", space_after=8))
    blocks.append(Spacer(4))
    blocks.append(_signature(profile, "", label="Hormat kami,"))
    return DocSpec(title=f"Surat {number}", blocks=blocks)


# --------------------------------------------------------------------------- payslip

INCOME_PREFIXES = ("gaji_pokok", "tunjangan", "lembur", "bonus", "insentif", "thr", "uang_",
                   "komisi", "upah")
DEDUCTION_PREFIXES = ("potongan", "bpjs", "pph", "pajak", "kasbon", "pinjaman", "denda", "iuran")
ACRONYMS = {"bpjs": "BPJS", "pph21": "PPh 21", "pph": "PPh", "thr": "THR", "tk": "TK",
            "jht": "JHT", "jp": "JP", "ppn": "PPN"}


def _label(column: str, strip_prefix: str = "") -> str:
    name = column[len(strip_prefix):] if strip_prefix and column.startswith(strip_prefix) else \
        column
    return " ".join(ACRONYMS.get(w, w.capitalize()) for w in name.strip("_").split("_"))


def payslip_spec(ctx: Context, profile: dict) -> DocSpec:
    color = profile.get("warna") or "#1F4E78"
    income, deduction = [], []
    for key, value in ctx.items():
        if key in ("hari_ini", "urut") or not isinstance(value, (int, float)) or \
                isinstance(value, bool):
            continue
        if key.startswith(DEDUCTION_PREFIXES):
            deduction.append((_label(key, "potongan_"), float(value)))
        elif key.startswith(INCOME_PREFIXES):
            income.append((_label(key), float(value)))
    income = [(k, v) for k, v in income if v]
    deduction = [(k, v) for k, v in deduction if v]
    total_in = sum(v for _, v in income)
    total_out = sum(v for _, v in deduction)
    net = total_in - total_out
    period = _first(ctx, "periode", "bulan")
    period_text = format_bulan(period) if isinstance(period, (pd.Timestamp, dt.date)) else \
        _text(period)
    name = _text(_first(ctx, "nama", "nama_karyawan")) or "-"

    blocks: list[Block] = _letterhead(profile, compact=True)
    blocks.append(Text("SLIP GAJI KARYAWAN", size=15, bold=True, align="center", color=color,
                       space_after=1))
    if period_text:
        blocks.append(Text(f"Periode: {period_text}", align="center", space_after=8))
    left = [["Nama", f": {name}"],
            ["NIK/NIP", f": {_text(_first(ctx, 'nik', 'nip', 'id_karyawan', 'no_karyawan'))}"]]
    right = [["Jabatan", f": {_text(_first(ctx, 'jabatan', 'posisi'))}"],
             ["Departemen", f": {_text(_first(ctx, 'departemen', 'divisi', 'bagian'))}"]]
    blocks.append(Table([[[Table(left, widths=[0.3, 0.7], padding=1)],
                          [Table(right, widths=[0.3, 0.7], padding=1)]]],
                        widths=[0.5, 0.5], padding=0))
    blocks.append(Spacer(3))

    rows: list[list[Cell]] = [["Komponen", "Jumlah"], ["PENDAPATAN", ""]]
    bold = {1}
    shade = {1: "#DDEBF7"}
    rows += [[f"   {k}", format_rupiah(v)] for k, v in income]
    rows.append(["Total Pendapatan", format_rupiah(total_in)])
    bold.add(len(rows) - 1)
    rows.append(["POTONGAN", ""])
    bold.add(len(rows) - 1)
    shade[len(rows) - 1] = "#FBE5D6"
    rows += [[f"   {k}", format_rupiah(v)] for k, v in deduction] or [["   -", format_rupiah(0)]]
    rows.append(["Total Potongan", format_rupiah(total_out)])
    bold.add(len(rows) - 1)
    rows.append(["GAJI BERSIH (TAKE HOME PAY)", format_rupiah(net)])
    bold.add(len(rows) - 1)
    shade[len(rows) - 1] = "#C6E0B4"
    blocks.append(Table(rows, widths=[0.65, 0.35], header=True, grid=True,
                        align=["left", "right"], bold_rows=bold, shade_rows=shade,
                        header_color=color, size=10))
    blocks.append(Text(f"Terbilang: {terbilang(net).capitalize()} rupiah", italic=True, size=9,
                       space_before=6))
    bank = _first(ctx, "bank", "nama_bank")
    account = _first(ctx, "no_rekening", "rekening", "nomor_rekening")
    if bank or account:
        blocks.append(Text(f"Ditransfer ke: {_text(bank)} {_text(account)}".strip(), size=9))
    blocks.append(Spacer(4))
    blocks.append(Table([[
        _signature(profile, _place_date(profile, None), label="Penerima,", name=name,
                   title="", side="left").rows[0][0],
        _signature(profile, " ", label="Bagian Keuangan/HRD,").rows[0][1],
    ]], widths=[0.5, 0.5]))
    blocks.append(Text("Dokumen ini dibuat otomatis dan bersifat rahasia.", size=7.5,
                       italic=True, color="#777777", align="center", space_before=10))
    return DocSpec(title=f"Slip gaji {name} {period_text}", blocks=blocks)


# --------------------------------------------------------------------------- custom DOCX


def _replace_in_paragraph(paragraph, ctx: Context, missing: set[str]) -> int:
    runs = paragraph.runs
    if not runs:
        return 0
    texts = [r.text for r in runs]
    full = "".join(texts)
    if "{{" not in full:
        return 0
    matches = list(PLACEHOLDER_RE.finditer(full))
    if not matches:
        return 0
    starts = []
    pos = 0
    for t in texts:
        starts.append(pos)
        pos += len(t)

    def run_at(offset: int) -> int:
        for i in range(len(runs) - 1, -1, -1):
            if starts[i] <= offset and (len(texts[i]) or starts[i] < offset):
                return i
        return 0

    count = 0
    for m in reversed(matches):
        key, filt = m.group(1).strip(), m.group(2)
        found, value = ctx.lookup(key)
        if not found:
            missing.add(key)
            continue
        new = format_value(normalize_column_name(key), value, filt)
        s, e = m.span()
        first, last = run_at(s), run_at(e - 1)
        if first == last:
            off = s - starts[first]
            texts[first] = texts[first][:off] + new + texts[first][off + (e - s):]
        else:
            texts[first] = texts[first][: s - starts[first]] + new
            for i in range(first + 1, last):
                texts[i] = ""
            texts[last] = texts[last][e - starts[last]:]
        count += 1
    for run, text in zip(runs, texts):
        if run.text != text:
            run.text = text
    return count


def fill_docx_template(template: str | Path, ctx: Context, output: str | Path,
                       missing: set[str] | None = None) -> Path:
    """Copy ``template`` replacing ``{{kolom}}`` placeholders in body, tables, text boxes,
    headers and footers. Formatting of the run where a placeholder starts is kept."""
    from docx import Document
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.oxml.ns import qn
    from docx.text.paragraph import Paragraph

    missing = missing if missing is not None else set()
    doc = Document(str(template))
    elements = [doc.element.body]
    for rel in doc.part.rels.values():
        if rel.reltype in (RT.HEADER, RT.FOOTER, RT.FOOTNOTES, RT.ENDNOTES) and not rel.is_external:
            elements.append(rel.target_part.element)
    for root in elements:
        for p in root.iter(qn("w:p")):
            _replace_in_paragraph(Paragraph(p, doc.part), ctx, missing)
    output = Path(output)
    doc.save(str(output))
    return output


def docx_to_pdf(files: list[Path], out_dir: Path) -> list[Path]:
    """Convert DOCX files to PDF with LibreOffice (``soffice``) when it is installed."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise DataToolsError(
            "Konversi DOCX ke PDF butuh LibreOffice (perintah 'soffice'). Install LibreOffice "
            "atau gunakan --format docx."
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as profile_dir:
        cmd = [soffice, f"-env:UserInstallation=file://{Path(profile_dir).as_posix()}",
               "--headless", "--convert-to", "pdf", "--outdir", str(out_dir),
               *[str(f) for f in files]]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=60 + 20 * len(files))
        except (subprocess.SubprocessError, OSError) as exc:
            raise DataToolsError(f"LibreOffice gagal mengonversi DOCX ke PDF: {exc}") from exc
    results = [out_dir / f"{f.stem}.pdf" for f in files]
    return [p for p in results if p.exists()]


# --------------------------------------------------------------------------- orchestration


@dataclass
class GenerateResult:
    files: list[Path] = field(default_factory=list)
    merged: Path | None = None
    missing: set[str] = field(default_factory=set)


DEFAULT_NAMES = {
    "invoice": "invoice_{urut}",
    "sertifikat": "sertifikat_{urut}_{nama}",
    "surat": "surat_{urut}_{nama}",
    "slip-gaji": "slip_gaji_{urut}_{nama}",
}


def _group_invoice_rows(df: pd.DataFrame, number_column: str | None) -> list[pd.DataFrame]:
    col = None
    if number_column:
        col = number_column if number_column in df.columns else normalize_column_name(
            number_column)
        if col not in df.columns:
            raise DataToolsError(f"Kolom nomor invoice '{number_column}' tidak ditemukan.")
    else:
        col = next((c for c in INVOICE_NUMBER if c in df.columns), None)
    if col is None:
        log.warning("Kolom nomor invoice tidak ditemukan: setiap baris jadi 1 invoice")
        return [df.iloc[[i]] for i in range(len(df))]
    groups = []
    for _, g in df.groupby(df[col].astype(str), sort=False):
        groups.append(g)
    log.info("%d baris dikelompokkan menjadi %d invoice (kolom '%s')", len(df), len(groups), col)
    return groups


def generate_documents(
    template: str,
    df: pd.DataFrame,
    out_dir: str | Path,
    formats: tuple[str, ...] = ("pdf",),
    name_pattern: str | None = None,
    profile: dict | None = None,
    body: str | None = None,
    merge_to: str | Path | None = None,
    number_column: str | None = None,
    ppn: float | None = None,
    limit: int | None = None,
    progress: Callable[[Path], None] | None = None,
) -> GenerateResult:
    """Generate one document per row (per invoice number for ``invoice``)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    profile = profile or dict(DEFAULT_PROFILE)
    result = GenerateResult()
    custom = template.lower().endswith(".docx")
    if not custom and template not in TEMPLATES:
        raise DataToolsError(
            f"Template '{template}' tidak dikenal. Pilih: {', '.join(TEMPLATES)} atau path .docx"
        )
    if custom and not Path(template).is_file():
        raise DataToolsError(f"Template DOCX tidak ditemukan: {template}")
    if df.empty:
        raise DataToolsError("Data kosong: tidak ada dokumen yang dibuat.")

    df = df.copy()
    df.columns = [normalize_column_name(c) or f"kolom_{i}" for i, c in enumerate(df.columns, 1)]
    if template == "invoice":
        groups = _group_invoice_rows(df, number_column)
    else:
        groups = [df.iloc[[i]] for i in range(len(df))]
    if limit:
        groups = groups[:limit]
    pattern = name_pattern or (
        f"{Path(template).stem}_{{urut}}" if custom else DEFAULT_NAMES[template]
    )
    used: set[Path] = set()
    docx_for_pdf: list[Path] = []
    for i, group in enumerate(groups, start=1):
        contexts = [make_context(rec, i) for rec in group.to_dict("records")]
        ctx = contexts[0]
        stem = render_filename(pattern, ctx)
        if custom:
            docx_path = unique_path(out_dir / f"{stem}.docx", used)
            fill_docx_template(template, ctx, docx_path, result.missing)
            if "docx" in formats:
                result.files.append(docx_path)
            if "pdf" in formats:
                docx_for_pdf.append(docx_path)
            continue
        if template == "invoice":
            spec = invoice_spec(contexts, profile, ppn)
        elif template == "sertifikat":
            spec = certificate_spec(ctx, profile)
        elif template == "surat":
            spec = letter_spec(ctx, profile, body, result.missing)
        else:
            spec = payslip_spec(ctx, profile)
        for fmt in formats:
            path = unique_path(out_dir / f"{stem}.{fmt}", used)
            (render_pdf if fmt == "pdf" else render_docx)(spec, path)
            result.files.append(path)
            if progress:
                progress(path)
    if docx_for_pdf:
        pdfs = docx_to_pdf(docx_for_pdf, out_dir)
        result.files.extend(pdfs)
        if "docx" not in formats:
            for f in docx_for_pdf:
                f.unlink(missing_ok=True)
    if result.missing:
        log.warning("Peringatan: placeholder tanpa kolom yang cocok: %s",
                    ", ".join(sorted(result.missing)))
    if merge_to:
        pdfs = [f for f in result.files if f.suffix == ".pdf"]
        if not pdfs:
            raise DataToolsError("Tidak ada PDF untuk digabung (gunakan --format pdf).")
        from data_tools.convert import merge_pdfs

        result.merged = merge_pdfs(pdfs, Path(merge_to))
    return result
