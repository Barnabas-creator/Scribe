# 图标源图

`icon-1024.png` 是 1024×1024 的**满幅正方形**源图——不留透明边距、不自己切圆角，
四角用同色绿渐变填满。**这是给 macOS 26 的**：系统会给旧式 icns 自己套标准
squircle 遮罩，图不满幅的话，系统会把它垫在一块白色底板上（实机看到的"白框"就是它）。底部 "PDF to Word" 字样是 Helvetica Neue Bold 96pt + 轻投影，
PIL 合成，别用 AI 生成——生成的文字会拼错。无字版底图在 tmp/icon/icon-1024.png。

重新生成各尺寸与 icns：

```bash
python3 - <<'PY'
from PIL import Image
src = Image.open("src-tauri/icons/source/icon-1024.png").convert("RGBA")
for name, s in [("32x32.png",32), ("128x128.png",128), ("128x128@2x.png",256),
                ("icon.png",512), ("Square107x107Logo.png",107)]:
    src.resize((s,s), Image.LANCZOS).save("src-tauri/icons/"+name)
PY
# icns：iconutil 要一个 *.iconset 目录，命名必须是 icon_16x16 / icon_16x16@2x / …
iconutil -c icns <iconset 目录> -o src-tauri/icons/icon.icns
```

32px 下细节会糊（纸上三条线加分数式挤在一起）。Dock 与访达常见尺寸是 128px 以上，
真要照顾 32px 就得另画一版更简的，别指望缩放。
