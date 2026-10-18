"""贴纸库管理 / 任务记录 / 设置 / 帮助 页面。"""
import os
import shutil

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QPixmap, QIcon, QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QFileDialog,
                               QInputDialog, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
                               QLineEdit, QCheckBox, QComboBox, QTextBrowser, QSplitter)

from mixer import stickers as stk, paths, utils, settings, ffmpeg, encoders, VERSION, APP_NAME
from mixer.batch import load_history, save_json, history_file
from . import theme
from .widgets import Card, button, hint, form_row, IconGrid, msg, confirm
from .workers import run_bg


# ============================================================================ 贴纸库
class StickerPage(QWidget):
    libraryChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        head = QHBoxLayout()
        t = QLabel("贴纸库")
        t.setObjectName("Title")
        head.addWidget(t)
        head.addWidget(hint("内置电商标签 / 互动引导 / 箭头 / 装饰 / 气泡（原创）+ 200 个 3D 表情（Microsoft Fluent Emoji，MIT 协议）。"
                            "支持导入自己的 PNG / GIF / WebP / APNG 动图贴纸。"), 1)
        root.addLayout(head)
        body = QHBoxLayout()
        root.addLayout(body, 1)
        left = Card("分类")
        left.setFixedWidth(240)
        self.cats = QListWidget()
        self.cats.currentRowChanged.connect(self._fill)
        left.add(self.cats)
        left.add(button("导入贴纸…", "add", kind="Primary", slot=self._import))
        left.add(button("新建分类并导入…", "folder", slot=self._import_new_cat))
        left.add(button("打开我的贴纸文件夹", "folder", slot=lambda: utils.open_path(paths.user_sticker_dir())))
        left.add(button("刷新", slot=self.reload))
        body.addWidget(left)
        right = Card()
        self.grid = IconGrid(icon_size=96, cell=(116, 132))
        self.grid.setSelectionMode(QAbstractItemView.ExtendedSelection)
        right.add(self.grid)
        row = QHBoxLayout()
        self.info = QLabel("")
        self.info.setObjectName("Sub")
        row.addWidget(self.info, 1)
        self.del_btn = button("删除选中（仅限我的贴纸）", "trash", kind="Danger", slot=self._delete)
        row.addWidget(self.del_btn)
        right.add(row)
        right.add(hint("关于“剪映同款贴纸”：剪映贴纸素材的版权属于字节跳动及原作者，只授权在剪映内使用，本工具不抓取、不内置。"
                       "如果你们有授权的贴纸素材（例如购买的贴纸包、设计部门出的素材），导入或直接复制到“我的贴纸”文件夹即可使用，"
                       "子文件夹名就是分类名，GIF/APNG/WebP 动图会自动转换并保留动画。"))
        body.addWidget(right, 1)
        self.reload()

    def reload(self):
        lib = stk.library(refresh=True)
        cur = self.cats.currentItem().data(Qt.UserRole) if self.cats.currentItem() else None
        self.cats.blockSignals(True)
        self.cats.clear()
        for k, v in lib.items():
            it = QListWidgetItem(f"{stk.display_name(k)}  （{len(v)}）")
            it.setData(Qt.UserRole, k)
            self.cats.addItem(it)
        self.cats.blockSignals(False)
        row = 0
        for i in range(self.cats.count()):
            if self.cats.item(i).data(Qt.UserRole) == cur:
                row = i
        self.cats.setCurrentRow(row)
        self._fill(row)
        self.libraryChanged.emit()

    def _fill(self, row):
        self.grid.clear()
        it = self.cats.item(row) if row >= 0 else None
        if not it:
            return
        key = it.data(Qt.UserRole)
        items = stk.library().get(key, [])
        user = key.startswith(stk.USER_PREFIX)
        self.del_btn.setEnabled(user)
        self.info.setText(f"{len(items)} 个贴纸" + ("（我的贴纸）" if user else "（内置）"))
        for s in items:
            self.grid.add(s.path, s.name + ("（动图）" if s.animated else ""), None, tip=s.path)
        paths_ = [s.path for s in items]

        def job():
            return [(p, stk.thumbnail(p, 96)) for p in paths_]

        def done(res, row=row):
            if self.cats.currentRow() != row:
                return
            for p, t in res:
                gi = self.grid.item_by_key(p)
                if gi:
                    gi.setIcon(QIcon(QPixmap(t)))
        run_bg(job, on_done=done)

    def _import(self, category=None):
        files, _ = QFileDialog.getOpenFileNames(self, "选择贴纸文件", "",
                                                "贴纸 (*.png *.gif *.webp *.apng *.jpg *.jpeg);;所有文件 (*.*)")
        if not files:
            return
        if category is None:
            it = self.cats.currentItem()
            key = it.data(Qt.UserRole) if it else ""
            category = key[len(stk.USER_PREFIX):] if key.startswith(stk.USER_PREFIX) else "我的贴纸"
        out = stk.import_files(files, category)
        self.reload()
        msg(self, f"已导入 {len(out)} 个贴纸到「{category}」。")

    def _import_new_cat(self):
        name, ok = QInputDialog.getText(self, "新建分类", "分类名称（如：节日、品牌元素）：")
        if ok and name.strip():
            self._import(name.strip())

    def _delete(self):
        sel = [it.data(Qt.UserRole) for it in self.grid.selectedItems()]
        if not sel or not confirm(self, f"删除选中的 {len(sel)} 个贴纸？（会删除“我的贴纸”文件夹中的文件）"):
            return
        n = sum(1 for p in sel if stk.delete_user_sticker(p))
        self.reload()
        msg(self, f"已删除 {n} 个。")


