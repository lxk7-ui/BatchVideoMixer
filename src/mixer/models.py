"""数据模型：任务配置、去水印方案、包装（滤镜/贴纸/文字）模板、成片计划。

所有模型都支持 to_dict / from_dict，缺字段用默认值、多余字段忽略，保证旧方案文件可继续加载。
"""
import copy
import dataclasses
import json
import os
from dataclasses import dataclass, field
from typing import List, Optional


def _from_dict(cls, d):
    if d is None:
        return cls()
    if isinstance(d, cls):
        return d
    kwargs = {}
    hints = {f.name: f for f in dataclasses.fields(cls)}
    for k, v in (d or {}).items():
        if k not in hints:
            continue
        kwargs[k] = v
    obj = cls(**kwargs)
    if hasattr(obj, "_post_load"):
        obj._post_load()
    return obj


class Model:
    def to_dict(self):
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d):
        return _from_dict(cls, d)

    def clone(self):
        return copy.deepcopy(self)


# ----------------------------------------------------------------------------
# 去水印 / 去遮挡（素材级，坐标为相对源画面的 0~1 比例，适配不同分辨率）
# ----------------------------------------------------------------------------
CLEAN_METHODS = {
    "delogo": "智能填充（推荐，去文字水印）",
    "blur": "高斯模糊",
    "mosaic": "马赛克",
    "color": "纯色覆盖",
}


@dataclass
class Region(Model):
    x: float = 0.8
    y: float = 0.03
    w: float = 0.18
    h: float = 0.06
    method: str = "delogo"
    strength: int = 12          # 模糊半径 / 马赛克块大小
    color: str = "#000000"      # 纯色覆盖颜色
    t0: float = 0.0             # 生效开始（源素材时间，秒）
    t1: float = 0.0             # 生效结束（0=到结尾）
    name: str = ""


@dataclass
class CleanupProfile(Model):
    regions: List[dict] = field(default_factory=list)   # Region.to_dict()
    mask_png: str = ""          # 画笔涂抹蒙版（白色=去除），任意分辨率，渲染时缩放到素材尺寸
    crop_l: float = 0.0         # 裁边（比例）
    crop_t: float = 0.0
    crop_r: float = 0.0
    crop_b: float = 0.0
    name: str = ""

    def region_objs(self):
        return [Region.from_dict(r) for r in self.regions]

    def is_empty(self):
        return not self.regions and not self.mask_png and not any(
            (self.crop_l, self.crop_t, self.crop_r, self.crop_b))


# ----------------------------------------------------------------------------
# 包装：滤镜、调节、贴纸/文字/角标（成片级或素材级）
# ----------------------------------------------------------------------------
@dataclass
class Adjust(Model):
    brightness: float = 0.0     # -1~1（实际映射 ±0.25）
    contrast: float = 0.0       # -1~1
    saturation: float = 0.0     # -1~1
    temperature: float = 0.0    # -1~1 冷暖
    tint: float = 0.0           # -1~1 色调（绿-品红）
    sharpen: float = 0.0        # 0~1
    vignette: float = 0.0       # 0~1 暗角
    grain: float = 0.0          # 0~1 颗粒
    highlights: float = 0.0     # -1~1
    shadows: float = 0.0        # -1~1

    def is_neutral(self):
        return all(abs(getattr(self, f.name)) < 1e-3 for f in dataclasses.fields(self))


ANIM_IN = {"none": "无", "fade": "渐显", "pop": "弹出", "zoom": "缩小入场", "slide_left": "从左滑入",
           "slide_right": "从右滑入", "slide_up": "从下滑入", "slide_down": "从上滑入"}
ANIM_LOOP = {"none": "无", "pulse": "呼吸缩放", "bounce": "上下弹跳", "shake": "左右抖动", "swing": "左右摇摆",
             "spin": "旋转", "float": "轻微漂浮", "blink": "闪烁"}
ANIM_OUT = {"none": "无", "fade": "渐隐"}


@dataclass
class OverlayItem(Model):
    kind: str = "sticker"       # sticker / text / image
    src: str = ""               # 贴纸/图片路径
    text: str = ""              # 文字内容（支持 {序号} {日期} {素材名} {任务名} 变量）
    style: dict = field(default_factory=dict)   # 文字样式（见 textstyle.TextStyle）
    cx: float = 0.5             # 中心位置（相对画面 0~1）
    cy: float = 0.2
    w: float = 0.3              # 宽度占画面宽比例
    rotation: float = 0.0       # 静态旋转角（度）
    opacity: float = 1.0
    t0: float = 0.0             # 出现时间（秒，成片时间）
    t1: float = 0.0             # 消失时间（0 = 到结尾）；负数表示“结尾前 N 秒”
    anim_in: str = "none"
    anim_loop: str = "none"
    anim_out: str = "none"
    random_pos: bool = False    # 每条成片随机位置（安全区内）
    corner_random: bool = False  # 角标：四角随机
    remove_bg: str = ""         # 图片去底：""/white/black
    name: str = ""


@dataclass
class StylePreset(Model):
    """包装模板：滤镜 + 调节 + 叠加元素。"""
    filter_name: str = ""
    filter_intensity: float = 1.0
    adjust: dict = field(default_factory=dict)
    overlays: List[dict] = field(default_factory=list)
    name: str = ""

    def adjust_obj(self):
        return Adjust.from_dict(self.adjust)

    def overlay_objs(self):
        return [OverlayItem.from_dict(o) for o in self.overlays]

    def is_empty(self):
        return not self.filter_name and self.adjust_obj().is_neutral() and not self.overlays


