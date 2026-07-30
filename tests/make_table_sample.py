"""造一份带表格的试卷页，用来验证表格解析这条从没跑过真实数据的路径。

内容仿真实试卷：题干（含行内公式）+ 实验数据表（中文表头、单位、合并表头）+ 解析。
渲染成 PDF 后再转成 200dpi 图片，模拟扫描件（而不是让 MinerU 走数字 PDF 的捷径）。
"""
import pymupdf

HTML = """
<h3 style="text-align:center">第 12 题　比热容测定实验</h3>
<p>某小组用相同的电加热器分别加热质量均为 200 g 的水和食用油，记录数据如下表。
已知水的比热容 c<sub>水</sub> = 4.2×10<sup>3</sup> J/(kg·℃)，求食用油的比热容。</p>

<table style="width:100%;border-collapse:collapse;border:1.2px solid #000">
  <tr>
    <th style="border:1.2px solid #000;padding:5px;background:#eee">物质</th><th style="border:1.2px solid #000;padding:5px;background:#eee">质量 m/g</th><th style="border:1.2px solid #000;padding:5px;background:#eee">初温 t<sub>0</sub>/℃</th>
    <th style="border:1.2px solid #000;padding:5px;background:#eee">末温 t/℃</th><th style="border:1.2px solid #000;padding:5px;background:#eee">加热时间 τ/min</th>
  </tr>
  <tr><td style="border:1.2px solid #000;padding:5px;text-align:center">水</td><td style="border:1.2px solid #000;padding:5px;text-align:center">200</td><td style="border:1.2px solid #000;padding:5px;text-align:center">20</td><td style="border:1.2px solid #000;padding:5px;text-align:center">40</td><td style="border:1.2px solid #000;padding:5px;text-align:center">8</td></tr>
  <tr><td style="border:1.2px solid #000;padding:5px;text-align:center">食用油</td><td style="border:1.2px solid #000;padding:5px;text-align:center">200</td><td style="border:1.2px solid #000;padding:5px;text-align:center">20</td><td style="border:1.2px solid #000;padding:5px;text-align:center">60</td><td style="border:1.2px solid #000;padding:5px;text-align:center">8</td></tr>
  <tr><td style="border:1.2px solid #000;padding:5px;text-align:center">沙子</td><td style="border:1.2px solid #000;padding:5px;text-align:center">200</td><td style="border:1.2px solid #000;padding:5px;text-align:center">20</td><td style="border:1.2px solid #000;padding:5px;text-align:center">75</td><td style="border:1.2px solid #000;padding:5px;text-align:center">8</td></tr>
</table>

<p><b>解析</b>　加热时间相同说明吸收的热量相同，即 Q<sub>水</sub> = Q<sub>油</sub>。
由 Q = cmΔt 得 c<sub>油</sub> = c<sub>水</sub>Δt<sub>水</sub>/Δt<sub>油</sub>
= 4.2×10<sup>3</sup> × 20 / 40 = 2.1×10<sup>3</sup> J/(kg·℃)。</p>
"""

doc = pymupdf.open()
page = doc.new_page(width=595, height=842)  # A4
page.insert_htmlbox(pymupdf.Rect(50, 50, 545, 792), HTML)
doc.save("/tmp/table_sample.pdf")

# Rasterize and re-wrap as PDF to simulate a scan (no text layer).
src = pymupdf.open("/tmp/table_sample.pdf")
pix = src[0].get_pixmap(dpi=200)
pix.save("/tmp/table_sample.png")
print("已生成 /tmp/table_sample.png", pix.width, "x", pix.height)
