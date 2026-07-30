"""Text-layer fast path: which PDFs qualify and which must fall back.

Wrongly accepting a file is worse than being slow, so these tests focus on
probe() gating rather than extraction itself.

Run: python3 tests/test_textlayer.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from p2w import model as M
from p2w.textlayer import _join_spans, extract, probe


def make_text_pdf(path: Path) -> None:
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_htmlbox(pymupdf.Rect(50, 50, 545, 792), """
      <h2>会议纪要</h2>
      <p>本次会议讨论了下一阶段的排期安排，与会各方对交付时间达成一致。
      具体分工见下表，执行过程中如有变更需提前一周通知。</p>
      <table style="width:100%;border-collapse:collapse;border:1px solid #000">
        <tr><th style="border:1px solid #000;padding:4px">事项</th>
            <th style="border:1px solid #000;padding:4px">负责人</th></tr>
        <tr><td style="border:1px solid #000;padding:4px">需求整理</td>
            <td style="border:1px solid #000;padding:4px">张三</td></tr>
      </table>
    """)
    doc.save(str(path))
    doc.close()


def make_scanned_pdf(path: Path, img: Path) -> None:
    """Full-page image plus an invisible OCR layer, as Acrobat scans produce."""
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    src = pymupdf.open(str(img))
    inner = pymupdf.open("pdf", src.convert_to_pdf())
    src.close()
    page.show_pdf_page(page.rect, inner, 0)
    page.insert_text((60, 100), "附加的 OCR 文字层，质量不如重新识别。" * 6,
                     fontname="china-s", fontsize=9, render_mode=3)
    doc.save(str(path))
    doc.close()
    inner.close()


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)

        # ---- Accept: digital typesetting, no formulas ----
        text_pdf = d / "text.pdf"
        make_text_pdf(text_pdf)
        assert probe(text_pdf) is None, probe(text_pdf)

        doc = extract(text_pdf, d / "img")
        kinds = [b.type for b in doc.sorted_blocks()]
        assert M.HEADING in kinds and M.PARAGRAPH in kinds, kinds
        assert M.TABLE in kinds, "有线的表格应该被 find_tables 认出来"
        table = next(b for b in doc.blocks if b.type == M.TABLE)
        cells = [["".join(s.text for s in c) for c in row] for row in table.rows]
        assert cells[0] == ["事项", "负责人"], cells
        assert ["需求整理", "张三"] in cells, cells
        # Table text must not be duplicated into the body
        body = "".join("".join(s.text for s in b.spans)
                       for b in doc.blocks if b.type in M.TEXT_TYPES)
        assert "需求整理" not in body, body

        # ---- Reject: scan (full-page image plus OCR layer) ----
        scan = d / "scan.pdf"
        src_img = repo / "tests" / "fixtures" / "real_page.png"
        if src_img.exists():
            make_scanned_pdf(scan, src_img)
            why = probe(scan)
            assert why and "扫描" in why, why

        # ---- Reject: too little text ----
        import pymupdf
        thin = d / "thin.pdf"
        doc2 = pymupdf.open()
        doc2.new_page().insert_text((60, 100), "图 1", fontname="china-s")
        doc2.save(str(thin)); doc2.close()
        why = probe(thin)
        assert why and "文字太少" in why, why

        # ---- Reject: not a PDF ----
        assert probe(repo / "tests" / "fixtures" / "real_page.png") == "不是 PDF"

    # ---- Reject: a LaTeX paper with formulas (the critical case) ----
    paper = repo / "tmp" / "Liu_2024_ApJL_965_L11.pdf"
    if paper.exists():
        why = probe(paper)
        assert why and "公式" in why, f"带公式的论文必须退回模型，实际：{why}"

    # ---- Reject: vector-drawn formulas (silent loss is worse than slowness) ----
    import pymupdf
    with tempfile.TemporaryDirectory() as tmp2:
        vec = Path(tmp2) / "vec.pdf"
        doc3 = pymupdf.open()
        pg = doc3.new_page(width=595, height=842)
        pg.insert_htmlbox(pymupdf.Rect(50, 40, 545, 140), "<p>" + "正文足够多。" * 30 + "</p>")
        # 30 Bezier curves stand in for vector glyphs (threshold is 25/page)
        for i in range(30):
            y = 150 + i * 18
            pg.draw_bezier((60, y), (120, y - 12), (180, y + 12), (240, y), color=(0, 0, 0))
        doc3.save(str(vec)); doc3.close()
        why = probe(vec)
        assert why and "矢量" in why, f"矢量公式必须挡回模型，实际：{why}"

        # Straight table rules must not trigger it
        tbl = Path(tmp2) / "tbl.pdf"
        doc4 = pymupdf.open()
        pg = doc4.new_page(width=595, height=842)
        pg.insert_htmlbox(pymupdf.Rect(50, 40, 545, 140), "<p>" + "正文足够多。" * 30 + "</p>")
        for i in range(40):
            pg.draw_line((60, 150 + i * 12), (500, 150 + i * 12), color=(0, 0, 0))
        doc4.save(str(tbl)); doc4.close()
        assert probe(tbl) is None, f"直线表格线不该被当成矢量公式：{probe(tbl)}"

    # ---- Joining: drop half-width spaces between CJK, keep full-width ones ----
    assert _join_spans(["数", " ", "据与结论"]) == "数据与结论"
    assert _join_spans(["第三章", "　", "实验报告"]) == "第三章　实验报告"
    assert _join_spans(["hello", " ", "world"]) == "hello world"

    print("OK  文字版放行、扫描件与带公式的都挡回模型")
    return 0


if __name__ == "__main__":
    sys.exit(main())
