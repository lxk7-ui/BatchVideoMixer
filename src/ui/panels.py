"""可复用的编辑面板：滤镜、调节、贴纸库浏览、花字样式、选中元素属性。"""
import os

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QPixmap, QColor, QIcon
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton, QPlainTextEdit,
                               QColorDialog, QCheckBox, QGridLayout, QScrollArea, QFrame, QSpinBox, QDoubleSpinBox,
                               QListWidget)

from mixer import filters_lib as fl, stickers as stk, textstyle, fonts
from mixer.models import Adjust, OverlayItem, ANIM_IN, ANIM_LOOP, ANIM_OUT
from . import theme
from .widgets import IconGrid, ValueSlider, hint, form_row, dspin, ispin, button
from .workers import run_bg
from .canvas import pil_to_qimage


def scroll_wrap(w, margins=(12, 10, 12, 10)):
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.NoFrame)
    w.setObjectName("ScrollInner")
    if w.layout() is not None:
        w.layout().setContentsMargins(*margins)
    sa.setWidget(w)
    sa.viewport().setAutoFillBackground(False)
    w.setAutoFillBackground(False)
    return sa


# ----------------------------------------------------------------------------
class FilterPanel(QWidget):
    changed = Signal()

    def __init__(self, parent=None, with_none=True):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 0)
        row = QHBoxLayout()
        self.cat = QComboBox()
        self.cat.addItems(["全部"] + fl.FILTER_CATEGORIES)
        self.cat.currentTextChanged.connect(self._fill)
        row.addWidget(QLabel("分类"))
        row.addWidget(self.cat)
        row.addStretch(1)
        lay.addLayout(row)
        self.grid = IconGrid(icon_size=92, cell=(104, 112))
        self.grid.picked.connect(self._pick)
        lay.addWidget(self.grid, 1)
        self.intensity = ValueSlider(0, 1, 1.0, fmt="{:.0%}")
        self.intensity.valueChanged.connect(lambda *_: self.changed.emit())
        lay.addLayout(form_row("滤镜强度", self.intensity, stretch=False, label_w=64))
        self.current = ""
        self.thumbs = {}
        self.with_none = with_none
        self._fill()

    def _fill(self, *_):
        self.grid.clear()
        if self.with_none:
            it = self.grid.add("", "无滤镜", self._thumb(""))
        for n in fl.filter_names(self.cat.currentText()):
            self.grid.add(n, n, self._thumb(n), tip=f"{fl.FILTERS[n][0]} · {n}")
        self._mark()

    def _thumb(self, name):
        p = self.thumbs.get(name)
        if p and os.path.isfile(p):
            return QPixmap(p)
        pm = QPixmap(92, 92)
        pm.fill(QColor(theme.C["surface3"]))
        return pm

    def set_thumbs(self, thumbs: dict, original=None):
        self.thumbs = dict(thumbs)
        if original:
            self.thumbs[""] = original
        self._fill()

    def _pick(self, key):
        self.current = key
        self._mark()
        self.changed.emit()

    def _mark(self):
        for i in range(self.grid.count()):
            it = self.grid.item(i)
            it.setSelected(it.data(Qt.UserRole) == self.current)

    def set_value(self, name, intensity=1.0):
        self.current = name or ""
        self.intensity.setValue(intensity)
        self._mark()

    def value(self):
        return self.current, self.intensity.value()


# ----------------------------------------------------------------------------
ADJ_FIELDS = [("brightness", "亮度"), ("contrast", "对比度"), ("saturation", "饱和度"), ("temperature", "色温（冷/暖）"),
              ("tint", "色调"), ("highlights", "高光"), ("shadows", "阴影"), ("sharpen", "锐化"), ("vignette", "暗角"),
              ("grain", "颗粒")]


class AdjustPanel(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 0)
        self.sliders = {}
        for key, label in ADJ_FIELDS:
            lo = 0.0 if key in ("sharpen", "vignette", "grain") else -1.0
            s = ValueSlider(lo, 1.0, 0.0, fmt="{:+.0%}" if lo < 0 else "{:.0%}")
            s.valueChanged.connect(lambda *_: self.changed.emit())
            self.sliders[key] = s
            lay.addLayout(form_row(label, s, stretch=False, label_w=86))
        b = button("全部重置", slot=self.reset)
        lay.addWidget(b, 0, Qt.AlignLeft)
        lay.addStretch(1)

    def reset(self):
        for s in self.sliders.values():
            s.setValue(0)
        self.changed.emit()

    def value(self):
        return {k: round(s.value(), 3) for k, s in self.sliders.items() if abs(s.value()) > 1e-3}

    def set_value(self, d):
        d = d or {}
        for k, s in self.sliders.items():
            s.setValue(float(d.get(k, 0)))


# ----------------------------------------------------------------------------
class StickerBrowser(QWidget):
    picked = Signal(object)   # Sticker

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 0)
        row = QHBoxLayout()
        self.cat = QComboBox()
        self.cat.setMinimumWidth(150)
        self.cat.currentIndexChanged.connect(self._fill)
        row.addWidget(QLabel("分类"))
        row.addWidget(self.cat, 1)
        rb = button("刷新", slot=lambda: self.reload(True))
        rb.setObjectName("Tool")
        row.addWidget(rb)
        lay.addLayout(row)
        self.grid = IconGrid(icon_size=72, cell=(88, 100))
        self.grid.picked.connect(self._pick)
        lay.addWidget(self.grid, 1)
        lay.addWidget(hint("点击贴纸即可添加到画面中央，可拖动位置、拖右下角圆点缩放。"
                           "自己的贴纸（含 GIF 动图）可在左侧“贴纸库”页导入。"))
        self.items = {}
        self.reload()

    def reload(self, refresh=False):
        lib = stk.library(refresh=refresh)
        cur = self.cat.currentData()
        self.cat.blockSignals(True)
        self.cat.clear()
        for k, v in lib.items():
            self.cat.addItem(f"{stk.display_name(k)}（{len(v)}）", k)
        self.cat.blockSignals(False)
        idx = self.cat.findData(cur) if cur else -1
        self.cat.setCurrentIndex(idx if idx >= 0 else (0 if self.cat.count() else -1))
        self._fill()

    def _fill(self, *_):
        self.grid.clear()
        self.items = {}
        key = self.cat.currentData()
        if not key:
            return
        items = stk.library().get(key, [])
        for s in items:
            self.items[s.path] = s
            it = self.grid.add(s.path, s.name + ("（动）" if s.animated else ""), None, tip=s.path)
        # 后台生成缩略图
        paths = [s.path for s in items]

        def job():
            return [(p, stk.thumbnail(p, 96)) for p in paths]

        def done(res, key=key):
            if self.cat.currentData() != key:
                return
            for p, t in res:
                it = self.grid.item_by_key(p)
                if it is not None:
                    it.setIcon(QIcon(QPixmap(t)))
        run_bg(job, on_done=done)

    def _pick(self, path):
        s = self.items.get(path)
        if s:
            self.picked.emit(s)


# ----------------------------------------------------------------------------
class ColorButton(QPushButton):
    changed = Signal(str)

    def __init__(self, color="#FFFFFF", parent=None):
        super().__init__(parent)
        self.setFixedSize(40, 26)
        self.setCursor(Qt.PointingHandCursor)
        self.set_color(color)
        self.clicked.connect(self._pick)

    def set_color(self, c):
        self.color = c
        self.setStyleSheet(f"QPushButton {{ background: {c}; border: 1px solid {theme.C['border']}; border-radius: 5px; }}")

    def _pick(self):
        c = QColorDialog.getColor(QColor(self.color), self, "选择颜色")
        if c.isValid():
            self.set_color(c.name().upper())
            self.changed.emit(self.color)