# ============================================================================ 任务记录
class HistoryPage(QWidget):
    resumeRequested = Signal(str)

    STATUS = {"done": "√ 完成", "partial": "部分失败", "stopped": "已停止(可继续)"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        head = QHBoxLayout()
        t = QLabel("任务记录")
        t.setObjectName("Title")
        head.addWidget(t)
        head.addWidget(hint("每次生成都会记录在这里。停止或失败的任务可以“继续未完成”（断点续跑，已完成的不会重做）。"), 1)
        head.addWidget(button("刷新", slot=self.reload))
        root.addLayout(head)
        c = Card()
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["时间", "任务名", "成功/总数", "失败", "状态", "输出目录"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(5, QHeaderView.Stretch)
        for col, w in ((0, 150), (1, 160), (2, 90), (3, 60), (4, 120)):
            self.table.setColumnWidth(col, w)
        self.table.cellDoubleClicked.connect(lambda r, c: self._open())
        c.add(self.table)
        row = QHBoxLayout()
        row.addWidget(button("打开输出目录", "folder", slot=self._open))
        row.addWidget(button("查看报告(CSV)", slot=self._report))
        row.addWidget(button("查看日志", slot=self._log))
        row.addWidget(button("继续未完成", "play", kind="Primary", slot=self._resume))
        row.addStretch(1)
        row.addWidget(button("删除记录", "trash", slot=self._delete))
        c.add(row)
        root.addWidget(c, 1)
        self.reload()

    def reload(self):
        self.hist = load_history()
        self.table.setRowCount(0)
        for h in self.hist:
            r = self.table.rowCount()
            self.table.insertRow(r)
            vals = [h.get("time", "").replace("T", " "), h.get("name", ""), f"{h.get('ok', 0)}/{h.get('total', 0)}",
                    str(h.get("failed", 0)), self.STATUS.get(h.get("status"), h.get("status", "")), h.get("dir", "")]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 4:
                    it.setForeground(QColor(theme.C["success"] if h.get("status") == "done" else theme.C["warning"]))
                if c == 5 and not os.path.isdir(h.get("dir", "")):
                    it.setForeground(QColor(theme.C["sub"]))
                    it.setText(v + "（已删除）")
                self.table.setItem(r, c, it)

    def _cur(self):
        r = self.table.currentRow()
        return self.hist[r] if 0 <= r < len(self.hist) else None

    def _open(self):
        h = self._cur()
        if h and os.path.isdir(h["dir"]):
            sub = os.path.join(h["dir"], "成片")
            utils.open_path(sub if os.path.isdir(sub) else h["dir"])

    def _report(self):
        h = self._cur()
        if h:
            p = os.path.join(h["dir"], "数据", "report.csv")
            if os.path.isfile(p):
                utils.open_path(p)
            else:
                msg(self, "没有找到报告文件。")

    def _log(self):
        h = self._cur()
        if h:
            p = os.path.join(h["dir"], "数据", "run.log")
            if os.path.isfile(p):
                utils.open_path(p)

    def _resume(self):
        h = self._cur()
        if not h:
            msg(self, "请先选择一条记录。")
            return
        if not os.path.isfile(os.path.join(h["dir"], "数据", "plan.json")):
            msg(self, "该目录缺少计划文件，无法继续。", kind="warn")
            return
        if h.get("status") == "done" and not confirm(self, "该任务已全部完成，仍要检查并补齐缺失的成片吗？"):
            return
        self.resumeRequested.emit(h["dir"])

    def _delete(self):
        h = self._cur()
        if not h:
            return
        if confirm(self, "只删除这条记录（不会删除已生成的视频文件）？"):
            hist = [x for x in load_history() if x.get("dir") != h.get("dir")]
            save_json(history_file(), hist)
            self.reload()


# ============================================================================ 设置
class SettingsPage(QWidget):
    themeChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        s = settings.get()
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(12)
        t = QLabel("设置")
        t.setObjectName("Title")
        root.addWidget(t)
        c = Card("运行环境")
        self.ff_label = QLabel()
        self.ff_label.setWordWrap(True)
        c.add(self.ff_label)
        self.ff_dir = QLineEdit(s.get("ffmpeg_dir", ""))
        self.ff_dir.setPlaceholderText("留空 = 使用程序自带的 ffmpeg（推荐）")
        c.add(form_row("FFmpeg 目录", self.ff_dir, button("浏览", "folder", slot=self._browse_ff),
                       button("应用", slot=self._apply_ff), stretch=False))
        self.enc_label = QLabel("显卡编码：点击检测")
        self.enc_label.setObjectName("Sub")
        c.add(form_row("硬件编码", self.enc_label, button("重新检测", slot=self._detect)))
        self.enc_default = QComboBox()
        self.enc_default.addItem("自动", "auto")
        for k, v in encoders.ENCODERS.items():
            self.enc_default.addItem(v["label"], k)
        i = self.enc_default.findData(s.get("default_encoder", "auto"))
        self.enc_default.setCurrentIndex(max(0, i))
        self.enc_default.currentIndexChanged.connect(
            lambda *_: settings.set("default_encoder", self.enc_default.currentData()))
        c.add(form_row("素材处理导出", self.enc_default))
        self.lowprio = QCheckBox("后台低优先级运行（批量生成时电脑不卡，速度略慢）")
        self.lowprio.setChecked(bool(s.get("low_priority", True)))
        self.lowprio.toggled.connect(self._lowprio)
        c.add(self.lowprio)
        root.addWidget(c)
        c2 = Card("外观与数据")
        self.theme = QComboBox()
        self.theme.addItem("深色（推荐）", "dark")
        self.theme.addItem("浅色", "light")
        self.theme.setCurrentIndex(max(0, self.theme.findData(s.get("theme", "dark"))))
        self.theme.currentIndexChanged.connect(lambda *_: self._theme())
        c2.add(form_row("界面主题", self.theme))
        self.out = QLineEdit(settings.output_root())
        c2.add(form_row("默认输出位置", self.out, button("浏览", "folder", slot=self._browse_out), stretch=False))
        self.cache_label = QLabel("")
        c2.add(form_row("缓存", self.cache_label, button("清理缓存", slot=self._clear_cache),
                        button("打开数据目录", slot=lambda: utils.open_path(paths.data_dir()))))
        c2.add(hint("缓存包含缩略图、镜头分析结果、查重指纹、预览图等，可随时清理（不影响已生成的视频和模板）。"))
        root.addWidget(c2)
        c3 = Card("环境自检")
        c3.add(hint("新电脑部署后建议运行一次：自动生成测试素材，真实跑一遍去水印、片段混剪、转场、滤镜、贴纸、BGM、封面、素材查重，"
                    "检查时长是否在上限内（约 30~60 秒）。"))
        self.st_btn = button("运行自检", "play", kind="Primary", slot=self._selftest)
        c3.add(self.st_btn)
        self.st_out = QTextBrowser()
        self.st_out.setFixedHeight(170)
        self.st_out.setVisible(False)
        c3.add(self.st_out)
        root.addWidget(c3)
        root.addStretch(1)
        self.refresh()

    def refresh(self):
        if encoders._detected is not None:
            self.enc_label.setText(encoders.describe(encoders._detected))
        try:
            v, major = ffmpeg.version_info()
            self.ff_label.setText(f"FFmpeg：{ffmpeg.ffmpeg_bin()}\n{v[:90]}")
        except Exception as e:
            self.ff_label.setText(f"! {e}")
        run_bg(settings.thumbs_cache_size, on_done=lambda n: self.cache_label.setText(f"占用 {utils.fmt_size(n)}"))

    def _selftest(self):
        from mixer import selftest
        self.st_btn.setEnabled(False)
        self.st_btn.setText("自检中，请稍候…")
        self.st_out.setVisible(True)
        self.st_out.setPlainText("正在自检…")

        def done(res):
            ok, report = res
            self.st_btn.setEnabled(True)
            self.st_btn.setText("运行自检")
            self.st_out.setPlainText(report)
        run_bg(selftest.run, on_done=done, on_error=lambda e: (self.st_btn.setEnabled(True),
                                                              self.st_out.setPlainText(f"自检失败：{e}")))

    def _browse_ff(self):
        d = QFileDialog.getExistingDirectory(self, "选择包含 ffmpeg.exe 的文件夹")
        if d:
            self.ff_dir.setText(d)

    def _apply_ff(self):
        d = self.ff_dir.text().strip()
        if d and not os.path.isfile(os.path.join(d, "ffmpeg.exe")):
            msg(self, "该文件夹里没有 ffmpeg.exe。", kind="warn")
            return
        settings.set("ffmpeg_dir", d)
        settings.apply()
        self.refresh()
        msg(self, "已应用。")

    def _detect(self):
        self.enc_label.setText("正在检测（实际试编码）…")
        run_bg(lambda: encoders.detect(force=True), on_done=lambda r: self.enc_label.setText(encoders.describe(r)))

    def _lowprio(self, on):
        settings.set("low_priority", bool(on))
        settings.apply()

    def _theme(self):
        name = self.theme.currentData()
        settings.set("theme", name)
        self.themeChanged.emit(name)

    def _browse_out(self):
        d = QFileDialog.getExistingDirectory(self, "选择默认输出位置", self.out.text())
        if d:
            self.out.setText(d)
            settings.set("output_root", d)

    def _clear_cache(self):
        if not confirm(self, "清理缩略图、镜头分析、预览等缓存？"):
            return
        root = paths.cache_dir()
        for name in os.listdir(root):
            shutil.rmtree(os.path.join(root, name), ignore_errors=True)
        self.refresh()


# ============================================================================ 帮助
HELP_HTML = """
<h2>快速上手（3 步）</h2>
<ol>
<li><b>导入素材</b>：在“混剪生产”页把视频/图片（或整个文件夹）拖进素材列表。可在左下角添加片头、片尾、背景音乐、产品角标。</li>
<li><b>设置参数</b>：选择<b>片段混剪</b>（推荐）或整段拼接；在“时长控制”里填<b>不超过 X 分钟</b>和随机浮动。
画面/包装/音频/输出四个标签里可设置尺寸、转场、滤镜、标题、贴纸、背景音乐等。</li>
<li><b>开始生成</b>：点右下角“开始生成”，右侧会显示每条进度和日志。完成后点“打开输出文件夹”。</li>
</ol>
<h2>去水印 / 去贴纸 / 去遮挡</h2>
<ul>
<li>在素材列表选中素材 → 点“去水印处理”，进入“素材处理”页。</li>
<li><b>框选区域</b>：在画面上拖出水印位置，选择去除方式：<b>智能填充</b>（文字水印首选）、模糊、马赛克、纯色覆盖。</li>
<li><b>画笔涂抹</b>：形状不规则的贴纸/遮挡物，用画笔涂满即可（智能填充）。</li>
<li><b>自动识别</b>：自动分析整段视频中“始终不动”的文字/台标，框出疑似水印，确认后即可。</li>
<li><b>裁剪</b>：直接裁掉边缘的水印/字幕条/黑边，可“自动检测黑边”。</li>
<li>会换位置的水印（例如先在左上后在右下），可给每个区域设置“生效时间段”。</li>
<li>打开右上角“<b>效果预览</b>”可看到与导出完全一致的效果；“播放5秒”可看动画。</li>
<li>设置好后：可“全部导出”成新素材，或“发送到混剪”——混剪时直接在生成过程中去水印，不额外转码，画质更好。</li>
<li>同一来源的一批视频，设置一次后点“应用到全部素材”，或保存为<b>去水印模板</b>，以后在混剪素材上右键“套用去水印模板”。</li>
</ul>
<h2>时长控制说明</h2>
<p>成片时长只保证<b>不超过上限</b>，不做精确卡点。例如上限 1 分钟、浮动 20%：每条成片会在 48~60 秒之间随机，
最后一个片段会自然截短，转场重叠部分也计算在内，<b>绝不会超过 60 秒</b>。</p>
<h2>防重复（素材查重 + 成片复查）</h2>
<ul>
<li>素材本身是剪辑拼接过的、同一段画面出现在多个素材里、同一视频重复导入，都能自动识别；
转码、调色、加字幕、镜像、加黑边、横屏改竖屏后的同一画面也能认出。</li>
<li><b>剪辑前查重</b>：同一条成片里，同一段画面只会出现一次；几乎完全相同的素材只保留一份。</li>
<li><b>剪辑后复查</b>：成片生成后再抽帧检查，找到的相似画面会对照素材原始画面确认，确实重复的片段自动剪掉（成片只会变短，不会超过时长上限）。</li>
<li>点素材栏的“<b>检查重复</b>”可查看重复报告；素材列表的“重复”列显示每个素材的重复画面占比。</li>
<li>灵敏度一般用“标准”；素材互相搬运、改动大时用“严格”；同一场景的口播素材觉得限制太多、成片偏短时用“宽松”。</li>
</ul>
<h2>常见问题</h2>
<ul>
<li><b>速度慢？</b> 在“输出”里选择显卡编码（NVIDIA/Intel/AMD，自动检测可用性，失败自动回退 CPU）；并行数量保持“自动”。</li>
<li><b>电脑卡？</b> 设置里开启“后台低优先级运行”。</li>
<li><b>中途关机/停止了？</b> 到“任务记录”选中任务，点“继续未完成”，已完成的不会重做。</li>
<li><b>成片太像？</b> 用片段混剪、增加素材数量、开启“避免成片雷同”、随机滤镜、转场、变速。</li>
<li><b>成片偏短？</b> 素材不够，或素材里重复画面太多（防重复会跳过重复部分）：增加素材，或把防重复灵敏度调为“宽松”。</li>
<li><b>某条失败？</b> 会自动重试一次；失败原因见进度表和“数据/run.log”。</li>
</ul>
<h2>使用规范</h2>
<p>本工具用于对<b>自有或已获授权</b>的素材进行批量整合、规格统一和模板化剪辑，提高生产效率。
请尊重著作权与平台规则，不要用于搬运、伪原创或规避平台审核。去水印功能仅用于处理自有素材上的旧水印、
误加的贴纸或遮挡物。“画面变体”是多版本测试能力，不承诺任何“过审/去重”效果。</p>
"""


class HelpPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        t = QLabel(f"帮助与关于 · {APP_NAME} v{VERSION}")
        t.setObjectName("Title")
        root.addWidget(t)
        tb = QTextBrowser()
        tb.setOpenExternalLinks(True)
        tb.setHtml(HELP_HTML + self._licenses())
        c = Card()
        c.add(tb)
        root.addWidget(c, 1)
        row = QHBoxLayout()
        doc = os.path.join(paths.exe_dir(), "使用文档.html")
        if os.path.isfile(doc):
            row.addWidget(button("打开完整使用文档", slot=lambda: utils.open_path(doc)))
        row.addWidget(button("打开数据目录", slot=lambda: utils.open_path(paths.data_dir())))
        row.addStretch(1)
        root.addLayout(row)

    @staticmethod
    def _licenses():
        return """
<h2>第三方组件与授权</h2>
<ul>
<li>FFmpeg（GPL v3，<a href="https://ffmpeg.org">ffmpeg.org</a>）——以独立程序方式随包提供，见 ffmpeg/LICENSE.txt</li>
<li>Qt / PySide6（LGPL v3）、Python（PSF）、NumPy（BSD）、Pillow（HPND）</li>
<li>Microsoft Fluent Emoji 3D 表情（MIT）</li>
<li>字体：思源黑体 Noto Sans SC、得意黑 Smiley Sans、站酷快乐体、站酷庆科黄油体、马善政毛笔楷书（均为 SIL OFL 1.1，可免费商用）</li>
<li>内置电商标签、互动引导、箭头、装饰、气泡贴纸及全部滤镜参数为本工具原创。</li>
</ul>
"""
