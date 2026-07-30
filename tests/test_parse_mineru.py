"""parse_mineru 自检：MinerU 的 content_list 各种块 → DocModel。

跑：PYTHONPATH=src python3 tests/test_parse_mineru.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from p2w import model as M
from p2w.parse_mineru import normalize_cjk, parse_content_list, spans_from_text

SAMPLE = [
    {"type": "text", "text": "一、选择题", "text_level": 1, "page_idx": 0, "bbox": [0, 0, 1, 1]},
    {"type": "text", "text": "已知 $x^2+1=0$，求 $x$ 的值。", "text_level": 0, "page_idx": 0},
    {"type": "equation", "text": "$$x = \\frac{-b \\pm \\sqrt{b^2-4ac}}{2a}$$",
     "text_format": "latex", "img_path": "images/eq1.jpg", "page_idx": 0},
    {"type": "table", "table_caption": ["表 1 数据"],
     "table_body": "<html><body><table><tr><td>x</td><td>$y=x^2$</td></tr>"
                   "<tr><td>1</td><td>1</td></tr></table></body></html>",
     "table_footnote": ["注：略"], "page_idx": 1},
    {"type": "image", "img_path": "images/fig1.jpg", "image_caption": ["图 1 示意"], "page_idx": 1},
    {"type": "list", "text": "(1) 第一项\n(2) 第二项", "page_idx": 1},
    {"type": "header", "text": "第 1 章 页眉不要进正文", "page_idx": 1},
    {"type": "page_number", "text": "3", "page_idx": 1},
]


def main() -> int:
    doc = parse_content_list(SAMPLE, image_dir="/nonexistent", source_file="x.pdf")
    types = [b.type for b in doc.sorted_blocks()]
    assert types == [
        M.HEADING, M.PARAGRAPH, M.FORMULA,
        M.PARAGRAPH, M.TABLE, M.PARAGRAPH,          # table: caption, 表体, footnote
        M.PICTURE, M.PARAGRAPH,                     # image: 图, caption
        M.LIST_ITEM, M.LIST_ITEM,
    ], types

    head = doc.blocks[0]
    assert head.level == 1 and head.spans[0].text == "一、选择题"

    # Inline formulas split into text/formula/text/formula/text
    para = doc.blocks[1]
    assert [s.is_formula for s in para.spans] == [False, True, False, True, False], para.spans
    assert para.spans[1].latex == "x^2+1=0"

    # Display formula: delimiters stripped, page number becomes 1-based
    eq = doc.blocks[2]
    assert eq.latex.startswith("x = \\frac") and eq.block_formula and eq.page == 1

    # Tables: inline formulas inside cells become formula spans too
    table = next(b for b in doc.blocks if b.type == M.TABLE)
    assert len(table.rows) == 2 and table.rows[0][1][0].is_formula
    assert table.rows[0][1][0].latex == "y=x^2"
    assert table.page == 2

    # Reading order is contiguous and sorted_blocks preserves it
    assert [b.reading_order for b in doc.blocks] == list(range(1, len(doc.blocks) + 1))

    # Plain text yields no formula spans; an unclosed $ is not misread
    assert spans_from_text("单价 $5 一件") == [M.Span(text="单价 $5 一件")]

    # CJK subscripts need \text{} to stay upright, and must not be double-wrapped
    assert normalize_cjk("c_{水}m_{水}") == "c_{\\text{水}}m_{\\text{水}}"
    assert normalize_cjk("Q_{\\text{吸}}") == "Q_{\\text{吸}}"
    assert normalize_cjk("\\frac{Q_{盐水放}}{Q_{水放}}") == "\\frac{Q_{\\text{盐水放}}}{Q_{\\text{水放}}}"
    # Units inside \mathrm{} and formulas without CJK are untouched
    assert normalize_cjk("4.2\\times10^{3}\\mathrm{~J/(kg\\cdot°C)}") == "4.2\\times10^{3}\\mathrm{~J/(kg\\cdot°C)}"
    # MinerU sometimes emits \text {x} with a space
    assert normalize_cjk("m _ {\\text {甲}} + m_{乙}") == "m _ {\\text {甲}} + m_{\\text{乙}}"

    # Split scientific notation is rejoined into one formula
    got = spans_from_text("1 答案 4.2×10 $^{5}$ J")
    assert [(s.text, s.latex) for s in got] == [("1 答案 ", ""), ("", "4.2 \\times 10^{5}"), (" J", "")], got
    # A superscript not preceded by scientific notation stays separate
    got2 = spans_from_text("设 $x$ 的 $^{2}$ 次")
    assert sum(1 for s in got2 if s.is_formula) == 2, got2

    check_column_merge()
    check_real_table()
    check_punct()
    print(f"OK  {len(doc.blocks)} blocks: {types}")
    return 0


# Real MinerU table block: table_body is a bare <table> with no <html><body>
# wrapper, caption/footnote are empty arrays, and unlike equation blocks it
# carries an img_path.
REAL_TABLE = {
    "type": "table",
    "img_path": "images/862e583d.jpg",
    "table_caption": [],
    "table_footnote": [],
    "table_body": "<table><tr><td>物质</td><td>质量 m/g</td><td>初温 t0/°C</td>"
                  "<td>末温 t/°C</td><td>加热时间 τ/min</td></tr>"
                  "<tr><td>水</td><td>200</td><td>20</td><td>40</td><td>8</td></tr>"
                  "<tr><td>食用油</td><td>200</td><td>20</td><td>60</td><td>8</td></tr></table>",
    "bbox": [62, 200, 946, 400],
    "page_idx": 0,
}


def check_real_table() -> None:
    doc = parse_content_list([REAL_TABLE], image_dir="/nonexistent")
    tables = [b for b in doc.blocks if b.type == M.TABLE]
    assert len(tables) == 1, doc.blocks
    rows = tables[0].rows
    assert len(rows) == 3 and all(len(r) == 5 for r in rows), [len(r) for r in rows]
    text = [[ "".join(s.text for s in cell) for cell in row] for row in rows]
    assert text[0] == ["物质", "质量 m/g", "初温 t0/°C", "末温 t/°C", "加热时间 τ/min"], text[0]
    assert text[2] == ["食用油", "200", "20", "60", "8"], text[2]


# Two-column layout: a paragraph breaks at the column bottom and resumes at the
# next column top. bboxes are normalized to 0-1000, y-down.
SPLIT = [
    {"type": "text", "text": "解析 实验中使用的测量工具有天平、温度计和",
     "bbox": [521, 931, 936, 948], "page_idx": 0},
    {"type": "page_number", "text": "2", "bbox": [61, 963, 117, 978], "page_idx": 0},
    {"type": "footer", "text": "初中物理 九年级", "bbox": [133, 962, 392, 977], "page_idx": 0},
    {"type": "text", "text": "秒表。", "bbox": [72, 43, 121, 59], "page_idx": 1},
]


def check_punct() -> None:
    from p2w.parse_mineru import normalize_punct
    # CJK context: half-width comma becomes full-width (either side counts)
    assert normalize_punct("你好,世界") == "你好，世界"
    assert normalize_punct("温度, 湿度, 气压") == "温度，湿度，气压"
    assert normalize_punct("hello,世界") == "hello，世界"
    # Semicolons follow the same rule
    assert normalize_punct("速度快;质量大") == "速度快；质量大"
    assert normalize_punct("温度高; 压强大") == "温度高；压强大"
    assert normalize_punct("for i in x; do") == "for i in x; do"   # 英文语境不动
    # English and numeric context is left alone
    assert normalize_punct("hello, world") == "hello, world"
    assert normalize_punct("1,000") == "1,000"
    assert normalize_punct("a, b, c") == "a, b, c"
    # Through the span layer: body normalized, formula commas untouched
    spans = spans_from_text("已知 $a,b$，求值, 保留两位。")
    assert spans[0].text == "已知 "
    assert spans[1].is_formula and spans[1].latex == "a,b"
    assert spans[2].text == "，求值，保留两位。"
    # Table cells take the same path
    doc = parse_content_list(
        [{"type": "table", "table_body": "<table><tr><td>时间, 温度</td></tr></table>",
          "page_idx": 0}],
        image_dir="/x")
    table = next(b for b in doc.blocks if b.type == M.TABLE)
    assert table.rows[0][0][0].text == "时间，温度"


def check_column_merge() -> None:
    doc = parse_content_list(SPLIT, image_dir="/nonexistent")
    got = ["".join(s.text for s in b.spans) for b in doc.blocks]
    # Headers and footers dropped, the split halves rejoined
    assert got == ["解析 实验中使用的测量工具有天平、温度计和秒表。"], got

    # Previous block ends with a period: two separate paragraphs, no merge
    closed = [dict(SPLIT[0], text="……故选 D。"), SPLIT[3]]
    assert len(parse_content_list(closed, image_dir="/x").blocks) == 2

    # Not at a column boundary: ordinary adjacent paragraphs, no merge
    midpage = [dict(SPLIT[0], bbox=[521, 400, 936, 430]), SPLIT[3]]
    assert len(parse_content_list(midpage, image_dir="/x").blocks) == 2

    # Headings never participate in merging
    heading = [dict(SPLIT[0], text="第 2 节 分子动理论", text_level=1),
               dict(SPLIT[3], text="基础夯实")]
    assert len(parse_content_list(heading, image_dir="/x").blocks) == 2

    # Input must not be mutated in place; callers may reuse the list
    assert SPLIT[0]["text"] == "解析 实验中使用的测量工具有天平、温度计和"


if __name__ == "__main__":
    sys.exit(main())
