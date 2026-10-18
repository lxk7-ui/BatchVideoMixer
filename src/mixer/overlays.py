"""叠加层（贴纸 / 文字 / 图片角标）：素材预处理 + 动画滤镜图生成。

静态变换（缩放、旋转、透明度、去底色）在 Pillow 里一次做好，ffmpeg 只负责叠加与动画，速度快、效果稳定。
"""
import math
import os
import random
from dataclasses import dataclass

from . import stickers as stk
from . import textstyle
from .models import OverlayItem

ANIM_IN_DUR = 0.4
ANIM_OUT_DUR = 0.35


@dataclass
class Prepared:
    item: OverlayItem
    path: str
    animated: bool
    pw: int
    ph: int
    cx: float
    cy: float
    t0: float
    t1: float


def _remove_bg(im, mode):
    """去除白底/黑底（软阈值，边缘平滑）。"""
    import numpy as np
    from PIL import Image
    a = np.asarray(im.convert("RGBA")).astype(np.float32)
    rgb = a[..., :3]
    if mode == "white":
        dist = (255.0 - rgb.min(axis=2))
    else:
        dist = rgb.max(axis=2)
    lo, hi = 18.0, 60.0
    alpha = np.clip((dist - lo) / (hi - lo), 0, 1) * a[..., 3]
    a[..., 3] = alpha
    return Image.fromarray(a.astype("uint8"), "RGBA")


