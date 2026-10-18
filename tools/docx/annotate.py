"""给上手文档的截图画红色编号标注框。

用法：python tools/docx/annotate.py [截图目录] [输出目录]
默认：_work/docshots_quickstart（由 tests/doc_shots_quickstart.py 生成，含 rects.json）→ docs/上手文档_图片
"""
import json
import os
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "_work", "docshots_quickstart")
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "docs", "上手文档_图片")
NO_MARK = ("q04_去水印预览", "q12_查重报告")   # 只看效果的截图不画框

os.makedirs(OUT, exist_ok=True)
rects = json.load(open(os.path.join(SH, "rects.json"), encoding="utf-8"))
font = ImageFont.truetype(r"C:\Windows\Fonts\msyhbd.ttc", 22)
RED = (255, 59, 48)
for name in sorted(os.listdir(SH)):
    if not name.endswith(".png"):
        continue
    stem = name[:-4]
    im = Image.open(os.path.join(SH, name)).convert("RGB")
    d = ImageDraw.Draw(im)
    marks = [] if stem in NO_MARK else rects.get(stem, [])
    for k, (x, y, w, h) in enumerate(marks, 1):
        pad = 4
        x0, y0, x1, y1 = max(2, x - pad), max(2, y - pad), min(im.width - 3, x + w + pad), min(im.height - 3, y + h + pad)
        d.rounded_rectangle((x0, y0, x1, y1), radius=8, outline=RED, width=4)
        r = 17
        cx = min(max(r + 2, x0 + 2), im.width - r - 2)
        cy = min(max(r + 2, y0 + 2), im.height - r - 2)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=RED, outline=(255, 255, 255), width=3)
        d.text((cx, cy - 1), str(k), font=font, fill=(255, 255, 255), anchor="mm")
    im.save(os.path.join(OUT, stem + ".jpg"), quality=92)
print("ok", OUT, len([n for n in os.listdir(OUT) if n.endswith(".jpg")]), "张")
