"""Official MinerU cloud API: recognition without a local model.

Trade-off: the local path needs a 2.2 GB download, the cloud path needs only an
API key and runs on their GPUs -- but files are uploaded, which the UI states
plainly so confidential work stays local.

The cloud zip contains the same content_list.json the local engine produces, so
this module only has to land it on disk and return the same (json, image dir)
tuple as mineru_backend.run_mineru. Parsing and rendering are untouched.

Files over the official 200-page / 200 MB limit are split locally, submitted as
one batch and reassembled. Each chunk's page_idx must be offset back to its real
index, since cross-page paragraph merging depends on continuous page numbers.
The 1,000 pages/day account quota cannot be worked around here.

Flow (official v4 batch upload, https://mineru.net/apiManage/docs):
1. POST /api/v4/file-urls/batch -> one OSS upload URL per chunk plus batch_id
2. PUT each chunk (no Content-Type header; it is not in the signature)
3. Poll GET /api/v4/extract-results/batch/{batch_id} until every chunk is done
4. Download each full_zip_url, offset page numbers, merge images
"""

from __future__ import annotations

import json
import shutil
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from .config import ConvertOptions
from .mineru_backend import Cancelled, OCRBackendError

_BASE = "https://mineru.net/api/v4"
APPLY_URL = "https://mineru.net/apiManage/token"     # "get a key" link target
# Official limits: 200 MB / 200 pages per file. Oversized PDFs are chunked.
_MAX_MB = 200
_MAX_PAGES = 200
# Headroom when sizing chunks by bytes: page sizes vary, so leave slack.
_SIZE_MARGIN = 0.9
_POLL_SEC = 3.0


def run_cloud(input_path: str | Path, output_dir: str | Path,
              opts: ConvertOptions | None = None,
              should_cancel: Callable[[], bool] | None = None) -> tuple[Path, Path]:
    """Send one file to mineru.net; returns (content_list.json, image dir).

    Signature-compatible with mineru_backend.run_mineru. Oversized PDFs are
    chunked and reassembled transparently.
    """
    opts = opts or ConvertOptions()
    if not opts.api_token:
        raise OCRBackendError("没有填 API Key，无法使用云端识别")

    src = Path(input_path).resolve()
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    ranges = plan_chunks(src)
    if not ranges:
        chunks = [(src, 0)]                     # within limits: one chunk
    else:
        chunks = _split(src, ranges, out / "_chunks")

    names = [c.name for c, _ in chunks]
    batch_id, urls = _request_upload(names, opts)
    for (chunk, _), url in zip(chunks, urls):
        if should_cancel and should_cancel():
            raise Cancelled("已停止")
        _upload(url, chunk)

    zips = _wait(batch_id, names, opts, should_cancel, factor=len(chunks))
    return _assemble(chunks, [zips[n] for n in names], out)


def plan_chunks(src: Path) -> list[tuple[int, int]]:
    """Plan chunking: returns [(first_page, last_page)] inclusive, 0-based, or an
    empty list when the file fits. Images cannot be split and error out instead.
    """
    mb = src.stat().st_size / 1024 ** 2
    if src.suffix.lower() != ".pdf":
        if mb > _MAX_MB:
            raise OCRBackendError(f"图片 {mb:.0f} MB，超过云端上限 {_MAX_MB} MB，请改用本地识别")
        return []

    import pymupdf
    try:
        with pymupdf.open(str(src)) as d:
            pages = d.page_count
    except Exception:
        return []      # 打不开的文件由 normalize.check_readable 负责，这里不重复判

    if pages <= _MAX_PAGES and mb <= _MAX_MB:
        return []

    # Chunk size is bounded both by page count and by average bytes per page.
    per = _MAX_PAGES
    if mb > _MAX_MB * _SIZE_MARGIN:
        per_by_size = int(pages * _MAX_MB * _SIZE_MARGIN / mb)
        per = max(1, min(per, per_by_size))
    return [(i, min(i + per, pages) - 1) for i in range(0, pages, per)]


