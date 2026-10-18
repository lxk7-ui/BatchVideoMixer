"""构建期脚本：生成内置原创贴纸（电商标签 / 互动引导 / 箭头指示 / 装饰元素 / 对话气泡）。

全部为程序绘制的原创图形，可自由商用。用法：python tools/gen_stickers.py <assets/stickers 目录>
"""
import json
import math
import os
import random
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
FONT_DIR = os.path.join(SRC, "assets", "fonts")
SS = 3  # 超采样倍数（抗锯齿）


def font(name, size, weight=None):
    f = ImageFont.truetype(os.path.join(FONT_DIR, name), size)
    if weight:
        f.set_variation_by_axes([weight])
    return f


def F_BLACK(size):
    return font("NotoSansSC-VF.ttf", size, 900)


def F_BOLD(size):
    return font("NotoSansSC-VF.ttf", size, 750)


def F_FUN(size):
    return font("ZCOOLKuaiLe-Regular.ttf", size)


def F_BUTTER(size):
    return font("ZCOOLQingKeHuangYou-Regular.ttf", size)


def F_SMILEY(size):
    return font("SmileySans-Oblique.ttf", size)


def hexc(h, a=255):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a)


def vgrad(size, c1, c2):
    w, h = size
    g = Image.new("RGBA", (1, h))
    for y in range(h):
        t = y / max(1, h - 1)
        g.putpixel((0, y), tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(4)))
    return g.resize((w, h))


def fill_shape(canvas, mask, c1, c2=None):
    if c2 is None:
        layer = Image.new("RGBA", canvas.size, c1)
    else:
        layer = vgrad(canvas.size, c1, c2)
    canvas.paste(layer, (0, 0), mask)


def finish(img, out, shadow=True):
    """加柔和投影，缩小到目标尺寸（抗锯齿），裁边保存。"""
    if shadow:
        a = img.split()[3]
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        sh.putalpha(a.point(lambda v: int(v * 0.35)))
        sh = sh.filter(ImageFilter.GaussianBlur(6 * SS))
        base = Image.new("RGBA", img.size, (0, 0, 0, 0))
        base.alpha_composite(sh, (0, 4 * SS))
        base.alpha_composite(img)
        img = base
    w, h = img.size
    img = img.resize((w // SS, h // SS), Image.LANCZOS)
    bb = img.getbbox()
    if bb:
        img = img.crop((max(0, bb[0] - 4), max(0, bb[1] - 4), min(img.width, bb[2] + 4), min(img.height, bb[3] + 4)))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    img.save(out, optimize=True)


def text_center(d, box, text, fnt, fill, stroke=0, stroke_fill=None):
    x0, y0, x1, y1 = box
    d.text(((x0 + x1) / 2, (y0 + y1) / 2), text, font=fnt, fill=fill, anchor="mm",
           stroke_width=stroke, stroke_fill=stroke_fill)


def fit_font(maker, text, max_w, start):
    s = start
    while s > 10:
        f = maker(s)
        if f.getlength(text) <= max_w:
            return f
        s -= 4
    return maker(s)


# ---------------------------------------------------------------- 形状
def pill(text, c1, c2, out, txt="#FFFFFF", border="#FFFFFF", w=640, h=180, fmaker=F_BLACK):
    W, H = w * SS, h * SS
    img = Image.new("RGBA", (W + 40 * SS, H + 40 * SS), (0, 0, 0, 0))
    m = Image.new("L", img.size, 0)
    ImageDraw.Draw(m).rounded_rectangle((20 * SS, 20 * SS, 20 * SS + W, 20 * SS + H), radius=H // 2, fill=255)
    bm = Image.new("L", img.size, 0)
    bw = 8 * SS
    ImageDraw.Draw(bm).rounded_rectangle((20 * SS - bw, 20 * SS - bw, 20 * SS + W + bw, 20 * SS + H + bw),
                                         radius=H // 2 + bw, fill=255)
    fill_shape(img, bm, hexc(border))
    fill_shape(img, m, hexc(c1), hexc(c2))
    # 高光
    hl = Image.new("L", img.size, 0)
    ImageDraw.Draw(hl).rounded_rectangle((40 * SS, 28 * SS, 20 * SS + W - 20 * SS, 20 * SS + H * 0.45),
                                         radius=H // 4, fill=70)
    img.paste(Image.new("RGBA", img.size, (255, 255, 255, 255)), (0, 0), hl)
    d = ImageDraw.Draw(img)
    f = fit_font(fmaker, text, W * 0.82, int(H * 0.56))
    text_center(d, (20 * SS, 20 * SS, 20 * SS + W, 20 * SS + H), text, f, hexc(txt), stroke=3 * SS,
                stroke_fill=hexc(c2) if txt.upper() == "#FFFFFF" else None)
    finish(img, out)


def star_burst(text, c1, c2, out, points=16, R=300, inner=0.78, txt="#FFFFFF", fmaker=F_BUTTER, rot=0):
    S = (R * 2 + 60) * SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    cx = cy = S / 2
    pts = []
    for i in range(points * 2):
        ang = math.pi * i / points + math.radians(rot)
        r = (R if i % 2 == 0 else R * inner) * SS
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    bm = Image.new("L", img.size, 0)
    ImageDraw.Draw(bm).polygon(pts, fill=255)
    bm = bm.filter(ImageFilter.MaxFilter(1 + 2 * (6 * SS // 2)))
    fill_shape(img, bm, hexc("#FFFFFF"))
    m = Image.new("L", img.size, 0)
    ImageDraw.Draw(m).polygon(pts, fill=255)
    fill_shape(img, m, hexc(c1), hexc(c2))
    d = ImageDraw.Draw(img)
    lines = text.split("\n")
    f = fit_font(fmaker, max(lines, key=len), R * 1.15 * SS, int(R * (0.62 if len(lines) == 1 else 0.42) * SS))
    if len(lines) == 1:
        text_center(d, (0, 0, S, S), text, f, hexc(txt), stroke=4 * SS, stroke_fill=hexc(c2))
    else:
        lh = f.size * 1.05
        for i, l in enumerate(lines):
            y = cy + (i - (len(lines) - 1) / 2) * lh
            d.text((cx, y), l, font=f, fill=hexc(txt), anchor="mm", stroke_width=4 * SS, stroke_fill=hexc(c2))
    finish(img, out)


def circle_badge(text, c1, c2, out, R=240, txt="#FFFFFF", sub="", fmaker=F_BLACK):
    S = (R * 2 + 60) * SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    c = S / 2
    d = ImageDraw.Draw(img)
    r = R * SS
    d.ellipse((c - r - 8 * SS, c - r - 8 * SS, c + r + 8 * SS, c + r + 8 * SS), fill=hexc("#FFFFFF"))
    m = Image.new("L", img.size, 0)
    ImageDraw.Draw(m).ellipse((c - r, c - r, c + r, c + r), fill=255)
    fill_shape(img, m, hexc(c1), hexc(c2))
    d = ImageDraw.Draw(img)
    rr = r * 0.84
    d.ellipse((c - rr, c - rr, c + rr, c + rr), outline=(255, 255, 255, 200), width=5 * SS)
    f = fit_font(fmaker, text, rr * 1.5, int(r * (0.62 if len(text) <= 2 else 0.44)))
    if sub:
        text_center(d, (0, 0, S, S - r * 0.3), text, f, hexc(txt))
        fs = F_BOLD(int(r * 0.2))
        text_center(d, (0, c + r * 0.25, S, c + r * 0.55), sub, fs, hexc(txt))
    else:
        text_center(d, (0, 0, S, S), text, f, hexc(txt))
    finish(img, out)


def ribbon(text, c1, c2, out, w=760, h=170, txt="#FFFFFF"):
    W, H = w * SS, h * SS
    pad = 30 * SS
    tail = 90 * SS
    img = Image.new("RGBA", (W + 2 * tail + 2 * pad, H + 2 * pad + 30 * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x0, y0 = pad + tail, pad
    x1, y1 = x0 + W, y0 + H
    dark = tuple(int(v * 0.6) for v in hexc(c2)[:3]) + (255,)
    # 尾巴
    off = 26 * SS
    d.polygon([(x0 - tail, y0 + off), (x0 + 10 * SS, y0 + off), (x0 + 10 * SS, y1 + off), (x0 - tail, y1 + off),
               (x0 - tail + 45 * SS, (y0 + y1) / 2 + off)], fill=dark)
    d.polygon([(x1 + tail, y0 + off), (x1 - 10 * SS, y0 + off), (x1 - 10 * SS, y1 + off), (x1 + tail, y1 + off),
               (x1 + tail - 45 * SS, (y0 + y1) / 2 + off)], fill=dark)
    d.polygon([(x0, y1), (x0 + 10 * SS, y1 + off), (x0 + 10 * SS, y1)], fill=(0, 0, 0, 160))
    d.polygon([(x1, y1), (x1 - 10 * SS, y1 + off), (x1 - 10 * SS, y1)], fill=(0, 0, 0, 160))
    m = Image.new("L", img.size, 0)
    ImageDraw.Draw(m).rectangle((x0, y0, x1, y1), fill=255)
    fill_shape(img, m, hexc(c1), hexc(c2))
    d = ImageDraw.Draw(img)
    d.line((x0, y0 + 12 * SS, x1, y0 + 12 * SS), fill=(255, 255, 255, 120), width=4 * SS)
    d.line((x0, y1 - 12 * SS, x1, y1 - 12 * SS), fill=(255, 255, 255, 120), width=4 * SS)
    f = fit_font(F_BLACK, text, W * 0.86, int(H * 0.58))
    text_center(d, (x0, y0, x1, y1), text, f, hexc(txt))
    finish(img, out)


def price_tag(text, c1, c2, out, w=560, h=220, txt="#FFFFFF", sub=""):
    W, H = w * SS, h * SS
    pad = 30 * SS
    notch = 70 * SS
    img = Image.new("RGBA", (W + 2 * pad, H + 2 * pad), (0, 0, 0, 0))
    x0, y0 = pad, pad
    x1, y1 = x0 + W, y0 + H
    pts = [(x0 + notch, y0), (x1, y0), (x1, y1), (x0 + notch, y1), (x0, (y0 + y1) / 2)]
    bm = Image.new("L", img.size, 0)
    ImageDraw.Draw(bm).polygon(pts, fill=255)
    bm = bm.filter(ImageFilter.MaxFilter(1 + 2 * (5 * SS // 2)))
    fill_shape(img, bm, hexc("#FFFFFF"))
    m = Image.new("L", img.size, 0)
    ImageDraw.Draw(m).polygon(pts, fill=255)
    hr = 16 * SS
    hx, hy = x0 + notch * 0.75, (y0 + y1) / 2
    ImageDraw.Draw(m).ellipse((hx - hr, hy - hr, hx + hr, hy + hr), fill=0)
    fill_shape(img, m, hexc(c1), hexc(c2))
    d = ImageDraw.Draw(img)
    area = (x0 + notch * 1.25, y0, x1 - 20 * SS, y1)
    if sub:
        f = fit_font(F_BLACK, text, (area[2] - area[0]) * 0.95, int(H * 0.42))
        text_center(d, (area[0], y0, area[2], y0 + H * 0.62), text, f, hexc(txt))
        fs = fit_font(F_BOLD, sub, (area[2] - area[0]) * 0.95, int(H * 0.22))
        text_center(d, (area[0], y0 + H * 0.58, area[2], y1 - H * 0.08), sub, fs, hexc(txt))
    else:
        f = fit_font(F_BLACK, text, (area[2] - area[0]) * 0.95, int(H * 0.5))
        text_center(d, area, text, f, hexc(txt))
    finish(img, out)


def bubble(text, c1, out, w=620, h=260, txt="#222222", border="#222222", tail="left", fmaker=F_FUN, c2=None):
    W, H = w * SS, h * SS
    pad = 40 * SS
    th = 80 * SS
    img = Image.new("RGBA", (W + 2 * pad, H + 2 * pad + th), (0, 0, 0, 0))
    x0, y0 = pad, pad
    x1, y1 = x0 + W, y0 + H
    tx = x0 + W * (0.25 if tail == "left" else 0.75)
    tip = (tx - (60 * SS if tail == "left" else -60 * SS), y1 + th)
    m = Image.new("L", img.size, 0)
    dm = ImageDraw.Draw(m)
    dm.rounded_rectangle((x0, y0, x1, y1), radius=int(H * 0.4), fill=255)
    dm.polygon([(tx - 40 * SS, y1 - 30 * SS), (tx + 40 * SS, y1 - 30 * SS), tip], fill=255)
    bm = m.filter(ImageFilter.MaxFilter(1 + 2 * (6 * SS // 2)))
    fill_shape(img, bm, hexc(border))
    fill_shape(img, m, hexc(c1), hexc(c2) if c2 else None)
    d = ImageDraw.Draw(img)
    if text:
        f = fit_font(fmaker, text, W * 0.82, int(H * 0.4))
        text_center(d, (x0, y0, x1, y1), text, f, hexc(txt))
    finish(img, out)


def heart_mask(size, cx, cy, s):
    m = Image.new("L", size, 0)
    d = ImageDraw.Draw(m)
    pts = []
    for i in range(200):
        t = 2 * math.pi * i / 200
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        pts.append((cx + x * s, cy - y * s))
    d.polygon(pts, fill=255)
    return m


def star_pts(cx, cy, R, r, n=5, rot=-90):
    pts = []
    for i in range(n * 2):
        ang = math.radians(rot) + math.pi * i / n
        rr = R if i % 2 == 0 else r
        pts.append((cx + rr * math.cos(ang), cy + rr * math.sin(ang)))
    return pts


def icon_button(icon, text, c1, c2, out, w=520, h=170):
    W, H = w * SS, h * SS
    pad = 30 * SS
    img = Image.new("RGBA", (W + 2 * pad, H + 2 * pad), (0, 0, 0, 0))
    x0, y0 = pad, pad
    x1, y1 = x0 + W, y0 + H
    bm = Image.new("L", img.size, 0)
    ImageDraw.Draw(bm).rounded_rectangle((x0 - 6 * SS, y0 - 6 * SS, x1 + 6 * SS, y1 + 6 * SS), radius=H // 2 + 6 * SS,
                                         fill=255)
    fill_shape(img, bm, hexc("#FFFFFF"))
    m = Image.new("L", img.size, 0)
    ImageDraw.Draw(m).rounded_rectangle((x0, y0, x1, y1), radius=H // 2, fill=255)
    fill_shape(img, m, hexc(c1), hexc(c2))
    d = ImageDraw.Draw(img)
    icx, icy = x0 + H * 0.62, (y0 + y1) / 2
    ir = H * 0.3
    white = (255, 255, 255, 255)
    if icon == "heart":
        hm = heart_mask(img.size, icx, icy + ir * 0.05, ir / 16)
        img.paste(Image.new("RGBA", img.size, white), (0, 0), hm)
    elif icon == "plus":
        d.ellipse((icx - ir, icy - ir, icx + ir, icy + ir), fill=white)
        bw = ir * 0.22
        d.rectangle((icx - ir * 0.55, icy - bw / 2, icx + ir * 0.55, icy + bw / 2), fill=hexc(c2))
        d.rectangle((icx - bw / 2, icy - ir * 0.55, icx + bw / 2, icy + ir * 0.55), fill=hexc(c2))
    elif icon == "star":
        d.polygon(star_pts(icx, icy, ir * 1.1, ir * 0.48), fill=white)
    elif icon == "chat":
        d.rounded_rectangle((icx - ir, icy - ir * 0.8, icx + ir, icy + ir * 0.55), radius=ir * 0.4, fill=white)
        d.polygon([(icx - ir * 0.4, icy + ir * 0.4), (icx - ir * 0.7, icy + ir), (icx, icy + ir * 0.5)], fill=white)
    elif icon == "share":
        d.polygon([(icx - ir, icy + ir * 0.6), (icx - ir * 0.2, icy - ir * 0.4), (icx - ir * 0.2, icy - ir * 0.9),
                   (icx + ir, icy - ir * 0.1), (icx - ir * 0.2, icy + ir * 0.7), (icx - ir * 0.2, icy + ir * 0.2)],
                  fill=white)
    elif icon == "cart":
        d.line([(icx - ir, icy - ir * 0.7), (icx - ir * 0.6, icy - ir * 0.7), (icx - ir * 0.3, icy + ir * 0.4),
                (icx + ir * 0.8, icy + ir * 0.4), (icx + ir, icy - ir * 0.35), (icx - ir * 0.5, icy - ir * 0.35)],
               fill=white, width=int(ir * 0.2), joint="curve")
        for dx in (-0.15, 0.6):
            cx = icx + ir * dx
            d.ellipse((cx - ir * 0.16, icy + ir * 0.62, cx + ir * 0.16, icy + ir * 0.94), fill=white)
    f = fit_font(F_BLACK, text, (x1 - icx - ir * 1.6), int(H * 0.5))
    text_center(d, (icx + ir * 1.1, y0, x1 - H * 0.25, y1), text, f, white)
    finish(img, out)


def arrow(out, color1="#FFD400", color2="#FF8A00", direction="right", length=520, thick=120, outline="#FFFFFF"):
    L, T = length * SS, thick * SS
    pad = 40 * SS
    head = T * 1.9
    img = Image.new("RGBA", (L + 2 * pad, int(head + 2 * pad)), (0, 0, 0, 0))
    cy = img.height / 2
    x0 = pad
    x1 = pad + L
    shaft_end = x1 - head * 0.9
    pts = [(x0, cy - T / 2), (shaft_end, cy - T / 2), (shaft_end, cy - head / 2), (x1, cy),
           (shaft_end, cy + head / 2), (shaft_end, cy + T / 2), (x0, cy + T / 2)]
    bm = Image.new("L", img.size, 0)
    ImageDraw.Draw(bm).polygon(pts, fill=255)
    bm = bm.filter(ImageFilter.MaxFilter(1 + 2 * (7 * SS // 2)))
    fill_shape(img, bm, hexc(outline))
    m = Image.new("L", img.size, 0)
    ImageDraw.Draw(m).polygon(pts, fill=255)
    fill_shape(img, m, hexc(color1), hexc(color2))
    rot = {"right": 0, "left": 180, "up": 90, "down": -90}[direction]
    if rot:
        img = img.rotate(rot, expand=True, resample=Image.BICUBIC)
    finish(img, out)


def curved_arrow(out, color="#FF3B30", flip=False):
    S = 600 * SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    w = 34 * SS
    pts = []
    for i in range(61):
        t = i / 60
        x = 80 * SS + t * 380 * SS
        y = 460 * SS - math.sin(t * math.pi * 0.85) * 300 * SS
        pts.append((x, y))
    d.line(pts, fill=hexc("#FFFFFF"), width=w + 16 * SS, joint="curve")
    for p in (pts[0], pts[-1]):
        d.ellipse((p[0] - (w + 16 * SS) / 2, p[1] - (w + 16 * SS) / 2, p[0] + (w + 16 * SS) / 2,
                   p[1] + (w + 16 * SS) / 2), fill=hexc("#FFFFFF"))
    d.line(pts, fill=hexc(color), width=w, joint="curve")
    d.ellipse((pts[0][0] - w / 2, pts[0][1] - w / 2, pts[0][0] + w / 2, pts[0][1] + w / 2), fill=hexc(color))
    ex, ey = pts[-1]
    px, py = pts[-4]
    ang = math.atan2(ey - py, ex - px)
    hl = 110 * SS
    head = [(ex + math.cos(ang) * hl * 0.5, ey + math.sin(ang) * hl * 0.5),
            (ex + math.cos(ang + 2.4) * hl, ey + math.sin(ang + 2.4) * hl),
            (ex + math.cos(ang - 2.4) * hl, ey + math.sin(ang - 2.4) * hl)]
    hm = Image.new("L", img.size, 0)
    ImageDraw.Draw(hm).polygon(head, fill=255)
    img.paste(Image.new("RGBA", img.size, hexc("#FFFFFF")), (0, 0), hm.filter(ImageFilter.MaxFilter(1 + 2 * (8 * SS // 2))))
    img.paste(Image.new("RGBA", img.size, hexc(color)), (0, 0), hm)
    if flip:
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
    finish(img, out)


def hand_circle(out, color="#FF2D2D", w=700, h=420, open_gap=True):
    W, H = w * SS, h * SS
    pad = 40 * SS
    img = Image.new("RGBA", (W + 2 * pad, H + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, cy = img.width / 2, img.height / 2
    rng = random.Random(7)
    pts = []
    turns = 1.12 if open_gap else 1.0
    n = 220
    for i in range(n + 1):
        t = i / n
        ang = -math.pi * 0.6 + 2 * math.pi * turns * t
        wob = 1 + 0.035 * math.sin(2 * ang + 0.7) + 0.05 * (t - 0.5)
        rx = W / 2 * 0.92 * wob
        ry = H / 2 * 0.92 * wob
        pts.append((cx + rx * math.cos(ang), cy + ry * math.sin(ang) - 0.04 * H * t))
    for i, (a, b) in enumerate(zip(pts, pts[1:])):
        t = i / n
        width = int((10 + 14 * math.sin(math.pi * t)) * SS)
        d.line([a, b], fill=hexc(color), width=width)
        d.ellipse((b[0] - width / 2, b[1] - width / 2, b[0] + width / 2, b[1] + width / 2), fill=hexc(color))
    finish(img, out, shadow=False)


def underline(out, color="#FFD400", w=700):
    W = w * SS
    img = Image.new("RGBA", (W + 80 * SS, 180 * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pts = []
    for i in range(101):
        t = i / 100
        pts.append((40 * SS + t * W, 110 * SS - math.sin(t * math.pi) * 40 * SS + t * 10 * SS))
    for i, (a, b) in enumerate(zip(pts, pts[1:])):
        t = i / 100
        width = int((12 + 30 * math.sin(math.pi * min(1, t * 1.3))) * SS)
        d.line([a, b], fill=hexc(color), width=max(2, width))
        d.ellipse((b[0] - width / 2, b[1] - width / 2, b[0] + width / 2, b[1] + width / 2), fill=hexc(color))
    finish(img, out, shadow=False)


def chevrons(out, color="#FFD400", n=3, direction="down"):
    S = 320 * SS
    img = Image.new("RGBA", (S, int(S * (0.45 * n + 0.4))), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i in range(n):
        y = 40 * SS + i * S * 0.42
        alpha = int(255 * (0.45 + 0.55 * (i + 1) / n))
        pts = [(40 * SS, y), (S / 2, y + S * 0.3), (S - 40 * SS, y)]
        d.line(pts, fill=hexc("#FFFFFF", alpha), width=60 * SS, joint="curve")
        d.line(pts, fill=hexc(color, alpha), width=40 * SS, joint="curve")
    if direction == "up":
        img = img.transpose(Image.FLIP_TOP_BOTTOM)
    elif direction == "right":
        img = img.rotate(90, expand=True)
    elif direction == "left":
        img = img.rotate(-90, expand=True)
    finish(img, out)


def sparkle(out, color="#FFE14D", n=4, R=260):
    S = (R * 2 + 60) * SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    c = S / 2
    pts = []
    for i in range(n * 2):
        ang = -math.pi / 2 + math.pi * i / n
        r = (R if i % 2 == 0 else R * 0.22) * SS
        pts.append((c + r * math.cos(ang), c + r * math.sin(ang)))
    glow = Image.new("L", img.size, 0)
    ImageDraw.Draw(glow).polygon(pts, fill=200)
    glow = glow.filter(ImageFilter.GaussianBlur(14 * SS))
    img.paste(Image.new("RGBA", img.size, hexc(color)), (0, 0), glow)
    m = Image.new("L", img.size, 0)
    ImageDraw.Draw(m).polygon(pts, fill=255)
    fill_shape(img, m, hexc("#FFFFFF"), hexc(color))
    finish(img, out, shadow=False)


def burst_lines(out, color="#FFD400", n=14):
    S = 620 * SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = S / 2
    for i in range(n):
        ang = 2 * math.pi * i / n
        r0, r1 = 150 * SS, 290 * SS
        w = 26 * SS if i % 2 == 0 else 16 * SS
        a = (c + r0 * math.cos(ang), c + r0 * math.sin(ang))
        b = (c + r1 * math.cos(ang), c + r1 * math.sin(ang))
        d.line([a, b], fill=hexc("#FFFFFF"), width=w + 10 * SS)
        d.line([a, b], fill=hexc(color), width=w)
    finish(img, out, shadow=False)


def confetti(out, seed=3):
    S = 700 * SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    rng = random.Random(seed)
    cols = ["#FF3B30", "#FFCC00", "#34C759", "#007AFF", "#AF52DE", "#FF2D55", "#5AC8FA"]
    for _ in range(70):
        x, y = rng.uniform(40, 660) * SS, rng.uniform(40, 660) * SS
        col = hexc(rng.choice(cols))
        kind = rng.random()
        s = rng.uniform(12, 26) * SS
        if kind < 0.4:
            ang = rng.uniform(0, math.pi)
            pts = [(x + math.cos(ang + k * math.pi / 2) * s * (1 if k % 2 == 0 else 0.45),
                    y + math.sin(ang + k * math.pi / 2) * s * (1 if k % 2 == 0 else 0.45)) for k in range(4)]
            d.polygon(pts, fill=col)
        elif kind < 0.7:
            d.ellipse((x - s / 2, y - s / 2, x + s / 2, y + s / 2), fill=col)
        else:
            d.polygon(star_pts(x, y, s * 0.8, s * 0.35), fill=col)
    finish(img, out, shadow=False)


def word_art(text, out, c1="#FFE600", c2="#FF7A00", stroke="#000000", fmaker=F_BUTTER, size=220, rot=-8):
    f = fmaker(size * SS)
    W = int(f.getlength(text)) + 120 * SS
    H = int(size * 1.6 * SS)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((W / 2, H / 2), text, font=f, anchor="mm", fill=hexc("#FFFFFF"), stroke_width=22 * SS,
           stroke_fill=hexc("#FFFFFF"))
    d.text((W / 2, H / 2), text, font=f, anchor="mm", fill=hexc(stroke), stroke_width=12 * SS,
           stroke_fill=hexc(stroke))
    m = Image.new("L", img.size, 0)
    ImageDraw.Draw(m).text((W / 2, H / 2), text, font=f, anchor="mm", fill=255)
    fill_shape(img, m, hexc(c1), hexc(c2))
    if rot:
        img = img.rotate(rot, expand=True, resample=Image.BICUBIC)
    finish(img, out)


# ---------------------------------------------------------------- 生成
def main(out_root):
    meta = {}

    def put(cat, name, anim=""):
        p = os.path.join(out_root, cat, name + ".png")
        meta.setdefault(cat, {})[name + ".png"] = {"anim": anim}
        return p

    RED = ("#FF5A4E", "#E8000B")
    ORG = ("#FFB03A", "#FF5E00")
    PINK = ("#FF7EB3", "#FF2D7A")
    PURP = ("#B57CFF", "#6E2BFF")
    BLUE = ("#5AC8FF", "#0A6CFF")
    GRN = ("#5BE38A", "#0DAA4F")
    GOLD = ("#FFE58A", "#E0A000")
    BLK = ("#3A3A3A", "#0A0A0A")

    c = "电商标签"
    pill("限时特惠", *RED, put(c, "限时特惠", "pulse"))
    pill("立即抢购", *ORG, put(c, "立即抢购", "pulse"))
    pill("新人专享", *PINK, put(c, "新人专享"))
    pill("今日特价", *RED, put(c, "今日特价", "pulse"))
    pill("官方正品", *BLUE, put(c, "官方正品"))
    pill("现货速发", *GRN, put(c, "现货速发"))
    pill("品质保证", *BLK, put(c, "品质保证"), txt="#FFE08A", border="#FFE08A")
    pill("工厂直发", *PURP, put(c, "工厂直发"))
    pill("库存告急", *ORG, put(c, "库存告急", "shake"))
    pill("好物分享", *PINK, put(c, "好物分享"))
    star_burst("爆款", *RED, put(c, "爆款", "pulse"))
    star_burst("热卖", *ORG, put(c, "热卖", "spin"), rot=8)
    star_burst("必买", *PINK, put(c, "必买", "pulse"), fmaker=F_BLACK)
    star_burst("秒杀", *PURP, put(c, "秒杀", "shake"), points=12, inner=0.7)
    star_burst("清仓", *BLUE, put(c, "清仓"), points=20, inner=0.85, fmaker=F_BLACK)
    star_burst("福利", *GOLD, put(c, "福利", "swing"), txt="#B3001B")
    star_burst("买一\n送一", *RED, put(c, "买一送一", "pulse"), points=18)
    star_burst("限量", *BLK, put(c, "限量"), txt="#FFE08A")
    circle_badge("包邮", *RED, put(c, "包邮"))
    circle_badge("正品", *BLUE, put(c, "正品"), sub="假一赔十")
    circle_badge("NEW", *GRN, put(c, "NEW", "pulse"))
    circle_badge("推荐", *ORG, put(c, "推荐"))
    circle_badge("直降", *PINK, put(c, "直降"), sub="限时优惠")
    circle_badge("HOT", *RED, put(c, "HOT", "pulse"))
    ribbon("新品上市", *RED, put(c, "新品上市"))
    ribbon("年度爆款", *PURP, put(c, "年度爆款"))
    ribbon("限时折扣", *ORG, put(c, "限时折扣"))
    ribbon("品牌特卖", *BLUE, put(c, "品牌特卖"))
    price_tag("到手价", *RED, put(c, "到手价"))
    price_tag("直降", *ORG, put(c, "直降价签"), sub="比平时更便宜")
    price_tag("满减", *PINK, put(c, "满减"), sub="下单立享")
    price_tag("SALE", *BLK, put(c, "SALE"), txt="#FFD400")

    c = "互动引导"
    icon_button("heart", "点赞", "#FF6A88", "#FF1F57", put(c, "点赞按钮", "pulse"))
    icon_button("plus", "关注", "#FF5A4E", "#E8000B", put(c, "关注按钮", "pulse"))
    icon_button("star", "收藏", "#FFC93C", "#FF9500", put(c, "收藏按钮", "pulse"))
    icon_button("chat", "评论", "#5AC8FF", "#0A84FF", put(c, "评论按钮"))
    icon_button("share", "转发", "#5BE38A", "#0DAA4F", put(c, "转发按钮"))
    icon_button("cart", "立即购买", "#FF7A45", "#F5222D", put(c, "购物车按钮", "pulse"), w=600)
    pill("一键三连", *PINK, put(c, "一键三连", "pulse"))
    pill("关注不迷路", *RED, put(c, "关注不迷路"))
    pill("点击下方链接", *ORG, put(c, "点击下方链接", "bounce"), w=700)
    pill("左下角下单", *RED, put(c, "左下角下单", "bounce"), w=680)
    pill("评论区见", *BLUE, put(c, "评论区见"))
    pill("双击屏幕点赞", *PINK, put(c, "双击屏幕点赞"), w=720)
    bubble("戳这里购买", "#FFFFFF", put(c, "戳这里购买", "bounce"), txt="#FF2D2D", border="#FF2D2D")
    bubble("快来抢！", "#FFE14D", put(c, "快来抢", "shake"), txt="#C00000", border="#C00000", tail="right")

    c = "箭头指示"
    arrow(put(c, "箭头右", "shake"), direction="right")
    arrow(put(c, "箭头左", "shake"), direction="left")
    arrow(put(c, "箭头上", "bounce"), direction="up", color1="#FF6A6A", color2="#E8000B")
    arrow(put(c, "箭头下", "bounce"), direction="down", color1="#FF6A6A", color2="#E8000B")
    arrow(put(c, "绿色箭头右", "shake"), direction="right", color1="#7CF29C", color2="#0DAA4F")
    curved_arrow(put(c, "弧形箭头", "swing"))
    curved_arrow(put(c, "弧形箭头反", "swing"), flip=True)
    curved_arrow(put(c, "黄色弧形箭头", "swing"), color="#FFC400")
    hand_circle(put(c, "手绘圈"))
    hand_circle(put(c, "黄色手绘圈"), color="#FFD400")
    underline(put(c, "手绘下划线"))
    underline(put(c, "红色下划线"), color="#FF2D2D")
    chevrons(put(c, "向下箭头组", "bounce"))
    chevrons(put(c, "向上箭头组", "bounce"), direction="up", color="#5BE38A")
    chevrons(put(c, "向右箭头组", "shake"), direction="right", color="#FF5A4E")

    c = "装饰元素"
    sparkle(put(c, "闪光星", "spin"))
    sparkle(put(c, "白色闪光", "pulse"), color="#FFFFFF")
    sparkle(put(c, "粉色闪光", "spin"), color="#FF8FC7")
    sparkle(put(c, "蓝色闪光", "pulse"), color="#7FD3FF")
    burst_lines(put(c, "放射线", "pulse"))
    burst_lines(put(c, "红色放射线", "pulse"), color="#FF3B30")
    confetti(put(c, "彩色纸屑", "float"))
    confetti(put(c, "彩色纸屑2", "float"), seed=11)
    word_art("哇!", put(c, "哇", "pulse"))
    word_art("绝了!", put(c, "绝了", "pulse"), c1="#FF9AD5", c2="#FF2D7A")
    word_art("好用!", put(c, "好用", "bounce"), c1="#B8FF7A", c2="#22B14C")
    word_art("YYDS", put(c, "YYDS", "pulse"), c1="#FFF07A", c2="#FF6A00", fmaker=F_BLACK, size=180)
    word_art("真香", put(c, "真香", "swing"), c1="#FFE88A", c2="#FF5E00")
    word_art("冲!", put(c, "冲", "shake"), c1="#FF8A8A", c2="#E8000B")
    word_art("666", put(c, "666", "pulse"), c1="#9AE6FF", c2="#2B6CFF", fmaker=F_BLACK, size=200)
    word_art("划重点", put(c, "划重点"), c1="#FFFFFF", c2="#FFE14D", rot=0)
    word_art("干货", put(c, "干货", "bounce"), c1="#FFE14D", c2="#FF9500", rot=-5)

    c = "对话气泡"
    bubble("", "#FFFFFF", put(c, "白色气泡"))
    bubble("", "#FFE14D", put(c, "黄色气泡"), tail="right")
    bubble("", "#FF8FB8", put(c, "粉色气泡"), border="#FFFFFF")
    bubble("", "#7FD3FF", put(c, "蓝色气泡"), border="#FFFFFF", tail="right")
    bubble("说得太对了", "#FFFFFF", put(c, "说得太对了"))
    bubble("我也想要！", "#FFE14D", put(c, "我也想要"), tail="right", txt="#C00000", border="#C00000")
    bubble("真的假的？", "#FFFFFF", put(c, "真的假的"), border="#FF5A4E", txt="#FF2D2D")
    bubble("已下单～", "#B8FFB0", put(c, "已下单"), border="#0DAA4F", txt="#0B6E32", tail="right")
    bubble("太划算了", "#FFD6E7", put(c, "太划算了"), border="#FF2D7A", txt="#FF2D7A")

    for cat, m in meta.items():
        with open(os.path.join(out_root, cat, "meta.json"), "w", encoding="utf-8") as fh:
            json.dump(m, fh, ensure_ascii=False, indent=1)
    print("ok", sum(len(v) for v in meta.values()))


if __name__ == "__main__":
    main(sys.argv[1])
