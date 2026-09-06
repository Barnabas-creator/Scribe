"""The wait loop has to survive a sleeping laptop and a dropped link."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from p2w import mineru_cloud as MC
from p2w.config import ConvertOptions
from p2w.mineru_backend import OCRBackendError

MC._POLL_SEC = 0.01
opts = ConvertOptions(api_token="k", timeout_sec=5)

# 1. a transient failure is retried, not fatal: the remote side is still working
calls = {"n": 0}
def flaky(path, o, body=None):
    calls["n"] += 1
    if calls["n"] < 4:
        raise OCRBackendError("连不上 mineru.net", transient=True)
    return {"extract_result": [{"file_name": "a.pdf", "state": "done",
                                "full_zip_url": "http://z"}]}
MC._call = flaky
assert MC._wait("b", ["a.pdf"], opts, None) == {"a.pdf": "http://z"}
assert calls["n"] == 4, calls

# 2. a real error (bad key) still fails at once
MC._call = lambda *a, **k: (_ for _ in ()).throw(OCRBackendError("API Key 无效或已过期"))
try:
    MC._wait("b", ["a.pdf"], opts, None); raise SystemExit("should have raised")
except OCRBackendError as e:
    assert "API Key" in str(e), e

# 3. staying unreachable past the grace period gives up with a clear line
MC._OFFLINE_GRACE_SEC = 0.05
MC._call = lambda *a, **k: (_ for _ in ()).throw(
    OCRBackendError("连不上 mineru.net", transient=True))
try:
    MC._wait("b", ["a.pdf"], opts, None); raise SystemExit("should have raised")
except OCRBackendError as e:
    assert "连不上" in str(e), e
MC._OFFLINE_GRACE_SEC = 600

# 4. time lost to sleep is given back instead of counting as a timeout
clock = {"t": 1000.0}
real_monotonic, real_sleep = MC.time.monotonic, MC.time.sleep
MC.time.monotonic = lambda: clock["t"]
MC.time.sleep = lambda s: clock.__setitem__("t", clock["t"] + s)
polls = {"n": 0}
def sleepy(path, o, body=None):
    polls["n"] += 1
    if polls["n"] == 2:
        clock["t"] += 3600          # lid closed for an hour, budget is 5 seconds
    if polls["n"] < 5:
        return {"extract_result": []}
    return {"extract_result": [{"file_name": "a.pdf", "state": "done",
                                "full_zip_url": "http://z"}]}
MC._call = sleepy
try:
    got = MC._wait("b", ["a.pdf"], opts, None)
    assert got == {"a.pdf": "http://z"}, got
finally:
    MC.time.monotonic, MC.time.sleep = real_monotonic, real_sleep

# 5. without that, the same hour would have blown the budget
assert opts.timeout_sec * 1 < 3600
print("OK  休眠顺延、断网重试、坏 Key 立刻失败")
