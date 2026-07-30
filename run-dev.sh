#!/bin/bash
# 启动 pdf2word 桌面 App（开发模式）。
# 关键：用系统 Apple clang 编译，避开 anaconda 注入的编译器/CFLAGS
# （否则 Tauri 的原生窗口库 tao 编译失败）。python3 仍用环境里的（需含 fastapi）。
cd "$(dirname "$0")" || exit 1

# 清理上次残留的后端进程，避免 8756 端口被占
lsof -ti:8756 2>/dev/null | xargs kill -9 2>/dev/null || true

# 在改 PATH 之前，锁定当前带依赖的 python3（fastapi/opendataloader 等）
export P2W_PYTHON="$(command -v python3)"

export CC=/usr/bin/clang
export CXX=/usr/bin/clang++
unset CFLAGS CPPFLAGS CXXFLAGS LDFLAGS SDKROOT MACOSX_DEPLOYMENT_TARGET

export PATH="$HOME/.cargo/bin:/opt/homebrew/opt/node@24/bin:/opt/homebrew/bin:$PATH"

# 预编译前端 jsx → bundle.js（确保 Tauri 加载到最新前端）
node scripts/build_frontend.js || { echo "前端预编译失败"; exit 1; }

exec tauri dev
