"""素材处理页：去水印 / 去贴纸 / 去遮挡、裁边、滤镜、调节、贴纸、花字，单个或批量导出新素材。"""
import hashlib
import os
import shutil
import subprocess

from PySide6.QtCore import Qt, Signal, QSize, QTimer
from PySide6.QtGui import QPixmap, QIcon
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLabel, QListWidget, QListWidgetItem, QTabWidget,
                               QToolButton, QSlider, QComboBox, QPushButton, QFileDialog, QInputDialog, QMenu,
                               QProgressDialog, QButtonGroup, QSplitter, QFrame, QAbstractItemView)

from mixer import probe, thumbs, studio, paths, utils, ffmpeg, settings
from mixer import cleanup as cln
from mixer.models import CleanupProfile, Region, StylePreset, CLEAN_METHODS, load_json, save_json
from . import theme
from .canvas import EditorCanvas, RegionItem, OverlayGfx
from .overlay_ctrl import OverlayController
from .panels import FilterPanel, AdjustPanel, StickerBrowser, TextStyleEditor, OverlayProps, ColorButton, scroll_wrap
from .widgets import Card, button, hint, form_row, dspin, ValueSlider, msg, confirm, VIDEO_FILTER, IMAGE_FILTER
from .workers import run_bg, LatestOnly, Debouncer, EventBridge


def tpl_dir(kind):
    d = os.path.join(paths.templates_dir(), kind)
    os.makedirs(d, exist_ok=True)
    return d


def list_templates(kind):
    d = tpl_dir(kind)
    return sorted([os.path.splitext(f)[0] for f in os.listdir(d) if f.endswith(".json")])


