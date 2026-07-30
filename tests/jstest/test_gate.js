// First-run flow: the user must pick local or cloud, and lands in Settings.
const fs = require("fs"), vm = require("vm"), { JSDOM } = require("jsdom");
const FE = require("path").join(__dirname, "..", "..", "src/p2w_gui/frontend") + "/";
const dom = new JSDOM('<!DOCTYPE html><html><body><div id="root"></div></body></html>', { pretendToBeVisual: true, url: "http://localhost/" });
const win = dom.window;
win.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
win.requestAnimationFrame = (cb) => setTimeout(cb, 0);
// No setItem: simulate a first launch
win.__TAURI__ = { dialog: { open: () => Promise.resolve(null) }, window: { appWindow: {} }, shell: { open() {} }, event: { listen: async () => () => {} }, invoke: async () => {} };
const posts = [];
win.fetch = (url, opts) => {
  if (opts && opts.body) posts.push({ url, body: JSON.parse(opts.body) });
  const cfg = { engine: "local", hasToken: false, tokenHint: "", localAvailable: true };
  return Promise.resolve({ json: () => Promise.resolve(
    url.includes("/model/status") ? { ready: false, downloading: false, percent: 0, error: "" }
    : url.includes("/settings") ? Object.assign({ ok: true, settings: cfg }, cfg)
    : { ok: true, files: [], running: false }) });
};
vm.createContext(win);
vm.runInContext(fs.readFileSync(FE + "vendor/react.production.min.js", "utf8"), win);
vm.runInContext(fs.readFileSync(FE + "vendor/react-dom.production.min.js", "utf8"), win);
vm.runInContext(fs.readFileSync(FE + "bundle.js", "utf8"), win, { filename: "bundle.js" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const doc = win.document;
const results = [];
const ck = (n, c) => { results.push(c); console.log(c ? "✅" : "❌", n); };
(async () => {
  await sleep(150);
  ck("首次打开不空白", doc.getElementById("root").children.length > 0 && doc.body.textContent.trim().length > 0);
  const cards = doc.querySelectorAll(".gate-card");
  ck("出现二选一卡片", cards.length === 2);
  const text = doc.body.textContent;
  ck("两个选项各有说明", text.includes("本机识别") && text.includes("云端识别")
    && text.includes("2.2 GB") && text.includes("API Key"));
  // 点"本机识别"→ 存 engine → 进主界面且设置面板已打开（下载按钮就在眼前）
  const local = [...cards].find((c) => c.textContent.includes("本机识别"));
  local.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
  await sleep(250);
  ck("选完把选择存到后端", posts.some((p) => p.url.includes("/settings") && p.body.engine === "local"));
  ck("落到设置面板", !!doc.querySelector(".sheet"));
  ck("下载模型按钮就在眼前", [...doc.querySelectorAll("button")].some((b) => b.textContent.trim() === "下载模型"));
  const fails = results.filter((r) => !r).length;
  console.log(fails ? `${fails} 项失败` : "首次引导全部通过 ✅");
  process.exit(fails ? 1 : 0);
})();
