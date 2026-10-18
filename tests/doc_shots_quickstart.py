"""为上手文档截图，同时导出关键控件在截图中的位置（rects.json），再用 tools/docx/annotate.py 画编号标注。

用法：BVM_DOC_DATA=_work/data_doc python tests/doc_shots_quickstart.py   （指定干净的数据目录，任务记录里只有演示任务）
"""
import os, sys, time, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from mixer import paths as _paths
if os.environ.get("BVM_DOC_DATA"):
    _paths._DATA_DIR = os.path.abspath(os.environ["BVM_DOC_DATA"])
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton, QWidget
from PySide6.QtGui import QFontDatabase, QFont
from PySide6.QtCore import QPoint
app = QApplication(sys.argv)
for fp in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc"): QFontDatabase.addApplicationFont(fp)
f = QFont("Microsoft YaHei UI"); f.setPointSizeF(9.5); app.setFont(f)
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
from mixer import settings, textstyle, paths
settings.get()["agree"] = True
from ui import theme
settings.apply(); theme.set_palette("dark"); app.setStyleSheet(theme.qss())
from ui.main_window import MainWindow
from mixer.models import Region
OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_work", "docshots_quickstart"))
os.makedirs(OUT, exist_ok=True)
TM = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_work", "testmedia"))
RECTS = {}
def wait(sec, cond=None):
    t = time.time()
    while time.time() - t < sec:
        app.processEvents(); time.sleep(0.02)
        if cond and cond(): return True
def rect(top, w):
    p = w.mapTo(top, QPoint(0, 0)); return [p.x(), p.y(), w.width(), w.height()]
def btn(parent, text):
    for b in parent.findChildren(QPushButton):
        if b.text().strip() == text and b.isVisible(): return b
    raise KeyError(text)
def shot(top, name, marks):
    wait(0.7); top.grab().save(os.path.join(OUT, name + ".png"))
    RECTS[name] = [rect(top, w) for w in marks]
