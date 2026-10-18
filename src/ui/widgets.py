"""通用控件：卡片、表单行、素材表（拖拽/缩略图）、文件列表、勾选列表、滑杆等。"""
import os
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import Qt, Signal, QSize, QObject, QTimer
from PySide6.QtGui import QIcon, QPixmap, QColor, QAction
from PySide6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel, QWidget, QTableWidget, QTableWidgetItem,
                               QAbstractItemView, QHeaderView, QListWidget, QListWidgetItem, QSlider, QMenu,
                               QFileDialog, QPushButton, QDoubleSpinBox, QSpinBox, QSizePolicy, QGridLayout,
                               QMessageBox)

from mixer import probe, thumbs, utils
from . import theme


class Card(QFrame):
    def __init__(self, title=None, parent=None, margins=(14, 12, 14, 14), spacing=10, sub=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(*margins)
        self.lay.setSpacing(spacing)
        if title:
            row = QHBoxLayout()
            t = QLabel(title)
            t.setObjectName("H2")
            row.addWidget(t)
            if sub:
                s = QLabel(sub)
                s.setObjectName("Sub")
                row.addWidget(s)
            row.addStretch(1)
            self.header = row
            self.lay.addLayout(row)

    def add(self, w):
        if isinstance(w, QWidget):
            self.lay.addWidget(w)
        else:
            self.lay.addLayout(w)
        return w


def hint(text):
    l = QLabel(text)
    l.setObjectName("Sub")
    l.setWordWrap(True)
    return l


def form_row(label, *widgets, tip=None, stretch=True, label_w=96):
    row = QHBoxLayout()
    row.setSpacing(8)
    l = QLabel(label)
    l.setFixedWidth(label_w)
    if tip:
        l.setToolTip(tip)
    row.addWidget(l)
    for w in widgets:
        if isinstance(w, str):
            x = QLabel(w)
            x.setObjectName("Sub")
            row.addWidget(x)
        elif isinstance(w, QWidget):
            row.addWidget(w)
        else:
            row.addLayout(w)
    if stretch:
        row.addStretch(1)
    return row


def dspin(lo, hi, val, step=0.1, dec=1, suffix="", width=90):
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(dec)
    s.setSingleStep(step)
    s.setValue(val)
    if suffix:
        s.setSuffix(suffix)
    s.setFixedWidth(width)
    return s


def ispin(lo, hi, val, step=1, suffix="", width=90):
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setValue(val)
    if suffix:
        s.setSuffix(suffix)
    s.setFixedWidth(width)
    return s


def button(text, icon_name=None, kind=None, tip=None, slot=None):
    b = QPushButton(text)
    if icon_name:
        b.setIcon(theme.icon(icon_name, theme.C["accent_text"] if kind == "Primary" else None))
        b.setIconSize(QSize(16, 16))
    if kind:
        b.setObjectName(kind)
    if tip:
        b.setToolTip(tip)
    if slot:
        b.clicked.connect(slot)
    b.setCursor(Qt.PointingHandCursor)
    return b


class ValueSlider(QWidget):
    """带数值显示的滑杆，float 值域。"""
    valueChanged = Signal(float)

    def __init__(self, lo=-1.0, hi=1.0, val=0.0, steps=200, fmt="{:+.2f}", parent=None):
        super().__init__(parent)
        self.lo, self.hi, self.steps, self.fmt = lo, hi, steps, fmt
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.s = QSlider(Qt.Horizontal)
        self.s.setRange(0, steps)
        self.lbl = QLabel()
        self.lbl.setFixedWidth(46)
        self.lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.s, 1)
        lay.addWidget(self.lbl)
        self.s.valueChanged.connect(self._changed)
        self.setValue(val)

    def _to_f(self, i):
        return self.lo + (self.hi - self.lo) * i / self.steps

    def value(self):
        return self._to_f(self.s.value())

    def setValue(self, v):
        v = max(self.lo, min(self.hi, float(v)))
        i = int(round((v - self.lo) / (self.hi - self.lo) * self.steps))
        self.s.blockSignals(True)
        self.s.setValue(i)
        self.s.blockSignals(False)
        self.lbl.setText(self.fmt.format(self.value()))

    def _changed(self, i):
        v = self._to_f(i)
        self.lbl.setText(self.fmt.format(v))
        self.valueChanged.emit(v)


