#!/bin/bash
# 打 macOS 的 .app / .dmg。**只有一种规格**：带引擎、不带模型权重。
#
#   ./packaging/build_mac.sh
#
# 用户装完在设置里二选一：填 mineru.net 的 API Key 走云端，或者点一下把 2.2 GB 的
# 模型下到本机。**模型权重不进安装包**——塞进去 DMG 要 3.8 GB，而选云端的用户
# 根本用不上它。
#
# 调试用的两个开关（正式分发别用）：
#   --with-models   把本机已有的模型缓存也塞进包里，做离线验收时省得再下一遍
#
# 产物：src-tauri/target/release/bundle/{macos/抄录.app, dmg/*.dmg}
# 签名与公证不在这里做（需要 Apple 开发者账号），见 DISTRIBUTION.md。
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
PAYLOAD="$ROOT/src-tauri/payload"   # 必须在 src-tauri 内：
# Tauri 1 对 ../ 之外的资源会加 _up_ 前缀，路径结构就乱了
MODE=engine                          # engine（正式规格）| full（仅调试用）
case "${1:-}" in
  --with-models) MODE=full ;;
  "")            ;;
  *) echo "不认识的参数：$1（只接受 --with-models，且仅供调试）"; exit 1 ;;
esac
echo "▸ 规格：$MODE"

export PATH="$HOME/.cargo/bin:/opt/homebrew/opt/node@24/bin:/opt/homebrew/bin:$PATH"
for tool in cargo node uv curl; do
  command -v "$tool" >/dev/null || { echo "缺少 $tool"; exit 1; }
done

echo "▸ 1/5 前端预编译"
node scripts/build_frontend.js

echo "▸ 2/5 准备随包的 Python 环境"
rm -rf "$PAYLOAD"
mkdir -p "$PAYLOAD"
# 不用 venv：venv 的 bin/python 是指向外部解释器的符号链接，pip 装出来的脚本
# shebang 里又写死打包机的绝对路径，搬到别人电脑上两样都废。
# 直接复制 uv 管理的独立解释器（python-build-standalone，自带可重定位能力），
# 把包装进它自己的 site-packages，运行时一律用 `python -m mineru.cli.*` 调用。
# 必须是 uv 自己下载的独立解释器（python-build-standalone），不能用系统上现成的：
# 那些可能是 conda 的，一 cp 就把十几 GB 的 anaconda 整个搬进安装包。
# 注意 `uv python find` 即使加了 --managed-python 也可能返回 conda 的（uv 0.7.16 实测），
# 所以直接从 `uv python dir` 里挑。
uv python install 3.12 >/dev/null 2>&1 || true
PY_ROOT="$(ls -d "$(uv python dir)"/cpython-3.12.*-macos-* 2>/dev/null | sort -V | tail -1)"
[ -n "$PY_ROOT" ] && [ -f "$PY_ROOT/bin/python3.12" ] \
  || { echo "没有 uv 管理的 3.12，先跑：uv python install 3.12"; exit 1; }
echo "  用解释器：${PY_ROOT} ($(du -sh "$PY_ROOT" | cut -f1))"
cp -R "$PY_ROOT" "$PAYLOAD/python"
PY="$PAYLOAD/python/bin/python3.12"
# uv 给自己管理的解释器打了 PEP 668 的 externally-managed 标记，装包会被拒。
# 这份是我们复制出来自用的副本，删掉标记是合适的。
rm -f "$PAYLOAD"/python/lib/python3.12/EXTERNALLY-MANAGED
# p2w 和 MinerU 的依赖不冲突，装同一个环境即可（实测只多一个 pymupdf）。
# 云端版不装 mineru[core]——省掉的就是 torch/transformers 那几个 GB。
COMMON="python-docx lxml beautifulsoup4 Pillow fastapi uvicorn pypdf pymupdf"
uv pip install --python "$PY" --quiet "mineru[core]" $COMMON
"$PY" -c "import mineru, docx, fitz; print('  随包环境自检通过')"

