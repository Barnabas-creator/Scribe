# 打包与分发

目标：给同事和学生一个**双击就能用**的 App，对方电脑上不需要装 Python、不需要碰命令行。

```bash
./packaging/build_mac.sh               # 唯一正式规格：带引擎、不带模型权重
./packaging/build_mac.sh --with-models # 仅调试：把本机的模型缓存也塞进去，离线验收用
```

装完在设置里二选一：填 mineru.net 的 API Key 走云端，或点"下载模型"把 2.2 GB 拉到本机。
模型权重不进安装包——塞进去 DMG 要 3.8 GB，而选云端的用户根本用不上。

产物在 `src-tauri/target/release/bundle/`：`macos/抄录.app` 和 `dmg/抄录_*.dmg`。

**为什么不再分"云端版/完整版"两个包**（走过弯路）：一次要用户在下载页做对选择，
不如装完在设置里随时切。模型权重按需下载后，单一包的体积已经可以接受。

## App 里装了什么

```
抄录.app/Contents/
  MacOS/pdf2word                   Tauri 外壳（Rust，二进制名仍是 Cargo 里的 pdf2word）
  Resources/payload/
    python/                        独立 Python 3.12 + 依赖（--cloud 时不含 MinerU）
    src/p2w, src/p2w_gui           核心库 + 后端 + 前端（bundle.js 已预编译）
    pandoc                         公式 LaTeX → Word OMML
    models/                        识别模型（仅 --with-models 时）
```

外壳启动时跑 `Resources/payload/python/bin/python3.12 -m p2w_gui.server 8756`，
前端通过本地 HTTP 跟它说话。**不再需要 Java**（旧的 opendataloader 引擎已经删掉了）。

## 三个踩过的坑（改之前先看这里）

**1. 不能用 `env!("CARGO_MANIFEST_DIR")` 找后端。** 那是编译期常量，指向打包机上的源码
目录。装到别人电脑上那个路径根本不存在，后端起不来、界面一直转圈。运行时必须用
`app.path_resolver().resource_dir()`，开发环境再回退到仓库路径（见 `main.rs` 的 `locate()`）。

**2. 不能用 venv，也不能直接调 `bin/mineru`。** venv 的 `bin/python` 是指向外部解释器的
符号链接，pip 装出来的脚本 shebang 里又写死了打包机的绝对路径——搬到别人电脑上两样都废。
所以直接复制 uv 管理的独立解释器（python-build-standalone，本身可重定位，实测复制到别处
`sys.prefix` 会自动跟随），把包装进它自己的 site-packages，运行时一律用
`python -m mineru.cli.client` / `python -m mineru.cli.fast_api` 调（见 `mineru_backend.mineru_cmd()`）。

**3. 不能抄 homebrew 的 pandoc。** 它链到 `/opt/homebrew/opt/gmp/lib/libgmp.10.dylib`，
别人机器上没装 homebrew 就崩。打包脚本从 pandoc 官方 release 下载自包含的 macOS 版本。

另外两点：
- payload 必须放在 `src-tauri/` 里面。Tauri 1 对 `../` 之外的资源会加 `_up_` 路径前缀，
  结构就乱了。
- p2w 和 MinerU 的依赖**不冲突**（实测只差一个 pymupdf），随包时共用同一个 Python 环境，
  不用装两份 torch。开发时仍分开（`.venv-mineru`），免得动到正在用的环境。

## 模型：捆还是不捆

识别模型 2.2 GB，是安装包体积的大头。

| | 安装包 | 第一次用 | 适合 |
|---|---|---|---|
| 默认 | ~1.4 GB | 要联网下 2.2 GB | 网络还行、能等一次 |
| `--with-models` | ~3.6 GB | 直接能用 | U 盘或局域网分发、机房断网 |

国内下载慢的话，让用户设 `HF_ENDPOINT=https://hf-mirror.com`，或者干脆用 `--with-models`。
首次引导页已经写明"要下载约 2.4 GB"，不会让人干等着以为卡死。

## 签名与公证（缺东西，做不了）

现在打出来的 `.app` **没有签名**，别人第一次打开会被 Gatekeeper 拦下（要右键→打开，
或去"系统设置 → 隐私与安全性"放行）。自己人用没问题，正经分发需要公证：

```bash
codesign --deep --force --options runtime \
         --sign "Developer ID Application: <名字> (<TEAMID>)" 抄录.app
xcrun notarytool submit 抄录.app --apple-id <id> --team-id <TEAMID> \
      --password <app专用密码> --wait
xcrun stapler staple 抄录.app
```

**需要用户提供**：Apple 开发者账号（99 美元/年）。没有账号这步做不了，只能交付未签名的 `.app`。

## Windows

**需要一台 Windows 机器**（或 GitHub Actions 的 windows runner）——Tauri 不能交叉编译，
Windows 包必须在 Windows 上打。流程和 mac 一样，三处不同：

- 独立 Python 换成 `cpython-3.12-windows-x86_64`，解释器在 `python\python.exe`（没有 `bin/`）
- pandoc 下载 `pandoc-<ver>-windows-x86_64.zip` 里的 `pandoc.exe`
- `tauri build` 产出 `.msi`；避免 SmartScreen 警告需要代码签名证书（另需购买）

`main.rs` 的 `locate()` 目前只找 `python/bin/python3.12`，做 Windows 包时要补上
`python\python.exe` 这一支。

## 实测结果（2026-07-29）

`.app` 1.4 GB，`.dmg` **361 MB**（压缩率很高，网盘/微信都能传）。

已经验证过随包环境是自足的——在**清空的环境**里跑完了一次真实转换：

```bash
env -i HOME="$HOME" PATH="/usr/bin:/bin" \
    PYTHONPATH="<app>/Contents/Resources/payload/src" \
    P2W_MINERU_PYTHON="<app>/Contents/Resources/payload/python/bin/python3.12" \
    "<app>/Contents/Resources/payload/python/bin/python3.12" -m p2w.cli 试卷.png -o out
```

`PATH` 只有 `/usr/bin:/bin`，既没有 homebrew 也没有 conda，转换照样成功、公式照样是
10 个 OMML（含 4 个分式）——说明用的确实是随包的 python / MinerU / pandoc。

**仍未验证**：没有在一台真正干净的电脑上装过。上面的测试排除了 PATH 的干扰，但排除不了
`~/.cache/huggingface`（模型缓存，设计上就该复用）和其他用户级残留。正经分发前还是得找
一台没装过 Python 的 Mac 走一遍完整流程：双击打开、选文件、转换、在 Word 里点开公式编辑。
