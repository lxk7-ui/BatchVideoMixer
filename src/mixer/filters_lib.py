"""滤镜库 / 画面调节 / 画面变体 / 转场定义（全部转换为 ffmpeg 滤镜链）。

滤镜参数为本工具原创调校，命名参考常见短视频剪辑软件的风格分类。
"""
import random

# 名称: (分类, ffmpeg 链)
FILTERS = {
    # 人像
    "自然": ("人像", "eq=brightness=0.02:contrast=1.03:saturation=1.06"),
    "白皙": ("人像", "eq=brightness=0.05:contrast=0.97:saturation=0.92:gamma=1.05,"
                     "colorbalance=rm=-0.02:bm=0.03:rh=0.02:bh=0.03"),
    "奶油": ("人像", "curves=master='0/0.06 0.5/0.55 1/0.96',colorbalance=rm=0.05:gm=0.02:bm=-0.04:rh=0.03,"
                     "eq=saturation=0.88"),
    "粉嫩": ("人像", "colorbalance=rm=0.06:gm=-0.03:bm=0.03:rh=0.04:bh=0.02,eq=brightness=0.03:saturation=1.05"),
    "清透": ("人像", "eq=brightness=0.04:contrast=1.06,colortemperature=temperature=7200:mix=0.5,unsharp=5:5:0.4"),
    # 风景
    "清新": ("风景", "colortemperature=temperature=7500:mix=0.6,eq=brightness=0.03:saturation=1.12:contrast=1.02,"
                     "curves=g='0/0 0.5/0.53 1/1'"),
    "鲜艳": ("风景", "eq=contrast=1.08:saturation=1.35,vibrance=intensity=0.2"),
    "晴空": ("风景", "selectivecolor=blues='0.15 0 -0.1 0':cyans='0.1 0 -0.05 0',eq=saturation=1.15:contrast=1.05"),
    "森林": ("风景", "colorbalance=gs=0.04:gm=0.05:bs=0.02,huesaturation=saturation=0.3:colors=g+y:strength=5,"
                     "eq=contrast=1.05"),
    "日落": ("风景", "colortemperature=temperature=4200:mix=0.7,colorbalance=rh=0.08:gh=0.02:bh=-0.06:rs=0.02,"
                     "eq=saturation=1.15:contrast=1.06"),
    # 复古 / 胶片
    "胶片": ("复古", "curves=preset=vintage,eq=saturation=0.9:contrast=1.04,noise=alls=6:allf=t,vignette=PI/6"),
    "复古": ("复古", "curves=r='0/0.1 1/0.95':g='0/0.05 1/0.95':b='0/0.08 1/0.85',eq=saturation=0.8:contrast=0.95,"
                     "vignette=PI/5"),
    "港风": ("复古", "colorbalance=rs=0.05:gs=-0.02:bs=0.06:rh=0.06:gh=0.02:bh=-0.04,"
                     "eq=contrast=1.1:saturation=1.1:gamma=0.95,curves=master='0/0.05 1/1',vignette=PI/6"),
    "老电影": ("复古", "colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131,curves=preset=vintage,"
                       "noise=alls=12:allf=t+u,vignette=PI/4"),
    "褪色": ("复古", "curves=master='0/0.12 0.5/0.5 1/0.9',eq=saturation=0.7"),
    "拍立得": ("复古", "curves=r='0/0.08 0.5/0.55 1/0.98':g='0/0.04 1/0.97':b='0/0.12 1/0.88',eq=saturation=0.9"),
    "富士": ("复古", "colorbalance=gs=0.03:bs=0.03:gm=0.02:rh=0.02,curves=master='0/0.03 0.25/0.22 0.75/0.8 1/1',"
                     "eq=saturation=1.05"),
    # 风格化
    "黑白": ("风格", "hue=s=0,eq=contrast=1.1"),
    "高对比黑白": ("风格", "hue=s=0,curves=preset=strong_contrast"),
    "冷调": ("风格", "colortemperature=temperature=8500,eq=saturation=0.95"),
    "暖调": ("风格", "colortemperature=temperature=4800,eq=saturation=1.05"),
    "青橙": ("风格", "colorbalance=rs=-0.08:gs=0.02:bs=0.1:rh=0.08:gh=0.02:bh=-0.08,eq=contrast=1.08:saturation=1.1"),
    "赛博朋克": ("风格", "colorbalance=rs=0.1:bs=0.15:rm=0.05:gm=-0.08:bm=0.12:rh=-0.02:bh=0.08,"
                         "eq=contrast=1.15:saturation=1.3"),
    "日系": ("风格", "curves=master='0/0.06 1/1',colortemperature=temperature=7000:mix=0.5,"
                     "eq=brightness=0.05:saturation=0.85:contrast=0.95,colorbalance=gm=0.02"),
    "莫兰迪": ("风格", "eq=saturation=0.6:contrast=0.92:brightness=0.02,colorbalance=rm=0.02:gm=0.02:bm=0.01,"
                       "curves=master='0/0.05 1/0.95'"),
    "高级灰": ("风格", "eq=saturation=0.55:contrast=1.08,colorbalance=bs=0.03:bm=0.02"),
    "电影感": ("风格", "curves=master='0/0.04 0.3/0.26 0.7/0.75 1/0.96',colorbalance=rs=-0.04:bs=0.06:rh=0.05:bh=-0.03,"
                       "eq=saturation=0.9:contrast=1.06,vignette=PI/6"),
    "黑金": ("风格", "huesaturation=saturation=-1:colors=g+c+b+m:strength=20,colortemperature=temperature=5000:mix=0.5,"
                     "eq=contrast=1.12:gamma=0.95"),
    # 美食
    "美味": ("美食", "colortemperature=temperature=5200:mix=0.6,eq=saturation=1.25:contrast=1.06:brightness=0.02"),
    "暖食": ("美食", "colorbalance=rm=0.05:gm=0.02:bm=-0.05:rh=0.04,eq=saturation=1.15,vibrance=intensity=0.15"),
    # 商品 / 带货
    "质感": ("商品", "unsharp=5:5:0.8,eq=contrast=1.08:saturation=1.05"),
    "明亮": ("商品", "eq=brightness=0.06:contrast=1.04:saturation=1.08,curves=master='0/0 0.5/0.56 1/1'"),
    "通透": ("商品", "eq=contrast=1.1:saturation=1.1,unsharp=3:3:0.5,colortemperature=temperature=6900:mix=0.4"),
}

