# -*- coding: utf-8 -*-
"""
生成 PyInstaller 打包用的应用图标 app.ico。
复用 tray_app.make_icon_image() 的绘制逻辑，保证托盘图标 / exe 图标一致。

用法：python build_icon.py
产物：app.ico（与脚本同目录）
"""

import os
import sys

# 把当前目录加入 sys.path，确保能 import tray_app
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from tray_app import make_icon_image  # noqa: E402


def main():
    img = make_icon_image(locked=False)
    # ico 需要多个尺寸，PyInstaller / Windows Explorer 会根据场景选用
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    out_path = os.path.join(_HERE, "app.ico")
    img.save(out_path, format="ICO", sizes=sizes)
    print(f"[OK] Icon saved: {out_path}")


if __name__ == "__main__":
    main()
