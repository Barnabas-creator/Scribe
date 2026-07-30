"""MinerU content_list.json -> DocModel.

content_list is a flat, reading-order sequence of blocks:
  {"type": text|equation|image|table|list|code, "page_idx": 0-based,
   "bbox": [x0,y0,x1,y1] normalized to 0-1000, ...}
  text     -> "text" (inline formulas embedded as $...$), "text_level" (0=body)
  equation -> "text" is $$...$$ LaTeX, "img_path" is the source crop
  image    -> "img_path" (relative to the json dir), "image_caption": [...]
  table    -> "table_body" is HTML <table>, "table_caption": [...]
  list     -> "text" or "list_items", one list_item per entry
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

from . import model as M

# Inline/display math delimiters: $$..$$ / $..$ / \(..\) / \[..\]
_MATH = re.compile(r"(\$\$.+?\$\$|\$[^$\n]+?\$|\\\(.+?\\\)|\\\[.+?\\\])", re.DOTALL)
# pandoc silently drops \tag{1.1.2}; rewrite it as trailing text to keep the number.
_TAG = re.compile(r"\\tag\s*\{([^}]*)\}")
# Layout furniture, dropped: MinerU emits these at the end of the reading order.
_SKIP_TYPES = {"header", "footer", "page_number", "page_footnote", "aside_text", "discarded"}

# Bare CJK inside math mode renders italic (treated as a variable), but CJK
# subscripts of physical quantities must stay upright.
_CJK = re.compile(r"[\u4e00-\u9fff]+")
_TEXT_CMD = re.compile(r"\\(?:text|textrm|mathrm|mathbf|mbox|hbox)\s*\{[^{}]*\}")


def normalize_cjk(latex: str) -> str:
    """Wrap unwrapped CJK in \\text{}; leave existing \\text/\\mathrm alone."""
    out, last = [], 0
    wrap = lambda seg: _CJK.sub(lambda m: "\\text{%s}" % m.group(), seg)
    for m in _TEXT_CMD.finditer(latex):
        out.append(wrap(latex[last:m.start()]))
        out.append(m.group())
        last = m.end()
    out.append(wrap(latex[last:]))
    return "".join(out)


# A paragraph split across columns/pages arrives as two text blocks. Rejoin only
# when all three hold: previous block hugs the column bottom, next hugs the top,
# and the previous text has no sentence-ending punctuation.
_SENT_END = "。！？；：”』）】.!?;:"
_COL_BOTTOM = 900
_COL_TOP = 100


def _merge_split_paragraphs(items: list) -> list:
    """Rejoin paragraphs broken by a column or page boundary (body text only)."""
    out: list = []
    for item in items:
        prev = out[-1] if out else None
        if (prev is not None and prev.get("type") == "text" and item.get("type") == "text"
                and not prev.get("text_level") and not item.get("text_level")):
            head = (prev.get("text") or "").rstrip()
            tail = (item.get("text") or "").strip()
            pb = prev.get("bbox") or [0, 0, 0, 0]
            cb = item.get("bbox") or [0, 0, 0, 0]
            if (head and tail and head[-1] not in _SENT_END
                    and pb[3] > _COL_BOTTOM and cb[1] < _COL_TOP):
                prev["text"] = head + tail
                continue
        out.append(dict(item))
    return out


def _strip_math_delims(s: str) -> str:
    s = s.strip()
    for open_d, close_d in (("$$", "$$"), ("\\[", "\\]"), ("\\(", "\\)"), ("$", "$")):
        if s.startswith(open_d) and s.endswith(close_d) and len(s) > len(open_d) + len(close_d):
            return s[len(open_d):-len(close_d)].strip()
    return s


# Scientific notation sometimes arrives split: mantissa "4.2x10" as body text and
# the exponent as a lone inline formula. Rejoin them.
_ORPHAN_SUP = re.compile(r"^\^\s*\{?[\w.+-]+\}?$")
_SCI_TAIL = re.compile(r"([\d.]+\s*[×✕]\s*10)\s*$")


def _join_orphan_superscripts(spans: list[M.Span]) -> list[M.Span]:
    for i, sp in enumerate(spans):
        if i == 0 or not sp.is_formula or not _ORPHAN_SUP.match(sp.latex):
            continue
        prev = spans[i - 1]
        if prev.is_formula:
            continue
        m = _SCI_TAIL.search(prev.text)
        if not m:
            continue
        prev.text = prev.text[:m.start()]
        sp.latex = re.sub(r"\s*[×✕]\s*", r" \\times ", m.group(1)) + sp.latex
    return [s for s in spans if s.is_formula or s.text]


# Half-width punctuation adjacent to CJK (either side) becomes full-width;
# English prose, thousands separators (1,000) and URLs keep half-width forms.
_HALF_TO_FULL = {",": "，", ";": "；"}
_PUNCT_CTX = re.compile(r"(?<=[\u4e00-\u9fff])[,;]|[,;](?=[\u4e00-\u9fff])")
_PUNCT_SPACE = re.compile(r"([，；]) (?=[\u4e00-\u9fff])")


def normalize_punct(text: str) -> str:
    """Convert half-width comma/semicolon to full-width in CJK context.

    Body text spans only -- never touch formula LaTeX, where commas are
    mathematical separators.
    """
    text = _PUNCT_CTX.sub(lambda m: _HALF_TO_FULL[m.group()], text)
    return _PUNCT_SPACE.sub(r"\1", text)


def spans_from_text(text: str) -> list[M.Span]:
    """Split a text run into plain-text and inline-formula spans."""
    if not text:
        return [M.Span(text="")]
    spans: list[M.Span] = []
    for part in _MATH.split(text):
        if not part:
            continue
        if _MATH.fullmatch(part):
            latex = _strip_math_delims(part)
            if latex:
                spans.append(M.Span(is_formula=True, latex=normalize_cjk(latex)))
        else:
            spans.append(M.Span(text=normalize_punct(part)))
    return _join_orphan_superscripts(spans) or [M.Span(text=normalize_punct(text))]


def _table_rows(html: str) -> list[list[list[M.Span]]]:
    """MinerU table_body (HTML) -> rows[row][col][span]."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "html.parser")
    rows: list[list[list[M.Span]]] = []
    for tr in soup.find_all("tr"):
        row: list[list[M.Span]] = []
        for cell in tr.find_all(["td", "th"]):
            spans = spans_from_text(cell.get_text(" ", strip=True))
            row.append(spans)
            # Merged cells: python-docx cannot restore them, so pad to keep
            # column alignment.
            for _ in range(max(1, int(cell.get("colspan", 1) or 1)) - 1):
                row.append([M.Span(text="")])
        if row:
            rows.append(row)
    return rows


