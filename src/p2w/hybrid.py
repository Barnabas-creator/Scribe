"""Hybrid path: body text from the text layer, formulas from the VLM.

The approach is subtractive. Cropping formulas one by one and sending each to
the engine loses: every call carries a fixed 12-14s overhead regardless of
image size. Instead the call count stays at one -- pure-text lines are painted
white so the model only has to emit formulas. Roughly 27% of lines carry
formulas but only 3% of characters, cutting output tokens by ~97%.

Whited-out pages must be assembled into a single PDF and sent once; per-page
images would multiply the fixed overhead. Pages with no formulas are excluded
entirely and served from the text layer.

Merging: erased lines come from the text layer verbatim; kept lines come from
the model, because formulas are stored in draw order in the text layer and
extract as scrambled characters. The two sets are disjoint and interleaved by
y coordinate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import model as M
from .config import ConvertOptions
from .textlayer import _MATH_FONTS, _join_spans

# Dilation applied to math spans before region merging.
_DILATE = 6.0
# Vertical gap still considered one formula: a fraction is numerator/bar/
# denominator stacked, and the middle pieces are often non-math glyphs.
_STACK_GAP_LINES = 2.0
# Halo added around regions when deciding keep/erase. Non-math glyphs embedded
# in a formula (italic H, D) sit some distance from the nearest math span;
# without the halo they get erased and the model sees a fraction with no
# numerator.
_HALO = 12.0


class HybridUnavailable(RuntimeError):
    """Document unsuitable for the hybrid path; caller falls back to full-page."""


def convert(pdf_path: Path, work: Path, opts: ConvertOptions,
            should_cancel: Callable[[], bool] | None = None,
            phase: Callable[[str], None] | None = None) -> tuple[M.DocModel, Path]:
    """Recognize a PDF via the hybrid path; returns (DocModel, image dir)."""
    from .mineru_backend import load_content_list, run_mineru

    pages = plan(pdf_path)
    if not any(p["regions"] for p in pages):
        # Math fonts declared but no formula region found: fall back to full-page.
        raise HybridUnavailable("没有找到公式区域")

    work.mkdir(parents=True, exist_ok=True)
    sparse = work / "sparse.pdf"
    page_map = render_sparse(pdf_path, pages, sparse)

    if phase:
        phase("ocr")
    json_path, image_dir = run_mineru(sparse, work / "out", opts,
                                      should_cancel=should_cancel)
    by_page = _group_by_page(load_content_list(json_path), page_map)

    # Safety net: a page with formula regions but no recognized block is redone
    # full-page, body text included -- mixing two sources on one page is worse
    # than being slow.
    missing = [p["page"] for p in pages if p["regions"] and not by_page.get(p["page"])]
    redo: dict[int, list] = {}
    if missing:
        redo_pdf = work / "redo.pdf"
        _subset(pdf_path, missing, redo_pdf)
        rj, ri = run_mineru(redo_pdf, work / "redo_out", opts,
                            should_cancel=should_cancel)
        redo = _group_by_page(load_content_list(rj), missing)
        image_dir = _merge_image_dirs(image_dir, ri)

    per_page = []
    for info in pages:
        pno = info["page"]
        if pno in redo:
            per_page.append(_vlm_only(info, redo[pno], image_dir))
        else:
            per_page.append(merge_page(info, by_page.get(pno, []), image_dir))
    return assemble(pages, per_page, source_file=str(pdf_path)), image_dir


def plan(pdf_path: str | Path) -> list[dict]:
    """Per page, decide which lines to erase and where formula regions are.

    Returns one dict per page. Pages with empty regions skip the sparse PDF and
    are served entirely from the text layer.
    """
    import pymupdf

    from .textlayer import _body_font_size

    out: list[dict] = []
    with pymupdf.open(str(pdf_path)) as doc:
        body_size = _body_font_size(doc)
        for pno, page in enumerate(doc):
            blocks, math_rects, heights = [], [], []
            for block in page.get_text("dict").get("blocks", []):
                if block.get("type") != 0:
                    continue
                lines = []
                for line in block.get("lines", []):
                    if not _join_spans([s.get("text", "") for s in line.get("spans", [])]):
                        continue
                    rect = pymupdf.Rect(line["bbox"])
                    lines.append((rect, line))
                    heights.append(rect.height)
                    for span in line.get("spans", []):
                        if (_MATH_FONTS.search(span.get("font", ""))
                                and span.get("text", "").strip()):
                            math_rects.append(
                                pymupdf.Rect(span["bbox"])
                                + (-_DILATE, -_DILATE, _DILATE, _DILATE))
                if lines:
                    blocks.append(lines)

            line_h = sorted(heights)[len(heights) // 2] if heights else 10.0
            regions = _merge_regions(math_rects, _STACK_GAP_LINES * line_h)
            # Never erase lines inside tables: rebuilding them as paragraphs
            # would lose the table structure. Leave them to the model.
            keep_zones = [r + (-_HALO, -_HALO, _HALO, _HALO) for r in regions]
            keep_zones += _table_rects(page)

            erase, text_blocks = [], []
            for lines in blocks:
                run: list[dict] = []          # consecutive erased lines -> one paragraph
                for rect, line in lines:
                    if any(rect.intersects(z) for z in keep_zones):
                        _flush(run, text_blocks)      # formula line breaks the run
                        continue
                    erase.append(rect)
                    run.append(line)
                _flush(run, text_blocks)

            out.append({
                "page": pno,
                "pdf": str(pdf_path),
                "erase": erase,             # line rects to paint white
                "text_blocks": text_blocks,  # PyMuPDF-shaped blocks for reassembly
                "body_size": body_size,
                "regions": regions,
                "size": (page.rect.width, page.rect.height),
            })
    return out


def _flush(run: list[dict], out: list[dict]) -> None:
    """Collect buffered erased lines into one PyMuPDF-shaped text block.

    Grouping by block rather than by line; per-line paragraphs would render as
    a wall of hard-broken short paragraphs in Word.
    """
    if not run:
        return
    xs = [b for line in run for b in (line["bbox"][0], line["bbox"][2])]
    ys = [b for line in run for b in (line["bbox"][1], line["bbox"][3])]
    out.append({"lines": list(run), "bbox": (min(xs), min(ys), max(xs), max(ys))})
    run.clear()


def _table_rects(page) -> list:
    import pymupdf
    try:
        return [pymupdf.Rect(t.bbox) for t in page.find_tables().tables]
    except Exception:
        return []


def _merge_regions(rects, stack_gap: float):
    """Merge math spans into formula regions: intersecting ones, plus vertically
    adjacent ones with overlapping x range (that is what a fraction looks like).
    """
    import pymupdf

    regs = [pymupdf.Rect(r) for r in rects]
    changed = True
    while changed:
        changed = False
        for i in range(len(regs)):
            for j in range(i + 1, len(regs)):
                a, b = regs[i], regs[j]
                x_overlap = min(a.x1, b.x1) - max(a.x0, b.x0)
                y_gap = max(b.y0 - a.y1, a.y0 - b.y1)
                if a.intersects(b) or (x_overlap > 0 and 0 <= y_gap <= stack_gap):
                    a.include_rect(b)
                    del regs[j]
                    changed = True
                    break
            if changed:
                break
    return regs


def render_sparse(pdf_path: str | Path, pages: list[dict], dest: Path) -> list[int]:
    """Whiten and assemble formula-bearing pages into one PDF; returns the
    original page number for each page in it.
    """
    import pymupdf

    page_map: list[int] = []
    with pymupdf.open(str(pdf_path)) as doc:
        out = pymupdf.open()
        for info in pages:
            if not info["regions"]:
                continue
            out.insert_pdf(doc, from_page=info["page"], to_page=info["page"])
            page = out[-1]
            for rect in info["erase"]:
                page.draw_rect(rect + (-1, -1, 1, 1), color=None,
                               fill=(1, 1, 1), overlay=True)
            page_map.append(info["page"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        out.save(str(dest))
        out.close()
    return page_map


def _subset(pdf_path: str | Path, page_nos: list[int], dest: Path) -> None:
    import pymupdf
    with pymupdf.open(str(pdf_path)) as doc:
        out = pymupdf.open()
        for pno in page_nos:
            out.insert_pdf(doc, from_page=pno, to_page=pno)
        out.save(str(dest))
        out.close()


def _group_by_page(blocks: list, page_map: list[int]) -> dict[int, list]:
    """Group model output by page_idx back onto original page numbers."""
    out: dict[int, list] = {}
    for b in blocks:
        if not isinstance(b, dict):
            continue
        idx = b.get("page_idx", 0)
        if 0 <= idx < len(page_map):
            out.setdefault(page_map[idx], []).append(b)
    return out


def _merge_image_dirs(primary: Path, extra: Path) -> Path:
    """Merge images from the redo run into the primary directory."""
    import shutil
    src = Path(extra) / "images"
    dst = Path(primary) / "images"
    if src.is_dir():
        dst.mkdir(parents=True, exist_ok=True)
        for f in src.glob("*"):
            shutil.copy2(f, dst / f.name)
    return primary


def _vlm_only(info: dict, blocks: list, image_dir: Path) -> list[M.Block]:
    """Safety-net page: use model output for the whole page."""
    from .parse_mineru import parse_content_list

    out = []
    for blk in parse_content_list(blocks, image_dir, source_file="").sorted_blocks():
        blk.page = info["page"] + 1
        out.append(blk)
    return out


def merge_page(info: dict, vlm_blocks: list, image_dir: Path) -> list[M.Block]:
    """Assemble one page: erased lines from the text layer, kept lines from the
    model, interleaved by y. Model bboxes are 0-1000 normalized and converted
    back to PDF points for comparison.
    """
    from .parse_mineru import parse_content_list
    from .textlayer import _image_blocks, _text_block

    height = info["size"][1]
    items: list[tuple[float, M.Block]] = []

    # Text-layer half: erased paragraphs verbatim (heading detection reused).
    for blk in info["text_blocks"]:
        built = _text_block(blk, info["body_size"], info["page"] + 1)
        if built is not None:
            items.append((blk["bbox"][1], built))

    if vlm_blocks:
        # Model half: formulas, their lines, tables and figures left on the page.
        sub = parse_content_list(vlm_blocks, image_dir, source_file="")
        for blk in sub.sorted_blocks():
            y = (blk.bbox[1] / 1000.0 * height) if any(blk.bbox) else 0.0
            blk.page = info["page"] + 1
            items.append((y, blk))
    elif not info["regions"]:
        # Page never went to the model: extract figures from the text layer.
        import pymupdf
        with pymupdf.open(info["pdf"]) as doc:
            for blk in _image_blocks(doc[info["page"]], info["page"] + 1, Path(image_dir)):
                items.append((blk.bbox[1], blk))

    items.sort(key=lambda t: t[0])
    return [b for _, b in items]


def assemble(pages: list[dict], per_page_blocks: list[list[M.Block]],
             source_file: str = "") -> M.DocModel:
    """Concatenate per-page blocks into one DocModel in page order."""
    doc = M.DocModel(source_file=source_file)
    order = 0
    for blocks in per_page_blocks:
        for blk in blocks:
            order += 1
            blk.reading_order = order
            doc.blocks.append(blk)
    return doc