FILTER_CATEGORIES = ["人像", "风景", "复古", "风格", "美食", "商品"]


def filter_names(category=None):
    return [n for n, (c, _) in FILTERS.items() if category in (None, "", "全部") or c == category]


def filter_chain(name):
    item = FILTERS.get(name)
    return item[1] if item else ""


def filter_graph(in_label, out_label, name, intensity=1.0, uid="f"):
    """返回滤镜图片段（含输入/输出标签）。intensity<1 时与原画按比例混合。"""
    chain = filter_chain(name)
    if not chain:
        return f"{in_label}null{out_label}"
    intensity = max(0.0, min(1.0, float(intensity)))
    if intensity >= 0.99:
        return f"{in_label}{chain}{out_label}"
    if intensity <= 0.01:
        return f"{in_label}null{out_label}"
    return (f"{in_label}split=2[{uid}o][{uid}s];[{uid}s]{chain}[{uid}f];"
            f"[{uid}o][{uid}f]blend=all_mode=normal:all_opacity={intensity:.3f}{out_label}")


def adjust_chain(adj) -> str:
    """画面调节 → 滤镜链（无需标签）。adj 为 models.Adjust。"""
    if adj is None:
        return ""
    parts = []
    b = float(adj.brightness or 0)
    c = float(adj.contrast or 0)
    s = float(adj.saturation or 0)
    if abs(b) > 1e-3 or abs(c) > 1e-3 or abs(s) > 1e-3:
        sat = 1.0 + (s * 0.8 if s > 0 else s)
        parts.append(f"eq=brightness={0.15 * b:.4f}:contrast={1 + 0.35 * c:.4f}:saturation={max(0.0, sat):.4f}")
    hl = float(adj.highlights or 0)
    sh = float(adj.shadows or 0)
    if abs(hl) > 1e-3 or abs(sh) > 1e-3:
        p1 = min(0.95, max(0.05, 0.25 + 0.1 * sh))
        p2 = min(0.95, max(0.05, 0.75 + 0.1 * hl))
        parts.append(f"curves=master='0/0 0.25/{p1:.3f} 0.75/{p2:.3f} 1/1'")
    t = float(adj.temperature or 0)
    if abs(t) > 1e-3:
        k = int(6500 - 2600 * t)   # t>0 偏暖（色温值越低画面越暖）
        parts.append(f"colortemperature=temperature={k}")
    tint = float(adj.tint or 0)
    if abs(tint) > 1e-3:
        parts.append(f"colorbalance=gm={-0.12 * tint:.4f}:rm={0.04 * tint:.4f}:bm={0.04 * tint:.4f}")
    sp = float(adj.sharpen or 0)
    if sp > 1e-3:
        parts.append(f"unsharp=5:5:{1.6 * sp:.3f}")
    vg = float(adj.vignette or 0)
    if vg > 1e-3:
        parts.append(f"vignette=angle={0.15 + 0.6 * vg:.3f}")
    gr = float(adj.grain or 0)
    if gr > 1e-3:
        parts.append(f"noise=alls={int(2 + 22 * gr)}:allf=t")
    return ",".join(parts)


