#!/bin/bash
# 跑全套测试。从任意目录调用都行。
#   ./tests/run_all.sh          全部
#   ./tests/run_all.sh --fast   跳过要真跑识别的（后端端到端那项，约 1 分钟 + 首次要下模型）
cd "$(dirname "$0")/.." || exit 1
export PATH="/opt/homebrew/opt/node@24/bin:$PATH"

FAIL=0
run() {  # run <名字> <命令...>
  local name="$1"; shift
  printf '\n\033[1m▸ %s\033[0m\n' "$name"
  if "$@"; then printf '\033[32m  ✓ %s\033[0m\n' "$name"
  else printf '\033[31m  ✗ %s\033[0m\n' "$name"; FAIL=$((FAIL+1)); fi
}

# 前端改了 jsx 却没重新生成 bundle 的话，后面的前端测试测的是旧快照
run "前端预编译" node scripts/build_frontend.js
run "JSX 可编译" node tests/check_jsx.js
run "解析 MinerU 输出" python3 tests/test_parse_mineru.py
run "坏输入的处理" python3 tests/test_error_paths.py
run "文字层直通的把关" python3 tests/test_textlayer.py
run "混合路径的涂白与拼装" python3 tests/test_hybrid.py
run "云端识别与 API Key" python3 tests/test_cloud.py
run "进度与 ETA 推算" python3 tests/test_progress.py
run "LaTeX → Word 公式" python3 tests/test_omml.py
run "DocModel → docx 渲染" python3 tests/test_render_docx.py
run "DocModel → Markdown 导出" python3 tests/test_render_md.py
run "前端每个按钮" bash -c 'cd tests/jstest && node test_frontend.js'
run "首次引导流程" bash -c 'cd tests/jstest && node test_gate.js'

if [ "$1" != "--fast" ]; then
  run "后端全端点 + 真实转换" python3 tests/test_backend.py
fi

printf '\n'
if [ "$FAIL" -eq 0 ]; then printf '\033[32m全部通过\033[0m\n'; else printf '\033[31m%d 项失败\033[0m\n' "$FAIL"; fi
exit "$FAIL"