def detect_plain_bg(path):
    """产品图自动判断是否为纯白/纯黑底（四角颜色一致且无透明通道）→ 返回 white/black/""。"""
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.seek(0)
            if im.mode in ("RGBA", "LA") or "transparency" in im.info:
                a = im.convert("RGBA").split()[3]
                if a.getextrema()[0] < 250:
                    return ""   # 已有透明通道
            rgb = im.convert("RGB")
            w, h = rgb.size
            pts = [(1, 1), (w - 2, 1), (1, h - 2), (w - 2, h - 2), (w // 2, 1), (w // 2, h - 2)]
            cols = [rgb.getpixel(p) for p in pts]
        if all(min(c) >= 235 for c in cols):
            return "white"
        if all(max(c) <= 22 for c in cols):
            return "black"
    except Exception:
        pass
    return ""


def load_item_image(item: OverlayItem, W: int, ctx=None):
    """返回该叠加项的 RGBA 原图（未缩放）。文字按 W 宽渲染。"""
    from PIL import Image
    if item.kind == "text":
        text = textstyle.substitute(item.text, ctx or {})
        return textstyle.render_text(text, item.style or {}, canvas_w=W)
    if not item.src or not os.path.isfile(item.src):
        return None
    with Image.open(item.src) as im:
        im.seek(0)
        img = im.convert("RGBA")
    if item.remove_bg in ("white", "black"):
        img = _remove_bg(img, item.remove_bg)
    bb = img.getbbox()
    if bb and item.kind == "image":
        img = img.crop(bb)
    return img


def target_width_px(item: OverlayItem, img, W):
    if item.kind == "text" and (not item.w or item.w <= 0):
        return img.width
    return max(8, int(round(float(item.w or 0.3) * W)))


def prepare(items, W, H, duration, workdir, ctx=None, rng=None, uid="ov"):
    """把叠加项准备成可直接输入 ffmpeg 的文件，并确定最终位置/时间。"""
    from PIL import Image
    rng = rng or random.Random()
    out = []
    os.makedirs(workdir, exist_ok=True)
    for k, item in enumerate(items):
        if isinstance(item, dict):
            item = OverlayItem.from_dict(item)
        # 时间
        t0 = float(item.t0 or 0)
        t1 = float(item.t1 or 0)
        if t0 < 0:
            t0 = max(0.0, duration + t0)
        if t1 <= 0:
            t1 = duration + t1 if t1 < 0 else duration
        t0 = max(0.0, min(duration, t0))
        t1 = max(0.0, min(duration, t1))
        if t1 - t0 < 0.15:
            continue
        animated_src = False
        path = None
        if item.kind in ("sticker", "image") and item.src:
            ext = os.path.splitext(item.src)[1].lower()
            if ext in stk.ANIM_EXTS and stk._is_animated(item.src):
                mov, _d = stk.normalize_animated(item.src)
                if mov:
                    animated_src = True
                    path = mov
        if animated_src:
            from PIL import Image as _I
            with _I.open(item.src) as im0:
                sw, sh = im0.size
            pw = target_width_px(item, None, W) if item.kind != "text" else sw
            pw = max(2, pw // 2 * 2)
            ph = max(2, int(round(sh * pw / max(1, sw))) // 2 * 2)
        else:
            img = load_item_image(item, W, ctx)
            if img is None:
                continue
            pw = target_width_px(item, img, W)
            pw = max(2, min(pw, W * 2))
            ph = max(2, int(round(img.height * pw / max(1, img.width))))
            if (pw, ph) != img.size:
                img = img.resize((pw, ph), Image.LANCZOS)
            if abs(float(item.rotation or 0)) > 0.1:
                img = img.rotate(-float(item.rotation), expand=True, resample=Image.BICUBIC)
            op = float(item.opacity if item.opacity is not None else 1.0)
            if op < 0.995:
                a = img.split()[3].point(lambda v: int(v * max(0.0, op)))
                img.putalpha(a)
            pw, ph = img.size
            path = os.path.join(workdir, f"{uid}_{k}.png")
            img.save(path)
        # 位置
        cx, cy = float(item.cx), float(item.cy)
        hw, hh = pw / 2.0 / W, ph / 2.0 / H
        if item.corner_random:
            m = rng.uniform(0.03, 0.06)
            mx = m
            my = m * W / H
            corners = [(mx + hw, my + hh + 0.04), (1 - mx - hw, my + hh + 0.04),
                       (mx + hw, 1 - my - hh - 0.10), (1 - mx - hw, 1 - my - hh - 0.10)]
            # 选与已摆放元素重叠最少的角（并列时随机），避免角标挡住贴纸/文字
            placed = [(o.cx - o.pw / 2.0 / W, o.cy - o.ph / 2.0 / H, o.cx + o.pw / 2.0 / W, o.cy + o.ph / 2.0 / H)
                      for o in out]

            def overlap(c):
                x0, y0, x1, y1 = c[0] - hw, c[1] - hh, c[0] + hw, c[1] + hh
                return sum(max(0.0, min(x1, b[2]) - max(x0, b[0])) * max(0.0, min(y1, b[3]) - max(y0, b[1]))
                           for b in placed)
            scored = [(round(overlap(c), 4), rng.random(), c) for c in corners]
            scored.sort()
            cx, cy = scored[0][2]
        elif item.random_pos:
            cx = rng.uniform(min(0.5, 0.08 + hw), max(0.5, 0.92 - hw))
            cy = rng.uniform(min(0.5, 0.10 + hh), max(0.5, 0.86 - hh))
        out.append(Prepared(item=item, path=path, animated=animated_src, pw=pw, ph=ph, cx=cx, cy=cy, t0=t0, t1=t1))
    return out


def _f(v):
    return f"{v:.4f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)


def build_graph(prepared, base_label, first_input_idx, W, H, fps, uid="o"):
    """生成叠加滤镜图。返回 (输入参数列表, 滤镜图字符串列表, 输出标签)。"""
    inputs = []
    graph = []
    cur = base_label
    idx = first_input_idx
    for k, p in enumerate(prepared):
        it = p.item
        tau = f"(t-{_f(p.t0)})"
        need_stream = p.animated or it.anim_in in ("fade", "pop", "zoom") or it.anim_out == "fade" or \
            it.anim_loop in ("pulse", "spin", "swing")
        if p.animated:
            inputs += ["-stream_loop", "-1", "-i", p.path]
        else:
            inputs += ["-i", p.path]
        chain = [f"[{idx}:v]format=rgba"]
        if p.animated:
            chain.append(f"fps={fps},scale={p.pw}:{p.ph}:flags=lanczos,setpts=N/({fps}*TB)")
        elif need_stream:
            chain.append(f"loop=loop=-1:size=1,setpts=N/({fps}*TB)")
        cur_w = p.pw
        # 旋转动画
        if it.anim_loop in ("spin", "swing"):
            if it.anim_loop == "spin":
                ang = f"{tau}*2*PI/3"
            else:
                ang = f"0.17*sin(2*PI*{tau}*0.9)"
            chain.append(f"rotate=a='{ang}':c=black@0:ow='hypot(iw,ih)':oh=ow")
            cur_w = int(math.ceil(math.hypot(p.pw, p.ph)))
        # 缩放动画
        s_parts = []
        if it.anim_in == "pop":
            T = f"max(0,{tau})"
            s_parts.append(f"if(lt({T},0.18),0.3+0.85*{T}/0.18,if(lt({T},0.3),1.15-0.15*({T}-0.18)/0.12,1))")
        elif it.anim_in == "zoom":
            T = f"max(0,{tau})"
            s_parts.append(f"if(lt({T},0.35),1.8-0.8*{T}/0.35,1)")
        if it.anim_loop == "pulse":
            s_parts.append(f"(1+0.07*sin(2*PI*{tau}*1.2))")
        if s_parts:
            S = "*".join(s_parts)
            chain.append(f"scale=w='max(2,trunc({cur_w}*{S}/2)*2)':h=-2:eval=frame")
        # 透明度渐变
        if it.anim_in == "fade":
            chain.append(f"fade=t=in:st={_f(p.t0)}:d={ANIM_IN_DUR}:alpha=1")
        if it.anim_out == "fade":
            st = max(p.t0, p.t1 - ANIM_OUT_DUR)
            chain.append(f"fade=t=out:st={_f(st)}:d={ANIM_OUT_DUR}:alpha=1")
        lab = f"[{uid}{k}]"
        graph.append(",".join(chain) + lab)

        # 位置表达式
        X = f"({_f(p.cx * W)}-w/2)"
        Y = f"({_f(p.cy * H)}-h/2)"
        D = ANIM_IN_DUR
        E = f"(1-pow(1-min(1,max(0,{tau})/{D}),3))"
        if it.anim_in == "slide_left":
            X = f"if(lt({tau},{D}),-w+({X}+w)*{E},{X})"
        elif it.anim_in == "slide_right":
            X = f"if(lt({tau},{D}),W-(W-{X})*{E},{X})"
        elif it.anim_in == "slide_up":
            Y = f"if(lt({tau},{D}),H-(H-{Y})*{E},{Y})"
        elif it.anim_in == "slide_down":
            Y = f"if(lt({tau},{D}),-h+({Y}+h)*{E},{Y})"
        if it.anim_loop == "bounce":
            Y = f"{Y}-abs(sin(PI*{tau}*1.6))*{_f(0.025 * H)}"
        elif it.anim_loop == "shake":
            X = f"{X}+if(lt(mod({tau},2),0.6),{_f(0.008 * W)}*sin({tau}*50),0)"
        elif it.anim_loop == "float":
            Y = f"{Y}+{_f(0.01 * H)}*sin(2*PI*{tau}*0.5)"
        en = f"between(t,{_f(p.t0)},{_f(p.t1)})"
        if it.anim_loop == "blink":
            en = f"{en}*lt(mod({tau},1),0.7)"
        nxt = f"[{uid}b{k}]"
        sh = ":shortest=1" if (need_stream or p.animated) else ""
        graph.append(f"{cur}{lab}overlay=x='{X}':y='{Y}':enable='{en}':eval=frame{sh}{nxt}")
        cur = nxt
        idx += 1
    return inputs, graph, cur
