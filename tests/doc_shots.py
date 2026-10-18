"""为使用文档截图（离屏渲染，载入测试素材，模拟真实操作状态）。"""
import os, sys, time, glob
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from mixer import paths as _paths
if os.environ.get("BVM_DOC_DATA"):
    _paths._DATA_DIR = os.path.abspath(os.environ["BVM_DOC_DATA"])
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtGui import QFontDatabase, QFont
app = QApplication(sys.argv)
for fp in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc"): QFontDatabase.addApplicationFont(fp)
f = QFont("Microsoft YaHei UI"); f.setPointSizeF(9.5); app.setFont(f)
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
from mixer import settings, textstyle
settings.get()["agree"] = True
from ui import theme
settings.apply(); theme.set_palette("dark"); app.setStyleSheet(theme.qss())
from ui.main_window import MainWindow
from mixer.models import Region
OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_work", "docshots"))
TM = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_work", "testmedia"))
def wait(sec, cond=None):
    t = time.time()
    while time.time() - t < sec:
        app.processEvents(); time.sleep(0.02)
        if cond and cond(): return True
def shot(widget, name):
    wait(0.6); widget.grab().save(os.path.join(OUT, name + ".png"))
w = MainWindow(); w.resize(1440, 900); w.show(); wait(1)
mats = [os.path.join(TM, f) for f in ("A_横屏_水印.mp4", "B_竖屏_无音轨.mp4", "C_720p60_单声道.mp4", "D_竖屏短.mp4", "E 4比3 带空格.mp4", "photo_landscape.jpg")]
mix = w.mix
mix.table.clear_all(); mix.cleanup_map.clear(); mix.table.add_paths(mats)
mix.bgms.set_paths([os.path.join(TM, "bgm1.mp3")]); mix.badges.set_paths([os.path.join(TM, "product_white_bg.png")])
mix.name.setText("618好物推荐"); mix.count.setValue(20); mix.limit_min.setValue(1.0); mix.limit_float.setValue(20)
mix.titles.setPlainText("今日爆款好物推荐\n这个价格真的绝了\n闭眼入不踩雷"); mix._set_style({})
wait(4, lambda: len(mix.table.infos) >= len(mats))
mix.settings_tabs.setCurrentIndex(0); shot(w, "01_混剪生产")
mix.settings_tabs.setCurrentIndex(1); mix._combo_set(mix.trans_mode, "mixed"); mix._combo_set(mix.filter_mode, "random"); mix.filter_list.set_checked(["清新", "日系", "质感", "明亮"]); shot(w, "02_画面设置")
mix.settings_tabs.setCurrentIndex(2); shot(w, "03_包装设置")
mix.settings_tabs.setCurrentIndex(3); shot(w, "04_音频设置")
mix.settings_tabs.setCurrentIndex(4); shot(w, "05_输出设置")
# 素材处理
A = mats[0]
w.studio.profiles.clear()
w._open_studio([A]); wait(4, lambda: w.studio.raw_png is not None and not w.studio._pending_profile)
st = w.studio
st.canvas.clear_regions()
st.canvas.add_region(Region(x=0.735, y=0.022, w=0.255, h=0.075, method="delogo"))
st.canvas.add_region(Region(x=0.025, y=0.82, w=0.13, h=0.13, method="mosaic", strength=20, t0=2, t1=8))
wait(1); shot(w, "06_素材处理_框选去水印")
st.pv_btn.setChecked(True); wait(8, lambda: "效果预览（" in st.status.text()); shot(w, "07_素材处理_效果预览")
st.pv_btn.setChecked(False); wait(2)
st.tabs.setCurrentIndex(2); wait(5); shot(w, "08_素材处理_滤镜")
st.tabs.setCurrentIndex(4); wait(3)
from mixer import stickers as stk
lib = stk.library()
st.ctrl.add_sticker([s for s in lib["电商标签"] if s.name == "爆款"][0]); wait(0.3)
g = st.canvas.overlays[-1]; g.item.cx, g.item.cy, g.item.w = 0.15, 0.25, 0.16; g.apply_geometry()
st.text_editor.set_text("夏日清凉好物"); st.text_editor.set_style(textstyle.preset_style("综艺花字", size=90).to_dict()); st.ctrl.add_text(); wait(0.3)
g2 = st.canvas.overlays[-1]; g2.item.cx, g2.item.cy = 0.5, 0.82; g2.apply_geometry(); st.canvas.scene().clearSelection(); g.setSelected(True)
wait(1); shot(w, "09_素材处理_贴纸")
st.tabs.setCurrentIndex(5); wait(2); shot(w, "10_素材处理_文字")
# 包装设计器
from ui.designer import PackageDesigner
cfg = mix.get_config()
dlg = PackageDesigner(cfg, {}, cfg.materials, w); dlg.resize(1280, 820); dlg.show(); wait(5, lambda: dlg.bg_png is not None)
dlg.ctrl.add_sticker([s for s in lib["互动引导"] if s.name == "点赞按钮"][0]); g = dlg.canvas.overlays[-1]; g.item.cx, g.item.cy, g.item.w = 0.72, 0.86, 0.36; g.apply_geometry()
dlg.ctrl.add_sticker([s for s in lib["表情_爱心符号"] if s.name == "火"][0]); g = dlg.canvas.overlays[-1]; g.item.cx, g.item.cy, g.item.w = 0.15, 0.36, 0.14; g.apply_geometry()
dlg.te.set_text("第{序号}期 · 好物分享"); dlg.te.set_style(textstyle.preset_style("红底白字", size=46).to_dict()); dlg.ctrl.add_text()
g = dlg.canvas.overlays[-1]; g.item.cx, g.item.cy = 0.5, 0.95; g.apply_geometry()
wait(1); shot(dlg, "11_包装设计器")
mix._set_style(dlg.current_style().to_dict()); dlg.close()
# 贴纸库
w.nav.setCurrentRow(2); wait(3); shot(w, "12_贴纸库")
w.stickers.cats.setCurrentRow(5); wait(3); shot(w, "13_贴纸库_3D表情")
# 运行中
w.nav.setCurrentRow(0); mix.count.setValue(4); mix.limit_min.setValue(0.3); mix._combo_set(mix.canvas, "720x1280")
mix.out_root.setText(os.environ.get("BVM_DOC_OUT") or os.path.abspath(os.path.join(OUT, "..", "out_doc")))
mix.start(); wait(25, lambda: mix.runner is not None and mix.runner.done >= 1); shot(w, "14_生成进度")
wait(240, lambda: mix.runner is None and mix.back_btn.isEnabled()); shot(w, "15_生成完成")
w.nav.setCurrentRow(3); wait(1); shot(w, "16_任务记录")
w.nav.setCurrentRow(4); wait(2); shot(w, "17_设置")
w.nav.setCurrentRow(5); wait(1); shot(w, "18_帮助")
print("done", getattr(mix, "runner_dir", ""))