def _read_crop(image_dir: Path, img_path: str) -> str:
    """Formula crop -> base64 PNG for the review report."""
    if not img_path:
        return ""
    p = image_dir / img_path
    try:
        return base64.b64encode(p.read_bytes()).decode()
    except Exception:
        return ""


def parse_content_list(items: list, image_dir: str | Path, source_file: str = "") -> M.DocModel:
    doc = M.DocModel(source_file=source_file)
    image_dir = Path(image_dir)
    order = 0

    def add(block: M.Block, item: dict) -> None:
        nonlocal order
        order += 1
        block.page = int(item.get("page_idx", 0) or 0) + 1
        block.reading_order = order
        bb = item.get("bbox") or [0, 0, 0, 0]
        block.bbox = tuple(bb) if len(bb) == 4 else (0, 0, 0, 0)
        doc.blocks.append(block)

    # Drop layout furniture first -- it sits between the two halves of a split
    # paragraph and would block the merge.
    items = _merge_split_paragraphs([
        it for it in items
        if isinstance(it, dict) and (it.get("type") or "").lower() not in _SKIP_TYPES
    ])

    for item in items:
        itype = (item.get("type") or "").lower()
        text = (item.get("text") or "").strip()

        if itype == "equation":
            latex = normalize_cjk(_TAG.sub(r"\\quad \\text{(\1)}", _strip_math_delims(text)))
            if latex:
                add(M.Block(type=M.FORMULA, latex=latex, block_formula=True,
                            crop_b64=_read_crop(image_dir, item.get("img_path", ""))), item)
        elif itype in ("image", "chart"):
            src = item.get("img_path", "")
            if src:
                add(M.Block(type=M.PICTURE, image_path=str((image_dir / src).resolve())), item)
            for cap in item.get("image_caption") or []:
                add(M.Block(type=M.PARAGRAPH, spans=spans_from_text(cap)), item)
        elif itype == "table":
            for cap in item.get("table_caption") or []:
                add(M.Block(type=M.PARAGRAPH, spans=spans_from_text(cap)), item)
            rows = _table_rows(item.get("table_body", ""))
            if rows:
                # Table blocks carry their own crop (equation blocks do not).
                add(M.Block(type=M.TABLE, rows=rows,
                            crop_b64=_read_crop(image_dir, item.get("img_path", ""))), item)
            elif item.get("img_path"):  # Structure unparsed: keep at least the image.
                add(M.Block(type=M.PICTURE,
                            image_path=str((image_dir / item["img_path"]).resolve())), item)
            for note in item.get("table_footnote") or []:
                add(M.Block(type=M.PARAGRAPH, spans=spans_from_text(note)), item)
        elif itype == "list":
            for line in (item.get("list_items") or [t for t in text.split("\n") if t.strip()]):
                line = line if isinstance(line, str) else (line.get("text") or "")
                if line.strip():
                    add(M.Block(type=M.LIST_ITEM, spans=spans_from_text(line.strip())), item)
        elif text:  # text / code / any other block carrying text
            level = int(item.get("text_level", 0) or 0)
            if level > 0:
                add(M.Block(type=M.HEADING, level=min(level, 9),
                            spans=spans_from_text(text)), item)
            else:
                add(M.Block(type=M.PARAGRAPH, spans=spans_from_text(text)), item)
    return doc