class CheckList(QListWidget):
    """可勾选列表。items: [(key, label)]"""
    changed = Signal()

    def __init__(self, items, checked=(), parent=None, columns_hint=None):
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        for key, label in items:
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, key)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if key in checked else Qt.Unchecked)
            self.addItem(it)
        if columns_hint:
            self.setViewMode(QListWidget.IconMode)
            self.setFlow(QListWidget.LeftToRight)
            self.setWrapping(True)
            self.setResizeMode(QListWidget.Adjust)
            self.setGridSize(QSize(columns_hint, 28))
        self.itemChanged.connect(lambda *_: self.changed.emit())

    def checked(self):
        return [self.item(i).data(Qt.UserRole) for i in range(self.count())
                if self.item(i).checkState() == Qt.Checked]

    def set_checked(self, keys):
        self.blockSignals(True)
        for i in range(self.count()):
            it = self.item(i)
            it.setCheckState(Qt.Checked if it.data(Qt.UserRole) in keys else Qt.Unchecked)
        self.blockSignals(False)

    def set_all(self, on=True):
        self.blockSignals(True)
        for i in range(self.count()):
            self.item(i).setCheckState(Qt.Checked if on else Qt.Unchecked)
        self.blockSignals(False)
        self.changed.emit()


# ----------------------------------------------------------------------------
# 缩略图加载（后台线程池）
# ----------------------------------------------------------------------------
class ThumbLoader(QObject):
    ready = Signal(str, str)        # path, thumb_path
    probed = Signal(str, object)    # path, MediaInfo

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = ThreadPoolExecutor(max_workers=4)

    def request(self, path, want_thumb=True):
        def job():
            info = probe.probe(path)
            self.probed.emit(path, info)
            if want_thumb and info.ok and info.kind in ("video", "image"):
                t = thumbs.thumb(info)
                if t:
                    self.ready.emit(path, t)
        self.pool.submit(job)


VIDEO_FILTER = "视频/图片 (*.mp4 *.mov *.avi *.mkv *.flv *.wmv *.m4v *.webm *.ts *.mts *.3gp *.mpg *.png *.jpg *.jpeg *.bmp *.webp);;所有文件 (*.*)"
AUDIO_FILTER = "音频 (*.mp3 *.m4a *.aac *.wav *.flac *.ogg *.wma *.opus);;所有文件 (*.*)"
IMAGE_FILTER = "图片 (*.png *.jpg *.jpeg *.bmp *.webp *.gif);;所有文件 (*.*)"


