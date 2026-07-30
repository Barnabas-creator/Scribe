"""Hybrid path: which lines get erased, what the sparse page looks like, and
how the two halves are reassembled.

No model runs here -- this covers the logic before and after recognition.

Run: python3 tests/test_hybrid.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from p2w import hybrid
from p2w import model as M
from p2w import textlayer


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    paper = repo / "tmp" / "Liu_2024_ApJL_965_L11.pdf"

    # ---- Merge: erased lines from the text layer, model blocks slotted by y ----
    info = {
        "page": 0, "pdf": "", "size": (595.0, 842.0), "body_size": 10.0,
        "regions": [object()],                      # non-empty just marks "went to model"
        "erase": [],
        "text_blocks": [_fake_block("上面这段是正文", 100), _fake_block("下面这段也是正文", 400)],
    }
    vlm = [{"type": "equation", "text": "$$E=mc^2$$", "page_idx": 0,
            "bbox": [100, 250, 400, 300]}]          # y 250/1000 -> 210pt
    blocks = hybrid.merge_page(info, vlm, Path("."))
    kinds = [b.type for b in blocks]
    assert kinds == [M.PARAGRAPH, M.FORMULA, M.PARAGRAPH], kinds
    assert blocks[0].spans[0].text == "上面这段是正文"
    assert "E=mc^2" in blocks[1].latex, blocks[1].latex

    # ---- Page mapping: model page_idx indexes the sparse PDF, not the original ----
    grouped = hybrid._group_by_page(
        [{"type": "text", "page_idx": 0}, {"type": "text", "page_idx": 2},
         {"type": "text", "page_idx": 9}],          # out of range, dropped
        page_map=[3, 5, 7])
    assert sorted(grouped) == [3, 7], grouped

    if not paper.exists():
        print("OK  拼装与归页（缺 tmp/ 论文，跳过真实 PDF 部分）")
        return 0

    # ---- Precondition: this paper is rejected by the fast path for formulas only ----
    why = textlayer.probe(paper)
    assert why and why.startswith(textlayer.MATH_REASON), why

    # ---- Line classification on page 3, the densest page ----
    pages = hybrid.plan(paper)
    p3 = pages[2]
    assert len(p3["regions"]) == 25, f"区域数变了：{len(p3['regions'])}"
    assert p3["erase"], "一行都没涂白说明判据坏了"
    # Invariant: erased lines must clear every formula region, or the model
    # receives a formula with a missing part.
    for rect in p3["erase"]:
        for reg in p3["regions"]:
            assert not rect.intersects(reg), f"涂到公式上了：{tuple(rect)}"
    # Grouped by block, not by line
    assert len(p3["text_blocks"]) < len(p3["erase"]) / 2, (
        f"{len(p3['erase'])} 行只收成了 {len(p3['text_blocks'])} 段，成段逻辑没生效")

    # ---- Sparse PDF: only formula pages, with page numbers preserved ----
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "sparse.pdf"
        page_map = hybrid.render_sparse(paper, pages, dest)
        assert page_map == [p["page"] for p in pages if p["regions"]], page_map
        import pymupdf
        with pymupdf.open(str(dest)) as d:
            assert d.page_count == len(page_map), (d.page_count, len(page_map))
            # Erased areas must actually be white
            page = d[page_map.index(2)]
            rect = p3["erase"][0]
            pix = page.get_pixmap(clip=rect, dpi=72)
            assert set(pix.samples) == {255}, "涂白行还有墨迹"

    print("OK  涂白/保留判据、稀疏 PDF 页号、两边拼装")
    return 0


def _fake_block(text: str, y: float) -> dict:
    """Minimal PyMuPDF text-block shape, enough for _text_block."""
    bbox = (50.0, y, 500.0, y + 12)
    return {"bbox": bbox,
            "lines": [{"bbox": bbox,
                       "spans": [{"text": text, "size": 10.0, "flags": 0, "bbox": bbox}]}]}


if __name__ == "__main__":
    sys.exit(main())
