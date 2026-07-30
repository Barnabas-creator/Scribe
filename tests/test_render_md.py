"""DocModel -> Markdown: headings, formulas, tables, figures, escaping, and no
orphan markers for empty blocks.

Run: python3 tests/test_render_md.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from p2w import model as M
from p2w.render_md import render_md


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        img = Path(tmp) / "fig.png"
        img.write_bytes(bytes.fromhex(
            "89504e470d0a1a0a0000000d494844520000000100000001080600000"
            "01f15c4890000000d49444154789c626001000000ffff03000006000557bfabd40000000049454e44ae426082"))
        doc = M.DocModel(source_file="x.pdf", blocks=[
            M.Block(type=M.HEADING, reading_order=0, level=2, spans=[M.Span(text="第一章")]),
            M.Block(type=M.PARAGRAPH, reading_order=1, spans=[
                M.Span(text="速度是 "), M.Span(is_formula=True, latex="v = s/t"),
                M.Span(text="，注意 5*3 里的星号")]),
            M.Block(type=M.FORMULA, reading_order=2, block_formula=True, latex="E = mc^2"),
            M.Block(type=M.HEADING, reading_order=3, level=1, spans=[M.Span(text="  ")]),  # 空标题
            M.Block(type=M.TABLE, reading_order=4, rows=[
                [[M.Span(text="物理量")], [M.Span(text="值|单位")]],
                [[M.Span(text="质量")], [M.Span(text="3 kg")]],
            ]),
            M.Block(type=M.PICTURE, reading_order=5, image_path=str(img)),
        ])
        out = Path(tmp) / "转出.md"
        render_md(doc, str(out))
        text = out.read_text(encoding="utf-8")

        assert "## 第一章" in text
        assert "$v = s/t$" in text, text                       # 行内公式保留 LaTeX
        assert "$$\nE = mc^2\n$$" in text                       # 独立公式
        assert "5\\*3" in text, text                            # 星号转义，Typora 里不变斜体
        assert "| 物理量 | 值\\|单位 |" in text, text           # 表格竖线转义
        assert "|---|---|" in text
        assert "![](转出.assets/001_fig.png)" in text           # 插图相对路径
        assert (Path(tmp) / "转出.assets" / "001_fig.png").exists()
        # An empty heading must not emit a bare "#"
        assert not any(l.strip() in ("#", "##") for l in text.splitlines()), text

    print("OK  Markdown 导出：结构、公式 LaTeX、转义、插图、空块过滤")
    return 0


if __name__ == "__main__":
    sys.exit(main())
