"""编辑画布：显示视频帧，可框选去水印区域、画笔涂抹蒙版、拖放/缩放贴纸与文字。

坐标约定：场景坐标 = 当前显示帧的像素坐标；保存时统一换算成 0~1 比例，适配任意分辨率。
"""
import math

from PySide6.QtCore import Qt, QRectF, QPointF, Signal, QSizeF
from PySide6.QtGui import (QPixmap, QImage, QPen, QColor, QBrush, QPainter, QFont, QCursor, QPainterPath)
from PySide6.QtWidgets import (QGraphicsView, QGraphicsScene, QGraphicsRectItem, QGraphicsPixmapItem,
                               QGraphicsItem, QGraphicsSimpleTextItem, QStyleOptionGraphicsItem, QStyle)

from mixer.models import Region, OverlayItem, CLEAN_METHODS
from . import theme

METHOD_COLORS = {"delogo": "#2F9BFF", "blur": "#A970FF", "mosaic": "#FFB020", "color": "#2FBF71"}
METHOD_SHORT = {"delogo": "智能填充", "blur": "模糊", "mosaic": "马赛克", "color": "色块"}


def pil_to_qimage(im):
    im = im.convert("RGBA")
    data = im.tobytes("raw", "RGBA")
    return QImage(data, im.width, im.height, im.width * 4, QImage.Format_RGBA8888).copy()


class _Handles:
    """统一的缩放手柄逻辑（以视图缩放换算手柄大小）。"""

    @staticmethod
    def hsize(item):
        sc = item.scene()
        if sc and sc.views():
            k = sc.views()[0].transform().m11() or 1.0
            return 9.0 / k
        return 9.0