# ----------------------------------------------------------------------------
# 混剪任务配置
# ----------------------------------------------------------------------------
CANVAS_PRESETS = {
    "1080x1920": "竖屏 9:16 · 1080×1920（抖音/快手/视频号）",
    "720x1280": "竖屏 9:16 · 720×1280（体积小）",
    "1080x1440": "竖屏 3:4 · 1080×1440（小红书）",
    "1080x1080": "方形 1:1 · 1080×1080",
    "1920x1080": "横屏 16:9 · 1920×1080（B站/西瓜）",
    "1280x720": "横屏 16:9 · 1280×720",
    "auto": "跟随第一个素材",
}
FIT_MODES = {
    "smart": "智能（比例接近则裁剪铺满，否则模糊背景）",
    "blur": "模糊背景填充",
    "pad": "黑边填充（不裁画面）",
    "crop": "裁剪铺满（不留边）",
}
TRANSITION_MODES = {
    "none": "无转场（硬切）",
    "effect": "切换特效（闪白/闪黑/推近/抖动，速度快）",
    "xfade": "叠化转场（溶解/滑动/擦除等，更顺滑）",
    "mixed": "混合随机",
}


@dataclass
class MixConfig(Model):
    name: str = "混剪任务"
    materials: List[str] = field(default_factory=list)
    openings: List[str] = field(default_factory=list)
    endings: List[str] = field(default_factory=list)
    bgms: List[str] = field(default_factory=list)
    badges: List[str] = field(default_factory=list)
    cleanup_map: dict = field(default_factory=dict)     # 素材路径 -> CleanupProfile dict

    count: int = 10
    mode: str = "segment"           # segment 片段混剪 / whole 整段拼接
    limit_enabled: bool = True
    limit_minutes: float = 1.0      # 成片时长上限（分钟）
    limit_float: int = 20           # 随机浮动百分比：成片时长在 [上限×(1-浮动), 上限] 之间
    seg_min: float = 2.0
    seg_max: float = 5.0
    clips_min: int = 1
    clips_max: int = 3
    trim_overflow: bool = True      # 整段模式：超出上限的素材自动截断
    use_scenes: bool = True         # 按镜头切点取片段
    allow_repeat: bool = True       # 素材不足时允许重复使用片段
    avoid_similar: bool = True      # 成片相似度控制
    dedup_enabled: bool = True      # 素材查重：识别素材间/素材内重复画面，同一条成片不重复使用
    dedup_postcheck: bool = True    # 成片复查：生成后抽帧检查，发现重复片段自动剪掉
    dedup_level: str = "standard"   # loose / standard / strict
    image_duration: float = 3.0     # 图片素材每张显示秒数
    opening_random: bool = True     # 多个片头时随机选
    trim_head: float = 0.0          # 掐头：每个素材跳过开头 N 秒
    trim_tail: float = 0.0          # 去尾：每个素材跳过结尾 N 秒（如平台片尾动画）

    speed_enabled: bool = False
    speed_min: float = 0.9
    speed_max: float = 1.15

    transition_mode: str = "none"
    transition_types: List[str] = field(default_factory=list)
    transition_dur: float = 0.5
    transition_prob: int = 60       # 每个切点出现转场的概率 %

    canvas: str = "1080x1920"
    fit: str = "smart"
    fps: int = 30
    quality: str = "standard"
    encoder: str = "auto"

    keep_audio: str = "keep"        # keep / lower / mute
    orig_volume: float = 1.0
    bgm_volume: float = 0.35
    bgm_random_start: bool = True
    loudnorm: bool = True
    audio_fade: bool = True

    style: dict = field(default_factory=dict)            # StylePreset（成片包装）
    filter_mode: str = "none"       # none / fixed / random
    filter_choices: List[str] = field(default_factory=list)
    filter_intensity: float = 0.8
    titles: List[str] = field(default_factory=list)      # 标题文案（多条随机）
    title_style: dict = field(default_factory=dict)
    title_pos: str = "top"          # top / center / bottom
    title_duration: float = 0.0     # 0=全程
    badge_enabled: bool = True
    badge_anim: str = "fade"

    variant_level: int = 0          # 0 关 / 1 轻微 / 2 中等 / 3 较强
    mirror: bool = False

    cover: bool = True
    cover_title: bool = True
    metadata: bool = True
    name_template: str = "{任务名}_{序号}"
    output_root: str = ""
    concurrency: int = 0            # 0 = 自动
    seed: int = 0                   # 0 = 每次随机
    keep_temp: bool = False

    def style_obj(self):
        return StylePreset.from_dict(self.style)

    def cleanup_for(self, path) -> Optional[CleanupProfile]:
        d = self.cleanup_map.get(path) or self.cleanup_map.get(os.path.abspath(path))
        if not d:
            return None
        c = CleanupProfile.from_dict(d)
        return None if c.is_empty() else c

    def canvas_size(self, first_info=None):
        if self.canvas == "auto" and first_info is not None and first_info.width:
            w, h = first_info.width, first_info.height
            # 限制最长边 1920，取偶数
            s = min(1.0, 1920.0 / max(w, h))
            w, h = int(w * s) // 2 * 2, int(h * s) // 2 * 2
            return max(64, w), max(64, h)
        if "x" in self.canvas:
            a, b = self.canvas.split("x")
            return int(a) // 2 * 2, int(b) // 2 * 2
        return 1080, 1920

    def limit_seconds(self):
        return max(0.0, float(self.limit_minutes) * 60.0) if self.limit_enabled else 0.0


def save_json(path, data):
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default
