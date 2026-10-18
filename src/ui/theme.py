"""主题与图标：深色（默认，接近剪映风格）/ 浅色两套配色；图标用 QPainter 矢量绘制，无需外部资源。"""
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QFont, QPolygonF

DARK = {
    "bg": "#16171B", "surface": "#1F2127", "surface2": "#272A31", "surface3": "#30343C", "border": "#363A44",
    "text": "#E8EAED", "sub": "#9AA0AA", "accent": "#2F9BFF", "accent_hover": "#4AA9FF", "accent_text": "#FFFFFF",
    "danger": "#FF5A5F", "success": "#2FBF71", "warning": "#FFB020", "sel": "rgba(47,155,255,0.20)",
    "input": "#15161A",
}
LIGHT = {
    "bg": "#F3F4F7", "surface": "#FFFFFF", "surface2": "#F6F7F9", "surface3": "#ECEEF2", "border": "#DADDE3",
    "text": "#1F2329", "sub": "#6B7280", "accent": "#1677FF", "accent_hover": "#3C8CFF", "accent_text": "#FFFFFF",
    "danger": "#F5474D", "success": "#16A05D", "warning": "#E8930C", "sel": "rgba(22,119,255,0.13)",
    "input": "#FFFFFF",
}

C = dict(DARK)


def set_palette(name):
    C.clear()
    C.update(LIGHT if name == "light" else DARK)