def _split(src: Path, ranges: list[tuple[int, int]], work: Path) -> list[tuple[Path, int]]:
    """Write chunks to disk; returns [(chunk file, first page in original)]."""
    import pymupdf

    work.mkdir(parents=True, exist_ok=True)
    out: list[tuple[Path, int]] = []
    with pymupdf.open(str(src)) as doc:
        for i, (a, b) in enumerate(ranges):
            piece = pymupdf.open()
            piece.insert_pdf(doc, from_page=a, to_page=b)
            dest = work / f"{src.stem}.part{i + 1:02d}.pdf"
            piece.save(str(dest))
            piece.close()
            if dest.stat().st_size / 1024 ** 2 > _MAX_MB:
                # Only reachable when page sizes are wildly uneven.
                raise OCRBackendError(
                    f"第 {a + 1}-{b + 1} 页切出来仍超 {_MAX_MB} MB（页面太大），请改用本地识别")
            out.append((dest, a))
    return out


def verify_token(opts: ConvertOptions) -> tuple[bool, str]:
    """Check whether the key works; returns (ok, reason).

    There is no dedicated validation endpoint, so query a non-existent batch:
    auth runs before business logic, so a bad key returns 401/403 while a good
    one reaches "batch not found". Consumes no quota.
    """
    req = urllib.request.Request(f"{_BASE}/extract-results/batch/p2w-verify-only")
    req.add_header("Authorization", f"Bearer {opts.api_token}")
    try:
        urllib.request.urlopen(req, timeout=20).read()
        return True, ""
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return False, "API Key 无效或已过期"
        return True, ""          # 404/参数错都说明认证已经过了
    except urllib.error.URLError as e:
        return False, f"连不上 mineru.net（{e.reason}）"


def _call(path: str, opts: ConvertOptions, body: dict | None = None) -> dict:
    """Call the API once and return the data payload; errors become one line."""
    url = _BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("Authorization", f"Bearer {opts.api_token}")
    req.add_header("Accept", "*/*")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            payload = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        raise OCRBackendError(_explain_http(e.code, raw), detail=f"{url}\n{e.code}\n{raw}")
    except urllib.error.URLError as e:
        raise OCRBackendError(f"连不上 mineru.net（{e.reason}），检查网络或改用本地识别")

    if payload.get("code") not in (0, 200):
        msg = payload.get("msg") or payload.get("message") or "云端返回了错误"
        raise OCRBackendError(f"云端识别失败：{msg}", detail=json.dumps(payload, ensure_ascii=False))
    return payload.get("data") or {}


def _explain_http(code: int, raw: str) -> str:
    if code in (401, 403):
        return "API Key 无效或已过期，请在设置里重新填写"
    if code == 429:
        return "调用太频繁或今日额度已用完（每天 1000 页），稍后再试或改用本地识别"
    if 500 <= code < 600:
        return "mineru.net 服务端出错，稍后再试"
    return f"云端接口返回 HTTP {code}"


def _request_upload(names: list[str], opts: ConvertOptions) -> tuple[str, list[str]]:
    data = _call("/file-urls/batch", opts, {
        "enable_formula": True,
        "enable_table": True,
        "language": opts.cloud_language,
        "model_version": opts.cloud_model,
        "files": [{"name": n} for n in names],
    })
    urls = data.get("file_urls") or []
    batch_id = data.get("batch_id")
    if len(urls) != len(names) or not batch_id:
        raise OCRBackendError("云端没有返回上传地址", detail=json.dumps(data, ensure_ascii=False))
    return batch_id, urls


