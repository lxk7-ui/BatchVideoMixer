"""花字 / 标题文字渲染（Pillow）：描边、双描边、渐变、阴影、底框。

文字统一先渲染成透明 PNG，再作为叠加层合成，保证界面预览与成片完全一致，
也避免了 ffmpeg drawtext 的中文转义和字体路径问题。
"""
import threading
from dataclasses import dataclass, asdict, field

from . import fonts

_render_lock = threading.Lock()


@dataclass
class TextStyle:
    font: str = fonts.DEFAULT_FONT
    size: int = 72              # 以 1080 宽画面为基准的字号（像素）
    color: str = "#FFFFFF"
    gradient: bool = False
    color2: str = "#FFD400"     # 渐变下半部颜色
    stroke_width: int = 6
    stroke_color: str = "#000000"
    stroke2_width: int = 0      # 外层第二描边
    stroke2_color: str = "#FFFFFF"
    shadow: bool = False
    shadow_color: str = "#000000"
    shadow_dx: int = 4
    shadow_dy: int = 6
    shadow_blur: int = 6
    shadow_opacity: float = 0.6
    bg: bool = False
    bg_color: str = "#FF3B30"
    bg_opacity: float = 1.0
    bg_radius: int = 24
    bg_pad_x: int = 28
    bg_pad_y: int = 14
    align: str = "center"
    line_spacing: float = 1.15
    max_width: float = 0.9      # 超过画面宽度比例自动换行

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        d = d or {}
        names = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in d.items() if k in names})


PRESETS = {
    "白字黑边": dict(color="#FFFFFF", stroke_width=6, stroke_color="#000000"),
    "黄字黑边": dict(color="#FFE600", stroke_width=6, stroke_color="#000000"),
    "红字白边": dict(color="#FF2D2D", stroke_width=7, stroke_color="#FFFFFF"),
    "综艺花字": dict(color="#FFF200", gradient=True, color2="#FF8A00", stroke_width=6, stroke_color="#C00000",
                 stroke2_width=6, stroke2_color="#FFFFFF", font="站酷黄油体"),
    "金色渐变": dict(color="#FFF6C8", gradient=True, color2="#E0A000", stroke_width=5, stroke_color="#5A2D00",
                 shadow=True),
    "粉紫渐变": dict(color="#FF7AD9", gradient=True, color2="#8A5CFF", stroke_width=6, stroke_color="#FFFFFF"),
    "霓虹蓝": dict(color="#E6FBFF", stroke_width=4, stroke_color="#00C8FF", stroke2_width=6, stroke2_color="#003A80",
                shadow=True, shadow_color="#00C8FF", shadow_dx=0, shadow_dy=0, shadow_blur=14, shadow_opacity=0.9),
    "红底白字": dict(color="#FFFFFF", stroke_width=0, bg=True, bg_color="#FF2D2D"),
    "黑底黄字": dict(color="#FFE600", stroke_width=0, bg=True, bg_color="#111111", bg_opacity=0.85),
    "白底黑字": dict(color="#111111", stroke_width=0, bg=True, bg_color="#FFFFFF", bg_opacity=0.95),
    "半透明底": dict(color="#FFFFFF", stroke_width=0, bg=True, bg_color="#000000", bg_opacity=0.5),
    "立体阴影": dict(color="#FFFFFF", stroke_width=5, stroke_color="#1A1A1A", shadow=True, shadow_dx=7, shadow_dy=9,
                 shadow_blur=2, shadow_opacity=0.9, shadow_color="#FF3B30"),
    "可爱卡通": dict(color="#FFFFFF", stroke_width=7, stroke_color="#FF5FA2", stroke2_width=5, stroke2_color="#FFFFFF",
                 font="站酷快乐体"),
    "毛笔大字": dict(color="#FFFFFF", stroke_width=4, stroke_color="#000000", font="马善政毛笔", size=96, shadow=True),
    "得意黑标题": dict(color="#FFFFFF", stroke_width=0, font="得意黑", size=88, shadow=True, shadow_blur=10,
                  shadow_dx=0, shadow_dy=4, shadow_opacity=0.7),
    "电商促销": dict(color="#FFFFFF", gradient=True, color2="#FFE08A", stroke_width=6, stroke_color="#D7000F",
                 stroke2_width=4, stroke2_color="#FFD000", font="思源黑体 特粗"),
}


def preset_style(name, **override):
    base = TextStyle().to_dict()
    base.update(PRESETS.get(name, {}))
    base.update(override)
    return TextStyle.from_dict(base)


def _rgba(hexstr, alpha=1.0):
    h = (hexstr or "#FFFFFF").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except Exception:
        r, g, b = 255, 255, 255
    return (r, g, b, int(max(0, min(1, alpha)) * 255))


def _wrap(text, font, max_px):
    lines = []
    for para in text.split("\n"):
        if not para:
            lines.append("")
            continue
        cur = ""
        for ch in para:
            test = cur + ch
            if font.getlength(test) > max_px and cur:
                lines.append(cur)
                cur = ch
            else:
                cur = test
        lines.append(cur)
    return lines