def _indicator_images():
    """生成勾选、下拉箭头等小图（QSS 里引用），存到缓存目录。"""
    import os
    from mixer import paths
    d = paths.cache_dir("ui")
    out = {}
    specs = {
        "check": ("check", "#FFFFFF"),
        "down": ("down", C["sub"]),
        "up": ("up", C["sub"]),
    }
    for key, (kind, color) in specs.items():
        fn = os.path.join(d, f"{key}_{color.strip('#')}.png")
        if not os.path.isfile(fn):
            pm = QPixmap(32, 32)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            pen = QPen(QColor(color), 4 if kind == "check" else 3.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            p.setPen(pen)
            if kind == "check":
                p.drawPolyline(QPolygonF([QPointF(7, 16.5), QPointF(13.5, 23), QPointF(25, 9.5)]))
            elif kind == "down":
                p.drawPolyline(QPolygonF([QPointF(9, 12.5), QPointF(16, 19.5), QPointF(23, 12.5)]))
            else:
                p.drawPolyline(QPolygonF([QPointF(9, 19.5), QPointF(16, 12.5), QPointF(23, 19.5)]))
            p.end()
            pm.save(fn)
        out[key] = fn.replace("\\", "/")
    return out


def qss():
    c = C
    im = _indicator_images()
    return f"""
* {{ font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans SC", "PingFang SC", sans-serif; font-size: 13px; }}
QMainWindow, QDialog {{ background: {c['bg']}; }}
QWidget {{ color: {c['text']}; }}
QWidget#Page, QWidget#PageInner {{ background: {c['bg']}; }}
QFrame#Card {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 10px; }}
QFrame#Sidebar {{ background: {c['surface']}; border-right: 1px solid {c['border']}; }}
QLabel#Title {{ font-size: 18px; font-weight: 600; }}
QLabel#H2 {{ font-size: 14px; font-weight: 600; }}
QLabel#Sub, QLabel[sub="true"] {{ color: {c['sub']}; }}
QLabel#Brand {{ font-size: 15px; font-weight: 700; color: {c['text']}; }}
QLabel#Badge {{ background: {c['surface3']}; border-radius: 8px; padding: 2px 8px; color: {c['sub']}; }}
QToolTip {{ background: {c['surface2']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 6px; }}

QPushButton {{ background: {c['surface3']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 6px 14px; }}
QPushButton:hover {{ border-color: {c['accent']}; }}
QPushButton:pressed {{ background: {c['surface2']}; }}
QPushButton:disabled {{ color: {c['sub']}; background: {c['surface2']}; border-color: {c['surface3']}; }}
QPushButton:checked {{ background: {c['sel']}; border-color: {c['accent']}; color: {c['accent']}; }}
QPushButton#Primary {{ background: {c['accent']}; color: {c['accent_text']}; border: none; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {c['accent_hover']}; }}
QPushButton#Primary:disabled {{ background: {c['surface3']}; color: {c['sub']}; }}
QPushButton#Big {{ padding: 10px 26px; font-size: 15px; border-radius: 8px; }}
QPushButton#Danger {{ background: transparent; color: {c['danger']}; border: 1px solid {c['danger']}; }}
QPushButton#Danger:hover {{ background: {c['danger']}; color: white; }}
QPushButton#Flat {{ background: transparent; border: none; color: {c['accent']}; padding: 4px 6px; }}
QPushButton#Flat:hover {{ text-decoration: underline; }}
QPushButton#Tool {{ padding: 5px 10px; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 4px; }}
QToolButton:hover {{ background: {c['surface3']}; }}
QToolButton:checked {{ background: {c['sel']}; border-color: {c['accent']}; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit, QTimeEdit {{
    background: {c['input']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 5px 8px;
    selection-background-color: {c['accent']}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {c['accent']}; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{ color: {c['sub']}; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {{ width: 16px; border: none; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: url({im['down']}); width: 12px; height: 12px; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({im['up']}); width: 9px; height: 9px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({im['down']}); width: 9px; height: 9px; }}
QComboBox QAbstractItemView {{ background: {c['surface2']}; border: 1px solid {c['border']}; selection-background-color: {c['accent']}; outline: none; }}

QCheckBox, QRadioButton {{ spacing: 6px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; }}
QCheckBox::indicator {{ border: 1px solid {c['border']}; border-radius: 4px; background: {c['input']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; image: url({im['check']}); }}
QListWidget::indicator {{ width: 15px; height: 15px; border: 1px solid {c['border']}; border-radius: 4px; background: {c['input']}; }}
QListWidget::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; image: url({im['check']}); }}
QRadioButton::indicator {{ border: 1px solid {c['border']}; border-radius: 8px; background: {c['input']}; }}
QRadioButton::indicator:checked {{ background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, stop:0 white, stop:0.35 white, stop:0.45 {c['accent']}, stop:1 {c['accent']}); border-color: {c['accent']}; }}

QSlider::groove:horizontal {{ height: 4px; background: {c['surface3']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: white; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; border: 2px solid {c['accent']}; }}

QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {c['sub']}; padding: 8px 14px; border: none; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {c['text']}; border-bottom: 2px solid {c['accent']}; font-weight: 600; }}
QTabBar::tab:hover {{ color: {c['text']}; }}

QListWidget, QTableWidget, QTreeWidget {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 8px; outline: none; }}
QListWidget::item, QTableWidget::item {{ padding: 2px; }}
QListWidget::item:selected, QTableWidget::item:selected {{ background: {c['sel']}; color: {c['text']}; }}
QListWidget::item:hover {{ background: {c['surface2']}; }}
QHeaderView::section {{ background: {c['surface2']}; color: {c['sub']}; border: none; border-bottom: 1px solid {c['border']}; padding: 6px; }}
QTableCornerButton::section {{ background: {c['surface2']}; border: none; }}

QListWidget#NavList {{ background: transparent; border: none; }}
QListWidget#NavList::item {{ padding: 10px 12px; border-radius: 8px; margin: 2px 8px; color: {c['sub']}; }}
QListWidget#NavList::item:selected {{ background: {c['sel']}; color: {c['text']}; }}
QListWidget#NavList::item:hover {{ background: {c['surface2']}; color: {c['text']}; }}

QScrollArea {{ border: none; background: transparent; }}
QWidget#ScrollInner {{ background: transparent; }}
QTabWidget#Panel QTabBar::tab {{ padding: 8px 10px; }}
QTabWidget#Panel::pane {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 10px; top: -1px; }}
QTabWidget#Panel > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {c['surface3']}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {c['border']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {c['surface3']}; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QProgressBar {{ background: {c['surface3']}; border: none; border-radius: 5px; height: 10px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 5px; }}
QGroupBox {{ border: 1px solid {c['border']}; border-radius: 8px; margin-top: 14px; padding: 12px 10px 10px 10px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {c['sub']}; }}
QMenu {{ background: {c['surface2']}; border: 1px solid {c['border']}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px; border-radius: 4px; }}
QMenu::item:selected {{ background: {c['accent']}; color: white; }}
QMenu::separator {{ height: 1px; background: {c['border']}; margin: 4px 6px; }}
QSplitter::handle {{ background: {c['bg']}; }}
QPlainTextEdit#Log {{ font-family: Consolas, "Microsoft YaHei UI"; font-size: 12px; background: {c['input']}; }}
QGraphicsView {{ background: {c['input']}; border: 1px solid {c['border']}; border-radius: 8px; }}
QStatusBar {{ background: {c['surface']}; color: {c['sub']}; }}
"""


# ----------------------------------------------------------------------------
# 矢量图标
# ----------------------------------------------------------------------------
def icon(name, color=None, size=40):
    col = QColor(color or C["text"])
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 24.0
    pen = QPen(col, 1.8 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)

    def R(x, y, w, h):
        return QRectF(x * s, y * s, w * s, h * s)

    def P(x, y):
        return QPointF(x * s, y * s)

    if name == "film":
        p.drawRoundedRect(R(3, 5, 18, 14), 2 * s, 2 * s)
        p.drawLine(P(7, 5), P(7, 19))
        p.drawLine(P(17, 5), P(17, 19))
        for y in (8.5, 12, 15.5):
            p.drawLine(P(3, y), P(7, y))
            p.drawLine(P(17, y), P(21, y))
    elif name == "wand":
        p.drawLine(P(4, 20), P(15, 9))
        p.drawLine(P(14, 7), P(17, 10))
        for (a, b, c2, d) in ((18, 3, 18, 6), (16.5, 4.5, 19.5, 4.5), (8, 4, 8, 6), (7, 5, 9, 5), (20, 12, 20, 14),
                              (19, 13, 21, 13)):
            p.drawLine(P(a, b), P(c2, d))
    elif name == "sticker":
        path = QPainterPath()
        path.moveTo(P(5, 4))
        path.lineTo(P(19, 4))
        path.quadTo(P(20, 4), P(20, 5))
        path.lineTo(P(20, 13))
        path.lineTo(P(13, 20))
        path.lineTo(P(5, 20))
        path.quadTo(P(4, 20), P(4, 19))
        path.lineTo(P(4, 5))
        path.quadTo(P(4, 4), P(5, 4))
        p.drawPath(path)
        p.drawLine(P(20, 13), P(14, 13))
        p.drawLine(P(14, 13), P(13, 20))
        p.drawPoint(P(9, 10))
        p.drawPoint(P(14.5, 10))
        p.drawArc(R(8, 11, 7, 4), 200 * 16, 140 * 16)
    elif name == "history":
        p.drawEllipse(R(4, 4, 16, 16))
        p.drawLine(P(12, 8), P(12, 12))
        p.drawLine(P(12, 12), P(15, 14))
    elif name == "settings":
        p.drawEllipse(R(9, 9, 6, 6))
        for k in range(8):
            import math
            a = k * math.pi / 4
            p.drawLine(P(12 + 6 * math.cos(a), 12 + 6 * math.sin(a)), P(12 + 8.5 * math.cos(a), 12 + 8.5 * math.sin(a)))
        p.drawEllipse(R(6, 6, 12, 12))
    elif name == "help":
        p.drawEllipse(R(3, 3, 18, 18))
        path = QPainterPath()
        path.moveTo(P(9.5, 9.5))
        path.cubicTo(P(9.5, 6.5), P(14.5, 6.5), P(14.5, 9.5))
        path.cubicTo(P(14.5, 11.5), P(12, 11.5), P(12, 14))
        p.drawPath(path)
        p.drawPoint(P(12, 17))
    elif name == "add":
        p.drawLine(P(12, 5), P(12, 19))
        p.drawLine(P(5, 12), P(19, 12))
    elif name == "folder":
        path = QPainterPath()
        path.moveTo(P(3, 7))
        path.lineTo(P(3, 18))
        path.lineTo(P(21, 18))
        path.lineTo(P(21, 8))
        path.lineTo(P(11, 8))
        path.lineTo(P(9, 5.5))
        path.lineTo(P(3, 5.5))
        path.closeSubpath()
        p.drawPath(path)
    elif name == "play":
        p.setBrush(col)
        p.drawPolygon(QPolygonF([P(8, 5), P(19, 12), P(8, 19)]))
    elif name == "stop":
        p.setBrush(col)
        p.drawRoundedRect(R(6, 6, 12, 12), 2 * s, 2 * s)
    elif name == "rect":
        p.setPen(QPen(col, 1.8 * s, Qt.DashLine))
        p.drawRect(R(4, 6, 16, 12))
    elif name == "brush":
        p.drawLine(P(6, 18), P(16, 8))
        p.drawEllipse(R(15, 4, 5, 5))
        p.drawArc(R(3, 15, 6, 6), 0, 180 * 16)
    elif name == "eraser":
        p.drawPolygon(QPolygonF([P(4, 15), P(13, 6), P(20, 13), P(13, 20), P(8, 20)]))
        p.drawLine(P(8.5, 10.5), P(15.5, 17.5))
    elif name == "magic":
        p.drawEllipse(R(4, 4, 11, 11))
        p.drawLine(P(13.5, 13.5), P(20, 20))
        p.drawLine(P(9.5, 7), P(9.5, 12))
        p.drawLine(P(7, 9.5), P(12, 9.5))
    elif name == "eye":
        path = QPainterPath()
        path.moveTo(P(2, 12))
        path.quadTo(P(12, 3), P(22, 12))
        path.quadTo(P(12, 21), P(2, 12))
        p.drawPath(path)
        p.drawEllipse(R(9, 9, 6, 6))
    elif name == "save":
        p.drawRoundedRect(R(4, 4, 16, 16), 2 * s, 2 * s)
        p.drawRect(R(8, 4, 8, 5))
        p.drawRect(R(7, 13, 10, 7))
    elif name == "export":
        p.drawLine(P(12, 4), P(12, 15))
        p.drawLine(P(8, 8), P(12, 4))
        p.drawLine(P(16, 8), P(12, 4))
        p.drawPolyline(QPolygonF([P(5, 13), P(5, 20), P(19, 20), P(19, 13)]))
    elif name == "trash":
        p.drawLine(P(4, 7), P(20, 7))
        p.drawPolyline(QPolygonF([P(6, 7), P(7, 20), P(17, 20), P(18, 7)]))
        p.drawLine(P(9, 7), P(10, 4))
        p.drawLine(P(10, 4), P(14, 4))
        p.drawLine(P(14, 4), P(15, 7))
    elif name == "text":
        p.drawLine(P(5, 5), P(19, 5))
        p.drawLine(P(12, 5), P(12, 20))
        p.drawLine(P(9, 20), P(15, 20))
    elif name == "logo":
        p.setPen(Qt.NoPen)
        g = QColor(C["accent"])
        p.setBrush(g)
        p.drawRoundedRect(R(2, 2, 20, 20), 5 * s, 5 * s)
        p.setBrush(QColor("white"))
        p.drawPolygon(QPolygonF([P(9, 7), P(17, 12), P(9, 17)]))
        p.setBrush(QColor(255, 90, 95))
        p.drawEllipse(R(15.5, 3.5, 5, 5))
    p.end()
    return QIcon(pm)


def app_icon():
    return icon("logo", size=256)
