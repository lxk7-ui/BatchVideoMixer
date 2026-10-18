"""生成程序图标 .ico（多尺寸）。"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QBuffer, QIODevice
app = QApplication([])
from ui import theme
from PIL import Image
import io
out = sys.argv[1]
icon = theme.app_icon()
pm = icon.pixmap(256, 256)
buf = QBuffer(); buf.open(QIODevice.WriteOnly); pm.save(buf, "PNG")
im = Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA")
im.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
im.save(out.replace(".ico", ".png"))
print("icon ok", out)
