"""Input type checks and readability validation."""

from __future__ import annotations

from pathlib import Path

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp", ".gif"}
PDF_EXT = ".pdf"


def is_image(path: str | Path) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTS


def is_pdf(path: str | Path) -> bool:
    return Path(path).suffix.lower() == PDF_EXT


def is_supported(path: str | Path) -> bool:
    return is_image(path) or is_pdf(path)


def check_readable(path: str | Path) -> str | None:
    """Return None if the file opens, else a user-facing reason.

    Screening broken files here keeps the OCR engine from spending a full run
    only to fail with a traceback no end user can read.
    """
    path = Path(path)
    try:
        if path.stat().st_size == 0:
            return "文件是空的（0 字节）"
    except OSError:
        return "文件读不到，可能已被移动或删除"

    import pymupdf
    try:
        with pymupdf.open(str(path)) as doc:
            if doc.needs_pass:
                return "文件已加密，需要密码才能打开"
            if doc.page_count == 0:
                return "文件里没有任何页面"
    except Exception:
        kind = "PDF" if is_pdf(path) else "图片"
        return f"这个{kind}打不开，可能已损坏或格式不受支持"
    return None
