"""字体：内置开源字体（SIL OFL）+ 系统中文字体。"""
import os

from . import paths

# 显示名: (文件名, 可变字重 or None)
BUNDLED = [
    ("思源黑体 粗", "NotoSansSC-VF.ttf", 800),
    ("思源黑体 常规", "NotoSansSC-VF.ttf", 500),
    ("思源黑体 特粗", "NotoSansSC-VF.ttf", 900),
    ("得意黑", "SmileySans-Oblique.ttf", None),
    ("站酷快乐体", "ZCOOLKuaiLe-Regular.ttf", None),
    ("站酷黄油体", "ZCOOLQingKeHuangYou-Regular.ttf", None),
    ("马善政毛笔", "MaShanZheng-Regular.ttf", None),
]

SYSTEM = [
    ("微软雅黑 粗", "msyhbd.ttc"),
    ("微软雅黑", "msyh.ttc"),
    ("黑体", "simhei.ttf"),
    ("楷体", "simkai.ttf"),
    ("宋体", "simsun.ttc"),
    ("仿宋", "simfang.ttf"),
    ("华文行楷", "STXINGKA.TTF"),
    ("华文琥珀", "STHUPO.TTF"),
    ("方正舒体", "FZSTK.TTF"),
    ("方正姚体", "FZYTK.TTF"),
    ("幼圆", "SIMYOU.TTF"),
    ("隶书", "SIMLI.TTF"),
]

DEFAULT_FONT = "思源黑体 粗"

_list_cache = None


def _win_fonts_dir():
    return os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")


def available_fonts():
    """[(显示名, 路径, 字重)]，只返回实际存在的。"""
    global _list_cache
    if _list_cache is not None:
        return _list_cache
    out = []
    for name, fn, wt in BUNDLED:
        p = paths.assets_dir("fonts", fn)
        if os.path.isfile(p):
            out.append((name, p, wt))
    wf = _win_fonts_dir()
    local = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts")
    for name, fn in SYSTEM:
        for d in (wf, local):
            p = os.path.join(d, fn)
            if os.path.isfile(p):
                out.append((name, p, None))
                break
    _list_cache = out
    return out


def font_names():
    return [n for n, _, _ in available_fonts()]


def resolve(name):
    """显示名 → (路径, 字重)。找不到则回退默认 / 任意中文字体。"""
    fonts = available_fonts()
    for n, p, w in fonts:
        if n == name:
            return p, w
    for n, p, w in fonts:
        if n == DEFAULT_FONT:
            return p, w
    if fonts:
        return fonts[0][1], fonts[0][2]
    return None, None


_pil_cache = {}


def pil_font(name, size):
    from PIL import ImageFont
    size = max(6, int(size))
    key = (name, size)
    if key in _pil_cache:
        return _pil_cache[key]
    path, weight = resolve(name)
    try:
        if path:
            f = ImageFont.truetype(path, size)
            if weight:
                try:
                    f.set_variation_by_axes([weight])
                except Exception:
                    pass
        else:
            f = ImageFont.load_default(size=size)
    except Exception:
        f = ImageFont.load_default(size=size)
    _pil_cache[key] = f
    return f
