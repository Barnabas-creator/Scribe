"""End-to-end render check on a synthetic DocModel (no OCR yet)."""

import sys
from pathlib import Path
import zipfile

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from PIL import Image, ImageDraw

from p2w import model as M
from p2w.render_docx import render

REPO = Path(__file__).resolve().parents[1]
(REPO / "tmp").mkdir(exist_ok=True)
IMG = str(REPO / "tests" / "fixtures" / "fig1.png")
OUT = str(REPO / "tmp" / "verify_render.docx")

# make a small test figure
im = Image.new("RGB", (400, 240), "white")
d = ImageDraw.Draw(im)
d.rectangle([20, 20, 380, 220], outline="black", width=3)
d.line([20, 220, 380, 20], fill="blue", width=2)
d.text((40, 40), "Figure 1 (test)", fill="black")
im.save(IMG)

doc = M.DocModel(source_file="synthetic")
doc.blocks = [
    M.Block(type=M.HEADING, reading_order=0, level=1,
            spans=[M.Span(text="一、选择题")]),
    M.Block(type=M.PARAGRAPH, reading_order=1, spans=[
        M.Span(text="1. 已知函数 "),
        M.Span(is_formula=True, latex=r"f(x)=\frac{1}{x}"),
        M.Span(text=" ，求其在 "),
        M.Span(is_formula=True, latex=r"x=2"),
        M.Span(text=" 处的导数。"),
    ]),
    M.Block(type=M.FORMULA, reading_order=2, block_formula=True,
            latex=r"x=\frac{-b\pm\sqrt{b^2-4ac}}{2a}"),
    M.Block(type=M.PARAGRAPH, reading_order=3, confidence=0.4, spans=[
        M.Span(text="2. 这是一段低置信度文字（应被标黄）。"),
    ]),
    M.Block(type=M.PICTURE, reading_order=4, image_path=IMG),
    M.Block(type=M.TABLE, reading_order=5, rows=[
        [[M.Span(text="x")], [M.Span(text="y")]],
        [[M.Span(text="1")], [M.Span(is_formula=True, latex=r"y_1^2")]],
    ]),
    M.Block(type=M.FORMULA, reading_order=6, block_formula=False,
            latex=r"\frac{1}{"),  # broken -> fallback
]

review = render(doc, OUT)

with zipfile.ZipFile(OUT) as zf:
    xml = zf.read("word/document.xml").decode("utf-8")

print("saved:", OUT)
print("native math elements:", xml.count("<m:oMath"))
print("has image (drawing):", "<w:drawing" in xml)
print("has table:", "<w:tbl" in xml)
print("highlighted (low-conf/fallback):", xml.count("highlight"))
print("review items:", len(review))
for r in review:
    print("   -", r.kind, r.reason, repr(r.detail))

assert xml.count("<m:oMath") >= 4, "formulas missing"
assert "<w:drawing" in xml, "image missing"
assert "<w:tbl" in xml, "table missing"
assert any(r.reason == "conversion_failed" for r in review), "fallback not recorded"
assert any(r.reason == "low_confidence" for r in review), "low-conf not recorded"
print("PASS: full render pipeline works end-to-end")

# ---- When the batch call fails, rendering must fall back per formula ----
import p2w.render_docx as R

R.batch_latex_to_omml = lambda items: [None] * len(items)  # simulate batch failure
OUT2 = str(REPO / "tmp" / "verify_render_fallback.docx")
render(doc, OUT2)
with zipfile.ZipFile(OUT2) as zf:
    xml2 = zf.read("word/document.xml").decode("utf-8")
assert xml2.count("<m:oMath") >= 4, "批量失败时逐条兜底没接上"
print("PASS: 批量失败时渲染退回逐条转换，公式不丢")

# ---- Control characters must not break the document ----
dirty = M.DocModel(source_file="dirty.pdf", blocks=[
    M.Block(type=M.HEADING, reading_order=0, level=1,
            spans=[M.Span(text="标\x02题\x0c")]),
    M.Block(type=M.PARAGRAPH, reading_order=1,
            spans=[M.Span(text="正文\x00里有\x1f脏字符\ttab和换行\n要保住")]),
    M.Block(type=M.FORMULA, reading_order=2, block_formula=True, latex="x\x08+1"),
    M.Block(type=M.TABLE, reading_order=3,
            rows=[[[M.Span(text="表\x0b格")], [M.Span(text="正常")]]]),
])
OUT3 = str(REPO / "tmp" / "verify_render_dirty.docx")
render(dirty, OUT3)          # 不抛异常就是主要断言
with zipfile.ZipFile(OUT3) as zf:
    xml3 = zf.read("word/document.xml").decode("utf-8")
assert "标题" in xml3 and "脏字符" in xml3 and "表格" in xml3, "清洗把正文清没了"
assert "\x02" not in xml3 and "&#2;" not in xml3, "控制字符漏进了 XML"
print("PASS: 控制字符被清洗，转换不再整份失败")
