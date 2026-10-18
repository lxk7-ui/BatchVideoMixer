"""主窗口：左侧导航 + 页面栈，页面间信号联动。"""
import os

from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QStackedWidget, QFrame, QDialog, QCheckBox, QTextBrowser, QPushButton, QApplication)

from mixer import VERSION, APP_NAME, settings, encoders, ffmpeg
from . import theme
from .page_mix import MixPage
from .page_studio import StudioPage
from .page_others import StickerPage, HistoryPage, SettingsPage, HelpPage
from .widgets import msg, confirm, button
from .workers import run_bg


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{VERSION}")
        self.setWindowIcon(theme.app_icon())
        self.resize(1440, 900)
        self.setMinimumSize(1100, 700)
        central = QWidget()
        self.setCentralWidget(central)
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(186)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 16, 0, 12)
        brand = QHBoxLayout()
        brand.setContentsMargins(16, 0, 10, 0)
        logo = QLabel()
        logo.setPixmap(theme.app_icon().pixmap(30, 30))
        brand.addWidget(logo)
        name = QLabel("批量视频混剪")
        name.setObjectName("Brand")
        brand.addWidget(name)
        brand.addStretch(1)
        sl.addLayout(brand)
        sl.addSpacing(18)
        self.nav = QListWidget()
        self.nav.setObjectName("NavList")
        self.nav.setIconSize(QSize(20, 20))
        self.nav.setFocusPolicy(Qt.NoFocus)
        self.pages_meta = [("混剪生产", "film"), ("素材处理", "wand"), ("贴纸库", "sticker"), ("任务记录", "history"),
                           ("设置", "settings"), ("帮助", "help")]
        for text, ic in self.pages_meta:
            it = QListWidgetItem(theme.icon(ic, theme.C["sub"]), "  " + text)
            it.setSizeHint(QSize(160, 42))
            self.nav.addItem(it)
        sl.addWidget(self.nav, 1)
        self.enc_status = QLabel("正在检测显卡…")
        self.enc_status.setObjectName("Sub")
        self.enc_status.setWordWrap(True)
        self.enc_status.setContentsMargins(16, 0, 10, 0)
        sl.addWidget(self.enc_status)
        ver = QLabel(f"v{VERSION}")
        ver.setObjectName("Sub")
        ver.setContentsMargins(16, 4, 10, 0)
        sl.addWidget(ver)
        lay.addWidget(side)

        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        self.mix = MixPage()
        self.studio = StudioPage()
        self.stickers = StickerPage()
        self.history = HistoryPage()
        self.settings_page = SettingsPage()
        self.help = HelpPage()
        for p in (self.mix, self.studio, self.stickers, self.history, self.settings_page, self.help):
            self.stack.addWidget(p)
        self.nav.currentRowChanged.connect(self._go)
        self.nav.setCurrentRow(0)

        # 页面联动
        self.mix.openStudio.connect(self._open_studio)
        self.studio.requestMixMaterials.connect(lambda: self.studio.add_paths(self.mix.table.paths()))
        self.studio.sendCleanupToMix.connect(self._cleanup_to_mix)
        self.studio.sendStyleToMix.connect(self._style_to_mix)
        self.history.resumeRequested.connect(self._resume)
        self.settings_page.themeChanged.connect(self._apply_theme)
        self.stickers.libraryChanged.connect(lambda: self.studio.sticker_browser.reload(True))
        self.mix.runStateChanged.connect(self._run_state)
        run_bg(encoders.detect, on_done=lambda r: self.enc_status.setText(
            encoders.describe(r)))
        QTimer.singleShot(300, self._first_run)

    def _go(self, i):
        self.stack.setCurrentIndex(i)
        for k in range(self.nav.count()):
            text, ic = self.pages_meta[k]
            self.nav.item(k).setIcon(theme.icon(ic, theme.C["text"] if k == i else theme.C["sub"]))
        w = self.stack.currentWidget()
        if w is self.history:
            self.history.reload()
        elif w is self.settings_page:
            self.settings_page.refresh()

    def _open_studio(self, paths_):
        self.studio.add_paths(paths_)
        # 让素材处理页使用混剪页已有的去水印设置
        for p in paths_:
            d = self.mix.cleanup_map.get(p)
            if d:
                self.studio.profiles.setdefault(p, d)
        for i in range(self.studio.mlist.count()):
            if self.studio.mlist.item(i).data(Qt.UserRole) == paths_[0]:
                self.studio.mlist.setCurrentRow(i)
                break
        self.studio._refresh_list_marks()
        self.nav.setCurrentRow(1)

    def _cleanup_to_mix(self, cmap, paths_):
        self.mix.set_cleanup(cmap, [p for p in paths_ if p in cmap])
        self.nav.setCurrentRow(0)
        msg(self, f"已把 {len(cmap)} 个素材的去水印设置发送到混剪。\n生成时会在剪辑过程中直接去水印（不额外转码）。")

    def _style_to_mix(self, st):
        self.mix.set_style_from_studio(st)
        self.nav.setCurrentRow(0)
        msg(self, "已设为混剪的成片包装（可在“包装”标签里点“设计包装…”在成片画布上微调位置）。")

    def _resume(self, d):
        if self.mix.is_running():
            msg(self, "当前有任务正在运行，请等待完成或停止后再继续其他任务。")
            return
        self.nav.setCurrentRow(0)
        self.mix.start(resume_dir=d)

    def _run_state(self, running):
        self.nav.item(0).setText("  混剪生产" + ("  ●" if running else ""))

    def _apply_theme(self, name):
        theme.set_palette(name)
        QApplication.instance().setStyleSheet(theme.qss())
        self._go(self.nav.currentRow())
        msg(self, "主题已切换，部分图标重启程序后完全生效。")

    def _first_run(self):
        s = settings.get()
        try:
            ffmpeg.ffmpeg_bin()
            ffmpeg.ffprobe_bin()
        except ffmpeg.FFmpegError as e:
            msg(self, f"{e}\n\n请确认程序目录下的 ffmpeg 文件夹完整（ffmpeg.exe、ffprobe.exe 及 dll）。", kind="error")
        if not s.get("agree"):
            dlg = AgreeDialog(self)
            if dlg.exec() == QDialog.Accepted:
                settings.set("agree", True)
            else:
                self.close()

    def closeEvent(self, e):
        if self.mix.is_running():
            if not confirm(self, "有任务正在生成，关闭程序会中止任务（下次可在“任务记录”中继续）。确定退出吗？"):
                e.ignore()
                return
            try:
                self.mix.runner.stop(True)
            except Exception:
                pass
        self.mix.save_last()
        settings.save()
        super().closeEvent(e)


class AgreeDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("使用须知")
        self.resize(560, 420)
        lay = QVBoxLayout(self)
        tb = QTextBrowser()
        tb.setHtml("""
<h3>欢迎使用批量视频混剪工具</h3>
<p>本工具用于对<b>自有或已获授权</b>的视频/图片素材进行批量整合、规格统一、模板化剪辑和多版本生产，提升团队效率。</p>
<ul>
<li>请只处理你有权使用的素材，尊重著作权与各平台规则；</li>
<li>去水印/去贴纸功能用于清理自有素材上的旧水印、误加贴纸或遮挡物，请勿用于去除他人作品的署名水印；</li>
<li>“画面变体 / 元数据”是多版本测试与归档能力，本工具不承诺、也不应被用于“过审 / 去重 / 伪原创”；</li>
<li>建议保留素材来源与授权记录，每个任务的输出目录中都有处理日志与报告，便于审计。</li>
</ul>""")
        lay.addWidget(tb)
        self.cb = QCheckBox("我已阅读并同意以上使用规范")
        lay.addWidget(self.cb)
        row = QHBoxLayout()
        row.addStretch(1)
        quit_ = QPushButton("退出")
        quit_.clicked.connect(self.reject)
        ok = QPushButton("开始使用")
        ok.setObjectName("Primary")
        ok.setEnabled(False)
        self.cb.toggled.connect(ok.setEnabled)
        ok.clicked.connect(self.accept)
        row.addWidget(quit_)
        row.addWidget(ok)
        lay.addLayout(row)
