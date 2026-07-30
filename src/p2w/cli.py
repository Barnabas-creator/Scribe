"""Command-line entry point: `p2w INPUT... -o OUTDIR`."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .config import ConvertOptions
from .pipeline import convert_batch


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="p2w", description="把 PDF/图片转成可编辑 Word（公式为 Word 原生公式）")
    ap.add_argument("inputs", nargs="+", help="PDF/PNG/JPG 文件或文件夹")
    ap.add_argument("-o", "--output", default="out", help="输出目录（默认 out/）")
    ap.add_argument("--device", default="mps", choices=["mps", "cuda", "cpu"],
                    help="识别用的设备（默认：macOS 用 mps，其它平台用 cpu）")
    ap.add_argument("--api-token", default="",
                    help="mineru.net 的 API Key。给了就走云端识别（文件会上传），"
                         "不用本地模型；也可以用环境变量 P2W_API_TOKEN")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    token = args.api_token or os.environ.get("P2W_API_TOKEN", "")
    opts = ConvertOptions(device=args.device, api_token=token)
    if opts.use_cloud:
        print("云端识别：文件会上传到 mineru.net")

    def progress(i, n, res):
        status = "OK" if res.ok else f"FAIL: {res.error}"
        tail = f" (需复核 {len(res.review_items)})" if res.needs_review else ""
        print(f"[{i}/{n}] {res.source.name} -> {status}{tail}")

    results = convert_batch(args.inputs, args.output, opts, on_progress=progress)
    ok = sum(r.ok for r in results)
    review = sum(r.needs_review for r in results)
    fail = sum(not r.ok for r in results)
    print(f"\n完成 ✅ {ok} ｜ 需复核 ⚠️ {review} ｜ 失败 ❌ {fail} ｜ 输出: {Path(args.output).resolve()}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
