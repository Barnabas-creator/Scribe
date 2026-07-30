"""Parallel recognition for multi-page PDFs: split into pages, run several
recognition services at once.

The service handles one request at a time, so speedup comes from extra
instances -- measured 1.49x at two workers, not 2x, since GPU bandwidth is
shared.

Memory is the hard constraint: each instance loads the 1.2B model, so the
default is two workers and machines with less RAM fall back to one.

Cost of splitting: cross-page paragraph merging depends on continuous page
numbers, so each page's page_idx must be rewritten to its real index.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from .config import ConvertOptions
from .mineru_backend import Cancelled, mineru_cmd, run_mineru

# Free memory required per worker (model plus inference peak, with headroom).
_MEM_PER_WORKER_GB = 2.0
# Below this page count the fixed cost of splitting and spawning outweighs the gain.
_MIN_PAGES_FOR_PARALLEL = 3


def available_memory_gb() -> float:
    """Available memory in GB; 0 when unknown, which callers treat as insufficient."""
    try:
        out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
        free = inactive = 0
        for line in out.splitlines():
            if "Pages free" in line:
                free = int(line.split(":")[1].strip().rstrip("."))
            elif "Pages inactive" in line:
                inactive = int(line.split(":")[1].strip().rstrip("."))
        return (free + inactive) * 4096 / 1024 ** 3
    except Exception:
        return 0.0


def plan_workers(page_count: int, opts: ConvertOptions) -> int:
    """Worker count for this PDF; 1 means no parallelism."""
    if page_count < _MIN_PAGES_FOR_PARALLEL or opts.max_workers <= 1:
        return 1
    by_mem = int(available_memory_gb() // _MEM_PER_WORKER_GB)
    return max(1, min(opts.max_workers, by_mem, page_count))


class ServicePool:
    """A pool of mineru-api instances; degrades to however many start."""

    def __init__(self, opts: ConvertOptions, size: int):
        self.opts = opts
        self.size = size
        self.procs: list[subprocess.Popen] = []
        self.workdirs: list[str] = []
        self.urls: list[str] = []

    def start(self, timeout: int = 240) -> list[str]:
        cmd = mineru_cmd("mineru.cli.fast_api")
        if not cmd:
            return []
        for i in range(self.size):
            port = self.opts.api_port + i
            url = f"http://{self.opts.api_host}:{port}"
            if _ready(url):
                self.urls.append(url)     # something already serves this port
                continue
            workdir = tempfile.mkdtemp(prefix="p2w_par_")
            proc = subprocess.Popen(
                cmd + ["--host", self.opts.api_host, "--port", str(port),
                       "--enable-vlm-preload", "true"],
                env=dict(os.environ, MINERU_DEVICE_MODE=self.opts.device),
                cwd=workdir,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self.procs.append(proc)
            self.workdirs.append(workdir)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready = [f"http://{self.opts.api_host}:{self.opts.api_port + i}"
                     for i in range(self.size)
                     if _ready(f"http://{self.opts.api_host}:{self.opts.api_port + i}")]
            if len(ready) == self.size:
                self.urls = ready
                return ready
            if all(p.poll() is not None for p in self.procs) and self.procs:
                break
            time.sleep(2)
        # On timeout, use whichever instances did come up.
        self.urls = [f"http://{self.opts.api_host}:{self.opts.api_port + i}"
                     for i in range(self.size)
                     if _ready(f"http://{self.opts.api_host}:{self.opts.api_port + i}")]
        return self.urls

    def stop(self) -> None:
        for proc in self.procs:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
        self.procs.clear()
        for d in self.workdirs:
            shutil.rmtree(d, ignore_errors=True)
        self.workdirs.clear()

    def __enter__(self) -> list[str]:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


def _ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "/docs", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def split_pages(pdf_path: Path, work: Path) -> list[Path]:
    """Split into one PDF per page, page number in the filename."""
    import pymupdf

    work.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    with pymupdf.open(str(pdf_path)) as doc:
        for i in range(doc.page_count):
            one = pymupdf.open()
            one.insert_pdf(doc, from_page=i, to_page=i)
            dest = work / f"page_{i + 1:04d}.pdf"
            one.save(str(dest))
            one.close()
            out.append(dest)
    return out


def run_parallel(pdf_path: Path, output_dir: Path, opts: ConvertOptions,
                 urls: list[str],
                 should_cancel: Callable[[], bool] | None = None,
                 on_page_done: Callable[[int, int], None] | None = None,
                 ) -> tuple[list, Path]:
    """Fan pages out across services; returns (merged content_list, image dir).

    Each page's page_idx is rewritten to its real index, which cross-page
    paragraph merging depends on.
    """
    work = output_dir / "_pages"
    pages = split_pages(pdf_path, work)
    image_root = output_dir / "images"
    image_root.mkdir(parents=True, exist_ok=True)

    done = [0]

    def one(idx_page: tuple[int, Path]) -> tuple[int, list, Path | None]:
        idx, page_pdf = idx_page
        if should_cancel and should_cancel():
            raise Cancelled("已停止")
        sub = ConvertOptions(**{**opts.__dict__, "api_url": urls[idx % len(urls)]})
        out = output_dir / f"_out_{idx:04d}"
        json_path, img_dir = run_mineru(page_pdf, out, sub, should_cancel=should_cancel)
        with open(json_path, encoding="utf-8") as f:
            blocks = json.load(f)
        for b in blocks:
            if isinstance(b, dict):
                b["page_idx"] = idx          # restore real page number
        done[0] += 1
        if on_page_done:
            on_page_done(done[0], len(pages))
        return idx, blocks, img_dir

    merged: list[tuple[int, list]] = []
    with ThreadPoolExecutor(max_workers=len(urls)) as pool:
        for idx, blocks, img_dir in pool.map(one, list(enumerate(pages))):
            # Move per-page images into one directory and repoint references.
            if img_dir and img_dir.exists():
                for src in (img_dir / "images").glob("*") if (img_dir / "images").is_dir() else []:
                    dest = image_root / f"p{idx}_{src.name}"
                    shutil.copy2(src, dest)
                    for b in blocks:
                        if isinstance(b, dict) and b.get("img_path", "").endswith(src.name):
                            b["img_path"] = dest.name
            merged.append((idx, blocks))

    merged.sort(key=lambda t: t[0])
    flat = [b for _, blocks in merged for b in blocks]
    return flat, image_root
