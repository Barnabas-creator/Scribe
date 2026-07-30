"""Review items flagged during rendering -> a self-contained HTML report.

Every entry is two columns: the cropped original on the left (base64-embedded,
no external files) and the LaTeX written into Word on the right. Entries without
a crop share the paragraph image of the preceding item.
"""

from __future__ import annotations

import html
from pathlib import Path

from .render_docx import ReviewItem

_REASON_LABEL = {
    "formula_check": "识别出的公式，请对照原件核对",
    "low_confidence": "识别置信度低，请核对",
    "conversion_failed": "转换失败，已降级为可编辑文本，需手动修正",
    "table_check": "识别出的表格，请对照原件核对行列与数字",
}
_KIND_LABEL = {"formula": "公式", "text": "文字", "image": "图片", "table": "表格"}
_KIND_CLASS = {"formula": "k-formula", "text": "k-text", "image": "k-image", "table": "k-table"}


def write_report(items: list[ReviewItem], out_html: str | Path, source_file: str = "") -> Path:
    out_html = Path(out_html)
    cards = [_card(i, it) for i, it in enumerate(items, 1)]
    body = "\n".join(cards) if cards else '<p class="empty">本次转换没有需要复核的内容 ✅</p>'
    failed = sum(1 for it in items if it.reason == "conversion_failed")
    out_html.write_text(
        _TEMPLATE.format(
            source=html.escape(Path(source_file).name if source_file else "(未知)"),
            count=len(items),
            failed_note=(f"　其中 <b class='bad'>{failed} 处转换失败</b>，务必手动修正。" if failed else ""),
            cards=body,
        ),
        encoding="utf-8",
    )
    return out_html


def _card(idx: int, it: ReviewItem) -> str:
    original = (f'<img src="data:image/png;base64,{it.crop}" alt="原件截图">'
                if it.crop else '<div class="no-crop">（与上一条同段，见上方截图）</div>')
    return f"""<article class="card">
  <header>
    <span class="num">{idx}</span>
    <span class="kind {_KIND_CLASS.get(it.kind, '')}">{_KIND_LABEL.get(it.kind, it.kind)}</span>
    <span class="page">第 {it.page} 页</span>
    <span class="reason">{_REASON_LABEL.get(it.reason, it.reason)}</span>
  </header>
  <div class="cols">
    <div class="col"><div class="lbl">原件</div><div class="scan">{original}</div></div>
    <div class="col"><div class="lbl">识别结果（已写入 Word）</div>
      <code>{html.escape(it.detail)}</code></div>
  </div>
</article>"""


_TEMPLATE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>复核报告 — {source}</title>
<style>
  :root {{
    --ink:#1c1f26; --ink-2:#5b6270; --ink-3:#8b92a1;
    --line:rgba(0,0,0,.09); --bg:#f5f5f7; --card:#fff;
    --warn:#b7791f; --warn-bg:rgba(183,121,31,.12);
    --bad:#d13b32; --bad-bg:rgba(209,59,50,.12);
    --accent:#3a4a6b;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --ink:#f2f4f8; --ink-2:#b9c0cc; --ink-3:#868d9b;
      --line:rgba(255,255,255,.12); --bg:#17181c; --card:#212228;
      --warn:#e3b34e; --warn-bg:rgba(227,179,78,.16);
      --bad:#ff6b60; --bad-bg:rgba(255,107,96,.16);
      --accent:#8aa0cf;
    }}
  }}
  * {{ box-sizing:border-box; }}
  body {{
    margin:0; padding:32px 24px 64px; background:var(--bg); color:var(--ink);
    font:14px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
    -webkit-font-smoothing:antialiased;
  }}
  .wrap {{ max-width:960px; margin:0 auto; }}
  h1 {{ font-size:22px; letter-spacing:-.02em; margin:0 0 6px; }}
  .meta {{ color:var(--ink-2); margin-bottom:8px; }}
  .tip {{
    background:var(--warn-bg); color:var(--warn); border-radius:10px;
    padding:11px 14px; font-size:13px; margin:16px 0 24px;
  }}
  .bad {{ color:var(--bad); }}
  .card {{
    background:var(--card); border-radius:12px; padding:14px 16px; margin-bottom:12px;
    box-shadow:0 1px 2px rgba(0,0,0,.05), inset 0 0 0 .5px var(--line);
  }}
  .card header {{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-bottom:11px; }}
  .num {{
    font:600 11px/1 ui-monospace,SFMono-Regular,Menlo,monospace; color:var(--ink-3);
    min-width:22px;
  }}
  .kind {{ font-size:11.5px; font-weight:600; padding:3px 8px; border-radius:6px; }}
  .k-formula {{ background:var(--warn-bg); color:var(--warn); }}
  .k-text    {{ background:rgba(127,134,148,.16); color:var(--ink-2); }}
  .k-image   {{ background:var(--bad-bg); color:var(--bad); }}
  .k-table   {{ background:rgba(58,74,107,.14); color:var(--accent); }}
  .page {{ font-size:12px; color:var(--ink-2); font-weight:600; }}
  .reason {{ font-size:12px; color:var(--ink-3); margin-left:auto; }}
  .cols {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; }}
  @media (max-width:720px) {{ .cols {{ grid-template-columns:1fr; }} }}
  .lbl {{ font-size:11px; color:var(--ink-3); margin-bottom:6px; }}
  .scan {{
    background:#fbfbfd; border-radius:8px; padding:8px; overflow-x:auto;
    box-shadow:inset 0 0 0 .5px var(--line); min-height:56px;
    display:flex; align-items:center; justify-content:center;
  }}
  .scan img {{ max-width:100%; display:block; }}
  .no-crop {{ font-size:12px; color:var(--ink-3); }}
  code {{
    display:block; background:rgba(127,134,148,.10); border-radius:8px; padding:10px 12px;
    font:13px/1.65 ui-monospace,SFMono-Regular,Menlo,monospace;
    word-break:break-word; white-space:pre-wrap; color:var(--ink);
    box-shadow:inset 0 0 0 .5px var(--line);
  }}
  .empty {{ color:var(--ink-2); padding:40px; text-align:center; }}
</style></head><body>
<div class="wrap">
  <h1>复核报告</h1>
  <div class="meta">{source}　·　{count} 处待核对{failed_note}</div>
  <div class="tip">
    这些内容在 Word 里已用黄色高亮标出。识别准确率取决于扫描清晰度，公式最容易出错——
    请对照下面的原件截图逐条核对，改完把高亮去掉即可。
  </div>
  {cards}
</div>
</body></html>
"""
