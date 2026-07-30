"""On-demand download of the local recognition model.

Installers ship without the 2.2 GB weights: it would inflate the DMG sevenfold
and cloud users never need them. The download runs from Settings instead.

Fetching is delegated to the official `python -m mineru.cli.models_download`,
which knows the file manifest and records paths in ~/mineru.json.

Progress is derived from cache directory size rather than parsed from output:
the downloader prints one tqdm bar per file, which cannot be summed, while the
total size is known.

ModelScope is the default source because most users are in mainland China,
where HuggingFace is usually unreachable. Override with MINERU_MODEL_SOURCE.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from .mineru_backend import Cancelled, OCRBackendError, mineru_cmd

# Measured cache size of MinerU2.5-Pro-2605-1.2B; only used for progress math.
TOTAL_BYTES = 2.2 * 1024 ** 3
# Floor for "complete": an interrupted download leaves a directory behind, so
# existence alone would report a partial model as ready.
_READY_BYTES = 1.8 * 1024 ** 3
# The two sources use different cache roots.
_CACHE_ROOTS = ("~/.cache/huggingface/hub", "~/.cache/modelscope/hub", "~/.cache/modelscope")
_MODEL_PAT = re.compile(r"MinerU.?2\.5", re.I)
_DEFAULT_SOURCE = "modelscope"


def model_dirs() -> list[Path]:
    """Every cache directory that may hold the model."""
    out = []
    for root in _CACHE_ROOTS:
        base = Path(root).expanduser()
        if not base.is_dir():
            continue
        for child in base.rglob("*"):
            # Skip helper dirs such as .locks, which mirror the model name.
            if any(part.startswith(".") for part in child.relative_to(base).parts):
                continue
            if child.is_dir() and _MODEL_PAT.search(child.name):
                out.append(child)
    return out


def _dir_bytes(path: Path) -> int:
    total = 0
    for f in path.rglob("*"):
        try:
            # lstat is required: snapshots/ is full of symlinks into blobs/,
            # and stat() would follow them and count the weights twice.
            st = f.lstat()
            # Regular files only: symlinks would double-count the weights and
            # directory st_size is noise.
            if (st.st_mode & 0o170000) == 0o100000:
                total += st.st_size
        except OSError:
            continue
    return total


def downloaded_bytes() -> int:
    """Bytes downloaded so far; the largest cache wins if several are partial."""
    return max((_dir_bytes(d) for d in model_dirs()), default=0)


def ready() -> bool:
    return downloaded_bytes() >= _READY_BYTES


def status() -> dict:
    got = downloaded_bytes()
    return {
        "ready": got >= _READY_BYTES,
        "bytes": got,
        "total": int(TOTAL_BYTES),
        "percent": min(99, int(got / TOTAL_BYTES * 100)) if got < _READY_BYTES else 100,
    }


def download(on_progress: Callable[[int], None] | None = None,
             should_cancel: Callable[[], bool] | None = None,
             source: str | None = None) -> None:
    """Download the model, blocking until done; should_cancel aborts midway.

    on_progress receives 0-100 percent, estimated from cache directory size.
    """
    cmd = mineru_cmd("mineru.cli.models_download")
    if not cmd:
        raise OCRBackendError("这个版本里没有本地识别引擎，只能用云端识别")

    src = source or os.environ.get("MINERU_MODEL_SOURCE") or _DEFAULT_SOURCE
    env = dict(os.environ, MINERU_MODEL_SOURCE=src)
    proc = subprocess.Popen(
        cmd + ["-s", src, "-m", "vlm"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        **group_kwargs(),
    )

    stop = threading.Event()

    def watch() -> None:
        while not stop.wait(1.5):
            if on_progress:
                on_progress(status()["percent"])

    ticker = threading.Thread(target=watch, daemon=True)
    ticker.start()
    try:
        while proc.poll() is None:
            if should_cancel and should_cancel():
                _kill(proc)
                raise Cancelled("已停止")
            time.sleep(0.5)
        tail = (proc.stdout.read() or b"").decode(errors="replace")[-4000:] if proc.stdout else ""
        if proc.returncode != 0:
            raise OCRBackendError(_explain(tail, src), detail=tail)
        if not ready():
            raise OCRBackendError("模型没下全，请重试", detail=tail)
    finally:
        stop.set()
        if proc.poll() is None:
            _kill(proc)


def _kill(proc: subprocess.Popen) -> None:
    import signal
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()


def _explain(tail: str, source: str) -> str:
    low = tail.lower()
    if "connection" in low or "timed out" in low or "resolve" in low or "network" in low:
        other = "huggingface" if source == "modelscope" else "modelscope"
        return f"下载失败，连不上模型源（{source}）。检查网络，或设环境变量 MINERU_MODEL_SOURCE={other} 换源"
    if "no space" in low or "errno 28" in low:
        return "磁盘空间不够，模型需要约 2.2 GB"
    if "permission" in low:
        return "没有写入缓存目录的权限"
    return "模型下载失败，详情见错误日志"
