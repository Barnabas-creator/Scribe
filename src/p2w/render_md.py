"""DocModel -> Markdown.

Second output format, for Obsidian/Typora/LaTeX workflows. Formulas keep their
LaTeX form ($...$ inline, $$...$$ display), which skips pandoc entirely and is
lossless.

Figures are copied into a sibling <name>.assets/ directory and referenced
relatively, so the file and its assets can be moved together.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from . import model as M


def render_md(doc_model: M.DocModel, out_path: str) -> None:
    """Write a DocModel out as Markdown."""
    out = Path(out_path)
    assets = out.with_name(out.stem + ".assets")
    lines: list[str] = []
    n_img = 0

    for blk in doc_model.sorted_blocks():
        if blk.type in (M.HEADING, M.PARAGRAPH, M.LIST_ITEM):
            body = _spans_md(blk.spans)
            if not body.strip():
                continue      # an empty block would emit a bare "#" or "-"
            prefix = ("#" * max(1, min(6, blk.level or 1)) + " " if blk.type == M.HEADING
                      else "- " if blk.type == M.LIST_ITEM else "")
            lines.append(prefix + body)
        elif blk.type == M.FORMULA:
            lines.append(f"$$\n{(blk.latex or '').strip()}\n$$")
        elif blk.type == M.TABLE:
            lines.append(_table_md(blk))
        elif blk.type == M.PICTURE and blk.image_path:
            src = Path(blk.image_path)
            if src.exists():
                assets.mkdir(parents=True, exist_ok=True)
                n_img += 1
                dest = assets / f"{n_img:03d}_{src.name}"
                shutil.copy2(src, dest)
                lines.append(f"![]({assets.name}/{dest.name})")
        # Other block types are skipped silently.

    out.write_text("\n\n".join(l for l in lines if l.strip()) + "\n", encoding="utf-8")


def _spans_md(spans: list[M.Span]) -> str:
    parts = []
    for s in spans:
        if s.is_formula:
            parts.append(f"${(s.latex or '').strip()}$")
        elif s.text:
            parts.append(_escape(s.text))
    return "".join(parts)


def _escape(text: str) -> str:
    # Escape only structural characters; escaping everything litters the prose.
    for ch in ("\\", "|", "#", "*", "_", "["):
        text = text.replace(ch, "\\" + ch)
    return text


def _table_md(blk: M.Block) -> str:
    rows = blk.rows or []
    if not rows:
        return ""
    width = max(len(r) for r in rows)

    def cell(spans) -> str:
        # Newlines break table structure
        return _spans_md(spans).replace("\n", " ")

    out = []
    for i, row in enumerate(rows):
        cells = [cell(c) for c in row] + [""] * (width - len(row))
        out.append("| " + " | ".join(cells) + " |")
        if i == 0:
            out.append("|" + "---|" * width)
    return "\n".join(out)