class MaterialTable(QTableWidget):
    """素材表：拖拽导入、缩略图、探测信息、右键菜单。"""
    changed = Signal()
    contextAction = Signal(str, list)   # action, paths

    COLS = ["", "文件名", "时长", "分辨率", "音频", "处理", "重复", "状态"]

    def __init__(self, kinds=("video", "image"), parent=None, actions=None):
        super().__init__(0, len(self.COLS), parent)
        self.kinds = kinds
        self.infos = {}
        self.rows = {}
        self.marks = {}
        self.actions = actions or []
        self.setHorizontalHeaderLabels(self.COLS)
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setIconSize(QSize(64, 36))
        self.verticalHeader().setDefaultSectionSize(42)
        h = self.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Fixed)
        self.setColumnWidth(0, 72)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        for c, w in ((2, 64), (3, 92), (4, 46), (5, 56), (6, 52), (7, 70)):
            h.setSectionResizeMode(c, QHeaderView.Fixed)
            self.setColumnWidth(c, w)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DropOnly)
        self.setShowGrid(False)
        self.setAlternatingRowColors(False)
        self.loader = ThumbLoader(self)
        self.loader.ready.connect(self._on_thumb)
        self.loader.probed.connect(self._on_probe)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.empty_hint = QLabel("把视频 / 图片 / 文件夹 拖到这里\n或点击上方“添加”", self)
        self.empty_hint.setAlignment(Qt.AlignCenter)
        self.empty_hint.setObjectName("Sub")
        self._update_hint()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.empty_hint.setGeometry(0, 30, self.viewport().width(), max(60, self.viewport().height() - 30))

    def _update_hint(self):
        self.empty_hint.setVisible(self.rowCount() == 0)

    # 拖拽
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        if e.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
            self.add_paths(paths)
            e.acceptProposedAction()

    def paths(self):
        return [self.item(r, 1).data(Qt.UserRole) for r in range(self.rowCount())]

    def selected_paths(self):
        rows = sorted({i.row() for i in self.selectedIndexes()})
        return [self.item(r, 1).data(Qt.UserRole) for r in rows]

    def add_paths(self, paths):
        files = probe.scan_paths(paths, kinds=self.kinds)
        existing = set(self.paths())
        added = 0
        for f in files:
            if f in existing:
                continue
            r = self.rowCount()
            self.insertRow(r)
            ic = QTableWidgetItem()
            ic.setIcon(theme.icon("film", theme.C["sub"]))
            self.setItem(r, 0, ic)
            it = QTableWidgetItem(os.path.basename(f))
            it.setData(Qt.UserRole, f)
            it.setToolTip(f)
            self.setItem(r, 1, it)
            for c in range(2, 8):
                self.setItem(r, c, QTableWidgetItem("…" if c == 7 else ""))
            self.rows[f] = it
            self.loader.request(f)
            existing.add(f)
            added += 1
        self._update_hint()
        if added:
            self.changed.emit()
        return added

    def _row_of(self, path):
        it = self.rows.get(path)
        if it is None:
            return -1
        return it.row()

    def _on_thumb(self, path, tp):
        r = self._row_of(path)
        if r < 0:
            return
        pm = QPixmap(tp)
        if not pm.isNull():
            self.item(r, 0).setIcon(QIcon(pm.scaled(64, 36, Qt.KeepAspectRatio, Qt.SmoothTransformation)))

    def _on_probe(self, path, info):
        self.infos[path] = info
        r = self._row_of(path)
        if r < 0:
            return
        if info.kind == "image":
            self.item(r, 2).setText("图片")
        else:
            self.item(r, 2).setText(utils.fmt_secs(info.duration) if info.duration else "-")
        self.item(r, 3).setText(info.res_text())
        self.item(r, 4).setText("有" if info.has_audio else ("-" if info.kind == "image" else "无"))
        st = self.item(r, 7)
        if info.ok:
            st.setText("√ 正常" + (" HDR" if info.hdr else ""))
            st.setForeground(QColor(theme.C["success"]))
        else:
            st.setText("× " + (info.error or "无效")[:8])
            st.setToolTip(info.error)
            st.setForeground(QColor(theme.C["danger"]))
        self.changed.emit()

    def set_mark(self, path, text, tip=""):
        self.marks[path] = text
        r = self._row_of(path)
        if r >= 0:
            it = self.item(r, 5)
            it.setText(text)
            it.setToolTip(tip)
            it.setForeground(QColor(theme.C["accent"]))

    def set_dup(self, path, ratio, tip=""):
        """“重复”列：素材中与其他素材/自身其他位置重复的画面占比。"""
        r = self._row_of(path)
        if r < 0:
            return
        it = self.item(r, 6)
        if ratio >= 0.85:
            it.setText("全重复")
            it.setForeground(QColor(theme.C["danger"]))
        elif ratio > 0.005:
            it.setText(f"{ratio:.0%}")
            it.setForeground(QColor(theme.C["warning"]))
        else:
            it.setText("无")
            it.setForeground(QColor(theme.C["sub"]))
        it.setToolTip(tip or "重复画面占比（检查素材重复后显示）")

    def remove_paths(self, paths):
        for p in paths:
            r = self._row_of(p)
            if r >= 0:
                self.removeRow(r)
                self.rows.pop(p, None)
                self.marks.pop(p, None)
        self._update_hint()
        self.changed.emit()

    def clear_all(self):
        self.setRowCount(0)
        self.rows.clear()
        self.marks.clear()
        self._update_hint()
        self.changed.emit()

    def stats(self):
        n = len(self.rows)
        ok = [self.infos[p] for p in self.rows if p in self.infos and self.infos[p].ok]
        dur = sum(i.duration for i in ok if i.kind == "video")
        imgs = sum(1 for i in ok if i.kind == "image")
        bad = sum(1 for p in self.rows if p in self.infos and not self.infos[p].ok)
        return n, len(ok), dur, imgs, bad

    def _menu(self, pos):
        sel = self.selected_paths()
        if not sel:
            return
        m = QMenu(self)
        for key, label in self.actions:
            if key == "-":
                m.addSeparator()
                continue
            a = m.addAction(label)
            a.triggered.connect(lambda _=False, k=key: self.contextAction.emit(k, self.selected_paths()))
        m.addSeparator()
        a = m.addAction("打开所在文件夹")
        a.triggered.connect(lambda: utils.open_path(sel[0], select=True))
        a = m.addAction("播放预览")
        a.triggered.connect(lambda: utils.open_path(sel[0]))
        m.addSeparator()
        a = m.addAction("从列表移除")
        a.triggered.connect(lambda: self.remove_paths(sel))
        m.exec(self.viewport().mapToGlobal(pos))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Delete:
            self.remove_paths(self.selected_paths())
            return
        super().keyPressEvent(e)


