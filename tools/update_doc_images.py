"""把截图脚本的输出整理进 docs/（先运行 tests/doc_shots.py 和 tests/doc_shots_quickstart.py）：

  _work/docshots/*.png                         → docs/使用文档_图片/*.jpg
  _work/docshots_quickstart/q11、q12            → docs/使用文档_图片/22_防重复设置.jpg、23_查重报告.jpg
  _work/docshots_quickstart/*.png + rects.json → docs/上手文档_图片/*.jpg（画红色编号标注）

20_成片示例.jpg、21_去水印前后.jpg 是用成片画面拼的示意图，不在这里生成。
"""
import os
import subprocess
import sys

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANUAL_SHOTS = os.path.join(ROOT, "_work", "docshots")
QS_SHOTS = os.path.join(ROOT, "_work", "docshots_quickstart")
MANUAL_IMG = os.path.join(ROOT, "docs", "使用文档_图片")


def to_jpg(src, dst):
    Image.open(src).convert("RGB").save(dst, quality=90, optimize=True)


def main():
    n = 0
    if os.path.isdir(MANUAL_SHOTS):
        for name in sorted(os.listdir(MANUAL_SHOTS)):
            if name.endswith(".png"):
                to_jpg(os.path.join(MANUAL_SHOTS, name), os.path.join(MANUAL_IMG, name[:-4] + ".jpg"))
                n += 1
    if os.path.isdir(QS_SHOTS):
        for src, dst in (("q11_防重复", "22_防重复设置"), ("q12_查重报告", "23_查重报告")):
            p = os.path.join(QS_SHOTS, src + ".png")
            if os.path.isfile(p):
                to_jpg(p, os.path.join(MANUAL_IMG, dst + ".jpg"))
                n += 1
        subprocess.run([sys.executable, os.path.join(ROOT, "tools", "docx", "annotate.py"), QS_SHOTS,
                        os.path.join(ROOT, "docs", "上手文档_图片")], check=True)
    print("使用文档图片更新", n, "张")


if __name__ == "__main__":
    main()