class RegionItem(QGraphicsRectItem):
    """去水印区域框（可移动、8 向缩放）。"""

    def __init__(self, rect: QRectF, region: Region, canvas):
        super().__init__(QRectF(0, 0, rect.width(), rect.height()))
        self.setPos(rect.topLeft())
        self.region = region
        self.canvas = canvas
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable |
                      QGraphicsItem.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.setZValue(20)
        self._drag = None
        self._start = None

    def color(self):
        return QColor(METHOD_COLORS.get(self.region.method, "#2F9BFF"))

    def paint(self, p, opt, w=None):
        r = self.rect()
        col = self.color()
        fill = QColor(col)
        fill.setAlpha(50)
        p.setBrush(fill)
        pen = QPen(col, 0)
        pen.setCosmetic(True)
        pen.setWidthF(2)
        if not self.isSelected():
            pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        p.drawRect(r)
        # 标签
        k = self.scene().views()[0].transform().m11() if self.scene() and self.scene().views() else 1
        f = QFont("Microsoft YaHei UI")
        f.setPixelSize(max(6, int(12 / max(0.05, k))))
        p.setFont(f)
        label = METHOD_SHORT.get(self.region.method, "")
        if self.region.t0 > 0 or self.region.t1 > 0:
            label += f" {self.region.t0:.1f}s~" + (f"{self.region.t1:.1f}s" if self.region.t1 > 0 else "结尾")
        tw = p.fontMetrics().horizontalAdvance(label) + 8 / k
        th = p.fontMetrics().height()
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawRect(QRectF(r.left(), r.top() - th, tw, th))
        p.setPen(QColor("white"))
        p.drawText(QRectF(r.left() + 4 / k, r.top() - th, tw, th), Qt.AlignVCenter | Qt.AlignLeft, label)
        if self.isSelected():
            hs = _Handles.hsize(self)
            p.setBrush(QColor("white"))
            hp = QPen(col)
            hp.setCosmetic(True)
            p.setPen(hp)
            for c in self._handle_points():
                p.drawRect(QRectF(c.x() - hs / 2, c.y() - hs / 2, hs, hs))

    def boundingRect(self):
        hs = _Handles.hsize(self)
        r = self.rect().adjusted(-hs, -hs * 3, hs, hs)
        return r

    def _handle_points(self):
        r = self.rect()
        return [r.topLeft(), QPointF(r.center().x(), r.top()), r.topRight(), QPointF(r.right(), r.center().y()),
                r.bottomRight(), QPointF(r.center().x(), r.bottom()), r.bottomLeft(), QPointF(r.left(), r.center().y())]

    def _hit(self, pos):
        hs = _Handles.hsize(self) * 1.2
        for i, c in enumerate(self._handle_points()):
            if abs(pos.x() - c.x()) <= hs and abs(pos.y() - c.y()) <= hs:
                return i
        return None

    def hoverMoveEvent(self, e):
        h = self._hit(e.pos()) if self.isSelected() else None
        cur = {0: Qt.SizeFDiagCursor, 4: Qt.SizeFDiagCursor, 2: Qt.SizeBDiagCursor, 6: Qt.SizeBDiagCursor,
               1: Qt.SizeVerCursor, 5: Qt.SizeVerCursor, 3: Qt.SizeHorCursor, 7: Qt.SizeHorCursor}.get(h)
        self.setCursor(cur if cur is not None else Qt.SizeAllCursor)
        super().hoverMoveEvent(e)

    def mousePressEvent(self, e):
        h = self._hit(e.pos()) if self.isSelected() else None
        if h is not None and e.button() == Qt.LeftButton:
            self._drag = h
            self._start = (e.scenePos(), QRectF(self.pos(), self.rect().size()))
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            sp, r0 = self._start
            d = e.scenePos() - sp
            x0, y0, x1, y1 = r0.left(), r0.top(), r0.right(), r0.bottom()
            h = self._drag
            if h in (0, 6, 7):
                x0 += d.x()
            if h in (2, 3, 4):
                x1 += d.x()
            if h in (0, 1, 2):
                y0 += d.y()
            if h in (4, 5, 6):
                y1 += d.y()
            fr = self.canvas.frame_rect()
            x0, x1 = sorted((max(fr.left(), x0), min(fr.right(), x1)))
            y0, y1 = sorted((max(fr.top(), y0), min(fr.bottom(), y1)))
            if x1 - x0 < 4:
                x1 = x0 + 4
            if y1 - y0 < 4:
                y1 = y0 + 4
            self.prepareGeometryChange()
            self.setPos(x0, y0)
            self.setRect(QRectF(0, 0, x1 - x0, y1 - y0))
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        was = self._drag is not None
        self._drag = None
        super().mouseReleaseEvent(e)
        self.canvas.sync_region(self)
        if was:
            self.update()

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and self.scene():
            fr = self.canvas.frame_rect()
            r = self.rect()
            x = min(max(value.x(), fr.left()), fr.right() - r.width())
            y = min(max(value.y(), fr.top()), fr.bottom() - r.height())
            return QPointF(x, y)
        if change == QGraphicsItem.ItemSelectedHasChanged:
            self.canvas.on_item_selected()
        return super().itemChange(change, value)

    def scene_rect(self):
        return QRectF(self.pos(), self.rect().size())


