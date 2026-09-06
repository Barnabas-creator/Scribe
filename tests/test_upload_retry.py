"""Covers the two fixes: upload retry/streaming, and output folder handling."""
import sys, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from p2w import mineru_cloud
from p2w.config import ConvertOptions
from p2w.mineru_backend import OCRBackendError, Cancelled
from p2w_gui import settings

state = {"hits": 0, "fail_times": 0, "status": 200, "got": b""}

class H(BaseHTTPRequestHandler):
    def do_PUT(self):
        state["hits"] += 1
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        if state["hits"] <= state["fail_times"]:
            self.close_connection = True          # drop mid-flight
            return
        state["got"] = body
        state["ctype"] = self.headers.get("Content-Type")
        self.send_response(state["status"]); self.end_headers(); self.wfile.write(b"ok")
    def log_message(self, *a): pass

srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{srv.server_address[1]}/put?sig=x"

tmp = Path(__file__).parent / "up.bin"
tmp.write_bytes(b"A" * 300000)
mineru_cloud._UPLOAD_BACKOFF_SEC = 0.05

# 1. clean upload: body arrives whole, no Content-Type (would break the OSS signature)
mineru_cloud._upload(URL, tmp)
assert state["hits"] == 1 and state["got"] == tmp.read_bytes(), "body mismatch"
assert not state["ctype"], f"Content-Type leaked: {state['ctype']}"

# 2. two dropped connections then success -> still succeeds
state.update(hits=0, fail_times=2, got=b"")
mineru_cloud._upload(URL, tmp)
assert state["hits"] == 3, state["hits"]
assert state["got"] == tmp.read_bytes()

# 3. always dropping -> gives up after _UPLOAD_TRIES with a readable message
state.update(hits=0, fail_times=99)
try:
    mineru_cloud._upload(URL, tmp); raise SystemExit("should have raised")
except OCRBackendError as e:
    assert state["hits"] == mineru_cloud._UPLOAD_TRIES, state["hits"]
    assert "上传中断" in str(e) and "重试" in str(e), str(e)

# 4. a 4xx is not retried: the signature will not get better
state.update(hits=0, fail_times=0, status=403)
try:
    mineru_cloud._upload(URL, tmp); raise SystemExit("should have raised")
except OCRBackendError:
    assert state["hits"] == 1, state["hits"]
state["status"] = 200

# 5. cancel wins over retrying
state.update(hits=0, fail_times=99)
try:
    mineru_cloud._upload(URL, tmp, lambda: True); raise SystemExit("should have raised")
except Cancelled:
    assert state["hits"] == 0, state["hits"]

# ---- output folder ----
d = settings.default_output_dir()
assert d.name == "抄录输出" and d.parent == settings.desktop_dir(), d

from p2w_gui.server import ConvertManager
mgr = ConvertManager()
assert mgr._output_dir == str(settings.default_output_dir())
co = ConvertOptions()
rec = {"path": str(tmp)}
assert mgr._resolve_output_dir(rec, co, {"outDir": "default"}) == \
       settings.default_output_dir() / f"{tmp.stem}_word"
assert mgr._resolve_output_dir(rec, co, {"outDir": "source"}) == tmp.parent / f"{tmp.stem}_word"
mgr._output_dir = "/tmp/pick"
assert mgr._resolve_output_dir(rec, co, {"outDir": "custom"}) == Path("/tmp/pick")

# open_path: creates the folder if it is not there, and uses a command that
# exists on this platform. Faking os.name is not an option -- pathlib renders
# every path with it -- so the Windows branch is checked at the source level.
import inspect, p2w_gui.server as S
src = inspect.getsource(S.open_path)
assert "os.startfile" in src, "Windows 分支没用 os.startfile"
assert "xdg-open" in src, "Linux 分支没用 xdg-open"
assert src.count('Popen(["open"') == 1, "macOS 的 open 不该用在其他平台"

missing = Path(__file__).parent / "never_made"
calls = []
S.subprocess.Popen = lambda a, *x, **k: calls.append(a)
r = S.open_path(S.OpenReq(path=str(missing)))
assert r["ok"], r
assert missing.is_dir(), "输出目录没被建出来"
assert calls and calls[0][0] == "xdg-open", calls

# a missing file, unlike a missing folder, is still an error
r = S.open_path(S.OpenReq(path=str(missing / "nope.docx")))
assert not r["ok"] and "不存在" in r["error"], r

tmp.unlink(); missing.rmdir()
srv.shutdown()
print("OK  上传重试/流式、取消、输出目录、打开文件夹")

# ---- cloud sub-steps: run_cloud reports each one, server turns it into UI state ----
import p2w.mineru_cloud as MC
from p2w.mineru_backend import run_mineru

phases = []
MC._request_upload = lambda names, opts: ("b1", ["http://x/1"] * len(names))
MC._upload = lambda url, chunk, cancel=None: None
MC._wait = lambda bid, names, opts, cancel, factor=1, phase=None: (
    phase and [phase(f"wait:{i}/{len(names)}") for i in range(len(names) + 1)],
    {n: "http://z" for n in names})[1]
MC._assemble = lambda chunks, urls, out, phase=None: (
    phase and [phase(f"fetch:{i + 1}/{len(chunks)}") for i in range(len(chunks))],
    (out / "content_list.json", out))[1]
MC.plan_chunks = lambda src: [(0, 199), (200, 399)]
MC._split = lambda src, ranges, work: [(src, a) for a, _ in ranges]

opts = ConvertOptions(api_token="k")
work = Path(__file__).parent / "cloudwork"
run_mineru(Path(__file__), work, opts, on_phase=phases.append)
assert phases[0] == "cloud:split", phases
assert "cloud:upload:0/2" in phases and "cloud:upload:2/2" in phases, phases
assert "cloud:wait:2/2" in phases and "cloud:fetch:2/2" in phases, phases

# the server maps them onto a bar that actually moves, and never past the 81 cap
mgr = ConvertManager()
rec = {"status": "ocr", "progress": 10}
def feed(ph):
    step, _, frac = ph[6:].partition(":")
    done, _, total = frac.partition("/")
    rec["step"] = {"k": step, "i": int(done or 0), "n": int(total or 0)}
    if step in ("upload", "wait", "fetch") and total:
        lo, hi = {"upload": (10, 40), "wait": (40, 75), "fetch": (75, 81)}[step]
        rec["progress"] = lo + int((hi - lo) * int(done) / max(1, int(total)))
seq = [p for p in phases if not p.endswith("split")]
vals = []
for ph in seq:
    feed(ph); vals.append(rec["progress"])
assert vals == sorted(vals), vals
assert max(vals) <= 81 and vals[0] >= 10, vals
assert rec["step"] == {"k": "fetch", "i": 2, "n": 2}, rec["step"]

# "step" has to survive the public() whitelist or the frontend never sees it
r = ConvertManager().public({"id": 1, "name": "a.pdf", "type": "pdf", "pages": 1,
                             "size": "1 MB", "status": "ocr", "progress": 10,
                             "reviewNote": None, "errNote": None,
                             "step": {"k": "wait", "i": 1, "n": 2}})
assert r["step"] == {"k": "wait", "i": 1, "n": 2}, r

import shutil; shutil.rmtree(work, ignore_errors=True)
print("OK  云端分步上报、进度不倒退不越界、step 能传到前端")