echo "▸ 3/5 复制源码与 pandoc"
mkdir -p "$PAYLOAD/src"
cp -R src/p2w src/p2w_gui "$PAYLOAD/src/"
find "$PAYLOAD/src" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
# 不能直接抄 homebrew 的 pandoc——它链到 /opt/homebrew 下的 libgmp，别人机器上没有。
# 官方 macOS 包是自包含的。
PANDOC_VER="3.6.3"
curl -fsSL "https://github.com/jgm/pandoc/releases/download/${PANDOC_VER}/pandoc-${PANDOC_VER}-arm64-macOS.zip" \
  -o /tmp/pandoc.zip
unzip -qo /tmp/pandoc.zip -d /tmp/pandoc-dist
cp "$(find /tmp/pandoc-dist -name pandoc -type f -perm +111 | head -1)" "$PAYLOAD/pandoc"
chmod +x "$PAYLOAD/pandoc"

if [ "$MODE" = full ]; then
  echo "▸ 4/5 复制识别模型（约 2.2 GB，慢；仅调试规格）"
  mkdir -p "$PAYLOAD/models"
  cp -R "$HOME/.cache/huggingface/hub/models--opendatalab--MinerU2.5-Pro-2605-1.2B" "$PAYLOAD/models/" \
    || echo "  ⚠️ 本机还没有模型缓存，跳过"
else
  echo "▸ 4/5 不带模型权重（用户在设置里选云端，或点一下自己下）"
fi

echo "▸ 5/5 编译 Tauri 外壳并打包"
# resources 只在打包时需要，用 --config 覆盖进去，免得污染开发用的 tauri.conf.json
# （主配置里写死路径的话，build/payload 不存在时 tauri dev 会报错）。
# 必须用系统 clang：conda 注入的 CFLAGS 会让窗口库 tao 编译失败。
# ld searches /usr/local/lib before the SDK, so a MacPorts/Homebrew libiconv on
# the build machine gets linked and the app crashes on every other Mac
# ("Library not loaded"). Putting the SDK's stub directory first via RUSTFLAGS
# makes the linker resolve -liconv to the system copy at link time.
SDKLIB="$(xcrun --show-sdk-path)/usr/lib"
env -u CFLAGS -u CPPFLAGS -u CXXFLAGS -u LDFLAGS -u LDFLAGS_LD -u LIBRARY_PATH \
    -u DYLD_LIBRARY_PATH -u DYLD_FALLBACK_LIBRARY_PATH \
    -u SDKROOT -u MACOSX_DEPLOYMENT_TARGET \
    CC=/usr/bin/clang CXX=/usr/bin/clang++ \
    RUSTFLAGS="-L ${SDKLIB}" \
    tauri build --config '{"tauri":{"bundle":{"resources":["payload/**/*"]}}}'

APP="src-tauri/target/release/bundle/macos/抄录.app"
echo
[ -d "$APP" ] || { echo "❌ 没找到 .app，检查上面的编译输出"; exit 1; }

# Hard gate, checked INSIDE THE DMG (the artifact actually shipped -- checking
# the .app once let a bad DMG through, since tauri creates the DMG before any
# post-processing could run). Anything outside /usr/lib and /System will be
# missing on other Macs. No silent fixing: if this trips, fix the link itself.
DMG=$(ls src-tauri/target/release/bundle/dmg/*.dmg 2>/dev/null | head -1)
[ -n "$DMG" ] || { echo "❌ 没找到 DMG"; exit 1; }
MNT=$(mktemp -d)
hdiutil attach -quiet -nobrowse -readonly -mountpoint "$MNT" "$DMG"
STRAY=$(otool -L "$MNT/抄录.app/Contents/MacOS/抄录" | tail -n +2 | awk '{print $1}' \
        | grep -vE '^/usr/lib/|^/System/' || true)
hdiutil detach -quiet "$MNT"
if [ -n "$STRAY" ]; then
  echo "❌ DMG 里的二进制依赖了非系统库，装到别人电脑上会崩："
  echo "$STRAY" | sed 's/^/     /'
  exit 1
fi
echo "  依赖自检通过（在 DMG 内校验）：只用系统库"

echo "✅ 打包完成：${APP} ($(du -sh "$APP" | cut -f1))"
echo "   未签名，别人打开会被 Gatekeeper 拦——签名与公证见 packaging/DISTRIBUTION.md"
