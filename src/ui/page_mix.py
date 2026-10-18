"""混剪生产页：素材 → 参数 → 一键批量生成；运行时显示每条进度与日志，支持停止 / 断点续跑。"""
import os
import threading
import time

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox, QCheckBox,
                               QRadioButton, QButtonGroup, QTabWidget, QPlainTextEdit, QSplitter, QStackedWidget,
                               QFileDialog, QProgressBar, QTableWidget, QTableWidgetItem, QHeaderView,
                               QAbstractItemView, QInputDialog, QMenu, QPushButton, QDialog, QDialogButtonBox)

from mixer import settings, paths, utils, encoders, probe, dedup
from mixer import filters_lib as fl
from mixer import textstyle
from mixer.models import (MixConfig, CANVAS_PRESETS, FIT_MODES, TRANSITION_MODES, StylePreset, CleanupProfile,
                          load_json, save_json)
from mixer.batch import BatchRunner
from . import theme
from .widgets import (Card, form_row, hint, dspin, ispin, button, MaterialTable, file_list_panel, CheckList,
                      ValueSlider, msg, confirm, VIDEO_FILTER, AUDIO_FILTER, IMAGE_FILTER)
from .panels import scroll_wrap
from .workers import EventBridge, run_bg
from .page_studio import tpl_dir, list_templates


