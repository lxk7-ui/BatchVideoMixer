"""把 PDF 每页渲染成小图并拼成联系表（每张 4×2 页），用于目视检查版面。用法：python contact.py x.pdf 输出前缀 [dpi]"""
import sys

import pymupdf
from PIL import Image

pdf, prefix = sys.argv[1], sys.argv[2]
dpi = int(sys.argv[3]) if len(sys.argv) > 3 else 48
doc = pymupdf.open(pdf)
pages = []
for pg in doc:
    pix = pg.get_pixmap(dpi=dpi)
    pages.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
cols, rows = 4, 2
w, h = pages[0].size
gap = 10
sheets = 0
for s in range(0, len(pages), cols * rows):
    sheet = Image.new("RGB", (cols * w + (cols + 1) * gap, rows * h + (rows + 1) * gap), (120, 120, 120))
    for k, im in enumerate(pages[s:s + cols * rows]):
        r, c = divmod(k, cols)
        sheet.paste(im, (gap + c * (w + gap), gap + r * (h + gap)))
    sheets += 1
    sheet.save(f"{prefix}_{sheets:02d}.png")
print("pages", len(pages), "sheets", sheets)
