import json

import pandas as pd
import pytest
from PIL import Image
from pypdf import PdfReader

from data_tools import DataToolsError
from data_tools.convert import (
    csv_to_excel,
    excel_to_csv,
    images_to_pdf,
    merge_pdfs,
    parse_ranges,
    split_pdf,
    table_to_pdf,
)
from data_tools.files import (
    STATUS_CONFLICT,
    STATUS_OK,
    STATUS_SAME,
    RenameOptions,
    apply_renames,
    plan_renames,
    undo_renames,
)
from data_tools.images import ImageOptions, process_images


def _touch(folder, *names):
    folder.mkdir(parents=True, exist_ok=True)
    for n in names:
        (folder / n).write_text(n, encoding="utf-8")


def test_rename_preview_does_not_touch_files(tmp_path):
    _touch(tmp_path, "IMG 2.JPG", "IMG 10.JPG", "IMG 1.JPG", "catatan.txt")
    opts = RenameOptions(pattern="foto_{no}", lower_ext=True, match="*.jpg")
    plans = plan_renames(tmp_path, opts)
    # natural sort: 1, 2, 10
    assert [(p.source.name, p.target.name) for p in plans] == [
        ("IMG 1.JPG", "foto_001.jpg"), ("IMG 2.JPG", "foto_002.jpg"), ("IMG 10.JPG", "foto_003.jpg"),
    ]
    assert (tmp_path / "IMG 1.JPG").exists()


def test_rename_apply_and_undo(tmp_path):
    _touch(tmp_path, "a b.txt", "c d.txt")
    opts = RenameOptions(prefix="2026_", spaces="_", case="besar")
    plans = plan_renames(tmp_path, opts)
    log = apply_renames(plans, tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir() if not p.name.startswith("_log")) == [
        "2026_A_B.txt", "2026_C_D.txt"]
    data = json.loads(log.path.read_text(encoding="utf-8"))
    assert data["jumlah"] == 2 and not data["dibatalkan"]
    _, restored, problems = undo_renames(tmp_path)  # folder -> latest log
    assert restored == 2 and not problems
    assert (tmp_path / "a b.txt").read_text(encoding="utf-8") == "a b.txt"
    with pytest.raises(DataToolsError):
        undo_renames(log.path)  # already undone


def test_rename_find_replace_regex_and_swap(tmp_path):
    _touch(tmp_path, "laporan-jan.pdf", "laporan-feb.pdf")
    opts = RenameOptions(find=r"laporan-(\w+)", replace=r"\1_laporan", regex=True)
    assert [p.target.name for p in plan_renames(tmp_path, opts)] == [
        "feb_laporan.pdf", "jan_laporan.pdf"]
    # swapping two names works thanks to temporary names
    _touch(tmp_path / "swap", "1.txt", "2.txt")
    plans = plan_renames(tmp_path / "swap", RenameOptions(pattern="{no:1}", start=1))
    plans[0].target, plans[1].target = plans[1].source, plans[0].source
    for p in plans:
        p.status = STATUS_OK
    apply_renames(plans, tmp_path / "swap")
    assert (tmp_path / "swap" / "1.txt").read_text(encoding="utf-8") == "2.txt"


def test_rename_conflicts(tmp_path):
    _touch(tmp_path, "a.txt", "b.txt", "x.txt")
    plans = plan_renames(tmp_path, RenameOptions(pattern="sama", match="a.txt,b.txt"))
    assert [p.status for p in plans] == [STATUS_OK, STATUS_CONFLICT]
    plans = plan_renames(tmp_path, RenameOptions(pattern="x", match="a.txt"))
    assert plans[0].status == STATUS_CONFLICT  # x.txt exists and is not renamed
    plans = plan_renames(tmp_path, RenameOptions())
    assert {p.status for p in plans} == {STATUS_SAME}
    with pytest.raises(DataToolsError):
        plan_renames(tmp_path, RenameOptions(pattern="{tidak_dikenal}"))


def test_excel_csv_roundtrip(tmp_path):
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.title = "Satu"
    wb["Satu"].append(["kode", "nilai"])
    wb["Satu"].append(["007", 1.0])
    wb.create_sheet("Dua").append(["x"])
    wb["Dua"].append([2])
    src = tmp_path / "data.xlsx"
    wb.save(src)
    csvs = excel_to_csv([src], tmp_path / "csv", delimiter=";")
    assert sorted(p.name for p in csvs) == ["data_Dua.csv", "data_Satu.csv"]
    text = (tmp_path / "csv" / "data_Satu.csv").read_text(encoding="utf-8-sig")
    assert text.splitlines() == ["kode;nilai", "007;1"]
    out = csv_to_excel(csvs, tmp_path / "balik.xlsx")
    sheets = pd.read_excel(out[0], sheet_name=None, dtype=object)
    assert set(sheets) == {"data_Dua", "data_Satu"}
    assert sheets["data_Satu"]["kode"][0] == "007"


def test_table_to_pdf(tmp_path):
    path = tmp_path / "t.csv"
    rows = "\n".join(f"{i};Produk {i};Rp {i}.000" for i in range(1, 150))
    path.write_text("no;produk;harga\n" + rows, encoding="utf-8")
    pdf = table_to_pdf([path], tmp_path / "t.pdf", title="Daftar")
    reader = PdfReader(str(pdf))
    assert len(reader.pages) >= 2
    assert "Rp 149.000" in reader.pages[-1].extract_text()


def _images(folder, n=3):
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(n):
        p = folder / f"foto{i}.{'png' if i == 2 else 'jpg'}"
        mode = "RGBA" if i == 2 else "RGB"
        Image.new(mode, (1600, 900 if i else 2000), (200, 100, 50, 255)[: len(mode)]).save(p)
        paths.append(p)
    return paths


def test_images_resize_convert_watermark(tmp_path):
    _images(tmp_path / "in")
    opts = ImageOptions(max_width=800, max_height=800, fmt="jpg", quality=70, watermark="UJI",
                        suffix="_kecil")
    results = process_images([tmp_path / "in"], tmp_path / "out", opts)
    assert len(results) == 3
    for r in results:
        assert r.target.name.endswith("_kecil.jpg")
        with Image.open(r.target) as im:
            assert max(im.size) <= 800 and im.mode == "RGB"
    first = next(r for r in results if r.source.name == "foto0.jpg")
    assert first.size_after == (640, 800)  # aspect 1600x2000 kept
    webp = process_images([tmp_path / "in" / "foto2.png"], tmp_path / "webp",
                          ImageOptions(fmt="webp"))
    assert webp[0].target.suffix == ".webp"
    with pytest.raises(DataToolsError):
        process_images([tmp_path / "in"], tmp_path / "x", ImageOptions(quality=0))


def test_pdf_images_merge_split(tmp_path):
    imgs = _images(tmp_path / "img")
    pdf = images_to_pdf(imgs, tmp_path / "foto.pdf")
    assert len(PdfReader(str(pdf)).pages) == 3
    copy = tmp_path / "salinan.pdf"
    copy.write_bytes(pdf.read_bytes())
    merged = merge_pdfs([pdf, copy], tmp_path / "gabung.pdf")
    assert len(PdfReader(str(merged)).pages) == 6
    parts = split_pdf(merged, tmp_path / "pisah", pages="1-2,3,4-")
    assert [len(PdfReader(str(p)).pages) for p in parts] == [2, 1, 3]
    assert len(split_pdf(merged, tmp_path / "per2", every=2)) == 3
    assert parse_ranges("2,4-", 5) == [(2, 2), (4, 5)]
    with pytest.raises(DataToolsError):
        parse_ranges("7", 5)