class MixPage(QWidget):
    openStudio = Signal(list)
    runStateChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.runner = None
        self.thread = None
        self.cleanup_map = {}
        self.style = {}
        self.bridge = EventBridge(self)
        self.bridge.event.connect(self._on_event)
        self.row_of = {}
        self.t_start = 0
        self._build()
        last = settings.get().get("last_config")
        self.load_config(MixConfig.from_dict(last) if last else MixConfig(output_root=settings.output_root()))
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)

    # ================================================================ 构建界面
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)
        head = QHBoxLayout()
        t = QLabel("混剪生产")
        t.setObjectName("Title")
        head.addWidget(t)
        head.addWidget(hint("导入素材 → 设置时长上限与风格 → 一键批量生成。时长只保证“不超过上限”，不做精确卡点。"), 1)
        self.preset_btn = button("方案", "save")
        m = QMenu(self)
        m.aboutToShow.connect(lambda: self._fill_preset_menu(m))
        self.preset_btn.setMenu(m)
        head.addWidget(self.preset_btn)
        root.addLayout(head)

        self.split = QSplitter(Qt.Horizontal)
        self.split.setChildrenCollapsible(False)
        root.addWidget(self.split, 1)

        # ---------------- 左：素材
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(10)
        mc = Card("视频 / 图片素材")
        bar = QHBoxLayout()
        bar.addWidget(button("添加文件", "add", slot=self._add_files))
        bar.addWidget(button("添加文件夹", "folder", slot=self._add_folder))
        bar.addWidget(button("去水印处理", "wand", slot=self._to_studio,
                             tip="把选中的素材（不选则全部）送到“素材处理”页设置去水印/去贴纸/去遮挡"))
        bar.addWidget(button("检查重复", "magic", slot=self._check_dups,
                             tip="分析素材之间 / 素材内部重复的画面（剪辑拼接过的素材常见）"))
        bar.addStretch(1)
        bar.addWidget(button("清空", slot=self._clear_materials))
        mc.add(bar)
        self.table = MaterialTable(actions=[("studio", "去水印 / 处理…"), ("apply_tpl", "套用去水印模板…"),
                                            ("clear_clean", "清除去水印设置"), ("-", ""),
                                            ("as_opening", "设为片头"), ("as_ending", "设为片尾")])
        self.table.changed.connect(self._update_summary)
        self.table.contextAction.connect(self._on_table_action)
        mc.add(self.table)
        self.mat_stats = QLabel()
        self.mat_stats.setObjectName("Sub")
        mc.add(self.mat_stats)
        ll.addWidget(mc, 3)
        rc = Card()
        rt = QTabWidget()
        w1, self.openings = file_list_panel("每条成片开头固定播放（多个则随机/轮流）", ("video", "image"), VIDEO_FILTER,
                                            "拖入片头视频/图片（可选）")
        w2, self.endings = file_list_panel("每条成片结尾固定播放", ("video", "image"), VIDEO_FILTER, "拖入片尾视频/图片（可选）")
        w3, self.bgms = file_list_panel("每条随机选一首，自动循环/淡入淡出", ("audio",), AUDIO_FILTER, "拖入背景音乐（可选）")
        w4, self.badges = file_list_panel("产品图/Logo 随机出现在四角（白底/黑底自动去除）", ("image",), IMAGE_FILTER,
                                          "拖入产品图（可选）")
        for w, n in ((w1, "片头"), (w2, "片尾"), (w3, "背景音乐"), (w4, "产品角标")):
            rt.addTab(w, n)
        for fl_ in (self.openings, self.endings, self.bgms, self.badges):
            fl_.changed.connect(self._update_summary)
        rc.add(rt)
        ll.addWidget(rc, 2)
        self.split.addWidget(left)

        # ---------------- 右：参数 / 运行
        self.rstack = QStackedWidget()
        self.settings_view = self._build_settings()
        self.run_view = self._build_run_view()
        self.rstack.addWidget(self.settings_view)
        self.rstack.addWidget(self.run_view)
        self.split.addWidget(self.rstack)
        self.split.setSizes([620, 560])

        # ---------------- 底部
        bottom = Card(margins=(14, 10, 14, 10))
        br = QHBoxLayout()
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        br.addWidget(self.summary, 1)
        self.start_btn = button("开始生成", "play", kind="Primary", slot=self.start)
        self.start_btn.setObjectName("Primary")
        self.start_btn.setProperty("big", True)
        self.start_btn.setMinimumHeight(40)
        self.start_btn.setMinimumWidth(150)
        br.addWidget(self.start_btn)
        bottom.add(br)
        root.addWidget(bottom)

    def _build_settings(self):
        tabs = QTabWidget()
        tabs.setObjectName("Panel")
        # ---------------- 基本
        w = QWidget()
        l = QVBoxLayout(w)
        l.setSpacing(10)
        self.name = QLineEdit()
        self.name.setMaximumWidth(260)
        l.addLayout(form_row("任务名称", self.name))
        self.count = ispin(1, 9999, 10, suffix=" 条", width=110)
        l.addLayout(form_row("生成数量", self.count))
        self.mode_seg = QRadioButton("片段混剪（推荐：从各素材随机截取片段重新组合）")
        self.mode_whole = QRadioButton("整段拼接（每条随机选几个完整素材拼接）")
        g = QButtonGroup(self)
        g.addButton(self.mode_seg)
        g.addButton(self.mode_whole)
        mv = QVBoxLayout()
        mv.addWidget(self.mode_seg)
        mv.addWidget(self.mode_whole)
        l.addLayout(form_row("混剪模式", mv))
        self.mode_seg.toggled.connect(self._mode_changed)
        # 时长
        tc = Card("时长控制", margins=(12, 10, 12, 12))
        self.limit_on = QCheckBox("限制成片时长")
        self.limit_min = dspin(0.1, 180, 1.0, 0.5, 1, " 分钟", width=110)
        self.limit_float = ispin(0, 80, 20, 5, " %", width=80)
        tc.add(form_row("", self.limit_on, "不超过", self.limit_min, label_w=0))
        tc.add(form_row("随机浮动", self.limit_float, "每条成片时长在 [上限×(1-浮动), 上限] 之间随机", label_w=64))
        self.limit_hint = QLabel()
        self.limit_hint.setStyleSheet(f"color: {theme.C['accent']};")
        tc.add(self.limit_hint)
        for wd in (self.limit_on,):
            wd.toggled.connect(self._update_summary)
        for wd in (self.limit_min, self.limit_float):
            wd.valueChanged.connect(self._update_summary)
        l.addWidget(tc)
        dc = Card("防重复（素材是剪辑拼接过的必开）", margins=(12, 10, 12, 12))
        self.dedup_on = QCheckBox("剪辑前查重：识别素材之间 / 素材内部重复的画面，同一条成片里不重复使用")
        self.dedup_post = QCheckBox("剪辑后复查：成片生成后再抽帧检查，发现重复片段自动剪掉")
        self.dedup_level = QComboBox()
        for k in ("standard", "strict", "loose"):
            self.dedup_level.addItem(dedup.LEVELS[k]["label"], k)
        dc.add(self.dedup_on)
        dc.add(self.dedup_post)
        dc.add(form_row("灵敏度", self.dedup_level, button("检查素材重复…", "magic", slot=self._check_dups),
                        label_w=50))
        dc.add(hint("能识别：同一段画面出现在多个素材里、素材内部重复、同一视频重复导入，"
                    "以及镜像、加滤镜、改画幅（黑边 / 模糊背景）后的同一画面。"))
        l.addWidget(dc)
        self.seg_min = dspin(0.5, 60, 2.0, 0.5, 1, " 秒")
        self.seg_max = dspin(0.5, 120, 5.0, 0.5, 1, " 秒")
        self.row_seg = QWidget()
        self.row_seg.setLayout(form_row("片段长度", self.seg_min, "~", self.seg_max))
        l.addWidget(self.row_seg)
        self.clips_min = ispin(1, 50, 1, suffix=" 个", width=80)
        self.clips_max = ispin(1, 50, 3, suffix=" 个", width=80)
        self.trim_overflow = QCheckBox("超出上限的素材自动截断")
        self.row_clips = QWidget()
        rcl = form_row("每条素材数", self.clips_min, "~", self.clips_max, self.trim_overflow)
        self.row_clips.setLayout(rcl)
        l.addWidget(self.row_clips)
        self.trim_head = dspin(0, 60, 0, 0.5, 1, " 秒")
        self.trim_tail = dspin(0, 60, 0, 0.5, 1, " 秒")
        l.addLayout(form_row("掐头去尾", "跳过开头", self.trim_head, "结尾", self.trim_tail,
                             tip="跳过每个素材的开头/结尾（例如平台片尾 logo 动画常在最后 3~4 秒）"))
        self.use_scenes = QCheckBox("智能镜头切分（按镜头切点取片段，更自然）")
        self.avoid_similar = QCheckBox("避免成片雷同（素材覆盖均衡 + 相似度控制）")
        self.allow_repeat = QCheckBox("素材不够时允许重复使用")
        for c in (self.use_scenes, self.avoid_similar, self.allow_repeat):
            l.addWidget(c)
        self.speed_on = QCheckBox("片段随机变速")
        self.speed_min = dspin(0.5, 2.0, 0.9, 0.05, 2, " 倍", width=86)
        self.speed_max = dspin(0.5, 2.0, 1.15, 0.05, 2, " 倍", width=86)
        l.addLayout(form_row("", self.speed_on, self.speed_min, "~", self.speed_max, label_w=0))
        self.image_dur = dspin(0.5, 30, 3.0, 0.5, 1, " 秒")
        self.opening_random = QCheckBox("多个片头/片尾时随机选择（否则轮流）")
        l.addLayout(form_row("图片素材时长", self.image_dur, "每张（自动缓慢推拉）"))
        l.addWidget(self.opening_random)
        l.addStretch(1)
        tabs.addTab(scroll_wrap(w), "基本")

        # ---------------- 画面
        w = QWidget()
        l = QVBoxLayout(w)
        l.setSpacing(10)
        self.canvas = QComboBox()
        for k, v in CANVAS_PRESETS.items():
            self.canvas.addItem(v, k)
        l.addLayout(form_row("画面尺寸", self.canvas))
        self.fit = QComboBox()
        for k, v in FIT_MODES.items():
            self.fit.addItem(v, k)
        l.addLayout(form_row("比例不一致时", self.fit))
        self.fps = QComboBox()
        for f in (24, 25, 30, 50, 60):
            self.fps.addItem(f"{f} fps", f)
        l.addLayout(form_row("帧率", self.fps))
        tc = Card("转场", margins=(12, 10, 12, 12))
        self.trans_mode = QComboBox()
        for k, v in TRANSITION_MODES.items():
            self.trans_mode.addItem(v, k)
        self.trans_mode.currentIndexChanged.connect(self._trans_changed)
        tc.add(form_row("转场方式", self.trans_mode, label_w=64))
        self.trans_dur = dspin(0.2, 1.5, 0.5, 0.1, 1, " 秒", width=80)
        self.trans_prob = ispin(0, 100, 60, 10, " %", width=80)
        tc.add(form_row("转场时长", self.trans_dur, "出现概率", self.trans_prob, label_w=64))
        self.xfade_list = CheckList([(k, v) for k, v in fl.XFADE_TYPES.items()], fl.DEFAULT_XFADE, columns_hint=96)
        self.xfade_list.setFixedHeight(130)
        self.effect_list = CheckList([(k, v) for k, v in fl.CUT_EFFECTS.items()], fl.DEFAULT_CUT_EFFECTS,
                                     columns_hint=96)
        self.effect_list.setFixedHeight(40)
        self.lbl_x = QLabel("叠化转场类型（随机选用）：")
        self.lbl_x.setObjectName("Sub")
        self.lbl_e = QLabel("切换特效类型（随机选用）：")
        self.lbl_e.setObjectName("Sub")
        tc.add(self.lbl_x)
        tc.add(self.xfade_list)
        tc.add(self.lbl_e)
        tc.add(self.effect_list)
        l.addWidget(tc)
        fc = Card("滤镜", margins=(12, 10, 12, 12))
        self.filter_mode = QComboBox()
        self.filter_mode.addItem("不加滤镜", "none")
        self.filter_mode.addItem("统一使用一个滤镜", "fixed")
        self.filter_mode.addItem("每条随机滤镜（从勾选中选）", "random")
        self.filter_mode.currentIndexChanged.connect(self._filter_mode_changed)
        self.filter_fixed = QComboBox()
        self.filter_fixed.addItems(list(fl.FILTERS.keys()))
        fc.add(form_row("滤镜方式", self.filter_mode, self.filter_fixed, label_w=64))
        self.filter_list = CheckList([(n, n) for n in fl.FILTERS], [], columns_hint=86)
        self.filter_list.setFixedHeight(120)
        fc.add(self.filter_list)
        self.filter_int = ValueSlider(0, 1, 0.8, fmt="{:.0%}")
        fc.add(form_row("滤镜强度", self.filter_int, stretch=False, label_w=64))
        fc.add(hint("可在“素材处理”页的滤镜标签里先看各滤镜在自己素材上的效果。"))
        l.addWidget(fc)
        vc = Card("画面变体（A/B 多版本）", margins=(12, 10, 12, 12))
        self.variant = QComboBox()
        for k, v in fl.VARIANT_LEVELS.items():
            self.variant.addItem(v, k)
        self.mirror = QCheckBox("水平镜像（会让画面中的文字反向，带货视频不建议）")
        vc.add(form_row("变体强度", self.variant, label_w=64))
        vc.add(self.mirror)
        vc.add(hint("用于多版本测试和画面差异化，属于轻微调色/颗粒/缩放；请勿用于搬运他人作品。"))
        l.addWidget(vc)
        l.addStretch(1)
        tabs.addTab(scroll_wrap(w), "画面")

        # ---------------- 包装
        w = QWidget()
        l = QVBoxLayout(w)
        l.setSpacing(10)
        pc = Card("贴纸 / 文字 / 滤镜 包装模板", margins=(12, 10, 12, 12))
        self.style_label = QLabel("未设置")
        self.style_label.setObjectName("Sub")
        pr = QHBoxLayout()
        pr.addWidget(button("设计包装…", "sticker", kind="Primary", slot=self._design))
        self.style_tpl_btn = button("载入模板")
        m = QMenu(self)
        m.aboutToShow.connect(lambda: self._fill_style_menu(m))
        self.style_tpl_btn.setMenu(m)
        pr.addWidget(self.style_tpl_btn)
        pr.addWidget(button("清除", slot=lambda: self._set_style({})))
        pr.addStretch(1)
        pc.add(pr)
        pc.add(self.style_label)
        pc.add(hint("在成片画布上拖放贴纸（剪映风格贴纸库 + 3D 表情 + 自定义贴纸）、花字文字、Logo，支持入场/循环/出场动画。"))
        l.addWidget(pc)
        tc = Card("标题文案（每条随机选一条）", margins=(12, 10, 12, 12))
        self.titles = QPlainTextEdit()
        self.titles.setPlaceholderText("每行一条标题，例如：\n今日爆款好物推荐\n这个价格真的绝了\n（留空则不加标题）")
        self.titles.setFixedHeight(96)
        tc.add(self.titles)
        self.title_preset = QComboBox()
        self.title_preset.addItems(list(textstyle.PRESETS.keys()))
        self.title_size = ispin(30, 200, 80, width=80)
        tc.add(form_row("花字样式", self.title_preset, "字号", self.title_size, label_w=64))
        self.title_pos = QComboBox()
        for k, v in (("top", "顶部"), ("center", "中间"), ("bottom", "底部")):
            self.title_pos.addItem(v, k)
        self.title_dur = dspin(0, 600, 0, 1, 0, " 秒", width=90)
        tc.add(form_row("位置", self.title_pos, "显示", self.title_dur, "0=全程", label_w=64))
        l.addWidget(tc)
        bc = Card("产品角标", margins=(12, 10, 12, 12))
        self.badge_on = QCheckBox("启用产品角标（在左侧“产品角标”标签里添加图片）")
        self.badge_anim = QComboBox()
        for k, v in (("fade", "渐显"), ("pop", "弹出"), ("slide_left", "从左滑入"), ("slide_right", "从右滑入"),
                     ("none", "无")):
            self.badge_anim.addItem(v, k)
        bc.add(self.badge_on)
        bc.add(form_row("入场动画", self.badge_anim, label_w=64))
        l.addWidget(bc)
        l.addStretch(1)
        tabs.addTab(scroll_wrap(w), "包装")

        # ---------------- 音频
        w = QWidget()
        l = QVBoxLayout(w)
        l.setSpacing(10)
        self.keep_audio = QComboBox()
        for k, v in (("keep", "保留原声"), ("lower", "原声压低（配合背景音乐）"), ("mute", "去掉原声（只用背景音乐）")):
            self.keep_audio.addItem(v, k)
        l.addLayout(form_row("素材原声", self.keep_audio))
        self.orig_vol = ValueSlider(0, 2, 1.0, fmt="{:.0%}")
        self.bgm_vol = ValueSlider(0, 1.5, 0.35, fmt="{:.0%}")
        l.addLayout(form_row("原声音量", self.orig_vol, stretch=False))
        l.addLayout(form_row("背景音乐音量", self.bgm_vol, stretch=False))
        self.bgm_rand = QCheckBox("背景音乐随机起点（同一首歌也能听起来不一样）")
        self.loudnorm = QCheckBox("响度统一（EBU R128，避免忽大忽小）")
        self.afade = QCheckBox("结尾音频淡出")
        for c in (self.bgm_rand, self.loudnorm, self.afade):
            l.addWidget(c)
        l.addWidget(hint("背景音乐在左侧“背景音乐”标签中添加；请使用有版权或可商用的音乐。"))
        l.addStretch(1)
        tabs.addTab(scroll_wrap(w), "音频")

        # ---------------- 输出
        w = QWidget()
        l = QVBoxLayout(w)
        l.setSpacing(10)
        self.out_root = QLineEdit()
        l.addLayout(form_row("输出位置", self.out_root, button("浏览", "folder", slot=self._browse_out),
                             stretch=False))
        self.name_tpl = QLineEdit()
        vb = QHBoxLayout()
        for v in ("{任务名}", "{序号}", "{日期}", "{素材名}", "{时长}"):
            b = QPushButton(v)
            b.setObjectName("Flat")
            b.clicked.connect(lambda _=False, v=v: self.name_tpl.insert(v))
            vb.addWidget(b)
        vb.addStretch(1)
        l.addLayout(form_row("文件命名", self.name_tpl, stretch=False))
        l.addLayout(form_row("", vb, label_w=96))
        self.quality = QComboBox()
        for k, v in (("high", "高清（文件较大）"), ("standard", "标准（推荐）"), ("small", "省空间")):
            self.quality.addItem(v, k)
        l.addLayout(form_row("画质", self.quality))
        self.encoder = QComboBox()
        self.encoder.addItem("自动（优先显卡，失败自动回退 CPU）", "auto")
        for k, v in encoders.ENCODERS.items():
            self.encoder.addItem(v["label"], k)
        self.enc_info = QLabel("")
        self.enc_info.setObjectName("Sub")
        l.addLayout(form_row("编码方式", self.encoder, button("检测显卡", slot=self._detect_enc)))
        l.addLayout(form_row("", self.enc_info))
        self.conc = QComboBox()
        self.conc.addItem("自动（按 CPU/内存）", 0)
        for i in range(1, 9):
            self.conc.addItem(f"{i} 路并行", i)
        l.addLayout(form_row("并行数量", self.conc))
        self.cover = QCheckBox("生成封面图（自动挑选清晰的一帧）")
        self.cover_title = QCheckBox("封面加标题文字")
        self.metadata = QCheckBox("写入标题/备注元数据（归档用）")
        self.keep_temp = QCheckBox("保留临时文件（排查问题用）")
        for c in (self.cover, self.cover_title, self.metadata, self.keep_temp):
            l.addWidget(c)
        self.seed = ispin(0, 2 ** 31 - 1, 0, width=140)
        l.addLayout(form_row("随机种子", self.seed, "0 = 每次不同；固定数值可复现同样结果"))
        l.addStretch(1)
        tabs.addTab(scroll_wrap(w), "输出")
        self.settings_tabs = tabs
        return tabs

    def _build_run_view(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 0, 0, 0)
        c = Card("生成进度")
        self.run_title = QLabel("准备中…")
        self.run_title.setObjectName("H2")
        c.add(self.run_title)
        self.pbar = QProgressBar()
        self.pbar.setRange(0, 1000)
        c.add(self.pbar)
        self.run_stat = QLabel("")
        self.run_stat.setObjectName("Sub")
        c.add(self.run_stat)
        self.items = QTableWidget(0, 4)
        self.items.setHorizontalHeaderLabels(["#", "文件名", "状态", "进度"])
        self.items.verticalHeader().setVisible(False)
        self.items.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.items.setSelectionBehavior(QAbstractItemView.SelectRows)
        h = self.items.horizontalHeader()
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        for col, wd in ((0, 44), (2, 120), (3, 70)):
            h.setSectionResizeMode(col, QHeaderView.Fixed)
            self.items.setColumnWidth(col, wd)
        self.items.cellDoubleClicked.connect(self._open_item)
        c.add(self.items)
        self.log = QPlainTextEdit()
        self.log.setObjectName("Log")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setFixedHeight(150)
        c.add(self.log)
        row = QHBoxLayout()
        self.stop_btn = button("停止（完成当前后）", "stop", slot=lambda: self.stop(False))
        self.abort_btn = button("立即中止", kind="Danger", slot=lambda: self.stop(True))
        self.open_btn = button("打开输出文件夹", "folder", slot=self._open_out)
        self.back_btn = button("返回设置", slot=self._back)
        for b in (self.stop_btn, self.abort_btn, self.open_btn, self.back_btn):
            row.addWidget(b)
        row.addStretch(1)
        c.add(row)
        l.addWidget(c)
        return w

    # ================================================================ 配置 <-> 界面
    def load_config(self, cfg: MixConfig):
        self.table.clear_all()
        self.table.add_paths([p for p in cfg.materials if os.path.exists(p)])
        self.openings.set_paths(cfg.openings)
        self.endings.set_paths(cfg.endings)
        self.bgms.set_paths(cfg.bgms)
        self.badges.set_paths(cfg.badges)
        self.cleanup_map = dict(cfg.cleanup_map or {})
        self.name.setText(cfg.name)
        self.count.setValue(cfg.count)
        (self.mode_seg if cfg.mode == "segment" else self.mode_whole).setChecked(True)
        self.limit_on.setChecked(cfg.limit_enabled)
        self.limit_min.setValue(cfg.limit_minutes)
        self.limit_float.setValue(cfg.limit_float)
        self.seg_min.setValue(cfg.seg_min)
        self.seg_max.setValue(cfg.seg_max)
        self.clips_min.setValue(cfg.clips_min)
        self.clips_max.setValue(cfg.clips_max)
        self.trim_overflow.setChecked(cfg.trim_overflow)
        self.trim_head.setValue(cfg.trim_head)
        self.trim_tail.setValue(cfg.trim_tail)
        self.use_scenes.setChecked(cfg.use_scenes)
        self.dedup_on.setChecked(cfg.dedup_enabled)
        self.dedup_post.setChecked(cfg.dedup_postcheck)
        self._combo_set(self.dedup_level, cfg.dedup_level or "standard")
        self.avoid_similar.setChecked(cfg.avoid_similar)
        self.allow_repeat.setChecked(cfg.allow_repeat)
        self.speed_on.setChecked(cfg.speed_enabled)
        self.speed_min.setValue(cfg.speed_min)
        self.speed_max.setValue(cfg.speed_max)
        self.image_dur.setValue(cfg.image_duration)
        self.opening_random.setChecked(cfg.opening_random)
        self._combo_set(self.canvas, cfg.canvas)
        self._combo_set(self.fit, cfg.fit)
        self._combo_set(self.fps, int(cfg.fps))
        self._combo_set(self.trans_mode, cfg.transition_mode)
        self.trans_dur.setValue(cfg.transition_dur)
        self.trans_prob.setValue(cfg.transition_prob)
        tt = cfg.transition_types or []
        self.xfade_list.set_checked([t for t in tt if t in fl.XFADE_TYPES] or fl.DEFAULT_XFADE)
        self.effect_list.set_checked([t for t in tt if t in fl.CUT_EFFECTS] or fl.DEFAULT_CUT_EFFECTS)
        self._combo_set(self.filter_mode, cfg.filter_mode)
        if cfg.filter_mode == "fixed" and cfg.filter_choices:
            self.filter_fixed.setCurrentText(cfg.filter_choices[0])
        self.filter_list.set_checked(cfg.filter_choices if cfg.filter_mode == "random" else [])
        self.filter_int.setValue(cfg.filter_intensity)
        self._combo_set(self.variant, int(cfg.variant_level))
        self.mirror.setChecked(cfg.mirror)
        self.titles.setPlainText("\n".join(cfg.titles or []))
        ts = cfg.title_style or {}
        self.title_preset.setCurrentText(ts.get("_preset", "综艺花字") if ts else "综艺花字")
        self.title_size.setValue(int(ts.get("size", 80)) if ts else 80)
        self._combo_set(self.title_pos, cfg.title_pos)
        self.title_dur.setValue(cfg.title_duration)
        self.badge_on.setChecked(cfg.badge_enabled)
        self._combo_set(self.badge_anim, cfg.badge_anim)
        self._combo_set(self.keep_audio, cfg.keep_audio)
        self.orig_vol.setValue(cfg.orig_volume)
        self.bgm_vol.setValue(cfg.bgm_volume)
        self.bgm_rand.setChecked(cfg.bgm_random_start)
        self.loudnorm.setChecked(cfg.loudnorm)
        self.afade.setChecked(cfg.audio_fade)
        self.out_root.setText(cfg.output_root or settings.output_root())
        self.name_tpl.setText(cfg.name_template)
        self._combo_set(self.quality, cfg.quality)
        self._combo_set(self.encoder, cfg.encoder)
        self._combo_set(self.conc, int(cfg.concurrency or 0))
        self.cover.setChecked(cfg.cover)
        self.cover_title.setChecked(cfg.cover_title)
        self.metadata.setChecked(cfg.metadata)
        self.keep_temp.setChecked(cfg.keep_temp)
        self.seed.setValue(int(cfg.seed or 0))
        self._set_style(cfg.style or {})
        self._mode_changed()
        self._trans_changed()
        self._filter_mode_changed()
        self._refresh_marks()
        self._update_summary()

    @staticmethod
    def _combo_set(cb, data):
        i = cb.findData(data)
        if i >= 0:
            cb.setCurrentIndex(i)

    def get_config(self) -> MixConfig:
        cfg = MixConfig()
        cfg.name = self.name.text().strip() or "混剪任务"
        cfg.materials = self.table.paths()
        cfg.openings = self.openings.paths()
        cfg.endings = self.endings.paths()
        cfg.bgms = self.bgms.paths()
        cfg.badges = self.badges.paths()
        cfg.cleanup_map = {k: v for k, v in self.cleanup_map.items() if k in cfg.materials}
        cfg.count = self.count.value()
        cfg.mode = "segment" if self.mode_seg.isChecked() else "whole"
        cfg.limit_enabled = self.limit_on.isChecked() or cfg.mode == "segment"
        cfg.limit_minutes = self.limit_min.value()
        cfg.limit_float = self.limit_float.value()
        cfg.seg_min, cfg.seg_max = sorted((self.seg_min.value(), self.seg_max.value()))
        cfg.clips_min, cfg.clips_max = sorted((self.clips_min.value(), self.clips_max.value()))
        cfg.trim_overflow = self.trim_overflow.isChecked()
        cfg.trim_head = self.trim_head.value()
        cfg.trim_tail = self.trim_tail.value()
        cfg.use_scenes = self.use_scenes.isChecked()
        cfg.dedup_enabled = self.dedup_on.isChecked()
        cfg.dedup_postcheck = self.dedup_post.isChecked()
        cfg.dedup_level = self.dedup_level.currentData()
        cfg.avoid_similar = self.avoid_similar.isChecked()
        cfg.allow_repeat = self.allow_repeat.isChecked()
        cfg.speed_enabled = self.speed_on.isChecked()
        cfg.speed_min, cfg.speed_max = sorted((self.speed_min.value(), self.speed_max.value()))
        cfg.image_duration = self.image_dur.value()
        cfg.opening_random = self.opening_random.isChecked()
        cfg.canvas = self.canvas.currentData()
        cfg.fit = self.fit.currentData()
        cfg.fps = self.fps.currentData()
        cfg.transition_mode = self.trans_mode.currentData()
        cfg.transition_types = self.xfade_list.checked() + self.effect_list.checked()
        cfg.transition_dur = self.trans_dur.value()
        cfg.transition_prob = self.trans_prob.value()
        cfg.filter_mode = self.filter_mode.currentData()
        if cfg.filter_mode == "fixed":
            cfg.filter_choices = [self.filter_fixed.currentText()]
        elif cfg.filter_mode == "random":
            cfg.filter_choices = self.filter_list.checked()
        else:
            cfg.filter_choices = []
        cfg.filter_intensity = self.filter_int.value()
        cfg.variant_level = self.variant.currentData()
        cfg.mirror = self.mirror.isChecked()
        cfg.titles = [t.strip() for t in self.titles.toPlainText().splitlines() if t.strip()]
        st = textstyle.preset_style(self.title_preset.currentText(), size=self.title_size.value()).to_dict()
        st["_preset"] = self.title_preset.currentText()
        cfg.title_style = st
        cfg.title_pos = self.title_pos.currentData()
        cfg.title_duration = self.title_dur.value()
        cfg.badge_enabled = self.badge_on.isChecked()
        cfg.badge_anim = self.badge_anim.currentData()
        cfg.keep_audio = self.keep_audio.currentData()
        cfg.orig_volume = self.orig_vol.value()
        cfg.bgm_volume = self.bgm_vol.value()
        cfg.bgm_random_start = self.bgm_rand.isChecked()
        cfg.loudnorm = self.loudnorm.isChecked()
        cfg.audio_fade = self.afade.isChecked()
        cfg.output_root = self.out_root.text().strip() or settings.output_root()
        cfg.name_template = self.name_tpl.text().strip() or "{任务名}_{序号}"
        cfg.quality = self.quality.currentData()
        cfg.encoder = self.encoder.currentData()
        cfg.concurrency = self.conc.currentData()
        cfg.cover = self.cover.isChecked()
        cfg.cover_title = self.cover_title.isChecked()
        cfg.metadata = self.metadata.isChecked()
        cfg.keep_temp = self.keep_temp.isChecked()
        cfg.seed = self.seed.value()
        cfg.style = dict(self.style or {})
        return cfg

    def save_last(self):
        try:
            settings.set("last_config", self.get_config().to_dict())
        except Exception:
            pass

    # ================================================================ 交互
    def _mode_changed(self, *_):
        seg = self.mode_seg.isChecked()
        self.row_seg.setVisible(seg)
        self.row_clips.setVisible(not seg)
        self.use_scenes.setEnabled(seg)
        self.speed_on.setEnabled(seg)
        if seg:
            self.limit_on.setChecked(True)
        self.limit_on.setEnabled(not seg)
        self._update_summary()

    def _trans_changed(self, *_):
        m = self.trans_mode.currentData()
        x = m in ("xfade", "mixed")
        e = m in ("effect", "mixed")
        self.xfade_list.setVisible(x)
        self.lbl_x.setVisible(x)
        self.effect_list.setVisible(e)
        self.lbl_e.setVisible(e)
        self.trans_dur.setEnabled(x)
        self.trans_prob.setEnabled(m != "none")

    def _filter_mode_changed(self, *_):
        m = self.filter_mode.currentData()
        self.filter_fixed.setVisible(m == "fixed")
        self.filter_list.setVisible(m == "random")
        self.filter_int.setEnabled(m != "none")

    def _update_summary(self, *_):
        n, ok, dur, imgs, bad = self.table.stats()
        self.mat_stats.setText(f"共 {n} 个素材：可用 {ok}（视频总时长 {utils.fmt_secs(dur)}"
                               + (f"，图片 {imgs} 张" if imgs else "") + "）"
                               + (f"，{bad} 个无效将被跳过" if bad else "")
                               + ("" if n else "。支持 mp4/mov/mkv/avi/flv 等，以及图片"))
        lim = self.limit_min.value() * 60
        f = self.limit_float.value() / 100.0
        seg = self.mode_seg.isChecked()
        if self.limit_on.isChecked() or seg:
            lo = lim * (1 - f)
            self.limit_hint.setText(f"→ 每条成片约 {utils.fmt_secs(lo)} ~ {utils.fmt_secs(lim)}"
                                    f"（{lo:.0f}~{lim:.0f} 秒），绝不超过 {utils.fmt_secs(lim)}")
            dtext = f"每条 {lo:.0f}~{lim:.0f} 秒"
        else:
            self.limit_hint.setText("→ 不限时长：时长随所选整段素材而定")
            dtext = "时长不限"
        cnt = self.count.value()
        extra = []
        if self.cleanup_map:
            extra.append(f"{len([k for k in self.cleanup_map if k in self.table.rows])} 个素材已设置去水印")
        if self.bgms.count():
            extra.append(f"{self.bgms.count()} 首背景音乐")
        self.summary.setText(f"<b>{'片段混剪' if seg else '整段拼接'}</b> · 生成 <b>{cnt}</b> 条 · {dtext}"
                             + (" · " + "，".join(extra) if extra else ""))

    def _add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "选择素材", "", VIDEO_FILTER)
        if files:
            self.table.add_paths(files)

    def _add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "选择素材文件夹")
        if d:
            n = self.table.add_paths([d])
            if n == 0:
                msg(self, "该文件夹中没有找到支持的视频/图片。")

    def _clear_materials(self):
        if self.table.rowCount() and confirm(self, "确定清空素材列表？"):
            self.table.clear_all()

    def add_materials(self, paths_):
        self.table.add_paths(paths_)
        self._refresh_marks()

    def _to_studio(self):
        sel = self.table.selected_paths() or self.table.paths()
        if not sel:
            msg(self, "请先添加素材。")
            return
        self.openStudio.emit(sel)

    def _on_table_action(self, key, sel):
        if key == "studio":
            self.openStudio.emit(sel)
        elif key == "apply_tpl":
            names = list_templates("去水印")
            if not names:
                msg(self, "还没有去水印模板。请在“素材处理”页设置好后，用“模板 → 保存当前去水印设置为模板”。")
                return
            name, ok = QInputDialog.getItem(self, "套用去水印模板", "选择模板：", names, 0, False)
            if ok:
                d = load_json(os.path.join(tpl_dir("去水印"), name + ".json"), {})
                for p in sel:
                    self.cleanup_map[p] = d
                self._refresh_marks()
        elif key == "clear_clean":
            for p in sel:
                self.cleanup_map.pop(p, None)
                self.table.set_mark(p, "", "")
            self._update_summary()
        elif key == "as_opening":
            self.openings.add_paths(sel)
            self.table.remove_paths(sel)
        elif key == "as_ending":
            self.endings.add_paths(sel)
            self.table.remove_paths(sel)

    def set_cleanup(self, cmap: dict, paths_):
        missing = [p for p in paths_ if p not in self.table.rows]
        if missing:
            self.table.add_paths(missing)
        self.cleanup_map.update(cmap)
        self._refresh_marks()
        self._update_summary()

    def _refresh_marks(self):
        for p in self.table.paths():
            d = self.cleanup_map.get(p)
            if d and not CleanupProfile.from_dict(d).is_empty():
                c = CleanupProfile.from_dict(d)
                tip = f"去水印区域 {len(c.regions)} 个" + ("，画笔蒙版" if c.mask_png else "") + \
                      ("，裁边" if any((c.crop_l, c.crop_t, c.crop_r, c.crop_b)) else "")
                self.table.set_mark(p, "去水印", tip)
            else:
                self.table.set_mark(p, "", "")

    def _check_dups(self):
        """手动检查素材重复：后台分析（结果缓存），完成后显示报告。"""
        from PySide6.QtWidgets import QProgressDialog
        from .dup_dialog import DupReportDialog
        cfg = self.get_config()
        infos = [self.table.infos[p] for p in cfg.materials + cfg.openings + cfg.endings
                 if p in self.table.infos and self.table.infos[p].ok]
        if len(infos) < 1:
            msg(self, "请先添加素材（并等待素材检查完成）。")
            return
        dlg = QProgressDialog("正在分析素材画面指纹（每 0.5 秒一帧，结果会缓存）…", "取消", 0, len(infos), self)
        dlg.setWindowTitle("检查素材重复")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        cancel = threading.Event()
        dlg.canceled.connect(cancel.set)
        bridge = EventBridge(self)
        bridge.event.connect(lambda ev, d: dlg.setValue(d.get("done", 0)) if ev == "p" else None)

        def job():
            return dedup.analyze_library(infos, cfg.dedup_level, cancel=cancel,
                                         progress=lambda d, n: bridge("p", {"done": d}))

        def done(idx):
            dlg.close()
            if cancel.is_set():
                return
            self.last_dup = idx
            for p in self.table.paths():
                r = idx.ratio.get(p)
                if r is not None:
                    self.table.set_dup(p, r, f"{r:.0%} 的画面在其他素材或本素材其他位置也出现过")
            rep = DupReportDialog(idx, self)
            rep.exec()
            if rep.remove_paths:
                self.table.remove_paths(rep.remove_paths)
        run_bg(job, on_done=done, on_error=lambda e: (dlg.close(), msg(self, f"查重失败：{e}", kind="warn")))

    def _browse_out(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出位置", self.out_root.text())
        if d:
            self.out_root.setText(d)

    def _detect_enc(self):
        self.enc_info.setText("正在检测（实际试编码）…")
        run_bg(lambda: encoders.detect(force=True), on_done=lambda r: self.enc_info.setText(encoders.describe(r)))

    def _set_style(self, st: dict):
        self.style = dict(st or {})
        sp = StylePreset.from_dict(self.style)
        parts = []
        if sp.filter_name:
            parts.append(f"滤镜「{sp.filter_name}」")
        if not sp.adjust_obj().is_neutral():
            parts.append("画面调节")
        if sp.overlays:
            kinds = [o.get("kind") for o in sp.overlays]
            parts.append(f"贴纸 {kinds.count('sticker')} 个 / 文字 {kinds.count('text')} 个 / 图片 {kinds.count('image')} 个")
        self.style_label.setText("当前包装：" + ("，".join(parts) if parts else "未设置"))

    def set_style_from_studio(self, st: dict):
        self._set_style(st)
        self.settings_tabs.setCurrentIndex(2)

    def _design(self):
        from .designer import PackageDesigner
        cfg = self.get_config()
        mats = [p for p in cfg.materials if self.table.infos.get(p) and self.table.infos[p].ok]
        dlg = PackageDesigner(cfg, self.style, mats, self)
        if dlg.exec() == QDialog.Accepted:
            self._set_style(dlg.current_style().to_dict())

    def _fill_style_menu(self, m):
        m.clear()
        names = list_templates("包装")
        for n in names:
            m.addAction(n).triggered.connect(
                lambda _=False, n=n: self._set_style(load_json(os.path.join(tpl_dir("包装"), n + ".json"), {})))
        if not names:
            m.addAction("（暂无模板，可在“设计包装”或“素材处理”中保存）").setEnabled(False)

    def _fill_preset_menu(self, m):
        m.clear()
        m.addAction("保存当前方案…").triggered.connect(self._save_preset)
        sub = m.addMenu("载入方案")
        d = paths.presets_dir()
        names = sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json"))
        for n in names:
            sub.addAction(n).triggered.connect(lambda _=False, n=n: self._load_preset(n))
        if not names:
            sub.addAction("（暂无）").setEnabled(False)
        m.addSeparator()
        m.addAction("恢复默认参数（保留素材）").triggered.connect(self._reset_defaults)
        m.addAction("打开方案文件夹").triggered.connect(lambda: utils.open_path(paths.presets_dir()))

    def _save_preset(self):
        name, ok = QInputDialog.getText(self, "保存方案", "方案名称：", text=self.name.text())
        if ok and name.strip():
            save_json(os.path.join(paths.presets_dir(), name.strip() + ".json"), self.get_config().to_dict())
            msg(self, f"已保存方案「{name.strip()}」（包含素材列表和全部参数）")

    def _load_preset(self, name):
        d = load_json(os.path.join(paths.presets_dir(), name + ".json"), {})
        if d:
            self.load_config(MixConfig.from_dict(d))

    def _reset_defaults(self):
        cur = self.get_config()
        cfg = MixConfig(materials=cur.materials, openings=cur.openings, endings=cur.endings, bgms=cur.bgms,
                        badges=cur.badges, cleanup_map=cur.cleanup_map, output_root=cur.output_root)
        self.load_config(cfg)

    # ================================================================ 运行
    def start(self, resume_dir=None):
        if self.runner is not None:
            return
        if not resume_dir:
            cfg = self.get_config()
            infos = self.table.infos
            ok = [p for p in cfg.materials if infos.get(p) and infos[p].ok]
            pending = [p for p in cfg.materials if p not in infos]
            if not cfg.materials:
                msg(self, "请先添加视频或图片素材（可直接拖拽到素材列表）。")
                return
            if not ok and not pending:
                msg(self, "没有可用的素材（全部无效）。", kind="warn")
                return
            if cfg.mode == "segment" and cfg.seg_min > cfg.limit_seconds():
                msg(self, "片段最短长度大于时长上限，请调整。", kind="warn")
                return
            try:
                os.makedirs(cfg.output_root, exist_ok=True)
            except OSError as e:
                msg(self, f"输出位置不可用：{e}", kind="warn")
                return
            if utils.free_disk_gb(cfg.output_root) < 1.0:
                if not confirm(self, "输出磁盘剩余空间不足 1GB，仍要继续吗？"):
                    return
            if cfg.mode == "whole" and len(ok) < 3 and cfg.count > 3:
                if not confirm(self, "整段拼接模式下素材较少，成片可能高度相似。\n建议改用“片段混剪”模式。仍要继续吗？"):
                    return
            self.save_last()
            runner = BatchRunner(cfg, callback=self.bridge)
        else:
            runner = BatchRunner(None, callback=self.bridge, resume_dir=resume_dir)
        self.runner = runner
        self.items.setRowCount(0)
        self.row_of = {}
        self.log.clear()
        self.pbar.setValue(0)
        self.run_title.setText("正在准备…")
        self.run_stat.setText("")
        self.rstack.setCurrentWidget(self.run_view)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.abort_btn.setEnabled(True)
        self.back_btn.setEnabled(False)
        self.open_btn.setEnabled(False)
        self.t_start = time.time()
        self.timer.start()
        self.runStateChanged.emit(True)
        self.thread = threading.Thread(target=runner.run, daemon=True)
        self.thread.start()

    def stop(self, immediate=False):
        if not self.runner:
            return
        if immediate and not confirm(self, "立即中止会结束正在处理的成片（已完成的不受影响），确定吗？"):
            return
        self.runner.stop(immediate)
        self.stop_btn.setEnabled(False)
        if immediate:
            self.abort_btn.setEnabled(False)
        self.run_title.setText("正在停止…" if not immediate else "正在中止…")

    def is_running(self):
        return self.runner is not None

    def _tick(self):
        if not self.runner:
            return
        el = time.time() - self.t_start
        pct = self.pbar.value() / 1000.0
        eta = (el / pct * (1 - pct)) if pct > 0.02 else 0
        r = self.runner
        self.run_stat.setText(f"已完成 {r.done}/{r.total or '?'}，失败 {r.failed} · 已用时 {utils.fmt_secs(el)}"
                              + (f" · 预计剩余 {utils.fmt_secs(eta)}" if eta else ""))

    def _on_event(self, ev, d):
        if ev == "log":
            lvl = d.get("level")
            m = d.get("msg", "")
            prefix = {"warn": "! ", "error": "× "}.get(lvl, "")
            self.log.appendPlainText(m if m.startswith(("!", "×", "√", "★", "■", "重试")) else prefix + m)
        elif ev == "stage":
            self.run_title.setText(d.get("text", ""))
        elif ev == "progress":
            self.pbar.setValue(int(d.get("pct", 0) * 1000))
            self.run_title.setText(f"正在生成… {d.get('done', 0)}/{d.get('total', 0)}")
            self._tick()
        elif ev == "item":
            idx = d["index"]
            r = self.row_of.get(idx)
            if r is None:
                r = self.items.rowCount()
                self.items.insertRow(r)
                self.row_of[idx] = r
                self.items.setItem(r, 0, QTableWidgetItem(str(idx)))
                self.items.setItem(r, 1, QTableWidgetItem(d.get("name", "") + ".mp4"))
                self.items.setItem(r, 2, QTableWidgetItem(""))
                self.items.setItem(r, 3, QTableWidgetItem(""))
                self.items.scrollToItem(self.items.item(r, 0))
            st = d.get("status")
            it = self.items.item(r, 2)
            if st == "running":
                it.setText(d.get("stage", "处理中"))
                it.setForeground(QColor(theme.C["accent"]))
                self.items.item(r, 3).setText(f"{d.get('pct', 0) * 100:.0f}%")
            elif st == "ok":
                it.setText("√ 完成")
                it.setForeground(QColor(theme.C["success"]))
                self.items.item(r, 3).setText("100%")
                self.items.item(r, 1).setData(Qt.UserRole, d.get("file"))
                self.items.item(r, 1).setToolTip("双击播放：" + (d.get("file") or ""))
            elif st == "failed":
                it.setText("× " + d.get("stage", "失败"))
                it.setToolTip(d.get("error", ""))
                it.setForeground(QColor(theme.C["danger"]))
            elif st == "skipped":
                it.setText("已停止")
                it.setForeground(QColor(theme.C["sub"]))
        elif ev == "finished":
            self.timer.stop()
            self._tick()
            self.runner_dir = d.get("dir")
            self.runner = None
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.abort_btn.setEnabled(False)
            self.back_btn.setEnabled(True)
            self.open_btn.setEnabled(bool(self.runner_dir))
            self.pbar.setValue(1000 if not d.get("stopped") else self.pbar.value())
            self.runStateChanged.emit(False)
            if d.get("error"):
                self.run_title.setText("任务未能开始")
                msg(self, f"任务未能开始：\n{d['error']}", kind="warn")
                return
            ok, failed = d.get("ok", 0), d.get("failed", 0)
            if d.get("stopped"):
                self.run_title.setText(f"已停止：完成 {ok} 条，失败 {failed} 条（可在“任务记录”中继续）")
            else:
                self.run_title.setText(f"全部完成：成功 {ok} 条" + (f"，失败 {failed} 条" if failed else "")
                                       + f"，用时 {utils.fmt_secs(d.get('elapsed', 0))}")
            self._notify_done(ok, failed)

    def _notify_done(self, ok, failed):
        try:
            from PySide6.QtWidgets import QApplication
            QApplication.alert(self.window(), 0)
            import winsound
            winsound.MessageBeep()
        except Exception:
            pass

    def _open_item(self, row, col):
        it = self.items.item(row, 1)
        f = it.data(Qt.UserRole) if it else None
        if f and os.path.isfile(f):
            from .page_studio import play_video
            play_video(f)

    def _open_out(self):
        d = getattr(self, "runner_dir", None)
        if d and os.path.isdir(d):
            sub = os.path.join(d, "成片")
            utils.open_path(sub if os.path.isdir(sub) else d)

    def _back(self):
        self.rstack.setCurrentWidget(self.settings_view)
