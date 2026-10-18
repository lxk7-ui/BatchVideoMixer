"""素材查重报告对话框。"""
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QHeaderView,
                               QAbstractItemView, QPushButton)

from mixer import utils
from . import theme
from .widgets import hint


class DupReportDialog(QDialog):
    """显示重复画面段；可一键移除“几乎完全重复”的素材。"""

    def __init__(self, idx, parent=None):
        super().__init__(parent)
        self.setWindowTitle("素材查重报告")
        self.resize(860, 560)
        self.idx = idx
        self.remove_paths = []
        lay = QVBoxLayout(self)
        s = idx.summary()
        title = QLabel(
            f"发现 <b>{s['runs']}</b> 处重复画面，约 <b>{s['seconds']}</b> 秒，涉及 <b>{s['materials']}</b> 个素材"
            + (f"；其中 <b>{s['full']}</b> 个素材几乎与其他素材完全相同" if s["full"] else "")
            if s["runs"] else "没有发现重复画面。")
        title.setObjectName("H2")
        lay.addWidget(title)
        lay.addWidget(hint("生成成片时会自动避开：同一条成片里，同一段画面只会出现一次（剪辑前查重）；"
                           "生成后还会抽帧复查，漏网的重复片段会被自动剪掉（剪辑后复查）。"
                           "镜像、加滤镜、改画幅（黑边/模糊背景）后的同一画面也能识别。"))
        tb = QTableWidget(0, 5)
        tb.setHorizontalHeaderLabels(["素材 A", "A 时间段", "素材 B", "B 时间段", "重复时长"])
        tb.verticalHeader().setVisible(False)
        tb.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tb.setSelectionBehavior(QAbstractItemView.SelectRows)
        h = tb.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        for c, w in ((1, 120), (3, 120), (4, 80)):
            h.setSectionResizeMode(c, QHeaderView.Fixed)
            tb.setColumnWidth(c, w)
        for pa, a0, a1, pb, b0, b1 in idx.runs:
            r = tb.rowCount()
            tb.insertRow(r)
            img = a1 <= a0
            vals = [os.path.basename(pa), "图片" if img else f"{utils.fmt_secs(a0)} ~ {utils.fmt_secs(a1)}",
                    os.path.basename(pb), "图片" if img else f"{utils.fmt_secs(b0)} ~ {utils.fmt_secs(b1)}",
                    "-" if img else f"{min(a1 - a0, b1 - b0):.1f} 秒"]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c in (0, 2):
                    it.setToolTip(pa if c == 0 else pb)
                tb.setItem(r, c, it)
        lay.addWidget(tb, 1)
        if idx.full_dups:
            lay.addWidget(QLabel("几乎完全重复的素材（生成时会自动只保留一份）："))
            for p, q, r in idx.full_dups:
                l = QLabel(f"· {os.path.basename(p)}  ≈  {os.path.basename(q)}（{r:.0%} 画面相同）")
                l.setStyleSheet(f"color: {theme.C['warning']};")
                lay.addWidget(l)
        row = QHBoxLayout()
        row.addStretch(1)
        if idx.full_dups:
            b = QPushButton("从素材列表移除完全重复的素材")
            b.clicked.connect(self._remove_full)
            row.addWidget(b)
        ok = QPushButton("知道了")
        ok.setObjectName("Primary")
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        lay.addLayout(row)

    def _remove_full(self):
        self.remove_paths = [p for p, _q, _r in self.idx.full_dups]
        self.accept()
