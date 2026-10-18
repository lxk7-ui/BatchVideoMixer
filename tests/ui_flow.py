"""界面功能联调（离屏）：混剪生成、素材处理去水印预览/导出、包装设计器、断点续跑。"""
import os, sys, time, glob, traceback
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from PySide6.QtWidgets import QApplication, QMessageBox, QDialog, QInputDialog
from PySide6.QtGui import QFontDatabase, QFont
app = QApplication(sys.argv)
for fp in (r"C:\Windows\Fonts\msyh.ttc",): QFontDatabase.addApplicationFont(fp)
errors = []
def hook(t, v, tb):
    errors.append("".join(traceback.format_exception(t, v, tb))); print("!!! EXC", v)
sys.excepthook = hook
# 自动关闭所有消息框
QMessageBox.information = staticmethod(lambda *a, **k: print("MSG:", a[2] if len(a) > 2 else a))
QMessageBox.warning = staticmethod(lambda *a, **k: print("WARN:", a[2] if len(a) > 2 else a))
QMessageBox.critical = staticmethod(lambda *a, **k: print("CRIT:", a[2] if len(a) > 2 else a))
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
from mixer import settings, probe
settings.get()["agree"] = True
from ui import theme
settings.apply(); theme.set_palette("dark"); app.setStyleSheet(theme.qss())
from ui.main_window import MainWindow
from mixer.models import Region
TM = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_work", "testmedia"))
OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_work", "out_ui")); os.makedirs(OUT, exist_ok=True)
def wait(sec, cond=None):
    t = time.time()
    while time.time() - t < sec:
        app.processEvents(); time.sleep(0.02)
        if cond and cond(): return True
    return False
w = MainWindow(); w.show(); wait(1)
A = os.path.join(TM, "A_横屏_水印.mp4")
mats = [os.path.join(TM, f) for f in ("A_横屏_水印.mp4", "B_竖屏_无音轨.mp4", "C_720p60_单声道.mp4", "E 4比3 带空格.mp4", "photo_landscape.jpg")]
mix = w.mix
mix.table.clear_all(); mix.table.add_paths(mats); mix.bgms.set_paths([os.path.join(TM, "bgm1.mp3")])
mix.badges.set_paths([os.path.join(TM, "product_white_bg.png")])
wait(3, lambda: len(mix.table.infos) >= len(mats))
print("probed", len(mix.table.infos))
# ---- 素材处理：去水印区域 + 预览 + 发送到混剪
w._open_studio([A]); wait(3, lambda: w.studio.raw_png is not None and not w.studio._pending_profile)
st = w.studio
st.canvas.add_region(Region(x=0.74, y=0.025, w=0.25, h=0.07, method="delogo"))
st._store_current()
print("profile", st.profiles.get(A, {}).get("regions"))
st.pv_btn.setChecked(True); wait(6, lambda: "效果预览（" in st.status.text())
print("studio status:", st.status.text())
st.pv_btn.setChecked(False); wait(1)
# 贴纸 + 文字
from mixer import stickers as stk
s0 = list(stk.library().values())[0][0]
st.ctrl.add_sticker(s0); st.text_editor.set_text("测试花字"); st.ctrl.add_text(); wait(0.5)
print("overlays", len(st.ctrl.items()))
st._send_cleanup(); wait(0.5)
print("mix cleanup_map", list(mix.cleanup_map.keys()))
# 导出当前（同步等待）
settings.get()["studio_out_dir"] = OUT
st._export(False); wait(60, lambda: len(glob.glob(os.path.join(OUT, "*_处理*.mp4"))) > 0 and not glob.glob(os.path.join(OUT, "*.tmp.mp4")))
print("studio export:", glob.glob(os.path.join(OUT, "*_处理*.mp4")))
# ---- 包装设计器
from ui.designer import PackageDesigner
cfg = mix.get_config()
dlg = PackageDesigner(cfg, {}, cfg.materials, w); dlg.show(); wait(4, lambda: dlg.bg_png is not None)
dlg.ctrl.add_sticker(list(stk.library().values())[1][0]); dlg.te.set_text("第{序号}条"); dlg.ctrl.add_text()
dlg.fp.set_value("清新", 0.8); dlg._changed()
dlg.pv.setChecked(True); wait(5, lambda: "效果预览" in dlg.status.text())
print("designer status:", dlg.status.text())
style = dlg.current_style().to_dict(); dlg.close()
mix._set_style(style); print(mix.style_label.text())
# ---- 混剪生成
mix.name.setText("界面联调"); mix.count.setValue(3); mix.limit_min.setValue(0.3); mix.limit_float.setValue(20)
mix.out_root.setText(OUT); mix.titles.setPlainText("爆款推荐\n今日好物")
mix._combo_set(mix.trans_mode, "mixed"); mix._combo_set(mix.canvas, "720x1280"); mix._combo_set(mix.filter_mode, "random")
mix.start(); wait(240, lambda: mix.runner is None and mix.back_btn.isEnabled())
print("run title:", mix.run_title.text())
d = getattr(mix, "runner_dir", None); print("dir", d)
files = glob.glob(os.path.join(d, "成片", "*.mp4")) if d else []
for f in files:
    i = probe.probe(f, use_cache=False); print("  ", os.path.basename(f), f"{i.duration:.2f}s", i.res_text(), i.has_audio)
# ---- 断点续跑：删除一个成片后继续
if files:
    os.remove(files[0])
    import json
    w.history.reload(); w._resume(d); wait(120, lambda: mix.runner is None and mix.back_btn.isEnabled())
    print("resume:", mix.run_title.text(), os.path.exists(files[0]))
w.nav.setCurrentRow(3); wait(0.5); print("history rows", w.history.table.rowCount())
w.mix.save_last(); w.close()
print("ERRORS:", len(errors))
for e in errors: print(e)