class FileList(QListWidget):
    """简单文件列表（片头/片尾/BGM/角标），支持拖拽。"""
    changed = Signal()

    def __init__(self, kinds=("video", "image"), placeholder="拖拽文件到这里", parent=None):
        super().__init__(parent)
        self.kinds = kinds
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DropOnly)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.ph = QLabel(placeholder, self)
        self.ph.setAlignment(Qt.AlignCenter)
        self.ph.setObjectName("Sub")
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self._upd()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.ph.setGeometry(0, 0, self.viewport().width(), self.viewport().height())

    def _upd(self):
        self.ph.setVisible(self.count() == 0)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.add_paths([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])
        e.acceptProposedAction()

    def add_paths(self, paths):
        files = probe.scan_paths(paths, kinds=self.kinds)
        have = set(self.paths())
        for f in files:
            if f in have:
                continue
            it = QListWidgetItem(os.path.basename(f))
            it.setData(Qt.UserRole, f)
            it.setToolTip(f)
            self.addItem(it)
        self._upd()
        self.changed.emit()

    def paths(self):
        return [self.item(i).data(Qt.UserRole) for i in range(self.count())]

    def set_paths(self, paths):
        self.clear()
        self.add_paths([p for p in paths if os.path.exists(p)])

    def remove_selected(self):
        for it in self.selectedItems():
            self.takeItem(self.row(it))
        self._upd()
        self.changed.emit()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Delete:
            self.remove_selected()
            return
        super().keyPressEvent(e)

    def _menu(self, pos):
        if not self.selectedItems():
            return
        m = QMenu(self)
        m.addAction("移除").triggered.connect(self.remove_selected)
        p = self.selectedItems()[0].data(Qt.UserRole)
        m.addAction("打开所在文件夹").triggered.connect(lambda: utils.open_path(p, select=True))
        m.addAction("播放 / 打开").triggered.connect(lambda: utils.open_path(p))
        m.exec(self.viewport().mapToGlobal(pos))


def file_list_panel(title, kinds, file_filter, placeholder, tip=None):
    """带“添加/移除/清空”按钮的文件列表面板。返回 (widget, FileList)。"""
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    fl = FileList(kinds, placeholder)
    row = QHBoxLayout()
    t = QLabel(title)
    t.setObjectName("Sub")
    row.addWidget(t)
    row.addStretch(1)

    def add():
        files, _ = QFileDialog.getOpenFileNames(w, "选择文件", "", file_filter)
        if files:
            fl.add_paths(files)
    b1 = button("添加", "add", slot=add)
    b1.setObjectName("Tool")
    b2 = button("移除", slot=fl.remove_selected)
    b2.setObjectName("Tool")
    b3 = button("清空", slot=lambda: (fl.clear(), fl._upd(), fl.changed.emit()))
    b3.setObjectName("Tool")
    for b in (b1, b2, b3):
        row.addWidget(b)
    lay.addLayout(row)
    if tip:
        lay.addWidget(hint(tip))
    lay.addWidget(fl, 1)
    return w, fl


class IconGrid(QListWidget):
    """缩略图网格（滤镜/贴纸/花字预设）。"""
    picked = Signal(str)

    def __init__(self, icon_size=96, cell=(112, 128), parent=None):
        super().__init__(parent)
        self.setViewMode(QListWidget.IconMode)
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Static)
        self.setIconSize(QSize(icon_size, icon_size))
        self.setGridSize(QSize(*cell))
        self.setWordWrap(True)
        self.setSpacing(4)
        self.setUniformItemSizes(True)
        self.itemClicked.connect(lambda it: self.picked.emit(it.data(Qt.UserRole) or ""))

    def add(self, key, label, pixmap=None, tip=None):
        it = QListWidgetItem(label)
        it.setData(Qt.UserRole, key)
        if pixmap is not None and not pixmap.isNull():
            it.setIcon(QIcon(pixmap))
        it.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
        if tip:
            it.setToolTip(tip)
        self.addItem(it)
        return it

    def item_by_key(self, key):
        for i in range(self.count()):
            if self.item(i).data(Qt.UserRole) == key:
                return self.item(i)
        return None


def msg(parent, text, title="提示", kind="info"):
    if kind == "warn":
        QMessageBox.warning(parent, title, text)
    elif kind == "error":
        QMessageBox.critical(parent, title, text)
    else:
        QMessageBox.information(parent, title, text)


def confirm(parent, text, title="确认"):
    return QMessageBox.question(parent, title, text, QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes
