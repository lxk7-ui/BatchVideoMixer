"""叠加元素控制器：把画布、贴纸库、花字编辑器、属性面板串起来（素材处理页和包装设计器共用）。"""
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QPixmap

from mixer import overlays as ovl, textstyle
from mixer.models import OverlayItem
from .canvas import pil_to_qimage, OverlayGfx


class OverlayController(QObject):
    changed = Signal()

    def __init__(self, canvas, browser, text_editor, props, src_w_func, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self.browser = browser
        self.te = text_editor
        self.props = props
        self.src_w = src_w_func
        canvas.selectionChanged.connect(self._on_select)
        canvas.overlaysChanged.connect(self._on_canvas_changed)
        browser.picked.connect(self.add_sticker)
        text_editor.changed.connect(self._on_text_changed)
        props.changed.connect(self._on_props)
        props.deleteRequested.connect(self._delete)

    # ------------------------------------------------------------
    def _pixmap(self, item: OverlayItem):
        W = max(64, int(self.src_w() or 1080))
        try:
            im = ovl.load_item_image(item, W, ctx={"序号": "001", "日期": "2026-01-01", "素材名": "素材",
                                                  "任务名": "任务", "时长": "30秒"})
        except Exception:
            im = None
        if im is None:
            pm = QPixmap(64, 64)
            pm.fill()
            return pm, 64
        return QPixmap.fromImage(pil_to_qimage(im)), im.width

    def _selected_gfx(self):
        it = self.canvas.selected()
        return it if isinstance(it, OverlayGfx) else None

    def add_sticker(self, sticker):
        item = OverlayItem(kind="sticker", src=sticker.path, cx=0.5, cy=0.5, w=0.25,
                           anim_loop=sticker.anim_hint or "none", name=sticker.name)
        pm, _ = self._pixmap(item)
        self.canvas.add_overlay(item, pm)
        self.changed.emit()

    def add_image(self, path, w=0.25):
        item = OverlayItem(kind="image", src=path, cx=0.5, cy=0.5, w=w, remove_bg=ovl.detect_plain_bg(path))
        pm, _ = self._pixmap(item)
        self.canvas.add_overlay(item, pm)
        self.changed.emit()

    def add_text(self, text=None):
        text = (text or self.te.get_text() or "双击编辑文字").strip()
        item = OverlayItem(kind="text", text=text, style=self.te.style(), cx=0.5, cy=0.3, w=0)
        pm, natural = self._pixmap(item)
        item.w = min(0.95, natural / float(max(64, self.src_w())))
        self.canvas.add_overlay(item, pm)
        self.changed.emit()

    def load(self, items):
        self.canvas.clear_overlays()
        for d in items or []:
            item = OverlayItem.from_dict(d) if isinstance(d, dict) else d
            pm, natural = self._pixmap(item)
            if item.kind == "text" and (not item.w or item.w <= 0):
                item.w = min(0.95, natural / float(max(64, self.src_w())))
            self.canvas.add_overlay(item, pm, select=False)
        self.props.bind(None)

    def items(self):
        return self.canvas.overlay_dicts()

    def refresh_all(self):
        """源宽度变化时重新渲染文字（字号按画面宽等比）。"""
        for g in self.canvas.overlays:
            pm, _ = self._pixmap(g.item)
            g.setPixmap(pm)
            g.apply_geometry()

    # ------------------------------------------------------------
    def _on_select(self, it):
        if isinstance(it, OverlayGfx):
            self.props.bind(it.item)
            if it.item.kind == "text":
                self.te.set_style(it.item.style)
                self.te.set_text(it.item.text)
        else:
            self.props.bind(None)

    def _on_canvas_changed(self):
        g = self._selected_gfx()
        if g is not None:
            self.props.bind(g.item)
        self.changed.emit()

    def _on_props(self):
        g = self._selected_gfx()
        if g is not None:
            g.apply_geometry()
            self.changed.emit()

    def _on_text_changed(self):
        g = self._selected_gfx()
        if g is None or g.item.kind != "text":
            return
        old_pm_w = max(1, g.pixmap().width())
        g.item.style = self.te.style()
        t = self.te.get_text().strip()
        if t:
            g.item.text = t
        pm, natural = self._pixmap(g.item)
        ratio = natural / float(old_pm_w)
        g.setPixmap(pm)
        if g.item.w and g.item.w > 0:
            g.item.w = max(0.02, min(1.5, g.item.w * ratio))
        g.apply_geometry()
        self.props.bind(g.item)
        self.changed.emit()

    def _delete(self):
        g = self._selected_gfx()
        if g is not None:
            self.canvas.remove_item(g)
            self.changed.emit()
