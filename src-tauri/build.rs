fn main() {
    // 前端在 src-tauri 之外，cargo 默认不追踪；显式监听整个 frontend 目录，
    // 否则改了前端（含预编译的 bundle.js）后 Tauri 仍嵌入旧快照，导致白屏。
    println!("cargo:rerun-if-changed=../src/p2w_gui/frontend");
    tauri_build::build()
}
