// Frontend behavior tests: real React render in jsdom, mocked Tauri/fetch,
// simulated clicks, asserted outcomes.
const fs = require("fs"), vm = require("vm"), { JSDOM } = require("jsdom");
const FE = require("path").join(__dirname, "..", "..", "src/p2w_gui/frontend") + "/";

const dom = new JSDOM('<!DOCTYPE html><html><body><div id="root"></div></body></html>',
  { pretendToBeVisual: true, url: "http://localhost/" });
const win = dom.window;
win.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
win.requestAnimationFrame = (cb) => setTimeout(cb, 0);
win.localStorage.setItem("pdfw_gate_done", "1"); // skip first-run, test the main UI

const calls = { fetch: [], dialog: [], win: [], shell: [] };
let pollPhase = "idle"; // drives the /poll mock
win.fetch = (url, opts) => { calls.fetch.push({ url, body: opts && opts.body ? JSON.parse(opts.body) : null }); return Promise.resolve({ json: () => Promise.resolve(mockResp(url)) }); };
win.__TAURI__ = {
  dialog: { open: (o) => { calls.dialog.push(o || {}); return Promise.resolve(o && o.directory ? "/picked/folder" : ["/picked/a.pdf", "/picked/b.png"]); } },
  window: { appWindow: { close() { calls.win.push("close"); }, minimize() { calls.win.push("minimize"); }, toggleMaximize() { calls.win.push("toggleMaximize"); } } },
  shell: { open: (p) => { calls.shell.push(p); } },
  invoke: (cmd, args) => { (calls.invoke = calls.invoke || []).push([cmd, args || {}]); return Promise.resolve(null); },
};
let addResp = { files: [{ id: 1, name: "a.pdf", type: "pdf", pages: 2, size: "1.0 MB", status: "pending", progress: 0 }, { id: 2, name: "b.png", type: "img", pages: 1, size: "0.5 MB", status: "pending", progress: 0 }] };
let startResp = { ok: true };
function mockResp(url) {
  if (url.endsWith("/add")) return addResp;
  if (url.endsWith("/add_folder")) return { files: [{ id: 3, name: "c.pdf", type: "pdf", pages: 1, size: "1.0 MB", status: "pending", progress: 0 }] };
  if (url.endsWith("/start")) return startResp;
  if (url.endsWith("/poll")) return { files: [{ id: 1, name: "a.pdf", type: "pdf", pages: 2, size: "1.0 MB", status: "done", progress: 100 }, { id: 2, name: "b.png", type: "img", pages: 1, size: "0.5 MB", status: "review", reviewNote: "1 处待核对", progress: 100 }], running: false };
  if (url.endsWith("/output_dir")) return { path: "/out" };
  if (url.endsWith("/reviews")) return { items: [{ file: "b.png", page: 1, loc: "位置 10, 20, 30, 40", kind: "公式", latex: "x^{2}", hint: "请核对乘号" }] };
  if (url.includes("/file_path")) return { path: "/out/a.docx" };
  if (url.endsWith("/settings/check")) return { ok: true };
  if (url.includes("/model/status")) return modelResp;
  if (url.includes("/model/")) return { ok: true };
  // GET and POST share the URL, so return both response shapes
  if (url.endsWith("/settings")) return Object.assign({ ok: true, settings: cfgResp }, cfgResp);
  return { ok: true };
}
let cfgResp = { engine: "local", hasToken: false, tokenHint: "", localAvailable: true, export: "docx" };
let modelResp = { ready: true, downloading: false, percent: 100, error: "" };

// Loaded exactly as Tauri does: vendor React plus the precompiled bundle.
vm.createContext(win);
vm.runInContext(fs.readFileSync(FE + "vendor/react.production.min.js", "utf8"), win);
vm.runInContext(fs.readFileSync(FE + "vendor/react-dom.production.min.js", "utf8"), win);
vm.runInContext(fs.readFileSync(FE + "bundle.js", "utf8"), win, { filename: "bundle.js" });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const doc = win.document;
const PASS = [], FAIL = [];
const ck = (n, c) => { (c ? PASS : FAIL).push(n); console.log(c ? "✅" : "❌", n); };
function findByText(txt, tag) {
  return [...doc.querySelectorAll(tag || "*")].find((e) => e.textContent.trim() === txt
    || (e.childNodes.length && [...e.childNodes].some((c) => c.nodeType === 3 && c.textContent.includes(txt))));
}
function click(el) { if (el) el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); }
// The engine toggle and the settings option share their labels, so scope
// lookups to the segmented control inside the sheet.
function segBtn(txt) {
  return [...doc.querySelectorAll(".sheet .seg button")].find((b) => b.textContent.trim() === txt);
}

