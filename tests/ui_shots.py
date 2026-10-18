"""界面截图（离屏渲染），用于检查布局。"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_work", "shots")
app = QApplication(sys.argv)
from PySide6.QtGui import QFontDatabase
for fp in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\seguisym.ttf", r"C:\Windows\Fonts\segoeui.ttf"):
    QFontDatabase.addApplicationFont(fp)
f = QFont("Microsoft YaHei UI"); f.setPointSizeF(9.5); app.setFont(f)
from mixer import settings
settings.get()["agree"] = True
from ui import theme
settings.apply(); theme.set_palette(settings.get().get("theme", "dark")); app.setStyleSheet(theme.qss())
from ui.main_window import MainWindow
w = MainWindow(); w.resize(1440, 900); w.show()
TM = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_work", "testmedia"))
mats = [os.path.join(TM, f) for f in sorted(os.listdir(TM)) if f.lower().endswith((".mp4", ".jpg"))]
w.mix.table.add_paths(mats)
w.mix.bgms.add_paths([os.path.join(TM, "bgm1.mp3")])
def wait(sec):
    t = time.time()
    while time.time() - t < sec:
        app.processEvents(); time.sleep(0.02)
wait(3)
def shot(name):
    wait(0.5); w.grab().save(os.path.join(OUT, name + ".png"))
shot("01_mix_basic")
w.mix.settings_tabs.setCurrentIndex(1); shot("02_mix_picture")
w.mix.settings_tabs.setCurrentIndex(2); shot("03_mix_package")
w.mix.settings_tabs.setCurrentIndex(3); shot("04_mix_audio")
w.mix.settings_tabs.setCurrentIndex(4); shot("05_mix_output")
w._open_studio([os.path.join(TM, "A_横屏_水印.mp4")])
wait(3); shot("06_studio")
w.studio.tabs.setCurrentIndex(2); wait(3); shot("07_studio_filter")
w.studio.tabs.setCurrentIndex(4); wait(2); shot("08_studio_sticker")
w.studio.tabs.setCurrentIndex(5); wait(2); shot("09_studio_text")
w.nav.setCurrentRow(2); wait(2); shot("10_stickers")
w.nav.setCurrentRow(3); shot("11_history")
w.nav.setCurrentRow(4); shot("12_settings")
w.nav.setCurrentRow(5); shot("13_help")
print("done")