class TextStyleEditor(QWidget):
    """花字样式编辑：预设 + 字体/字号/颜色/描边/渐变/阴影/底框。"""
    changed = Signal()

    def __init__(self, parent=None, show_text=True):
        super().__init__(parent)
        self._lock = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.setSpacing(8)
        if show_text:
            self.text = QPlainTextEdit()
            self.text.setPlaceholderText("输入文字，可换行；支持变量 {序号} {日期} {素材名} {任务名}")
            self.text.setFixedHeight(64)
            self.text.textChanged.connect(self._emit)
            lay.addWidget(self.text)
        else:
            self.text = None
        lay.addWidget(QLabel("花字预设"))
        self.presets = IconGrid(icon_size=120, cell=(132, 62))
        self.presets.setIconSize(QSize(120, 36))
        self.presets.setFixedHeight(150)
        for name in textstyle.PRESETS:
            self.presets.add(name, "", None, tip=name)
        self.presets.picked.connect(self._apply_preset)
        lay.addWidget(self.presets)
        run_bg(self._render_previews, on_done=self._set_previews)
        g = QGridLayout()
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(6)
        self.font = QComboBox()
        self.font.addItems(fonts.font_names())
        self.size = ispin(12, 300, 72, width=70)
        self.color = ColorButton("#FFFFFF")
        self.gradient = QCheckBox("渐变")
        self.color2 = ColorButton("#FFD400")
        self.sw = ispin(0, 30, 6, width=60)
        self.sc = ColorButton("#000000")
        self.sw2 = ispin(0, 30, 0, width=60)
        self.sc2 = ColorButton("#FFFFFF")
        self.shadow = QCheckBox("阴影")
        self.bg = QCheckBox("底框")
        self.bgc = ColorButton("#FF3B30")
        self.bgo = dspin(0.1, 1.0, 1.0, 0.1, 1, width=64)
        g.addWidget(QLabel("字体"), 0, 0)
        g.addWidget(self.font, 0, 1, 1, 3)
        g.addWidget(QLabel("字号"), 1, 0)
        g.addWidget(self.size, 1, 1)
        g.addWidget(QLabel("颜色"), 1, 2)
        h = QHBoxLayout()
        h.addWidget(self.color)
        h.addWidget(self.gradient)
        h.addWidget(self.color2)
        h.addStretch(1)
        g.addLayout(h, 1, 3)
        g.addWidget(QLabel("描边"), 2, 0)
        g.addWidget(self.sw, 2, 1)
        g.addWidget(self.sc, 2, 2)
        g.addWidget(QLabel("外描边"), 3, 0)
        g.addWidget(self.sw2, 3, 1)
        g.addWidget(self.sc2, 3, 2)
        h2 = QHBoxLayout()
        h2.addWidget(self.shadow)
        h2.addWidget(self.bg)
        h2.addWidget(self.bgc)
        h2.addWidget(QLabel("不透明"))
        h2.addWidget(self.bgo)
        h2.addStretch(1)
        g.addLayout(h2, 4, 0, 1, 4)
        lay.addLayout(g)
        for w in (self.font,):
            w.currentTextChanged.connect(self._emit)
        for w in (self.size, self.sw, self.sw2):
            w.valueChanged.connect(self._emit)
        self.bgo.valueChanged.connect(self._emit)
        for w in (self.gradient, self.shadow, self.bg):
            w.toggled.connect(self._emit)
        for w in (self.color, self.color2, self.sc, self.sc2, self.bgc):
            w.changed.connect(self._emit)
        self.base = textstyle.preset_style("白字黑边")
        self.set_style(self.base.to_dict())

    def _render_previews(self):
        out = {}
        for name in textstyle.PRESETS:
            st = textstyle.preset_style(name, size=60)
            im = textstyle.render_text("花字Ab", st, canvas_w=1080)
            out[name] = im
        return out

    def _set_previews(self, ims):
        for name, im in ims.items():
            it = self.presets.item_by_key(name)
            if it is None:
                continue
            q = pil_to_qimage(im)
            pm = QPixmap.fromImage(q).scaled(120, 36, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            canvas = QPixmap(120, 36)
            canvas.fill(QColor(theme.C["surface3"]))
            from PySide6.QtGui import QPainter
            p = QPainter(canvas)
            p.drawPixmap((120 - pm.width()) // 2, (36 - pm.height()) // 2, pm)
            p.end()
            it.setIcon(QIcon(canvas))

    def _apply_preset(self, name):
        st = textstyle.preset_style(name, size=self.size.value())
        self.set_style(st.to_dict())
        self._emit()

    def _emit(self, *_):
        if not self._lock:
            self.changed.emit()

    def set_style(self, d):
        st = textstyle.TextStyle.from_dict(d or {})
        self._lock = True
        self.base = st
        i = self.font.findText(st.font)
        self.font.setCurrentIndex(i if i >= 0 else 0)
        self.size.setValue(int(st.size))
        self.color.set_color(st.color)
        self.gradient.setChecked(bool(st.gradient))
        self.color2.set_color(st.color2)
        self.sw.setValue(int(st.stroke_width))
        self.sc.set_color(st.stroke_color)
        self.sw2.setValue(int(st.stroke2_width))
        self.sc2.set_color(st.stroke2_color)
        self.shadow.setChecked(bool(st.shadow))
        self.bg.setChecked(bool(st.bg))
        self.bgc.set_color(st.bg_color)
        self.bgo.setValue(float(st.bg_opacity))
        self._lock = False

    def style(self):
        st = textstyle.TextStyle.from_dict(self.base.to_dict())
        st.font = self.font.currentText()
        st.size = self.size.value()
        st.color = self.color.color
        st.gradient = self.gradient.isChecked()
        st.color2 = self.color2.color
        st.stroke_width = self.sw.value()
        st.stroke_color = self.sc.color
        st.stroke2_width = self.sw2.value()
        st.stroke2_color = self.sc2.color
        st.shadow = self.shadow.isChecked()
        st.bg = self.bg.isChecked()
        st.bg_color = self.bgc.color
        st.bg_opacity = self.bgo.value()
        return st.to_dict()

    def get_text(self):
        return self.text.toPlainText() if self.text else ""

    def set_text(self, t):
        if self.text:
            self._lock = True
            self.text.setPlainText(t or "")
            self._lock = False


# ----------------------------------------------------------------------------
class OverlayProps(QWidget):
    """选中贴纸/文字的属性：大小、旋转、透明度、时间、动画。"""
    changed = Signal()
    deleteRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.title = QLabel("选中元素属性")
        self.title.setObjectName("H2")
        lay.addWidget(self.title)
        self.w = dspin(1, 200, 30, 1, 0, "%", width=72)
        self.rot = dspin(-180, 180, 0, 5, 0, "°", width=68)
        self.op = dspin(5, 100, 100, 5, 0, "%", width=72)
        self.t0 = dspin(-600, 3600, 0, 0.5, 1, " 秒", width=90)
        self.t1 = dspin(-600, 3600, 0, 0.5, 1, " 秒", width=90)
        self.ain = QComboBox()
        self.aloop = QComboBox()
        self.aout = QComboBox()
        for cb, d in ((self.ain, ANIM_IN), (self.aloop, ANIM_LOOP), (self.aout, ANIM_OUT)):
            for k, v in d.items():
                cb.addItem(v, k)
        self.rand = QCheckBox("每条随机位置")
        lay.addLayout(form_row("大小", self.w, "旋转", self.rot, "透明", self.op, label_w=36))
        lay.addLayout(form_row("出现", self.t0, "消失", self.t1, label_w=36))
        lay.addWidget(hint("消失=0 表示到结尾；负数表示“距结尾 N 秒”。"))
        g = QGridLayout()
        g.setHorizontalSpacing(6)
        g.addWidget(QLabel("入场"), 0, 0)
        g.addWidget(self.ain, 0, 1)
        g.addWidget(QLabel("循环"), 0, 2)
        g.addWidget(self.aloop, 0, 3)
        g.addWidget(QLabel("出场"), 1, 0)
        g.addWidget(self.aout, 1, 1)
        g.addWidget(self.rand, 1, 2, 1, 2)
        lay.addLayout(g)
        self.delbtn = button("删除该元素", "trash", kind="Danger", slot=lambda: self.deleteRequested.emit())
        lay.addWidget(self.delbtn, 0, Qt.AlignLeft)
        for w in (self.w, self.rot, self.op, self.t0, self.t1):
            w.valueChanged.connect(self._emit)
        for w in (self.ain, self.aloop, self.aout):
            w.currentIndexChanged.connect(self._emit)
        self.rand.toggled.connect(self._emit)
        self.item = None
        self.setEnabled(False)

    def _emit(self, *_):
        if self._lock or self.item is None:
            return
        it = self.item
        it.w = self.w.value() / 100.0
        it.rotation = self.rot.value()
        it.opacity = self.op.value() / 100.0
        it.t0 = self.t0.value()
        it.t1 = self.t1.value()
        it.anim_in = self.ain.currentData()
        it.anim_loop = self.aloop.currentData()
        it.anim_out = self.aout.currentData()
        it.random_pos = self.rand.isChecked()
        self.changed.emit()

    def bind(self, item: OverlayItem, natural_w=None):
        self.item = item
        self.setEnabled(item is not None)
        if item is None:
            self.title.setText("选中元素属性（请在画面中点选贴纸/文字）")
            return
        self._lock = True
        self.title.setText("选中元素：" + ({"text": "文字", "sticker": "贴纸", "image": "图片"}.get(item.kind, "")))
        w = item.w if item.w and item.w > 0 else (natural_w or 0.3)
        self.w.setValue(w * 100)
        self.rot.setValue(float(item.rotation or 0))
        self.op.setValue(float(item.opacity if item.opacity is not None else 1) * 100)
        self.t0.setValue(float(item.t0 or 0))
        self.t1.setValue(float(item.t1 or 0))
        for cb, v in ((self.ain, item.anim_in), (self.aloop, item.anim_loop), (self.aout, item.anim_out)):
            i = cb.findData(v or "none")
            cb.setCurrentIndex(i if i >= 0 else 0)
        self.rand.setChecked(bool(item.random_pos))
        self._lock = False