w = MainWindow(); w.resize(1440, 900); w.show(); wait(1)
mats = [os.path.join(TM, f) for f in ("A_横屏_水印.mp4", "B_竖屏_无音轨.mp4", "C_720p60_单声道.mp4", "D_竖屏短.mp4", "E 4比3 带空格.mp4", "photo_landscape.jpg")]
mix = w.mix
mix.table.clear_all(); mix.cleanup_map.clear(); mix.table.add_paths(mats)
mix.bgms.set_paths([os.path.join(TM, "bgm1.mp3")]); mix.badges.set_paths([os.path.join(TM, "product_white_bg.png")])
mix.name.setText("618好物推荐"); mix.count.setValue(20); mix.limit_min.setValue(1.0); mix.limit_float.setValue(20)
mix.titles.setPlainText("今日爆款好物推荐\n这个价格真的绝了\n闭眼入不踩雷")
mix._set_style({})
wait(4, lambda: len(mix.table.infos) >= len(mats))
mix.settings_tabs.setCurrentIndex(0)
time_card = mix.limit_min.parentWidget()
shot(w, "q01_混剪主界面", [mix.table, time_card, mix.count, mix.start_btn])
mix.settings_tabs.setCurrentIndex(2); wait(0.5)
shot(w, "q02_包装", [btn(mix, "设计包装…"), mix.titles])
# 素材处理
A = mats[0]
st = w.studio
st.profiles.clear()
w._open_studio([A]); wait(4, lambda: st.raw_png is not None and not st._pending_profile)
st.canvas.clear_regions()
st.canvas.add_region(Region(x=0.735, y=0.022, w=0.255, h=0.075, method="delogo"))
st.tabs.setCurrentIndex(0); wait(1)
shot(w, "q03_去水印", [btn(st, "框选区域"), st.r_method, st.pv_btn, st.send_btn])
st.pv_btn.setChecked(True); wait(8, lambda: "效果预览（" in st.status.text())
shot(w, "q04_去水印预览", [st.canvas])
st.pv_btn.setChecked(False); wait(2)
# 包装设计器
from ui.designer import PackageDesigner
from mixer import stickers as stk
lib = stk.library()
cfg = mix.get_config()
dlg = PackageDesigner(cfg, {}, cfg.materials, w); dlg.resize(1280, 820); dlg.show(); wait(5, lambda: dlg.bg_png is not None)
dlg.ctrl.add_sticker([s for s in lib["互动引导"] if s.name == "点赞按钮"][0]); g = dlg.canvas.overlays[-1]; g.item.cx, g.item.cy, g.item.w = 0.7, 0.86, 0.36; g.apply_geometry()
dlg.ctrl.add_sticker([s for s in lib["表情_爱心符号"] if s.name == "火"][0]); g = dlg.canvas.overlays[-1]; g.item.cx, g.item.cy, g.item.w = 0.15, 0.36, 0.14; g.apply_geometry()
dlg.te.set_text("第{序号}期 · 好物分享"); dlg.te.set_style(textstyle.preset_style("红底白字", size=46).to_dict()); dlg.ctrl.add_text()
g = dlg.canvas.overlays[-1]; g.item.cx, g.item.cy = 0.5, 0.95; g.apply_geometry()
dlg.canvas.scene().clearSelection(); dlg.canvas.overlays[0].setSelected(True); wait(1)
shot(dlg, "q05_包装设计器", [dlg.browser.grid, dlg.tabs.tabBar(), dlg.props, btn(dlg, "确定使用")])
mix._set_style(dlg.current_style().to_dict()); dlg.close()
# 生成进度
w.nav.setCurrentRow(0); mix.count.setValue(4); mix.limit_min.setValue(0.3); mix._combo_set(mix.canvas, "720x1280")
mix.out_root.setText(os.environ.get("BVM_DOC_OUT") or os.path.abspath(os.path.join(OUT, "..", "out")))
mix.start(); wait(120, lambda: mix.runner is not None and mix.runner.done >= 1)
shot(w, "q06_生成进度", [mix.pbar, mix.items, mix.stop_btn, mix.open_btn])
wait(240, lambda: mix.runner is None and mix.back_btn.isEnabled())
shot(w, "q07_生成完成", [mix.run_title, mix.open_btn])
w.nav.setCurrentRow(3); wait(1); w.history.table.selectRow(0); wait(0.5)
shot(w, "q08_任务记录", [w.history.table, btn(w.history, "继续未完成")])
w.nav.setCurrentRow(4); wait(2)
shot(w, "q09_设置自检", [w.settings_page.st_btn])
w.nav.setCurrentRow(2); wait(3)
shot(w, "q10_贴纸库", [btn(w.stickers, "导入贴纸…")])

# ---------------- v1.1：防重复
w.nav.setCurrentRow(0); wait(0.5)
mix.rstack.setCurrentWidget(mix.settings_view); mix.settings_tabs.setCurrentIndex(0); wait(0.5)
DM = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_work", "dedup_media"))
dmats = [os.path.join(DM, f) for f in ("S1_原素材.mp4", "S2_原素材.mp4", "S3_原素材.mp4", "S3_副本(重复导入).mp4", "S4_原素材.mp4", "S5_原素材.mp4", "W_内部重复.mp4", "X_拼接合集.mp4", "Y_拼接合集.mp4", "Z_镜像竖屏盗版.mp4")]
mix.table.clear_all(); mix.table.add_paths(dmats)
wait(30, lambda: all(p in mix.table.infos for p in dmats))
from mixer import dedup as _dd
from ui.dup_dialog import DupReportDialog
from mixer import probe as _pb
idx = _dd.analyze_library([_pb.probe(p) for p in dmats], "standard")
for p in dmats:
    mix.table.set_dup(p, idx.ratio.get(p, 0.0))
area = mix.settings_tabs.widget(0)
sa = area if hasattr(area, "ensureWidgetVisible") else None
dcard = mix.dedup_on.parentWidget()
if sa is not None:
    sa.ensureWidgetVisible(dcard, 0, 40)
wait(1)
shot(w, "q11_防重复", [mix.table, dcard, btn(w.mix, "检查重复")])
rep = DupReportDialog(idx, w); rep.resize(900, 600); rep.show(); wait(1.5)
shot(rep, "q12_查重报告", [])
rep.close()
json.dump(RECTS, open(os.path.join(OUT, "rects.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("v11 done")