class OverlayGfx(QGraphicsPixmapItem):
    """贴纸/文字/图片叠加元素：拖动移动，右下角手柄等比缩放。"""

    def __init__(self, item: OverlayItem, pixmap: QPixmap, canvas):
        super().__init__(pixmap)
        self.item = item
        self.canvas = canvas
        self.src_w = pixmap.width()
        self.setTransformationMode(Qt.SmoothTransformation)
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable |
                      QGraphicsItem.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.setZValue(30)
        self._resizing = None
        self.setTransformOriginPoint(pixmap.width() / 2, pixmap.height() / 2)

    def apply_geometry(self):
        """按 item(cx,cy,w,rotation,opacity) 摆放。变换原点固定在图片中心，缩放/旋转都围绕中心。"""
        fr = self.canvas.frame_rect()
        pw = max(1, self.pixmap().width())
        ph = max(1, self.pixmap().height())
        target_w = max(4.0, self.item.w * fr.width()) if self.item.w and self.item.w > 0 else pw
        self.setTransformOriginPoint(pw / 2.0, ph / 2.0)
        self.setScale(target_w / pw)
        self.setRotation(float(self.item.rotation or 0))
        cx = fr.left() + self.item.cx * fr.width()
        cy = fr.top() + self.item.cy * fr.height()
        self.setPos(cx - pw / 2.0, cy - ph / 2.0)
        self.setOpacity(float(self.item.opacity if self.item.opacity is not None else 1.0))

    def paint(self, p, opt, w=None):
        o = QStyleOptionGraphicsItem(opt)
        o.state &= ~QStyle.State_Selected  # 去掉 Qt 默认的虚线选中框，用自定义的
        super().paint(p, o, w)
        if self.isSelected():
            pen = QPen(QColor(theme.C["accent"]))
            pen.setCosmetic(True)
            pen.setWidthF(1.5)
            pen.setStyle(Qt.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            r = QRectF(0, 0, self.pixmap().width(), self.pixmap().height())
            p.drawRect(r)
            hs = _Handles.hsize(self) / max(0.01, self.scale()) * 1.3
            p.setBrush(QColor("white"))
            p.drawEllipse(QRectF(r.right() - hs / 2, r.bottom() - hs / 2, hs, hs))

    def boundingRect(self):
        hs = _Handles.hsize(self) / max(0.01, self.scale()) * 1.3
        return QRectF(0, 0, self.pixmap().width(), self.pixmap().height()).adjusted(-hs, -hs, hs, hs)

    def _on_handle(self, pos):
        hs = _Handles.hsize(self) / max(0.01, self.scale()) * 1.6
        r = QRectF(0, 0, self.pixmap().width(), self.pixmap().height())
        return abs(pos.x() - r.right()) <= hs and abs(pos.y() - r.bottom()) <= hs

    def hoverMoveEvent(self, e):
        self.setCursor(Qt.SizeFDiagCursor if (self.isSelected() and self._on_handle(e.pos())) else Qt.SizeAllCursor)
        super().hoverMoveEvent(e)

    def mousePressEvent(self, e):
        if self.isSelected() and self._on_handle(e.pos()) and e.button() == Qt.LeftButton:
            center = self.mapToScene(QPointF(self.pixmap().width() / 2, self.pixmap().height() / 2))
            self._resizing = (center, e.scenePos(), self.item.w if self.item.w > 0 else
                              self.pixmap().width() * self.scale() / self.canvas.frame_rect().width())
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._resizing:
            center, start, w0 = self._resizing
            d0 = math.hypot(start.x() - center.x(), start.y() - center.y()) or 1
            d1 = math.hypot(e.scenePos().x() - center.x(), e.scenePos().y() - center.y())
            self.item.w = max(0.02, min(2.0, w0 * d1 / d0))
            self.apply_geometry()
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._resizing = None
        super().mouseReleaseEvent(e)
        self.canvas.sync_overlay(self)

    def mouseDoubleClickEvent(self, e):
        self.canvas.overlayDoubleClicked.emit(self.item)
        super().mouseDoubleClickEvent(e)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemSelectedHasChanged:
            self.canvas.on_item_selected()
        return super().itemChange(change, value)


class EditorCanvas(QGraphicsView):
    regionsChanged = Signal()
    overlaysChanged = Signal()
    maskChanged = Signal()
    selectionChanged = Signal(object)
    overlayDoubleClicked = Signal(object)
    regionCreated = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setBackgroundBrush(QColor(theme.C["input"]))
        self.frame_item = QGraphicsPixmapItem()
        self.frame_item.setZValue(0)
        self.frame_item.setTransformationMode(Qt.SmoothTransformation)
        self.scene().addItem(self.frame_item)
        self.mask_item = QGraphicsPixmapItem()
        self.mask_item.setZValue(10)
        self.mask_item.setOpacity(0.55)
        self.scene().addItem(self.mask_item)
        self.mask_img = None
        self.crop_items = []
        self.crop = (0.0, 0.0, 0.0, 0.0)
        self.mode = "select"
        self.brush = 24
        self.regions = []
        self.overlays = []
        self._rubber = None
        self._rb_start = None
        self._painting = False
        self._last_pt = None
        self._fitted = True
        self.allow_regions = True
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.empty_text = QGraphicsSimpleTextItem("请先在左侧选择素材")
        self.empty_text.setBrush(QColor(theme.C["sub"]))
        f = QFont("Microsoft YaHei UI")
        f.setPixelSize(16)
        self.empty_text.setFont(f)
        self.scene().addItem(self.empty_text)

    # ------------------------------------------------------------ 帧
    def frame_rect(self):
        pm = self.frame_item.pixmap()
        if pm.isNull():
            return QRectF(0, 0, 1280, 720)
        return QRectF(0, 0, pm.width(), pm.height())

    def set_frame(self, pixmap: QPixmap, keep_items=True):
        old = self.frame_rect()
        had = not self.frame_item.pixmap().isNull()
        self.frame_item.setPixmap(pixmap)
        self.empty_text.setVisible(pixmap.isNull())
        fr = self.frame_rect()
        self.scene().setSceneRect(fr.adjusted(-40, -40, 40, 40))
        if self.mask_img is None or self.mask_img.size() != pixmap.size():
            self._reset_mask(pixmap.width(), pixmap.height(), keep=self.mask_img if had else None)
        if had and (abs(old.width() - fr.width()) > 0.5 or abs(old.height() - fr.height()) > 0.5):
            self.relayout()
        self.update_crop()
        if self._fitted:
            self.fit()

    def fit(self):
        self._fitted = True
        self.fitInView(self.frame_rect().adjusted(-10, -10, 10, 10), Qt.KeepAspectRatio)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._fitted:
            self.fit()

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            k = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
            self.scale(k, k)
            self._fitted = False
            return
        super().wheelEvent(e)

    # ------------------------------------------------------------ 区域
    def clear_regions(self):
        for it in self.regions:
            self.scene().removeItem(it)
        self.regions = []

    def set_regions(self, regions):
        self.clear_regions()
        fr = self.frame_rect()
        for r in regions:
            if isinstance(r, dict):
                r = Region.from_dict(r)
            rect = QRectF(fr.left() + r.x * fr.width(), fr.top() + r.y * fr.height(), r.w * fr.width(),
                          r.h * fr.height())
            it = RegionItem(rect, r, self)
            self.scene().addItem(it)
            self.regions.append(it)

    def add_region(self, region: Region, select=True):
        fr = self.frame_rect()
        rect = QRectF(fr.left() + region.x * fr.width(), fr.top() + region.y * fr.height(),
                      region.w * fr.width(), region.h * fr.height())
        it = RegionItem(rect, region, self)
        self.scene().addItem(it)
        self.regions.append(it)
        if select:
            self.scene().clearSelection()
            it.setSelected(True)
        self.regionsChanged.emit()
        return it

    def sync_region(self, it: RegionItem):
        fr = self.frame_rect()
        r = it.scene_rect()
        it.region.x = max(0.0, (r.left() - fr.left()) / fr.width())
        it.region.y = max(0.0, (r.top() - fr.top()) / fr.height())
        it.region.w = min(1.0, r.width() / fr.width())
        it.region.h = min(1.0, r.height() / fr.height())
        self.regionsChanged.emit()

    def region_dicts(self):
        return [it.region.to_dict() for it in self.regions]

    def remove_item(self, it):
        if it in self.regions:
            self.regions.remove(it)
            self.scene().removeItem(it)
            self.regionsChanged.emit()
        elif it in self.overlays:
            self.overlays.remove(it)
            self.scene().removeItem(it)
            self.overlaysChanged.emit()
        self.selectionChanged.emit(None)

    # ------------------------------------------------------------ 叠加
    def clear_overlays(self):
        for it in self.overlays:
            self.scene().removeItem(it)
        self.overlays = []

    def add_overlay(self, item: OverlayItem, pixmap: QPixmap, select=True):
        g = OverlayGfx(item, pixmap, self)
        self.scene().addItem(g)
        g.apply_geometry()
        self.overlays.append(g)
        if select:
            self.scene().clearSelection()
            g.setSelected(True)
        return g

    def sync_overlay(self, g: OverlayGfx):
        fr = self.frame_rect()
        c = g.mapToScene(QPointF(g.pixmap().width() / 2, g.pixmap().height() / 2))
        g.item.cx = (c.x() - fr.left()) / fr.width()
        g.item.cy = (c.y() - fr.top()) / fr.height()
        self.overlaysChanged.emit()

    def overlay_dicts(self):
        return [g.item.to_dict() for g in self.overlays]

    def relayout(self):
        """帧尺寸变化后，按比例重新摆放全部元素。"""
        regs = [it.region for it in self.regions]
        self.set_regions(regs)
        for g in self.overlays:
            g.apply_geometry()

    def selected(self):
        items = self.scene().selectedItems()
        return items[0] if items else None

    def on_item_selected(self):
        self.selectionChanged.emit(self.selected())

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            it = self.selected()
            if it is not None:
                self.remove_item(it)
                return
        super().keyPressEvent(e)

    # ------------------------------------------------------------ 裁剪
    def set_crop(self, l, t, r, b):
        self.crop = (l, t, r, b)
        self.update_crop()

    def update_crop(self):
        for it in self.crop_items:
            self.scene().removeItem(it)
        self.crop_items = []
        l, t, r, b = self.crop
        if not any((l, t, r, b)):
            return
        fr = self.frame_rect()
        W, H = fr.width(), fr.height()
        shade = QColor(0, 0, 0, 150)
        rects = [QRectF(0, 0, W, H * t), QRectF(0, H * (1 - b), W, H * b),
                 QRectF(0, H * t, W * l, H * (1 - t - b)), QRectF(W * (1 - r), H * t, W * r, H * (1 - t - b))]
        for rc in rects:
            if rc.width() > 0 and rc.height() > 0:
                it = self.scene().addRect(rc, QPen(Qt.NoPen), QBrush(shade))
                it.setZValue(15)
                self.crop_items.append(it)
        pen = QPen(QColor(theme.C["warning"]))
        pen.setCosmetic(True)
        pen.setStyle(Qt.DashLine)
        it = self.scene().addRect(QRectF(W * l, H * t, W * (1 - l - r), H * (1 - t - b)), pen)
        it.setZValue(16)
        self.crop_items.append(it)

    # ------------------------------------------------------------ 蒙版
    def _reset_mask(self, w, h, keep=None):
        img = QImage(max(1, w), max(1, h), QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        if keep is not None and not keep.isNull():
            p = QPainter(img)
            p.drawImage(QRectF(0, 0, w, h), keep)
            p.end()
        self.mask_img = img
        self.mask_item.setPixmap(QPixmap.fromImage(img))

    def clear_mask(self):
        fr = self.frame_rect()
        self._reset_mask(int(fr.width()), int(fr.height()))
        self.maskChanged.emit()

    def _alpha_array(self):
        import numpy as np
        a = self.mask_img.convertToFormat(QImage.Format_Alpha8)
        bpl = a.bytesPerLine()
        arr = np.frombuffer(a.constBits(), dtype=np.uint8, count=bpl * a.height()).reshape(a.height(), bpl)
        return arr[:, : a.width()].copy()

    def has_mask(self):
        if self.mask_img is None:
            return False
        return bool((self._alpha_array() > 20).any())

    def load_mask(self, path):
        img = QImage(path)
        fr = self.frame_rect()
        out = QImage(int(fr.width()), int(fr.height()), QImage.Format_ARGB32_Premultiplied)
        out.fill(Qt.transparent)
        if not img.isNull():
            g = img.convertToFormat(QImage.Format_Grayscale8).scaled(out.size())
            p = QPainter(out)
            red = QImage(out.size(), QImage.Format_ARGB32_Premultiplied)
            red.fill(QColor(255, 40, 60))
            red.setAlphaChannel(g)
            p.drawImage(0, 0, red)
            p.end()
        self.mask_img = out
        self.mask_item.setPixmap(QPixmap.fromImage(out))

    def save_mask(self, path):
        """导出为黑底白色的灰度蒙版 PNG。"""
        if self.mask_img is None:
            return False
        from PIL import Image
        arr = (self._alpha_array() > 20).astype("uint8") * 255
        Image.fromarray(arr, "L").save(path)
        return True

    def _paint_to(self, pt: QPointF, erase=False):
        if self.mask_img is None:
            return
        p = QPainter(self.mask_img)
        p.setRenderHint(QPainter.Antialiasing)
        if erase:
            p.setCompositionMode(QPainter.CompositionMode_Clear)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 40, 60))
        r = self.brush / 2.0
        if self._last_pt is not None:
            pen = QPen(QColor(255, 40, 60), self.brush, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            p.setPen(pen)
            p.drawLine(self._last_pt, pt)
        else:
            p.drawEllipse(pt, r, r)
        p.end()
        self._last_pt = pt
        self.mask_item.setPixmap(QPixmap.fromImage(self.mask_img))

    # ------------------------------------------------------------ 鼠标
    def set_mode(self, mode):
        self.mode = mode
        if mode in ("brush", "erase"):
            self.setCursor(Qt.CrossCursor)
            self.setDragMode(QGraphicsView.NoDrag)
        elif mode == "rect":
            self.setCursor(Qt.CrossCursor)
            self.setDragMode(QGraphicsView.NoDrag)
        else:
            self.unsetCursor()
            self.setDragMode(QGraphicsView.NoDrag)

    def mousePressEvent(self, e):
        if e.button() == Qt.MiddleButton:
            self.setDragMode(QGraphicsView.ScrollHandDrag)
            self._fitted = False
            return super().mousePressEvent(e)
        sp = self.mapToScene(e.position().toPoint())
        if self.mode == "rect" and e.button() == Qt.LeftButton and self.allow_regions:
            self._rb_start = sp
            pen = QPen(QColor(theme.C["accent"]))
            pen.setCosmetic(True)
            pen.setStyle(Qt.DashLine)
            self._rubber = self.scene().addRect(QRectF(sp, sp), pen, QBrush(QColor(47, 155, 255, 40)))
            self._rubber.setZValue(50)
            return
        if self.mode in ("brush", "erase") and e.button() == Qt.LeftButton and self.allow_regions:
            self._painting = True
            self._last_pt = None
            self._paint_to(sp, erase=self.mode == "erase")
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        sp = self.mapToScene(e.position().toPoint())
        if self._rubber is not None:
            fr = self.frame_rect()
            x0, y0 = self._rb_start.x(), self._rb_start.y()
            x1 = min(max(sp.x(), fr.left()), fr.right())
            y1 = min(max(sp.y(), fr.top()), fr.bottom())
            self._rubber.setRect(QRectF(QPointF(min(x0, x1), min(y0, y1)), QPointF(max(x0, x1), max(y0, y1))))
            return
        if self._painting:
            self._paint_to(sp, erase=self.mode == "erase")
            return
        if self.mode in ("brush", "erase"):
            self.viewport().update()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MiddleButton:
            self.setDragMode(QGraphicsView.NoDrag)
            return super().mouseReleaseEvent(e)
        if self._rubber is not None:
            r = self._rubber.rect()
            self.scene().removeItem(self._rubber)
            self._rubber = None
            fr = self.frame_rect()
            if r.width() > 6 and r.height() > 6:
                reg = Region(x=(r.left() - fr.left()) / fr.width(), y=(r.top() - fr.top()) / fr.height(),
                             w=r.width() / fr.width(), h=r.height() / fr.height())
                it = self.add_region(reg)
                self.regionCreated.emit(it)
            self.set_mode("select")
            return
        if self._painting:
            self._painting = False
            self._last_pt = None
            self.maskChanged.emit()
            return
        super().mouseReleaseEvent(e)

    def drawForeground(self, p, rect):
        # 画笔光标
        if self.mode in ("brush", "erase") and self.underMouse():
            pos = self.mapToScene(self.mapFromGlobal(QCursor.pos()))
            pen = QPen(QColor(255, 255, 255, 200))
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(pos, self.brush / 2, self.brush / 2)

    def enterEvent(self, e):
        self.viewport().update()
        super().enterEvent(e)
