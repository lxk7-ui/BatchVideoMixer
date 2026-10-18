"""成片包装设计器：在“成片画布”上摆放贴纸、文字、角标，设置滤镜与调节。"""
import os

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QVBoxLayout, QLabel, QComboBox, QSlider, QTabWidget, QWidget,
                               QPushButton, QInputDialog, QMenu, QFileDialog)

from mixer import studio, probe, paths
from mixer.models import StylePreset, load_json, save_json
from . import theme
from .canvas import EditorCanvas
from .overlay_ctrl import OverlayController
from .panels import FilterPanel, AdjustPanel, StickerBrowser, TextStyleEditor, OverlayProps, scroll_wrap
from .widgets import Card, button, hint, msg, IMAGE_FILTER
from .workers import run_bg, LatestOnly, Debouncer
from .page_studio import tpl_dir, list_templates, play_video


class PackageDesigner(QDialog):
    def __init__(self, cfg, style: dict, materials, parent=None):
        super().__init__(parent)
        self.setWindowTitle("成片包装设计")
        self.resize(1280, 820)
        self.cfg = cfg
        self.W, self.H = cfg.canvas_size(probe.probe(materials[0]) if materials else None)
        self.materials = [m for m in materials if probe.probe(m).ok][:200]
        self.style = StylePreset.from_dict(style or {})
        self.bg_png = None
        self.preview_on = False
        self.req = LatestOnly()
        self._build()
        self.set_style(self.style)
        self._load_bg()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        top = QHBoxLayout()
        t = QLabel("成片包装设计")
        t.setObjectName("Title")
        top.addWidget(t)
        top.addWidget(hint(f"画布 {self.W}×{self.H}。在这里摆放的贴纸/文字/滤镜会应用到每一条成片；"
                           "文字支持变量 {序号} {日期} {素材名} {任务名}；贴纸可勾选“每条成片随机位置”。"), 1)
        root.addLayout(top)
        body = QHBoxLayout()
        root.addLayout(body, 1)
        left = Card()
        bar = QHBoxLayout()
        bar.addWidget(QLabel("背景素材"))
        self.mat = QComboBox()
        for m in self.materials:
            self.mat.addItem(os.path.basename(m), m)
        self.mat.currentIndexChanged.connect(lambda *_: self._load_bg())
        self.mat.setMinimumWidth(200)
        bar.addWidget(self.mat)
        self.tslider = QSlider(Qt.Horizontal)
        self.tslider.setRange(0, 100)
        self.tslider.setValue(25)
        self.tslider.setFixedWidth(160)
        self.deb = Debouncer(200, self._load_bg, self)
        self.tslider.valueChanged.connect(lambda *_: self.deb())
        bar.addWidget(self.tslider)
        bar.addStretch(1)
        self.pv = button("效果预览", "eye")
        self.pv.setCheckable(True)
        self.pv.toggled.connect(self._toggle_preview)
        bar.addWidget(self.pv)
        bar.addWidget(button("播放5秒", "play", slot=self._play))
        left.add(bar)
        self.canvas = EditorCanvas()
        self.canvas.allow_regions = False
        self.canvas.setMinimumSize(480, 520)
        left.add(self.canvas)
        self.status = QLabel("")
        self.status.setObjectName("Sub")
        left.add(self.status)
        body.addWidget(left, 1)

        right = QWidget()
        right.setFixedWidth(380)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        tabs = QTabWidget()
        tabs.setObjectName("Panel")
        sw = QWidget()
        sl = QVBoxLayout(sw)
        sl.setContentsMargins(0, 0, 0, 0)
        self.browser = StickerBrowser()
        sl.addWidget(self.browser, 1)
        sl.addWidget(button("添加图片（Logo/产品图）", "add", slot=self._add_image))
        tabs.addTab(sw, "贴纸")
        tw = QWidget()
        tl = QVBoxLayout(tw)
        tl.setContentsMargins(0, 0, 0, 0)
        self.te = TextStyleEditor()
        tl.addWidget(self.te)
        tl.addWidget(button("添加文字到画面", "text", kind="Primary", slot=lambda: self.ctrl.add_text()))
        tl.addStretch(1)
        tabs.addTab(scroll_wrap(tw), "文字")
        self.fp = FilterPanel()
        self.fp.changed.connect(self._changed)
        tabs.addTab(self.fp, "滤镜")
        self.ap = AdjustPanel()
        self.ap.changed.connect(self._changed)
        tabs.addTab(scroll_wrap(self.ap), "调节")
        tabs.currentChanged.connect(lambda i: self._thumbs() if tabs.widget(i) is self.fp else None)
        self.tabs = tabs
        rl.addWidget(tabs, 1)
        self.props = OverlayProps()
        pc = Card()
        pc.add(self.props)
        rl.addWidget(pc)
        body.addWidget(right)
        self.ctrl = OverlayController(self.canvas, self.browser, self.te, self.props, lambda: self.W, self)
        self.ctrl.changed.connect(self._changed)

        bottom = QHBoxLayout()
        self.tpl = button("模板", "save")
        m = QMenu(self)
        m.aboutToShow.connect(lambda: self._fill_menu(m))
        self.tpl.setMenu(m)
        bottom.addWidget(self.tpl)
        bottom.addWidget(button("清空全部", "trash", slot=self._clear))
        bottom.addStretch(1)
        bottom.addWidget(button("取消", slot=self.reject))
        ok = button("确定使用", kind="Primary", slot=self.accept)
        ok.setObjectName("Primary")
        bottom.addWidget(ok)
        root.addLayout(bottom)

    # ------------------------------------------------------------
    def _load_bg(self):
        m = self.mat.currentData()
        if not m:
            pm = QPixmap(self.W // 2, self.H // 2)
            pm.fill(Qt.darkGray)
            self.canvas.set_frame(pm)
            self.ctrl.refresh_all()
            return
        info = probe.probe(m)
        t = info.duration * self.tslider.value() / 100.0 if info.kind == "video" else 0
        out = os.path.join(paths.cache_dir("preview"), "designer_bg.png")
        W, H, fit = self.W, self.H, self.cfg.fit

        def job():
            return studio.canvas_frame(m, t, W, H, fit, out_png=out, scale=0.5)

        def done(png):
            self.bg_png = png
            if self.preview_on:
                self._render_preview()
            else:
                self.canvas.set_frame(QPixmap(png))
                if not getattr(self, "_placed", False):
                    self._placed = True
                    self.ctrl.refresh_all()
        self.req.submit(job, done, lambda e: self.status.setText(f"背景加载失败：{e}"))

    def _thumbs(self):
        if not self.bg_png:
            return
        from mixer import filters_lib as fl
        src = os.path.join(paths.cache_dir("preview"), "designer_filter_src.png")
        import shutil
        shutil.copyfile(self.bg_png, src)
        run_bg(studio.filter_thumbs, src, list(fl.FILTERS.keys()), 150,
               on_done=lambda th: self.fp.set_thumbs(th, original=src))

    def _changed(self, *_):
        self.style.filter_name, self.style.filter_intensity = self.fp.value()
        self.style.adjust = self.ap.value()
        if self.preview_on:
            self._render_preview()

    def current_style(self):
        st = StylePreset.from_dict(self.style.to_dict())
        st.filter_name, st.filter_intensity = self.fp.value()
        st.adjust = self.ap.value()
        st.overlays = self.ctrl.items()
        return st

    def set_style(self, st: StylePreset):
        self.style = StylePreset.from_dict(st.to_dict())
        self.fp.set_value(st.filter_name, st.filter_intensity or 1.0)
        self.ap.set_value(st.adjust)
        self.ctrl.load(st.overlays)

    def _toggle_preview(self, on):
        self.preview_on = on
        for g in self.canvas.overlays:
            g.setVisible(not on)
        if on:
            self._render_preview()
        elif self.bg_png:
            self.canvas.set_frame(QPixmap(self.bg_png))

    def _render_preview(self):
        if not self.bg_png:
            return
        st = self.current_style()
        out = os.path.join(paths.cache_dir("preview"), "designer_preview.png")
        bg = self.bg_png
        self.status.setText("正在渲染预览…")

        def done(png):
            if self.preview_on:
                self.canvas.set_frame(QPixmap(png))
                self.status.setText("效果预览（动画显示的是第 1 秒的状态，点“播放5秒”看动态效果）")
        self.req.submit(lambda: studio.preview_frame(bg, 1.0, None, st, max_w=self.W // 2, out_png=out), done,
                        lambda e: self.status.setText(f"预览失败：{e}"))

    def _play(self):
        m = self.mat.currentData()
        if not m:
            return
        st = self.current_style()
        info = probe.probe(m)
        t = info.duration * self.tslider.value() / 100.0 if info.kind == "video" else 0
        W, H, fit = self.W, self.H, self.cfg.fit
        self.status.setText("正在渲染 5 秒小样…")

        def job():
            from mixer import ffmpeg, render as rd
            tmp = os.path.join(paths.cache_dir("preview"), "designer_canvas.mp4")
            Wd, Hd = rd.even(W / 2), rd.even(H / 2)
            g = rd.fit_graph("[0:v]", "[o]", info, Wd, Hd, fit, "f")
            src = ["-ss", f"{t:.2f}", "-t", "5", "-i", m] if info.kind == "video" else ["-loop", "1", "-t", "5", "-i", m]
            ffmpeg.run_ffmpeg(src + ["-filter_complex", g, "-map", "[o]", "-map", "0:a?", "-t", "5", "-c:v", "libx264",
                                     "-preset", "ultrafast", "-crf", "20", "-c:a", "aac", tmp], duration=5)
            return studio.preview_clip(tmp, 0, 5.0, None, st)

        def done(out):
            self.status.setText("小样已生成，正在播放。")
            play_video(out)
        run_bg(job, on_done=done, on_error=lambda e: self.status.setText(f"小样渲染失败：{e}"))

    def _add_image(self):
        f, _ = QFileDialog.getOpenFileName(self, "选择图片", "", IMAGE_FILTER)
        if f:
            self.ctrl.add_image(f, w=0.22)

    def _clear(self):
        self.set_style(StylePreset())
        self._changed()

    def _fill_menu(self, m):
        m.clear()
        m.addAction("保存为包装模板…").triggered.connect(self._save_tpl)
        sub = m.addMenu("载入包装模板")
        names = list_templates("包装")
        for n in names:
            sub.addAction(n).triggered.connect(lambda _=False, n=n: self.set_style(
                StylePreset.from_dict(load_json(os.path.join(tpl_dir("包装"), n + ".json"), {}))))
        if not names:
            sub.addAction("（暂无）").setEnabled(False)

    def _save_tpl(self):
        st = self.current_style()
        name, ok = QInputDialog.getText(self, "保存包装模板", "模板名称：")
        if ok and name.strip():
            st.name = name.strip()
            save_json(os.path.join(tpl_dir("包装"), st.name + ".json"), st.to_dict())
            msg(self, f"已保存模板「{st.name}」")
