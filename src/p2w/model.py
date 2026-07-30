"""In-memory document model decoupling layout parsing from docx rendering.

A DocModel is an ordered list of Blocks (already sorted by reading order).
Text-bearing blocks (heading/paragraph/list_item) carry a list of Spans so a
single line can mix plain text, bold runs, and inline formulas. Formula,
picture and table blocks carry their own typed payload.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Block types
HEADING = "heading"
PARAGRAPH = "paragraph"
LIST_ITEM = "list_item"
FORMULA = "formula"
PICTURE = "picture"
TABLE = "table"

TEXT_TYPES = {HEADING, PARAGRAPH, LIST_ITEM}


@dataclass
class Span:
    """A run of text, or an inline formula, inside a text block."""

    text: str = ""
    bold: bool = False
    italic: bool = False
    is_formula: bool = False
    latex: str = ""


@dataclass
class Block:
    type: str
    page: int = 0
    reading_order: int = 0
    bbox: tuple = (0.0, 0.0, 0.0, 0.0)  # x0, y0, x1, y1 in page units
    confidence: float = 1.0
    level: int = 1  # heading / list nesting level

    # text blocks (heading/paragraph/list_item)
    spans: list[Span] = field(default_factory=list)

    # formula block
    latex: str = ""
    block_formula: bool = True
    crop_b64: str = ""  # base64 PNG of the original region, for the review report

    # picture block
    image_path: str = ""
    image_size_px: Optional[tuple] = None  # (w, h) hint if known

    # table block: rows -> cols -> spans
    rows: list[list[list[Span]]] = field(default_factory=list)


@dataclass
class DocModel:
    blocks: list[Block] = field(default_factory=list)
    source_file: str = ""
    meta: dict = field(default_factory=dict)

    def sorted_blocks(self) -> list[Block]:
        return sorted(self.blocks, key=lambda b: (b.page, b.reading_order))