def _upload(url: str, src: Path) -> None:
    """PUT to OSS. The presigned URL does not cover Content-Type, so sending one
    breaks the signature.

    urllib cannot be used: it auto-adds Content-Type on requests with a body,
    which OSS rejects with 403 SignatureDoesNotMatch. http.client gives full
    control over the headers.
    """
    import http.client
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    path = parts.path + ("?" + parts.query if parts.query else "")
    conn_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(parts.netloc, timeout=600)
    try:
        conn.request("PUT", path, body=src.read_bytes(),
                     headers={"Host": parts.netloc})   # Host plus implicit Content-Length only
        resp = conn.getresponse()
        body = resp.read()
        if resp.status not in (200, 201, 204):
            raise OCRBackendError(
                "上传到云端失败",
                detail=f"PUT {resp.status}\n{body.decode(errors='replace')[:2000]}")
    except OSError as e:
        raise OCRBackendError(f"上传中断（{e}）")
    finally:
        conn.close()


def _wait(batch_id: str, names: list[str], opts: ConvertOptions,
          should_cancel: Callable[[], bool] | None, factor: int = 1) -> dict[str, str]:
    """Wait for every file in the batch; returns {filename: zip url}."""
    deadline = time.monotonic() + opts.timeout_sec * max(1, factor)
    want = set(names)
    got: dict[str, str] = {}
    while True:
        if should_cancel and should_cancel():
            raise Cancelled("已停止")
        if time.monotonic() > deadline:
            raise OCRBackendError("云端识别超时，稍后重试或改用本地识别")

        for item in _call(f"/extract-results/batch/{batch_id}", opts).get("extract_result") or []:
            name = item.get("file_name")
            if name not in want:
                continue
            state = item.get("state")
            if state == "done":
                zip_url = item.get("full_zip_url")
                if not zip_url:
                    raise OCRBackendError("云端说完成了，但没给结果地址")
                got[name] = zip_url
            elif state == "failed":
                raise OCRBackendError(
                    f"云端识别失败：{item.get('err_msg') or '未说明原因'}",
                    detail=json.dumps(item, ensure_ascii=False))
        if len(got) == len(want):
            return got
        time.sleep(_POLL_SEC)


def _assemble(chunks: list[tuple[Path, int]], zip_urls: list[str],
              out: Path) -> tuple[Path, Path]:
    """Download and unpack each chunk, restore page numbers, merge images."""
    result = out / "result"
    (result / "images").mkdir(parents=True, exist_ok=True)
    merged: list = []

    for idx, ((_, start), zip_url) in enumerate(zip(chunks, zip_urls)):
        part = out / f"_zip_{idx:02d}"
        _fetch_zip(zip_url, part)
        hits = sorted(part.rglob("*content_list.json"))
        if not hits:
            listing = [p.name for p in part.rglob("*")][:40]
            raise OCRBackendError(f"云端结果里没有 content_list.json（第 {idx + 1} 段）",
                                  detail="\n".join(listing))
        blocks = json.loads(hits[0].read_text(encoding="utf-8"))
        src_dir = hits[0].parent
        for b in blocks:
            if not isinstance(b, dict):
                continue
            b["page_idx"] = b.get("page_idx", 0) + start   # offset to the real page
            ip = b.get("img_path")
            if ip:
                # Move into one directory, prefixing with the chunk index.
                new_name = f"c{idx}_{Path(ip).name}"
                src_img = src_dir / ip
                if src_img.exists():
                    shutil.copy2(src_img, result / "images" / new_name)
                b["img_path"] = f"images/{new_name}"
        merged.extend(blocks)

    json_path = result / "content_list.json"
    json_path.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
    return json_path, result


def _fetch_zip(zip_url: str, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest_dir / "r.zip"
    try:
        with urllib.request.urlopen(zip_url, timeout=600) as r:
            tmp.write_bytes(r.read())
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        raise OCRBackendError(f"下载识别结果失败（{e}）")
    with zipfile.ZipFile(tmp) as z:
        z.extractall(dest_dir)
    tmp.unlink(missing_ok=True)