def render_text(text, style: TextStyle, canvas_w=1080, scale=1.0):
    """渲染文字为 RGBA 图片。canvas_w 为目标画面宽度（字号按 1080 基准等比缩放）。"""
    from PIL import Image, ImageDraw, ImageFilter

    if isinstance(style, dict):
        style = TextStyle.from_dict(style)
    text = (text or "").strip("\n") or " "
    k = canvas_w / 1080.0 * scale
    size = max(8, int(style.size * k))
    sw1 = max(0, int(round(style.stroke_width * k)))
    sw2 = max(0, int(round(style.stroke2_width * k)))
    swt = sw1 + sw2
    with _render_lock:
        font = fonts.pil_font(style.font, size)
        max_px = max(50, int(canvas_w * max(0.2, style.max_width or 0.9)) - 2 * swt)
        lines = _wrap(text, font, max_px)
        asc, desc = font.getmetrics()
        line_h = int((asc + desc) * style.line_spacing)
        widths = [int(font.getlength(l)) for l in lines]
        tw = max(widths) if widths else 10
        th = line_h * (len(lines) - 1) + asc + desc
        sh_pad = 0
        if style.shadow:
            sh_pad = int((abs(style.shadow_dx) + abs(style.shadow_dy) + style.shadow_blur * 2) * k) + 2
        pad = swt + sh_pad + 4
        bgx = int(style.bg_pad_x * k) if style.bg else 0
        bgy = int(style.bg_pad_y * k) if style.bg else 0
        W = tw + 2 * (pad + bgx)
        H = th + 2 * (pad + bgy)
        ox, oy = pad + bgx, pad + bgy

        def positions():
            for i, l in enumerate(lines):
                if style.align == "left":
                    x = ox
                elif style.align == "right":
                    x = ox + tw - widths[i]
                else:
                    x = ox + (tw - widths[i]) // 2
                yield l, x, oy + i * line_h

        canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        # 底框
        if style.bg:
            box = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            d = ImageDraw.Draw(box)
            r = int(style.bg_radius * k)
            d.rounded_rectangle((pad, pad, W - pad - 1, H - pad - 1), radius=r,
                                fill=_rgba(style.bg_color, style.bg_opacity))
            canvas = Image.alpha_composite(canvas, box)
        # 阴影
        if style.shadow:
            sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            d = ImageDraw.Draw(sh)
            dx, dy = int(style.shadow_dx * k), int(style.shadow_dy * k)
            col = _rgba(style.shadow_color, style.shadow_opacity)
            for l, x, y in positions():
                d.text((x + dx, y + dy), l, font=font, fill=col, stroke_width=swt, stroke_fill=col)
            if style.shadow_blur > 0:
                sh = sh.filter(ImageFilter.GaussianBlur(max(1, style.shadow_blur * k)))
            canvas = Image.alpha_composite(canvas, sh)
        # 外描边
        if sw2 > 0:
            lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            d = ImageDraw.Draw(lay)
            col = _rgba(style.stroke2_color)
            for l, x, y in positions():
                d.text((x, y), l, font=font, fill=col, stroke_width=swt, stroke_fill=col)
            canvas = Image.alpha_composite(canvas, lay)
        # 内描边
        if sw1 > 0:
            lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            d = ImageDraw.Draw(lay)
            col = _rgba(style.stroke_color)
            for l, x, y in positions():
                d.text((x, y), l, font=font, fill=col, stroke_width=sw1, stroke_fill=col)
            canvas = Image.alpha_composite(canvas, lay)
        # 填充
        if style.gradient:
            mask = Image.new("L", (W, H), 0)
            d = ImageDraw.Draw(mask)
            for l, x, y in positions():
                d.text((x, y), l, font=font, fill=255)
            c1, c2 = _rgba(style.color), _rgba(style.color2)
            grad = Image.new("RGBA", (1, H))
            top, bot = oy, oy + th
            for yy in range(H):
                t = 0.0 if yy <= top else (1.0 if yy >= bot else (yy - top) / max(1, bot - top))
                grad.putpixel((0, yy), tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(4)))
            grad = grad.resize((W, H))
            fill = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            fill.paste(grad, (0, 0), mask)
            canvas = Image.alpha_composite(canvas, fill)
        else:
            lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            d = ImageDraw.Draw(lay)
            for l, x, y in positions():
                d.text((x, y), l, font=font, fill=_rgba(style.color))
            canvas = Image.alpha_composite(canvas, lay)
    bbox = canvas.getbbox()
    if bbox:
        x0, y0, x1, y1 = bbox
        m = 2
        canvas = canvas.crop((max(0, x0 - m), max(0, y0 - m), min(W, x1 + m), min(H, y1 + m)))
    return canvas


def substitute(text, ctx: dict):
    """替换模板变量：{序号} {日期} {任务名} {素材名} {时长} 等。"""
    out = text or ""
    for k, v in (ctx or {}).items():
        out = out.replace("{" + k + "}", str(v))
    return out