# ----------------------------------------------------------------------------
# 画面变体（多版本/A-B 测试用，轻微调色/噪点/缩放；镜像默认关闭）
# ----------------------------------------------------------------------------
VARIANT_LEVELS = {
    0: "关闭",
    1: "轻微（几乎看不出）",
    2: "中等",
    3: "较强（轻微放大裁边）",
}


def variant_chain(level, rng: random.Random, W, H, mirror=False):
    if level <= 0 and not mirror:
        return ""
    parts = []
    if level == 1:
        c, s, b, n, z = rng.uniform(1.02, 1.06), rng.uniform(1.02, 1.06), rng.uniform(-0.02, 0.02), rng.randint(3, 7), 1.0
    elif level == 2:
        c, s, b, n, z = rng.uniform(1.05, 1.09), rng.uniform(1.05, 1.09), rng.uniform(-0.03, 0.03), rng.randint(6, 10), \
            rng.uniform(1.02, 1.04)
    elif level >= 3:
        c, s, b, n, z = rng.uniform(1.08, 1.12), rng.uniform(1.08, 1.12), rng.uniform(-0.04, 0.04), rng.randint(8, 12), \
            rng.uniform(1.03, 1.05)
    else:
        c = s = 1.0
        b = 0.0
        n = 0
        z = 1.0
    if level > 0:
        parts.append(f"eq=contrast={c:.3f}:saturation={s:.3f}:brightness={b:.3f}")
        if n:
            parts.append(f"noise=alls={n}:allf=t")
        if z > 1.001:
            zw = int(W * z) // 2 * 2
            zh = int(H * z) // 2 * 2
            parts.append(f"scale={zw}:{zh},crop={W}:{H}")
    if mirror:
        parts.append("hflip")
    return ",".join(parts)


# ----------------------------------------------------------------------------
# 转场
# ----------------------------------------------------------------------------
XFADE_TYPES = {
    "fade": "淡入淡出", "dissolve": "溶解", "fadeblack": "闪黑", "fadewhite": "闪白",
    "slideleft": "左滑", "slideright": "右滑", "slideup": "上滑", "slidedown": "下滑",
    "smoothleft": "平滑左移", "smoothright": "平滑右移", "smoothup": "平滑上移",
    "wipeleft": "左擦除", "wiperight": "右擦除", "circleopen": "圆形展开", "circleclose": "圆形收缩",
    "radial": "径向扫过", "zoomin": "放大", "pixelize": "像素化", "hblur": "模糊过渡",
    "diagtl": "对角擦除", "squeezeh": "横向挤压", "coverleft": "左覆盖", "revealright": "右揭开",
}
DEFAULT_XFADE = ["fade", "dissolve", "slideleft", "slideup", "smoothleft", "wipeleft", "circleopen", "zoomin",
                 "fadewhite", "hblur"]

CUT_EFFECTS = {
    "flash_white": "闪白", "flash_black": "闪黑", "zoom_punch": "推近", "shake": "抖动", "blur_in": "模糊渐清",
}
DEFAULT_CUT_EFFECTS = ["flash_white", "flash_black", "zoom_punch", "shake"]
