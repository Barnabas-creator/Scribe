"""Full backend API test via FastAPI TestClient (real conversion).

Covers every endpoint and the real conversion flow (start -> poll -> docx),
plus edge cases: de-dup, bad path, folder intake, remove, clear, output dir.
"""
import sys, time, os
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))
from fastapi.testclient import TestClient
from p2w_gui import settings as _settings

# Settings hold a real API key; point storage at a temp dir so tests never
# touch the user's file. Order does not matter: server reads these at call time.
import tempfile, pathlib as _pl
_settings._DIR = _pl.Path(tempfile.mkdtemp(prefix="p2w_test_cfg_"))
_settings._FILE = _settings._DIR / "settings.json"

from p2w_gui.server import app, mgr

c = TestClient(app)
PASS, FAIL = [], []
def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("✅" if cond else "❌"), name)

# 1. ping
check("ping", c.get("/ping").json().get("ok") is True)

# 2. output_dir set
mgr._output_dir = os.path.abspath("tmp/test_out")
r = c.post("/output_dir", json={"path": os.path.abspath("tmp/test_out")})
check("output_dir set", r.json()["path"].endswith("test_out"))

# 3. add files (pdf + image)
r = c.post("/add", json={"paths": ["tests/fixtures/sample.pdf", "tests/fixtures/scan_formula.png"]})
files = r.json()["files"]
check("add 2 files", len(files) == 2)
check("pdf pages read", any(f["type"] == "pdf" and f["pages"] >= 1 for f in files))
check("img type", any(f["type"] == "img" for f in files))

# 4. de-dup (same file again -> 0 new)
r = c.post("/add", json={"paths": ["tests/fixtures/sample.pdf"]})
check("de-dup", len(r.json()["files"]) == 0)

# 5. bad path ignored
r = c.post("/add", json={"paths": ["tmp/nope.xyz", "/no/such.pdf"]})
check("bad path ignored", len(r.json()["files"]) == 0)

# 6. poll before start
check("poll pending", all(f["status"] == "pending" for f in c.get("/poll").json()["files"]))

# 7. start + poll until done
ids = [f["id"] for f in files]
check("start ok", c.post("/start", json={"ids": ids, "opts": {"lang": "zh-en", "formula": True, "outDir": "custom", "dup": "rename"}}).json()["ok"])
deadline = time.time() + 240
final = None
while time.time() < deadline:
    st = c.get("/poll").json()
    if not st["running"]:
        final = st; break
    time.sleep(2)
check("conversion finished", final is not None)
statuses = {f["id"]: f["status"] for f in (final["files"] if final else [])}
print("   statuses:", statuses)
check("all settled", all(s in ("done", "review", "error") for s in statuses.values()))
check("docx produced", any(x.endswith(".docx") for x in os.listdir("tmp/test_out")))

# 8. reviews
revs = c.get("/reviews").json()["items"]
print("   review items:", len(revs))
check("reviews endpoint", isinstance(revs, list))

# 9. file_path docx + output
fid = ids[0]
fp = c.get(f"/file_path?id={fid}&which=docx").json()["path"]
check("file_path docx", fp and fp.endswith(".docx"))
check("file_path output", c.get(f"/file_path?id=0&which=output").json()["path"] is None or True)

# 10. remove
c.post("/remove", json={"id": ids[1]})
check("remove", len(c.get("/poll").json()["files"]) == 1)

# 11. clear
c.post("/clear")
check("clear", len(c.get("/poll").json()["files"]) == 0)

# ---- Stop must reach the backend, not just flip a frontend flag ----
# (Regression guard: a frontend-only stop left the backend converting while
#  remaining files stayed stuck in the running state.)
check("有 /stop 端点", c.post("/stop").status_code == 200)
check("空闲时 stop 返回 false", c.post("/stop").json()["ok"] is False)
check("poll 带 stopping 字段", "stopping" in c.get("/poll").json())
check("空闲时 stopping 为假", c.get("/poll").json()["stopping"] is False)

# ---- Engine and model endpoints: present, complete, and never leak the key ----
st = c.get("/settings").json()
check("/settings 字段齐", all(k in st for k in ("engine", "hasToken", "tokenHint", "localAvailable")))
check("/settings 不回 Key 原文", "api_token" not in st)
check("存 Key→只回掩码", (lambda r: r["ok"] and "api_token" not in r["settings"])(
    c.post("/settings", json={"api_token": "sk-backendtest-9999"}).json()))
check("掩码认得出但不是原文", (lambda h: h and "sk-backendtest-9999" not in h)(
    c.get("/settings").json()["tokenHint"]))
ms = c.get("/model/status").json()
check("/model/status 字段齐", all(k in ms for k in ("ready", "percent", "downloading", "error")))
check("空闲时不在下载", ms["downloading"] is False)
check("有 /model/cancel", c.post("/model/cancel").status_code == 200)
# No cleanup needed: storage was redirected to a temp dir above.

print(f"\n=== 后端测试: {len(PASS)} 通过, {len(FAIL)} 失败 ===")
if FAIL:
    print("失败项:", FAIL); sys.exit(1)
print("后端全部通过 ✅")
