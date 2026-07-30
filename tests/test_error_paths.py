"""流水线的边界情况：坏输入的拦截与提示、同名输出的处理。

跑：python3 tests/test_error_paths.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from p2w.mineru_backend import _explain_failure
from p2w.normalize import check_readable, is_supported
from p2w.pipeline import _unique_path


def make_bad_files(d: Path) -> dict:
    import pymupdf
    files = {}

    (d / "empty.pdf").write_bytes(b"")
    files["空文件"] = d / "empty.pdf"

    (d / "fake.pdf").write_text("not a pdf at all")
    files["假 PDF"] = d / "fake.pdf"

    enc = d / "encrypted.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "secret")
    doc.save(str(enc), encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")
    doc.close()
    files["加密 PDF"] = enc

    good = d / "good.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "hello")
    doc.save(str(good))
    doc.close()
    files["正常 PDF"] = good
    return files


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        f = make_bad_files(Path(tmp))

        empty = check_readable(f["空文件"])
        assert empty and "空" in empty, empty

        fake = check_readable(f["假 PDF"])
        assert fake and "损坏" in fake, fake

        enc = check_readable(f["加密 PDF"])
        assert enc and "加密" in enc, enc

        assert check_readable(f["正常 PDF"]) is None
        # A missing file must yield a message, not an exception.
        gone = check_readable(Path(tmp) / "根本不存在.pdf")
        assert gone and "读不到" in gone, gone

        # Errors must read as a sentence, not a traceback or JSON blob.
        for msg in (empty, fake, enc, gone):
            assert "\n" not in msg and "Traceback" not in msg and "{" not in msg, msg
            assert len(msg) < 60, msg

    # Engine failures must be condensed to one line.
    noisy = [
        '2026-07-29 04:03:56.740 | INFO | mineru.cli.client:run_planned_task:832 - Submitting batch 1/1',
        'File "/long/path/to/pypdfium2/_helpers/document.py", line 584, in _open_pdf',
        '    raise PdfiumError(f"Failed to load document")',
        'Error: 1 task(s) failed while processing documents:',
        '- task#1 (truncated): {"task_id": "c90dbd53", "status": "failed", '
        '"error": "Failed to load file truncated.png: Truncated File Read", "queued_ahead": 0}',
    ]
    got = _explain_failure(noisy)
    assert got == "文件不完整，可能没下载完或已损坏", got
    assert len(got) < 60 and "{" not in got

    # Unrecognized errors still need a readable fallback.
    fallback = _explain_failure(["something totally unexpected happened"])
    assert fallback == "识别失败，请检查文件是否完整", fallback

    # No known pattern but the engine reported Error: use its own line.
    passthru = _explain_failure(["Error: disk quota exceeded"])
    assert passthru == "disk quota exceeded", passthru

    # Special characters in filenames must not be treated as unsupported.
    assert is_supported("名字里有 空格 和#井号&符号.png")

    check_output_naming()
    check_error_detail()
    print("OK  坏输入全部拦住且给的是人话；同名输出会自动让路")
    return 0


def check_output_naming() -> None:
    """同名文件默认自动重命名，别把老师上次转好、已经校对过的成果覆盖掉。"""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        first = d / "试卷.docx"
        assert _unique_path(first) == first        # 没冲突就用原名

        first.write_text("已经校对过的成果")
        second = _unique_path(first)
        assert second.name == "试卷 (2).docx", second.name
        assert first.read_text() == "已经校对过的成果"   # 原件不能被动

        second.write_text("x")
        assert _unique_path(first).name == "试卷 (3).docx"

        # CJK names with multiple extensions must work too.
        report = d / "试卷.复核报告.html"
        report.write_text("r")
        assert _unique_path(report).name == "试卷.复核报告 (2).html"


def check_error_detail() -> None:
    """引擎失败要把原始报错落盘——打包后的 App 没有任何日志文件，
    界面上只有一句人话，原文丢了就没法排查。"""
    from p2w.mineru_backend import OCRBackendError
    from p2w.pipeline import _write_error_detail

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        exc = OCRBackendError("文件不完整", detail="RuntimeError: Truncated File Read\n  at foo()")
        _write_error_detail(d, Path("/somewhere/试卷.png"), exc)
        note = d / "试卷.错误详情.txt"
        assert note.exists(), list(d.iterdir())
        body = note.read_text(encoding="utf-8")
        assert "文件不完整" in body and "Truncated File Read" in body, body

        # Pre-flight errors carry no detail and should not write a file.
        _write_error_detail(d, Path("/somewhere/空的.pdf"), ValueError("文件是空的"))
        assert not (d / "空的.错误详情.txt").exists()


if __name__ == "__main__":
    sys.exit(main())
