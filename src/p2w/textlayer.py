"""Text-layer fast path: pull text straight out of digital PDFs.

Digitally typeset PDFs already carry text, sizes, positions and table rules, so
running a 1.2B vision model over them wastes tens of seconds per page for no
gain. `probe()` decides eligibility and is deliberately strict: rejecting a file
only costs time, while wrongly accepting one corrupts the output.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import model as M

# Math fonts imply formulas, which the text layer extracts as scrambled glyphs.
_MATH_FONTS = re.compile(
    r"cmmi|cmsy|cmex|msam|msbm|mathjax|"          # TeX / MathJax
    r"cambria\s*math|mt\s*extra|mtsy|mtmi|"       # Word / MathType
    r"symbol|euclid|mathematica|stix",            # 其它常见数学字体
    re.I,
)
# Minimum average characters per page. Averaged rather than per-page: covers,
# section dividers and plate pages legitimately carry almost no text.
_MIN_CHARS_PER_PAGE = 50
# Image coverage above this fraction means a scan with an attached OCR layer.
_SCAN_IMAGE_COVERAGE = 0.7
# Reason prefix used when probe() rejects for formulas; pipeline keys the
# hybrid path off it.
MATH_REASON = "含公式字体"
# Average curved vector drawings per page above which formulas are assumed to be
# vector-drawn (and therefore absent from the text layer).
_CURVES_PER_PAGE = 25


def probe(pdf_path: str | Path) -> str | None:
    """Return None if the fast path applies, else a user-facing reason."""
    import pymupdf

    path = Path(pdf_path)
    if path.suffix.lower() != ".pdf":
        return "不是 PDF"

    try:
        doc = pymupdf.open(str(path))
    except Exception:
        return "打不开"

    with doc:
        if doc.page_count == 0:
            return "没有页面"

        total_chars = 0
        for page in doc:
            total_chars += len(page.get_text().strip())
            if _looks_scanned(page):
                return "扫描件（文字层像是 OCR 附加的）"

        if total_chars / doc.page_count < _MIN_CHARS_PER_PAGE:
            return "文字太少，更像是图片版"

        # Math-font check: returning MATH_REASON means every other criterion
        # passed, so the hybrid path can take over. It must run before the
        # vector check -- papers with font-based formulas plus vector plots
        # would otherwise be diverted to full-page recognition.
        for page in doc:
            for font in page.get_fonts(full=False):
                # get_fonts 每项形如 (xref, ext, type, basefont, name, encoding)
                name = " ".join(str(x) for x in font[3:5])
                if _MATH_FONTS.search(name):
                    return f"{MATH_REASON}（{font[3]}）"

        # PDFs that draw formulas as vector paths (Notion/Feishu/KaTeX exports)
        # leave nothing but gaps in the text layer, so the fast path would drop
        # every formula silently. Glyph outlines are Bezier curves while table
        # rules are straight lines, which separates the two cleanly.
        curved = 0
        for page in doc:
            for dr in page.get_drawings():
                if any(it[0] == "c" for it in dr["items"]):
                    curved += 1
        if curved / doc.page_count >= _CURVES_PER_PAGE:
            return "公式或插图是矢量绘制的，文字层里没有"

    return None


def _looks_scanned(page) -> bool:
    """A single image covering most of the page means a scan."""
    page_area = abs(page.rect.width * page.rect.height)
    if page_area <= 0:
        return False
    for img in page.get_images(full=True):
        try:
            for rect in page.get_image_rects(img[0]):
                if abs(rect.width * rect.height) / page_area >= _SCAN_IMAGE_COVERAGE:
                    return True
        except Exception:
            continue
    return False


def extract(pdf_path: str | Path, image_dir: str | Path) -> M.DocModel:
    """Build a DocModel from the text layer; figures are written to image_dir."""
    import pymupdf

    path = Path(pdf_path)
    image_dir = Path(image_dir)
    image_dir.mkdir(parents=True, exist_ok=True)
    doc_model = M.DocModel(source_file=str(path))
    order = 0

    with pymupdf.open(str(path)) as doc:
        body_size = _body_font_size(doc)
        for pno, page in enumerate(doc, start=1):
            table_rects = []
            for blk in _table_blocks(page, pno):
                order += 1
                blk.reading_order = order
                doc_model.blocks.append(blk)
                table_rects.append(blk.bbox)

            # No sorting: content-stream order is authoring order, which is
            # normally reading order. Coordinate sorting (sort=True) inverts
            # two-column label/value layouts.
            data = page.get_text("dict")
            for block in data.get("blocks", []):
                if block.get("type") == 1:      # image block, handled below
                    continue
                if _inside_any(block.get("bbox"), table_rects):
                    continue                    # text already captured by the table
                built = _text_block(block, body_size, pno)
                if built is None:
                    continue
                order += 1
                built.reading_order = order
                doc_model.blocks.append(built)

            for blk in _image_blocks(page, pno, image_dir):
                order += 1
                blk.reading_order = order
                doc_model.blocks.append(blk)

    return doc_model


def _body_font_size(doc) -> float:
    """Body size is the most common font size; headings are notably larger."""
    counts: dict[float, int] = {}
    for page in doc:
        for block in page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    size = round(span.get("size", 0), 1)
                    counts[size] = counts.get(size, 0) + len(span.get("text", ""))
    return max(counts, key=counts.get) if counts else 12.0


def _text_block(block: dict, body_size: float, page_no: int) -> M.Block | None:
    text_parts: list[str] = []
    max_size = 0.0
    bold = False
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            t = span.get("text", "")
            if not t:
                continue
            text_parts.append(t)
            max_size = max(max_size, span.get("size", 0))
            # flags bit 4 marks bold
            bold = bold or bool(span.get("flags", 0) & 2 ** 4)
        text_parts.append(" ")
    text = _join_spans(text_parts)
    if not text:
        return None

    bbox = tuple(block.get("bbox", (0, 0, 0, 0)))
    # Larger than body means heading; same size but bold and short is a subheading.
    if max_size >= body_size * 1.25:
        level = 1 if max_size >= body_size * 1.6 else 2
        return M.Block(type=M.HEADING, level=level, page=page_no, bbox=bbox,
                       spans=[M.Span(text=text)])
    if bold and len(text) <= 30:
        return M.Block(type=M.HEADING, level=3, page=page_no, bbox=bbox,
                       spans=[M.Span(text=text)])
    return M.Block(type=M.PARAGRAPH, page=page_no, bbox=bbox, spans=[M.Span(text=text)])


# PyMuPDF splits a line into spans at every font/size change. Rejoining needs
# spaces between Latin words but not between CJK characters. Only half-width
# spaces are dropped: U+3000 is deliberate typography.
_CJK = r"\u4e00-\u9fff\uff00-\uffef"
_SPACE_BETWEEN_CJK = re.compile(rf"(?<=[{_CJK}])[ \t]+(?=[{_CJK}])")


def _join_spans(parts: list[str]) -> str:
    text = re.sub(r"[ \t]+", " ", "".join(parts))
    text = re.sub(r"\n+", " ", text)
    return _SPACE_BETWEEN_CJK.sub("", text).strip()


def _table_blocks(page, page_no: int) -> list[M.Block]:
    """PyMuPDF table detection, driven by real rules rather than inference."""
    out: list[M.Block] = []
    try:
        found = page.find_tables()
    except Exception:
        return out
    for table in found.tables:
        try:
            rows = table.extract()
        except Exception:
            continue
        cells = [[[M.Span(text=(c or "").strip())] for c in row] for row in rows if row]
        if not cells:
            continue
        out.append(M.Block(type=M.TABLE, page=page_no, rows=cells,
                           bbox=tuple(table.bbox)))
    return out


def _image_blocks(page, page_no: int, image_dir: Path) -> list[M.Block]:
    out: list[M.Block] = []
    for i, img in enumerate(page.get_images(full=True)):
        xref = img[0]
        try:
            rects = page.get_image_rects(xref)
            bbox = tuple(rects[0]) if rects else (0, 0, 0, 0)
            pix = page.parent.extract_image(xref)
            dest = image_dir / f"p{page_no}_img{i}.{pix['ext']}"
            dest.write_bytes(pix["image"])
        except Exception:
            continue
        out.append(M.Block(type=M.PICTURE, page=page_no, bbox=bbox,
                           image_path=str(dest)))
    return out


def _inside_any(bbox, rects) -> bool:
    if not bbox or not rects:
        return False
    x0, y0, x1, y1 = bbox
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    for r in rects:
        if r[0] <= cx <= r[2] and r[1] <= cy <= r[3]:
            return True
    return False