class StudioPage(QWidget):
    sendCleanupToMix = Signal(dict, list)
    sendStyleToMix = Signal(dict)
    requestMixMaterials = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.paths = []
        self.profiles = {}
        self.style = StylePreset()
        self.cur = None
        self.info = None
        self.t = 0.0
        self.preview_on = False
        self._pending_profile = False
        self.frame_req = LatestOnly()
        self.prev_req = LatestOnly()
        self.raw_png = None
        self._build()
        self._update_enabled()

    # ================================================================ UI
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)
        head = QHBoxLayout()
        t = QLabel("素材处理")
        t.setObjectName("Title")
        head.addWidget(t)
        head.addWidget(hint("去水印 / 去贴纸 / 去遮挡 · 裁边 · 滤镜 · 调节 · 贴纸 · 花字，可批量导出新素材，也可把去水印设置直接发送给混剪使用（不额外转码一次）"), 1)
        root.addLayout(head)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        root.addWidget(split, 1)

        # ---------------- 左：素材列表
        left = Card("素材")
        left.setMinimumWidth(240)
        left.setMaximumWidth(320)
        row = QHBoxLayout()
        for text, slot in (("添加", self._add_files), ("文件夹", self._add_folder), ("移除", self._remove_selected)):
            b = button(text, slot=slot)
            b.setObjectName("Tool")
            row.addWidget(b)
        left.add(row)
        self.mlist = QListWidget()
        self.mlist.setIconSize(QSize(96, 54))
        self.mlist.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.mlist.setAcceptDrops(True)
        self.mlist.setDragDropMode(QAbstractItemView.DropOnly)
        self.mlist.dragEnterEvent = self._drag_enter
        self.mlist.dragMoveEvent = self._drag_enter
        self.mlist.dropEvent = self._drop
        self.mlist.currentItemChanged.connect(self._on_pick)
        left.add(self.mlist)
        left.add(button("从混剪素材导入", slot=lambda: self.requestMixMaterials.emit()))
        left.add(hint("提示：可拖拽视频/图片到列表。每个素材可单独设置去水印；“应用到全部”可一键复用。"))
        split.addWidget(left)

        # ---------------- 中：画布
        mid = Card()
        tb = QHBoxLayout()
        tb.setSpacing(4)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_btns = {}
        for key, ic, tip in (("select", "film", "选择/移动（V）"), ("rect", "rect", "框选去水印区域（R）"),
                             ("brush", "brush", "画笔涂抹不规则水印/贴纸（B）"), ("erase", "eraser", "橡皮擦（E）")):
            btn = QToolButton()
            btn.setIcon(theme.icon(ic))
            btn.setIconSize(QSize(20, 20))
            btn.setCheckable(True)
            btn.setToolTip(tip)
            btn.clicked.connect(lambda _=False, k=key: self._set_mode(k))
            self.mode_group.addButton(btn)
            self.mode_btns[key] = btn
            tb.addWidget(btn)
        self.mode_btns["select"].setChecked(True)
        tb.addWidget(QLabel(" 笔刷"))
        self.brush = QSlider(Qt.Horizontal)
        self.brush.setRange(4, 120)
        self.brush.setValue(24)
        self.brush.setFixedWidth(80)
        self.brush.valueChanged.connect(lambda v: setattr(self.canvas, "brush", v))
        tb.addWidget(self.brush)
        tb.addSpacing(8)
        tb.addStretch(1)
        self.pv_btn = button("效果预览", "eye", tip="显示最终效果（与导出完全一致）")
        self.pv_btn.setCheckable(True)
        self.pv_btn.toggled.connect(self._toggle_preview)
        tb.addWidget(self.pv_btn)
        tb.addWidget(button("播放5秒", "play", slot=self._play_clip, tip="渲染当前位置起 5 秒小样并播放（可查看动画效果）"))
        fitb = QToolButton()
        fitb.setText("适应")
        fitb.setToolTip("画面适应窗口（Ctrl+滚轮缩放，中键拖动）")
        fitb.clicked.connect(lambda: self.canvas.fit())
        tb.addWidget(fitb)
        mid.add(tb)
        self.canvas = EditorCanvas()
        self.canvas.setMinimumSize(420, 300)
        mid.add(self.canvas)
        self.canvas.regionsChanged.connect(self._on_regions_changed)
        self.canvas.selectionChanged.connect(self._on_canvas_select)
        self.canvas.maskChanged.connect(self._on_style_or_clean_changed)
        tl = QHBoxLayout()
        self.prev1 = QToolButton()
        self.prev1.setArrowType(Qt.LeftArrow)
        self.prev1.setToolTip("后退 1 秒（←）")
        self.prev1.clicked.connect(lambda: self._step(-1))
        self.next1 = QToolButton()
        self.next1.setArrowType(Qt.RightArrow)
        self.next1.setToolTip("前进 1 秒（→）")
        self.next1.clicked.connect(lambda: self._step(1))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.valueChanged.connect(self._on_slider)
        self.tlabel = QLabel("00:00.0 / 00:00.0")
        self.tlabel.setObjectName("Sub")
        self.tlabel.setFixedWidth(130)
        tl.addWidget(self.prev1)
        tl.addWidget(self.slider, 1)
        tl.addWidget(self.next1)
        tl.addWidget(self.tlabel)
        mid.add(tl)
        self.status = QLabel("")
        self.status.setObjectName("Sub")
        mid.add(self.status)
        split.addWidget(mid)
        self.frame_debounce = Debouncer(140, self._load_frame, self)
        self.preview_debounce = Debouncer(260, self._render_preview, self)

        # ---------------- 右：工具标签页
        right = QWidget()
        right.setMinimumWidth(380)
        right.setMaximumWidth(470)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("Panel")
        rl.addWidget(self.tabs, 1)
        self._build_clean_tab()
        self._build_crop_tab()
        self.filter_panel = FilterPanel()
        self.filter_panel.changed.connect(self._on_filter)
        self.tabs.addTab(self.filter_panel, "滤镜")
        self.adjust_panel = AdjustPanel()
        self.adjust_panel.changed.connect(self._on_adjust)
        self.tabs.addTab(scroll_wrap(self.adjust_panel), "调节")
        stw = QWidget()
        sl = QVBoxLayout(stw)
        sl.setContentsMargins(0, 0, 0, 0)
        self.sticker_browser = StickerBrowser()
        sl.addWidget(self.sticker_browser, 1)
        sl.addWidget(button("添加图片（产品图/Logo）", "add", slot=self._add_image_overlay))
        self.tabs.addTab(stw, "贴纸")
        txw = QWidget()
        txl = QVBoxLayout(txw)
        txl.setContentsMargins(0, 0, 0, 0)
        self.text_editor = TextStyleEditor()
        txl.addWidget(self.text_editor)
        txl.addWidget(button("添加文字到画面", "text", kind="Primary", slot=lambda: self.ctrl.add_text()))
        txl.addStretch(1)
        self.tabs.addTab(scroll_wrap(txw), "文字")
        self.props = OverlayProps()
        self.props_card = Card()
        self.props_card.add(self.props)
        rl.addWidget(self.props_card)
        self.props_card.setVisible(False)
        self.tabs.currentChanged.connect(self._on_tab)
        split.addWidget(right)
        split.setSizes([250, 760, 410])
        self.ctrl = OverlayController(self.canvas, self.sticker_browser, self.text_editor, self.props,
                                      lambda: self.info.width if self.info else 1080, self)
        self.ctrl.changed.connect(self._on_overlays_changed)

        # ---------------- 底：模板 / 导出
        bottom = Card(margins=(12, 8, 12, 8))
        br = QHBoxLayout()
        self.tpl_btn = button("模板", "save")
        self.tpl_btn.setMenu(QMenu(self))
        self.tpl_btn.menu().aboutToShow.connect(self._fill_tpl_menu)
        br.addWidget(self.tpl_btn)
        br.addSpacing(12)
        br.addWidget(QLabel("导出到"))
        self.out_label = QLabel()
        self.out_label.setObjectName("Sub")
        self.out_label.setMinimumWidth(160)
        br.addWidget(self.out_label, 1)
        br.addWidget(button("更改", "folder", slot=self._choose_out))
        self.q_combo = QComboBox()
        self.q_combo.addItem("高清", "high")
        self.q_combo.addItem("标准", "standard")
        br.addWidget(self.q_combo)
        self.exp_one = button("导出当前", "export", slot=lambda: self._export(False))
        self.exp_all = button("全部导出", "export", kind="Primary", slot=lambda: self._export(True))
        br.addWidget(self.exp_one)
        br.addWidget(self.exp_all)
        self.send_btn = button("发送到混剪", "film")
        m = QMenu(self)
        m.addAction("发送去水印设置（并加入混剪素材）").triggered.connect(self._send_cleanup)
        m.addAction("把滤镜/贴纸/文字设为混剪的成片包装").triggered.connect(self._send_style)
        self.send_btn.setMenu(m)
        br.addWidget(self.send_btn)
        bottom.add(br)
        root.addWidget(bottom)
        self._refresh_out_label()

    def _build_clean_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(hint("① 点“框选区域”在画面上拖出水印/贴纸/遮挡物的位置；② 选择去除方式；"
                           "③ 打开右上角“效果预览”查看结果。水印会换位置的（如抖音），可分别设置生效时间段。"))
        r = QHBoxLayout()
        r.addWidget(button("框选区域", "rect", kind="Primary", slot=lambda: self._set_mode("rect")))
        r.addWidget(button("画笔涂抹", "brush", slot=lambda: self._set_mode("brush")))
        r.addWidget(button("自动识别", "magic", slot=self._auto_detect))
        lay.addLayout(r)
        self.rlist = QListWidget()
        self.rlist.setFixedHeight(120)
        self.rlist.currentRowChanged.connect(self._on_rlist)
        lay.addWidget(self.rlist)
        self.r_method = QComboBox()
        for k, v in CLEAN_METHODS.items():
            self.r_method.addItem(v, k)
        self.r_method.currentIndexChanged.connect(self._on_region_edit)
        self.r_strength = ValueSlider(2, 60, 12, steps=58, fmt="{:.0f}")
        self.r_strength.valueChanged.connect(self._on_region_edit)
        self.r_color = ColorButton("#000000")
        self.r_color.changed.connect(self._on_region_edit)
        self.r_t0 = dspin(0, 36000, 0, 0.5, 1, " 秒", width=86)
        self.r_t1 = dspin(0, 36000, 0, 0.5, 1, " 秒", width=86)
        self.r_t0.valueChanged.connect(self._on_region_edit)
        self.r_t1.valueChanged.connect(self._on_region_edit)
        lay.addLayout(form_row("去除方式", self.r_method, label_w=64))
        lay.addLayout(form_row("强度", self.r_strength, stretch=False, label_w=64))
        lay.addLayout(form_row("覆盖颜色", self.r_color, label_w=64))
        b0 = QPushButton("当前")
        b0.setObjectName("Flat")
        b0.clicked.connect(lambda: self.r_t0.setValue(self.t))
        b1 = QPushButton("当前")
        b1.setObjectName("Flat")
        b1.clicked.connect(lambda: self.r_t1.setValue(self.t))
        lay.addLayout(form_row("生效从", self.r_t0, b0, label_w=64))
        lay.addLayout(form_row("到", self.r_t1, b1, "0=结尾", label_w=64))
        rr = QHBoxLayout()
        rr.addWidget(button("删除选中区域", "trash", slot=self._delete_region))
        rr.addWidget(button("清空区域", slot=self._clear_regions))
        lay.addLayout(rr)
        mr = QHBoxLayout()
        mr.addWidget(QLabel("画笔蒙版（智能填充不规则区域）"))
        mr.addStretch(1)
        mr.addWidget(button("清除涂抹", slot=lambda: (self.canvas.clear_mask(), self._on_style_or_clean_changed())))
        lay.addLayout(mr)
        lay.addSpacing(6)
        lay.addWidget(button("把本素材的去水印设置应用到全部素材", slot=self._apply_clean_all))
        lay.addStretch(1)
        self.region_editor_widgets = [self.r_method, self.r_strength, self.r_color, self.r_t0, self.r_t1]
        self.tabs.addTab(scroll_wrap(w), "去水印")

    def _build_crop_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.addWidget(hint("裁掉画面边缘（如顶部/底部的水印、字幕条、黑边），导出时自动放大回原尺寸；"
                           "在混剪中则按画布规格重新适配。"))
        self.crop_sliders = {}
        for key, label in (("crop_l", "左"), ("crop_t", "上"), ("crop_r", "右"), ("crop_b", "下")):
            s = ValueSlider(0, 0.4, 0, steps=400, fmt="{:.1%}")
            s.valueChanged.connect(self._on_crop)
            self.crop_sliders[key] = s
            lay.addLayout(form_row(f"裁{label}边", s, stretch=False, label_w=60))
        r = QHBoxLayout()
        r.addWidget(button("自动检测黑边", "magic", slot=self._auto_crop))
        r.addWidget(button("重置", slot=lambda: self._set_crop((0, 0, 0, 0))))
        lay.addLayout(r)
        lay.addStretch(1)
        self.tabs.addTab(w, "裁剪")

    # ================================================================ 素材列表
    def _drag_enter(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def _drop(self, e):
        self.add_paths([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])
        e.acceptProposedAction()

    def _add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "选择素材", "", VIDEO_FILTER)
        if files:
            self.add_paths(files)

    def _add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "选择素材文件夹")
        if d:
            self.add_paths([d])

    def add_paths(self, paths_):
        files = probe.scan_paths(paths_, kinds=("video", "image"))
        first_new = None
        for f in files:
            if f in self.paths:
                continue
            self.paths.append(f)
            it = QListWidgetItem(os.path.basename(f))
            it.setData(Qt.UserRole, f)
            it.setToolTip(f)
            it.setIcon(theme.icon("film", theme.C["sub"]))
            self.mlist.addItem(it)
            first_new = first_new or it

            def job(p=f):
                info = probe.probe(p)
                return p, thumbs.thumb(info) if info.ok else None

            def done(res):
                p, tp = res
                for i in range(self.mlist.count()):
                    item = self.mlist.item(i)
                    if item.data(Qt.UserRole) == p and tp:
                        item.setIcon(QIcon(QPixmap(tp)))
            run_bg(job, on_done=done)
        if first_new is not None and self.cur is None:
            self.mlist.setCurrentItem(first_new)
        self._update_enabled()

    def _remove_selected(self):
        for it in self.mlist.selectedItems():
            p = it.data(Qt.UserRole)
            self.paths.remove(p)
            self.profiles.pop(p, None)
            self.mlist.takeItem(self.mlist.row(it))
            if p == self.cur:
                self.cur = None
                self.info = None
        if self.cur is None:
            if self.mlist.count():
                self.mlist.setCurrentRow(0)
            else:
                self.canvas.set_frame(QPixmap())
                self.canvas.clear_regions()
        self._update_enabled()

    def _update_enabled(self):
        has = self.cur is not None
        for w in (self.exp_one, self.exp_all, self.send_btn, self.pv_btn):
            w.setEnabled(has)

    # ================================================================ 当前素材
    def _on_pick(self, cur, prev):
        if cur is None:
            return
        self._store_current()
        path = cur.data(Qt.UserRole)
        info = probe.probe(path)
        if not info.ok:
            self.status.setText(f"素材无效：{info.error}")
            return
        self.cur = path
        self.info = info
        self.t = round(info.duration * 0.2, 2) if info.kind == "video" else 0.0
        self.slider.blockSignals(True)
        self.slider.setValue(int(self.t / info.duration * 1000) if info.duration else 0)
        self.slider.setEnabled(info.kind == "video")
        self.slider.blockSignals(False)
        self._update_time_label()
        self.canvas.clear_regions()
        self._pending_profile = True
        self._load_frame()
        self.ctrl.refresh_all()
        self._update_enabled()
        self.status.setText(f"{info.name} · {info.res_text()} · "
                            + (f"{utils.fmt_secs(info.duration)} · {info.fps:.0f}fps" if info.kind == "video" else "图片")
                            + (" · HDR（导出时自动转为普通动态范围）" if info.hdr else ""))

    def _profile(self, path=None):
        path = path or self.cur
        d = self.profiles.get(path)
        return CleanupProfile.from_dict(d) if d else CleanupProfile()

    def _store_current(self):
        """把画布上的区域/蒙版/裁边写回当前素材的方案。"""
        if not self.cur or self._pending_profile:
            return
        prof = self._profile()
        prof.regions = self.canvas.region_dicts()
        crop = {k: s.value() for k, s in self.crop_sliders.items()}
        for k, v in crop.items():
            setattr(prof, k, round(v, 4))
        if self.canvas.has_mask():
            key = hashlib.md5(self.cur.encode("utf-8")).hexdigest()[:12]
            mp = os.path.join(paths.cache_dir("masks"), f"mask_{key}.png")
            self.canvas.save_mask(mp)
            prof.mask_png = mp
        else:
            prof.mask_png = ""
        self.profiles[self.cur] = prof.to_dict()
        self._refresh_list_marks()

    def _apply_profile_to_canvas(self):
        prof = self._profile()
        self.canvas.set_regions(prof.regions)
        if prof.mask_png and os.path.isfile(prof.mask_png):
            self.canvas.load_mask(prof.mask_png)
        else:
            self.canvas.clear_mask()
        self._set_crop((prof.crop_l, prof.crop_t, prof.crop_r, prof.crop_b), emit=False)
        self._refresh_rlist()

    def _refresh_list_marks(self):
        for i in range(self.mlist.count()):
            it = self.mlist.item(i)
            p = it.data(Qt.UserRole)
            prof = self._profile(p)
            name = os.path.basename(p)
            it.setText(("★ " if not prof.is_empty() else "") + name)

    # ================================================================ 帧 / 预览
    def _on_slider(self, v):
        if not self.info or self.info.kind != "video":
            return
        self.t = round(self.info.duration * v / 1000.0, 2)
        self._update_time_label()
        if self.preview_on:
            self.preview_debounce()
        else:
            self.frame_debounce()

    def _step(self, d):
        if not self.info or self.info.kind != "video":
            return
        self.t = max(0.0, min(self.info.duration - 0.05, self.t + d))
        self.slider.blockSignals(True)
        self.slider.setValue(int(self.t / self.info.duration * 1000))
        self.slider.blockSignals(False)
        self._update_time_label()
        self._render_preview() if self.preview_on else self._load_frame()

    def _update_time_label(self):
        d = self.info.duration if self.info else 0
        self.tlabel.setText(f"{self._fmt(self.t)} / {self._fmt(d)}")

    @staticmethod
    def _fmt(s):
        m, sec = divmod(max(0.0, s), 60)
        return f"{int(m):02d}:{sec:04.1f}"

    def _load_frame(self):
        if not self.cur:
            return
        path, t = self.cur, self.t
        out = os.path.join(paths.cache_dir("preview"), "studio_raw.png")

        def job():
            return studio.raw_frame(path, t, max_w=1280, out_png=out)

        def done(png):
            if path != self.cur:
                return
            self.raw_png = png
            pm = QPixmap(png)
            self.canvas.set_frame(pm)
            if self._pending_profile:
                self._pending_profile = False
                self._apply_profile_to_canvas()
            if self.tabs.currentWidget() is self.filter_panel:
                self._refresh_filter_thumbs()
            self.ctrl.refresh_all() if not self.canvas.overlays else None
        self.frame_req.submit(job, done, lambda e: self.status.setText(f"取帧失败：{e}"))

    def _toggle_preview(self, on):
        self.preview_on = on
        for it in self.canvas.regions + self.canvas.overlays:
            it.setVisible(not on)
        self.canvas.mask_item.setVisible(not on)
        for it in self.canvas.crop_items:
            it.setVisible(not on)
        if on:
            self._render_preview()
        else:
            self._load_frame()
            if self.info:
                self.status.setText(f"{self.info.name} · {self.info.res_text()}"
                                    + (f" · {utils.fmt_secs(self.info.duration)}" if self.info.kind == "video" else ""))

    def _render_preview(self):
        if not self.cur or not self.preview_on:
            return
        self._store_current()
        path, t = self.cur, self.t
        prof = self._profile()
        style = self._current_style()
        out = os.path.join(paths.cache_dir("preview"), "studio_preview.png")
        self.status.setText("正在渲染效果预览…")

        def job():
            return studio.preview_frame(path, t, prof, style, max_w=1280, out_png=out)

        def done(png):
            if path != self.cur or not self.preview_on:
                return
            self.canvas.set_frame(QPixmap(png))
            self.status.setText("效果预览（与导出结果一致）。关闭“效果预览”可继续编辑。")

        def err(e):
            self.status.setText(f"预览失败：{e}")
        self.prev_req.submit(job, done, err)

    def _on_style_or_clean_changed(self):
        if self.preview_on:
            self.preview_debounce()

    def _current_style(self):
        st = StylePreset.from_dict(self.style.to_dict())
        st.overlays = self.ctrl.items()
        return st

    # ================================================================ 去水印
    def _set_mode(self, mode):
        if self.preview_on:
            self.pv_btn.setChecked(False)
        self.canvas.set_mode(mode)
        b = self.mode_btns.get(mode)
        if b:
            b.setChecked(True)
        if mode in ("rect", "brush", "erase"):
            self.tabs.setCurrentIndex(0)

    def _on_regions_changed(self):
        self._refresh_rlist()
        self._on_style_or_clean_changed()
        if self.canvas.mode == "select":
            self.mode_btns["select"].setChecked(True)

    def _refresh_rlist(self):
        self.rlist.blockSignals(True)
        self.rlist.clear()
        for i, it in enumerate(self.canvas.regions):
            r = it.region
            tm = "" if (r.t0 <= 0 and r.t1 <= 0) else f"  {r.t0:.1f}s~{('%.1fs' % r.t1) if r.t1 > 0 else '结尾'}"
            self.rlist.addItem(f"#{i + 1}  {CLEAN_METHODS.get(r.method, r.method)[:6]}"
                               f"  ({r.x:.0%},{r.y:.0%}) {r.w:.0%}×{r.h:.0%}{tm}")
        sel = self.canvas.selected()
        if isinstance(sel, RegionItem) and sel in self.canvas.regions:
            self.rlist.setCurrentRow(self.canvas.regions.index(sel))
        self.rlist.blockSignals(False)
        self._bind_region_editor()

    def _on_rlist(self, row):
        if 0 <= row < len(self.canvas.regions):
            self.canvas.scene().clearSelection()
            self.canvas.regions[row].setSelected(True)

    def _on_canvas_select(self, it):
        if isinstance(it, RegionItem):
            self.rlist.blockSignals(True)
            self.rlist.setCurrentRow(self.canvas.regions.index(it))
            self.rlist.blockSignals(False)
            self._bind_region_editor()
        elif isinstance(it, OverlayGfx):
            if self.tabs.currentIndex() < 4:
                self.tabs.setCurrentIndex(5 if it.item.kind == "text" else 4)

    def _sel_region(self):
        it = self.canvas.selected()
        return it if isinstance(it, RegionItem) else None

    def _bind_region_editor(self):
        it = self._sel_region()
        for w in self.region_editor_widgets:
            w.setEnabled(it is not None)
        if it is None:
            return
        r = it.region
        for w in self.region_editor_widgets:
            w.blockSignals(True)
        self.r_method.setCurrentIndex(max(0, self.r_method.findData(r.method)))
        self.r_strength.setValue(r.strength)
        self.r_color.set_color(r.color)
        self.r_t0.setValue(r.t0)
        self.r_t1.setValue(r.t1)
        for w in self.region_editor_widgets:
            w.blockSignals(False)

    def _on_region_edit(self, *_):
        it = self._sel_region()
        if it is None:
            return
        r = it.region
        r.method = self.r_method.currentData()
        r.strength = int(self.r_strength.value())
        r.color = self.r_color.color
        r.t0 = self.r_t0.value()
        r.t1 = self.r_t1.value()
        it.update()
        self._refresh_rlist()
        self._on_style_or_clean_changed()

    def _delete_region(self):
        it = self._sel_region()
        if it is not None:
            self.canvas.remove_item(it)

    def _clear_regions(self):
        self.canvas.clear_regions()
        self.canvas.regionsChanged.emit()

    def _auto_detect(self):
        if not self.info or self.info.kind != "video":
            msg(self, "自动识别需要选择一个视频素材。")
            return
        info = self.info
        dlg = QProgressDialog("正在分析整段视频中不动的文字/台标…", None, 0, 0, self)
        dlg.setWindowTitle("自动识别水印")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.show()

        def done(res):
            dlg.close()
            res = [r for r in res if r["score"] >= 0.25][:4]
            if not res:
                msg(self, "没有识别到明显的固定水印。\n可以手动“框选区域”或用“画笔涂抹”。")
                return
            if self.preview_on:
                self.pv_btn.setChecked(False)
            for r in res:
                self.canvas.add_region(Region(x=r["x"], y=r["y"], w=r["w"], h=r["h"], method="delogo"),
                                       select=False)
            self.tabs.setCurrentIndex(0)
            msg(self, f"识别到 {len(res)} 处疑似水印/固定贴纸，已自动框出。\n"
                      f"请拖动微调或删除误识别的框，然后打开“效果预览”查看。")

        def err(e):
            dlg.close()
            msg(self, f"识别失败：{e}", kind="warn")
        run_bg(cln.detect_static_overlays, info, on_done=done, on_error=err)

    def _apply_clean_all(self):
        if not self.cur:
            return
        self._store_current()
        prof = self.profiles.get(self.cur)
        if not prof:
            msg(self, "当前素材还没有去水印设置。")
            return
        n = 0
        for p in self.paths:
            if p != self.cur:
                self.profiles[p] = dict(prof)
                n += 1
        self._refresh_list_marks()
        msg(self, f"已把当前去水印设置（按比例坐标）应用到其余 {n} 个素材。\n适用于同一来源、水印位置相同的一批视频。")

    # ================================================================ 裁剪
    def _on_crop(self, *_):
        c = tuple(self.crop_sliders[k].value() for k in ("crop_l", "crop_t", "crop_r", "crop_b"))
        self.canvas.set_crop(*c)
        self._on_style_or_clean_changed()

    def _set_crop(self, c, emit=True):
        for k, v in zip(("crop_l", "crop_t", "crop_r", "crop_b"), c):
            self.crop_sliders[k].setValue(v)
        self.canvas.set_crop(*[float(x) for x in c])
        if emit:
            self._on_style_or_clean_changed()

    def _auto_crop(self):
        if not self.cur:
            return
        path = self.cur

        def done(c):
            if path != self.cur:
                return
            if not any(c):
                msg(self, "未检测到黑边。")
                return
            self._set_crop(c)
            msg(self, "已按检测结果设置裁边：左 {:.1%} 上 {:.1%} 右 {:.1%} 下 {:.1%}".format(*c))
        run_bg(studio.detect_black_bars, path, on_done=done, on_error=lambda e: msg(self, f"检测失败：{e}"))

    # ================================================================ 风格
    def _on_tab(self, idx):
        if self.tabs.widget(idx) is self.filter_panel:
            self._refresh_filter_thumbs()
        self.canvas.allow_regions = True
        self.props_card.setVisible(idx >= 4)

    def _refresh_filter_thumbs(self):
        if not self.raw_png or not os.path.isfile(self.raw_png):
            return
        src = os.path.join(paths.cache_dir("preview"), "filter_src.png")
        try:
            shutil.copyfile(self.raw_png, src)
        except OSError:
            return
        from mixer import filters_lib as fl

        def job():
            return studio.filter_thumbs(src, list(fl.FILTERS.keys()), width=150)
        run_bg(job, on_done=lambda th: self.filter_panel.set_thumbs(th, original=src))

    def _on_filter(self):
        name, inten = self.filter_panel.value()
        self.style.filter_name = name
        self.style.filter_intensity = inten
        if not self.preview_on and name:
            self.status.setText(f"已选择滤镜「{name}」，打开“效果预览”查看最终效果")
        self._on_style_or_clean_changed()

    def _on_adjust(self):
        self.style.adjust = self.adjust_panel.value()
        self._on_style_or_clean_changed()

    def _on_overlays_changed(self):
        self._on_style_or_clean_changed()

    def _add_image_overlay(self):
        f, _ = QFileDialog.getOpenFileName(self, "选择图片", "", IMAGE_FILTER)
        if f:
            self.ctrl.add_image(f)

    # ================================================================ 模板
    def _fill_tpl_menu(self):
        m = self.tpl_btn.menu()
        m.clear()
        m.addAction("保存当前去水印设置为模板…").triggered.connect(self._save_clean_tpl)
        m.addAction("保存当前滤镜/贴纸/文字为包装模板…").triggered.connect(self._save_style_tpl)
        m.addSeparator()
        sub1 = m.addMenu("载入去水印模板（应用到当前素材）")
        for n in list_templates("去水印"):
            sub1.addAction(n).triggered.connect(lambda _=False, n=n: self._load_clean_tpl(n))
        if not list_templates("去水印"):
            sub1.addAction("（暂无）").setEnabled(False)
        sub2 = m.addMenu("载入包装模板")
        for n in list_templates("包装"):
            sub2.addAction(n).triggered.connect(lambda _=False, n=n: self._load_style_tpl(n))
        if not list_templates("包装"):
            sub2.addAction("（暂无）").setEnabled(False)
        m.addSeparator()
        m.addAction("打开模板文件夹").triggered.connect(lambda: utils.open_path(paths.templates_dir()))

    def _save_clean_tpl(self):
        if not self.cur:
            return
        self._store_current()
        prof = self._profile()
        if prof.is_empty():
            msg(self, "当前素材还没有去水印/裁边设置。")
            return
        name, ok = QInputDialog.getText(self, "保存去水印模板", "模板名称（如：抖音水印-竖屏）：")
        if not ok or not name.strip():
            return
        name = name.strip()
        d = prof.to_dict()
        if prof.mask_png and os.path.isfile(prof.mask_png):
            mp = os.path.join(tpl_dir("去水印"), name + "_mask.png")
            shutil.copyfile(prof.mask_png, mp)
            d["mask_png"] = mp
        d["name"] = name
        save_json(os.path.join(tpl_dir("去水印"), name + ".json"), d)
        msg(self, f"已保存去水印模板「{name}」。\n在混剪页素材列表右键也可以直接套用。")

    def _load_clean_tpl(self, name):
        if not self.cur:
            msg(self, "请先选择一个素材。")
            return
        d = load_json(os.path.join(tpl_dir("去水印"), name + ".json"), {})
        if not d:
            return
        if self.preview_on:
            self.pv_btn.setChecked(False)
        self.profiles[self.cur] = d
        self._apply_profile_to_canvas()
        self._refresh_list_marks()

    def _save_style_tpl(self):
        st = self._current_style()
        if st.is_empty():
            msg(self, "还没有设置滤镜/调节/贴纸/文字。")
            return
        name, ok = QInputDialog.getText(self, "保存包装模板", "模板名称：")
        if not ok or not name.strip():
            return
        st.name = name.strip()
        save_json(os.path.join(tpl_dir("包装"), st.name + ".json"), st.to_dict())
        msg(self, f"已保存包装模板「{st.name}」，可在混剪页“包装”中选用。")

    def _load_style_tpl(self, name):
        d = load_json(os.path.join(tpl_dir("包装"), name + ".json"), {})
        if not d:
            return
        self.set_style(StylePreset.from_dict(d))

    def set_style(self, st: StylePreset):
        self.style = StylePreset.from_dict(st.to_dict())
        self.filter_panel.set_value(st.filter_name, st.filter_intensity)
        self.adjust_panel.set_value(st.adjust)
        self.ctrl.load(st.overlays)
        self._on_style_or_clean_changed()

    # ================================================================ 导出 / 发送
    def _refresh_out_label(self):
        d = settings.get().get("studio_out_dir") or ""
        self.out_label.setText(d or "（默认：素材所在文件夹\\处理后）")
        self.out_label.setToolTip(d)

    def _choose_out(self):
        d = QFileDialog.getExistingDirectory(self, "选择导出文件夹", settings.get().get("studio_out_dir") or "")
        if d:
            settings.set("studio_out_dir", d)
            self._refresh_out_label()

    def _out_path(self, src):
        d = settings.get().get("studio_out_dir") or os.path.join(os.path.dirname(src), "处理后")
        os.makedirs(d, exist_ok=True)
        stem, ext = os.path.splitext(os.path.basename(src))
        info = probe.probe(src)
        ext = ".png" if info.kind == "image" and ext.lower() == ".png" else (".jpg" if info.kind == "image" else ".mp4")
        out = os.path.join(d, f"{stem}_处理{ext}")
        i = 2
        while os.path.exists(out):
            out = os.path.join(d, f"{stem}_处理_{i}{ext}")
            i += 1
        return out

    def _export(self, all_):
        if not self.cur:
            return
        if self.preview_on:
            self.pv_btn.setChecked(False)
        self._store_current()
        targets = list(self.paths) if all_ else [self.cur]
        style = self._current_style()
        jobs = [(p, self._out_path(p), self._profile(p)) for p in targets]
        quality = self.q_combo.currentData()
        dlg = QProgressDialog("准备导出…", "取消", 0, 1000, self)
        dlg.setWindowTitle("导出处理后的素材")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        import threading
        cancel = threading.Event()
        dlg.canceled.connect(cancel.set)
        bridge = EventBridge(self)

        def on_ev(ev, data):
            if ev == "progress":
                dlg.setValue(int(data["pct"] * 1000))
                dlg.setLabelText(data["text"])
        bridge.event.connect(on_ev)

        def job():
            done = []
            for i, (src, out, prof) in enumerate(jobs):
                if cancel.is_set():
                    break
                name = os.path.basename(src)

                def prog(f, i=i, name=name):
                    bridge("progress", {"pct": (i + f) / len(jobs), "text": f"[{i + 1}/{len(jobs)}] {name}"})
                prog(0.0)
                studio.export(src, out, prof, style, quality=quality, encoder=settings.get().get("default_encoder",
                                                                                                 "auto"),
                              cancel=cancel, on_progress=prog)
                done.append(out)
            return done

        def finished(done):
            dlg.close()
            if not done:
                return
            if confirm(self, f"已导出 {len(done)} 个文件。\n\n是否打开所在文件夹？", "导出完成"):
                utils.open_path(done[-1], select=True)

        def failed(e):
            dlg.close()
            if isinstance(e, ffmpeg.Cancelled):
                return
            msg(self, f"导出失败：{e}", kind="warn")
        run_bg(job, on_done=finished, on_error=failed)

    def _send_cleanup(self):
        self._store_current()
        cmap = {p: d for p, d in self.profiles.items() if not CleanupProfile.from_dict(d).is_empty()}
        if not cmap:
            msg(self, "还没有任何素材设置了去水印/裁边。")
            return
        self.sendCleanupToMix.emit(cmap, list(self.paths))

    def _send_style(self):
        st = self._current_style()
        if st.is_empty():
            msg(self, "还没有设置滤镜/调节/贴纸/文字。")
            return
        self.sendStyleToMix.emit(st.to_dict())

    def _play_clip(self):
        if not self.cur:
            return
        self._store_current()
        path, t = self.cur, self.t
        prof, style = self._profile(), self._current_style()
        self.status.setText("正在渲染 5 秒小样…")

        def done(out):
            self.status.setText("小样已生成，正在播放。")
            play_video(out)
        run_bg(studio.preview_clip, path, t, 5.0, prof, style, on_done=done,
               on_error=lambda e: self.status.setText(f"小样渲染失败：{e}"))

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key_V:
            self._set_mode("select")
        elif k == Qt.Key_R:
            self._set_mode("rect")
        elif k == Qt.Key_B:
            self._set_mode("brush")
        elif k == Qt.Key_E:
            self._set_mode("erase")
        elif k == Qt.Key_Left:
            self._step(-1)
        elif k == Qt.Key_Right:
            self._step(1)
        else:
            super().keyPressEvent(e)


def play_video(path):
    """优先用随包 ffplay 播放（LTSC 系统可能没有“电影和电视”应用），否则用系统默认播放器。"""
    fp = ffmpeg.ffplay_bin()
    if fp:
        try:
            info = probe.probe(path, use_cache=False)
            w, h = (info.width or 540), (info.height or 960)
            k = min(1.0, 540.0 / w, 900.0 / h)
            subprocess.Popen([fp, "-autoexit", "-window_title", "预览（空格暂停 / Esc 关闭）", "-x", str(int(w * k)),
                              "-y", str(int(h * k)), "-loglevel", "quiet", path],
                             creationflags=ffmpeg.CREATE_NO_WINDOW, stdin=subprocess.DEVNULL)
            return
        except Exception:
            pass
    utils.open_path(path)