(async () => {
  await sleep(150);
  ck("渲染非空", doc.getElementById("root").children.length > 0);
  ck("空状态投递区文案", doc.body.textContent.includes("把 PDF 拖进来"));
  ck("使用原生窗口交通灯", doc.querySelectorAll(".light").length === 0);
  ck("设置按钮存在", !!doc.querySelector('.icon-btn[title="设置"]'));
  ck("主页有识别方式开关且选中本机", (() => {
    const on = doc.querySelector(".seg-engine button.on");
    return on && on.textContent === "本机识别";
  })());
  // Toggling from the main screen: posts immediately, updates selection,
  // and opens Settings when the key is missing
  cfgResp = { engine: "cloud", hasToken: false, tokenHint: "", localAvailable: true };
  click([...doc.querySelectorAll(".seg-engine button")].find((b) => b.textContent === "云端识别"));
  await sleep(250);
  ck("主页切云端→POST /settings", calls.fetch.some((c) =>
    c.url.endsWith("/settings") && c.body && c.body.engine === "cloud"));
  ck("主页切云端→开关选中态变更", doc.querySelector(".seg-engine button.on").textContent === "云端识别");
  ck("主页切云端→有切换确认", doc.body.textContent.includes("已切换到云端识别")
    || doc.body.textContent.includes("云端识别要先填 API Key"));
  ck("缺 Key→设置面板自动打开", !!doc.querySelector(".sheet"));
  doc.dispatchEvent(new win.KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
  cfgResp = { engine: "local", hasToken: false, tokenHint: "", localAvailable: true };
  click([...doc.querySelectorAll(".seg-engine button")].find((b) => b.textContent === "本机识别"));
  await sleep(250);
  doc.dispatchEvent(new win.KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
  await sleep(100);

  // Blank space is inert; only the tray glyph opens a dialog
  click(doc.querySelector(".drop"));
  await sleep(120);
  ck("点投递区空白→不弹对话框", calls.dialog.length === 0);
  click(doc.querySelector(".drop-glyph"));
  await sleep(150);
  ck("点托盘图标→Tauri 文件对话框", calls.dialog.length >= 1 && !calls.dialog[0].directory);
  ck("点托盘图标→请求 /add", calls.fetch.some((c) => c.url.endsWith("/add")));
  ck("文件进入列表", doc.body.textContent.includes("a.pdf"));

  // Settings: open, switch theme (syncs window appearance), close with Esc
  click(doc.querySelector('.icon-btn[title="设置"]'));
  await sleep(80);
  ck("点设置→弹窗出现", !!doc.querySelector(".sheet-mask"));
  ck("启动时同步窗口外观（system）", (calls.invoke || []).some(([c, a]) => c === "set_appearance" && a.theme === "system"));
  click(findByText("深色", "button"));
  await sleep(80);
  ck("切深色→invoke set_appearance(dark)", (calls.invoke || []).some(([c, a]) => c === "set_appearance" && a.theme === "dark"));
  ck("切深色→根节点 theme-dark", !!doc.querySelector(".app-root.theme-dark"));
  click(findByText("跟随系统", "button"));
  await sleep(80);

  // Export format: defaults to Word, switches to Markdown immediately
  ck("设置里有导出格式选项", !!segBtn("Word") && !!segBtn("Markdown"));
  cfgResp = Object.assign({}, cfgResp, { export: "md" });
  click(segBtn("Markdown"));
  await sleep(150);
  ck("切 Markdown→POST /settings 带 export", calls.fetch.some((c) =>
    c.url.endsWith("/settings") && c.body && c.body.export === "md"));
  ck("切 Markdown→说明跟着变", doc.querySelector(".sheet").textContent.includes("LaTeX 原文"));
  cfgResp = Object.assign({}, cfgResp, { export: "docx" });
  click(segBtn("Word"));
  await sleep(150);

  // Language: defaults to Chinese, switches to English, and reverts
  ck("品牌区显示中英文名", doc.body.textContent.includes("抄录 · Scribe"));
  ck("设置里有语言选项", !!segBtn("English") && !!segBtn("中文"));
  click(segBtn("English"));
  await sleep(150);
  ck("切 English→设置标题变英文", doc.querySelector(".sheet h2").textContent === "Settings");
  ck("切 English→导出格式行变英文", doc.querySelector(".sheet").textContent.includes("Export Format"));
  click(segBtn("中文"));
  await sleep(150);
  ck("切回中文→还原", doc.querySelector(".sheet h2").textContent === "设置");

  // Engine: local by default, stating that files stay on the machine
  ck("默认本机识别", !!segBtn("本机识别") &&
    doc.querySelector(".sheet").textContent.includes("模型在本机运行"));
  ck("本机模式不显示 API Key 输入框", !doc.querySelector(".key-input"));
  ck("模型已就绪→不显示下载按钮", !findByText("下载模型", "button") &&
    doc.querySelector(".sheet").textContent.includes("已就绪"));

  // Model missing: a download entry point that actually calls the backend
  modelResp = { ready: false, downloading: false, percent: 0, error: "" };
  await sleep(4300);                       // wait for the 4s settings poll
  ck("模型未下载→出现下载按钮", !!findByText("下载模型", "button"));
  ck("模型未下载→写明体积", doc.querySelector(".sheet").textContent.includes("2.2 GB"));
  modelResp = { ready: false, downloading: true, percent: 37, error: "" };
  click(findByText("下载模型", "button"));
  await sleep(200);
  ck("点下载→POST /model/download", calls.fetch.some((c) => c.url.includes("/model/download")));
  // Downloading: progress bar plus cancel
  ck("下载中→显示百分比", doc.querySelector(".sheet").textContent.includes("37%"));
  ck("下载中→有进度条", !!doc.querySelector(".track.dl .bar"));
  ck("下载中→可取消", !!findByText("取消下载", "button"));
  click(findByText("取消下载", "button"));
  await sleep(150);
  ck("点取消→POST /model/cancel", calls.fetch.some((c) => c.url.includes("/model/cancel")));
  modelResp = { ready: true, downloading: false, percent: 100, error: "" };
  await sleep(1300);
  // Switching to cloud persists, reveals the key field, and warns about upload
  cfgResp = { engine: "cloud", hasToken: false, tokenHint: "" };
  click(segBtn("云端识别"));
  await sleep(120);
  ck("切云端→POST /settings 带 engine", calls.fetch.some((c) =>
    c.url.endsWith("/settings") && c.body && c.body.engine === "cloud"));
  ck("切云端→主页开关同步", doc.querySelector(".seg-engine button.on").textContent === "云端识别");
  ck("设置里切换→有即时生效的确认", doc.body.textContent.includes("已切换到云端识别"));
  ck("云端→出现 API Key 输入框", !!doc.querySelector(".key-input"));
  ck("云端→有申请入口", !!findByText("去 mineru.net 申请 →", "button"));
  ck("云端→明确告知文件会上传", doc.querySelector(".sheet").textContent.includes("上传到 mineru.net"));
  ck("云端→提示每日额度与 Key 有效期", (() => {
    const t = doc.querySelector(".sheet").textContent;
    return t.includes("1000 页") && t.includes("200 页") && t.includes("90 天");
  })());
  // Saving a key: sent by POST, never rendered into the DOM
  const input = doc.querySelector(".key-input");
  // React controlled input: assign through the prototype setter
  Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")
    .set.call(input, "sk-test-123456789");
  input.dispatchEvent(new win.Event("input", { bubbles: true }));
  await sleep(60);
  cfgResp = { engine: "cloud", hasToken: true, tokenHint: "sk-t…6789" };
  click(findByText("保存", "button"));
  await sleep(120);
  ck("保存 Key→POST /settings 带 api_token", calls.fetch.some((c) =>
    c.url.endsWith("/settings") && c.body && c.body.api_token === "sk-test-123456789"));
  ck("Key 存完清空输入框", doc.querySelector(".key-input").value === "");
  ck("界面只显示掩码", doc.querySelector(".sheet").textContent.includes("sk-t…6789"));
  // Test button validates the key
  click(findByText("测试", "button"));
  await sleep(120);
  ck("测试→POST /settings/check", calls.fetch.some((c) => c.url.endsWith("/settings/check")));
  ck("测试通过→显示可以用", doc.querySelector(".sheet").textContent.includes("可以用"));
  // A build without a local engine must disable the local option
  cfgResp = { engine: "cloud", hasToken: true, tokenHint: "sk-t…6789", localAvailable: false };
  click(segBtn("云端识别"));
  await sleep(120);
  ck("没有本地引擎时本机选项禁用", !!segBtn("本机识别").disabled);
  ck("没有本地引擎时说明这个版本只能用云端",
    doc.querySelector(".sheet").textContent.includes("没有内置本地模型"));

  // Regression: an old backend returns {detail:"Not Found"}, which must not
  // blank the window.
  cfgResp = null;          // mockResp 里 Object.assign({ok:true,settings:null}, null) → 无 engine
  const mockBackup = win.fetch;
  win.fetch = (url, opts) => {
    if (opts && url.endsWith("/settings")) return Promise.resolve({ json: () => Promise.resolve({ detail: "Not Found" }) });
    return mockBackup(url, opts);
  };
  click(segBtn("云端识别"));
  await sleep(200);
  ck("旧后端 404 形状→界面不白屏", doc.getElementById("root").children.length > 0
    && !!doc.querySelector(".sheet"));
  ck("旧后端 404 形状→给出错误提示", doc.body.textContent.includes("设置没保存上"));
  win.fetch = mockBackup;

  // Switch back to local so later cases are unaffected
  cfgResp = { engine: "local", hasToken: true, tokenHint: "sk-t…6789", localAvailable: true };
  click(segBtn("本机识别"));
  await sleep(120);

  doc.dispatchEvent(new win.KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
  await sleep(80);
  ck("Esc→关闭设置弹窗", !doc.querySelector(".sheet-mask"));

  // Remove a pending file
  const beforeRemove = calls.fetch.length;
  const removeBtn = doc.querySelector('.icon-btn[title="移除"]');
  click(removeBtn);
  await sleep(80);
  ck("移除→请求 /remove", calls.fetch.some((c) => c.url.endsWith("/remove")));

  // Start conversion
  const startBtn = [...doc.querySelectorAll("button")].find((b) => /^转换 \d+ 个文件$/.test(b.textContent.trim()));
  click(startBtn);
  await sleep(150);
  ck("点开始→请求 /start", calls.fetch.some((c) => c.url.endsWith("/start")));
  ck("start 传了文件 id", calls.fetch.some((c) => c.url.endsWith("/start") && c.body && Array.isArray(c.body.ids)));

  // Completed state
  await sleep(700);
  ck("完成后出现汇总", doc.body.textContent.includes("完成") && !!doc.querySelector(".summary"));

  // 文件行"打开" → shell.open
  const openBtn = findByText("打开 Word", "button");
  click(openBtn);
  await sleep(80);
  ck("打开文件→file_path+shell", calls.fetch.some((c) => c.url.includes("/file_path")) && calls.shell.length >= 1);

  // 汇总：打开输出文件夹 → /output_dir + shell
  const shellBefore = calls.shell.length;
  click(findByText("打开输出文件夹", "button"));
  await sleep(80);
  ck("打开输出文件夹→/output_dir+shell", calls.fetch.some((c) => c.url.endsWith("/output_dir")) && calls.shell.length > shellBefore);

  // 复核报告：老师要的是 Word 和报告并排看，所以直接用浏览器打开那份 HTML，
  // 不做 App 内页。入口在需复核的文件行上，以及汇总条里。
  const shellBefore2 = calls.shell.length;
  click(findByText("复核", "button"));
  await sleep(120);
  ck("复核入口→取 report 路径", calls.fetch.some((c) => c.url.includes("which=report")));
  ck("复核入口→交给系统打开", calls.shell.length > shellBefore2);

  // 汇总：再转一批 → /clear → 回空状态
  click(findByText("再转一批", "button"));
  await sleep(120);
  ck("再转一批→/clear", calls.fetch.some((c) => c.url.endsWith("/clear")));
  ck("清空后回到空状态", doc.body.textContent.includes("把 PDF 拖进来"));

  // 文件夹按钮 → dialog(directory) + /add_folder
  click(findByText("选择文件夹", "button"));
  await sleep(120);
  ck("选文件夹→Tauri 目录对话框", calls.dialog.some((d) => d.directory));
  ck("选文件夹→/add_folder", calls.fetch.some((c) => c.url.endsWith("/add_folder")));

  // ---- 失败重试 + start 被拒回滚 ----
  // 清空列表，换一个 error 状态的文件进来
  click(findByText("清空列表", "button"));
  await sleep(100);
  addResp = { files: [{ id: 9, name: "bad.pdf", type: "pdf", pages: 3, size: "2.0 MB", status: "error", errNote: "文件已损坏", progress: 0 }] };
  click(doc.querySelector(".drop-glyph"));
  await sleep(150);
  ck("error 文件进入列表", doc.body.textContent.includes("bad.pdf"));
  const retryBtn = findByText("重试", "button");
  ck("error 行有重试按钮", !!retryBtn);

  // 后端拒绝 start（ok:false）→ 行状态回滚为 error，不卡在"排队中"
  startResp = { ok: false };
  click(retryBtn);
  await sleep(150);
  ck("start 被拒→回滚为 error（错误说明仍在）", doc.body.textContent.includes("文件已损坏"));
  ck("start 被拒→不进入 running（底部不是停止按钮）", !findByText("停止", "button"));

  // 后端接受 → 重试发出 /start 且只带这个文件的 id
  startResp = { ok: true };
  click(findByText("重试", "button"));
  await sleep(150);
  const retryStart = calls.fetch.filter((c) => c.url.endsWith("/start")).pop();
  ck("重试→/start 只带失败文件 id", !!retryStart && JSON.stringify(retryStart.body.ids) === "[9]");
  await sleep(700); // 等 poll（running:false）把 running 落下来，不影响后续断言

  console.log(`\n=== 前端测试: ${PASS.length} 通过, ${FAIL.length} 失败 ===`);
  if (FAIL.length) { console.log("失败:", FAIL); process.exit(1); }
  console.log("前端按钮全部通过 ✅");
})().catch((e) => { console.error("harness error:", e); process.exit(2); });
