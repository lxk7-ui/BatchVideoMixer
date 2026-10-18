"""去水印 / 去贴纸 / 去遮挡：区域处理滤镜生成 + 静态水印自动识别。

坐标统一使用相对源画面的比例（0~1），渲染时按每个素材的真实分辨率换算，
所以一套“去水印模板”可以直接套用到同一来源、不同分辨率的一批素材上。
"""
import hashlib
import os

from .models import CleanupProfile, Region


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def region_px(r: Region, W: int, H: int, margin=1):
    """比例 → 像素（偶数对齐、保证在画面内）。"""
    x = int(round(r.x * W))
    y = int(round(r.y * H))
    w = int(round(r.w * W))
    h = int(round(r.h * H))
    x = _clamp(x, margin, max(margin, W - margin - 4))
    y = _clamp(y, margin, max(margin, H - margin - 4))
    w = _clamp(w, 4, W - margin - x)
    h = _clamp(h, 4, H - margin - y)
    return x, y, max(4, w), max(4, h)


def _hex_color(c: str):
    c = (c or "#000000").strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    if len(c) != 6:
        c = "000000"
    return "0x" + c.upper()


def _enable_expr(r: Region, seg_start: float, seg_dur: float):
    """把源时间窗口换算为片段内时间窗口。返回 (是否参与, enable 表达式或 '')。"""
    t0 = float(r.t0 or 0)
    t1 = float(r.t1 or 0)
    if t0 <= 0 and t1 <= 0:
        return True, ""
    a = t0 - seg_start
    b = (t1 - seg_start) if t1 > 0 else 1e9
    if seg_dur is not None and seg_dur > 0:
        if b <= 0 or a >= seg_dur:
            return False, ""
        if a <= 0 and b >= seg_dur:
            return True, ""
    a = max(0.0, a)
    if b >= 1e8:
        return True, f"gte(t,{a:.3f})"
    return True, f"between(t,{a:.3f},{b:.3f})"


def prepare_mask(mask_png: str, W: int, H: int, workdir: str):
    """把画笔蒙版缩放到素材分辨率并二值化，存到 workdir（removelogo 需要同尺寸蒙版）。返回文件名（相对 workdir）。"""
    from PIL import Image
    if not mask_png or not os.path.isfile(mask_png):
        return None
    st = os.stat(mask_png)
    key = hashlib.md5(f"{mask_png}|{st.st_size}|{st.st_mtime}|{W}x{H}".encode("utf-8")).hexdigest()[:12]
    name = f"mask_{key}.png"
    out = os.path.join(workdir, name)
    if not os.path.isfile(out):
        im = Image.open(mask_png).convert("L").resize((W, H), Image.BILINEAR)
        im = im.point(lambda v: 255 if v >= 96 else 0)
        # 至少要有像素，且不能全白
        hist = im.histogram()
        white = hist[255]
        if white == 0 or white >= W * H * 0.9:
            return None
        os.makedirs(workdir, exist_ok=True)
        im.save(out)
    return name


def cleanup_graph(in_label, out_label, profile: CleanupProfile, W, H, uid="c",
                  seg_start=0.0, seg_dur=None, workdir=None, restore_size=False):
    """生成去水印滤镜图片段。返回 (graph, out_w, out_h)。

    in_label/out_label 形如 "[v0]"；无处理时返回 null 直通。
    """
    if profile is None or profile.is_empty():
        return f"{in_label}null{out_label}", W, H
    chains = []
    cur = in_label
    idx = 0

    def nxt():
        nonlocal idx
        idx += 1
        return f"[{uid}{idx}]"

    # 1) 画笔蒙版
    if profile.mask_png and workdir:
        name = prepare_mask(profile.mask_png, W, H, workdir)
        if name:
            o = nxt()
            chains.append(f"{cur}removelogo=f={name}{o}")
            cur = o

    # 2) 区域
    for r in profile.region_objs():
        use, en = _enable_expr(r, seg_start, seg_dur)
        if not use:
            continue
        x, y, w, h = region_px(r, W, H)
        en_opt = f":enable='{en}'" if en else ""
        method = r.method or "delogo"
        if method == "delogo":
            o = nxt()
            chains.append(f"{cur}delogo=x={x}:y={y}:w={w}:h={h}{en_opt}{o}")
            cur = o
        elif method == "color":
            o = nxt()
            chains.append(f"{cur}drawbox=x={x}:y={y}:w={w}:h={h}:color={_hex_color(r.color)}@1:t=fill{en_opt}{o}")
            cur = o
        elif method in ("blur", "mosaic"):
            m, s, b, o = nxt(), nxt(), nxt(), nxt()
            if method == "blur":
                sigma = max(2, min(60, int(r.strength or 12)))
                proc = f"gblur=sigma={sigma}:steps=2"
            else:
                block = max(4, min(80, int(r.strength or 16)))
                sw = max(1, w // block)
                sh = max(1, h // block)
                proc = f"scale={sw}:{sh}:flags=area,scale={w}:{h}:flags=neighbor"
            chains.append(f"{cur}split=2{m}{s};{s}crop={w}:{h}:{x}:{y},{proc}{b};"
                          f"{m}{b}overlay={x}:{y}{en_opt}{o}")
            cur = o

    # 3) 裁边
    out_w, out_h = W, H
    if any((profile.crop_l, profile.crop_t, profile.crop_r, profile.crop_b)):
        l = _clamp(float(profile.crop_l), 0, 0.45)
        t = _clamp(float(profile.crop_t), 0, 0.45)
        rr = _clamp(float(profile.crop_r), 0, 0.45)
        bb = _clamp(float(profile.crop_b), 0, 0.45)
        cw = int(W * (1 - l - rr)) // 2 * 2
        ch = int(H * (1 - t - bb)) // 2 * 2
        cx = int(W * l) // 2 * 2
        cy = int(H * t) // 2 * 2
        cw, ch = max(16, cw), max(16, ch)
        o = nxt()
        if restore_size:
            chains.append(f"{cur}crop={cw}:{ch}:{cx}:{cy},scale={W}:{H}:flags=lanczos,setsar=1{o}")
        else:
            chains.append(f"{cur}crop={cw}:{ch}:{cx}:{cy}{o}")
            out_w, out_h = cw, ch
        cur = o

    if not chains:
        return f"{in_label}null{out_label}", W, H
    # 把最后一个标签换成 out_label
    last = chains[-1]
    chains[-1] = last[: last.rfind(cur)] + out_label
    return ";".join(chains), out_w, out_h


# ----------------------------------------------------------------------------
# 静态水印 / 贴纸自动识别
# ----------------------------------------------------------------------------
def _remove_long_runs(mask, axis, max_run):
    """把沿 axis 方向连续长度超过 max_run 的像素串清零（原地修改）。axis=0 竖线，axis=1 横线。"""
    import numpy as np
    arr = mask if axis == 1 else mask.T
    for i in range(arr.shape[0]):
        row = arr[i]
        if row.sum() <= max_run:
            continue
        d = np.diff(np.concatenate(([0], row.astype(np.int8), [0])))
        starts = np.where(d == 1)[0]
        ends = np.where(d == -1)[0]
        for s, e in zip(starts, ends):
            if e - s > max_run:
                row[s:e] = False
def detect_static_overlays(info, max_frames=48, analysis_width=480, log=None):
    """分析整段视频中“始终不动的边缘”，找出疑似水印/台标/固定贴纸的位置。

    返回 [{"x","y","w","h","score"}]（比例坐标），按可信度排序。
    原理：抽取分散在全片的若干帧，统计每个像素“持续存在边缘”的比例；
    画面内容在动，而水印的笔画边缘几乎每帧都在，因此会被凸显出来。
    """
    import numpy as np
    from . import ffmpeg

    W = analysis_width
    H = int(round(info.height * W / max(1, info.width))) // 2 * 2
    if H < 32:
        return []

    def grab(args):
        p = ffmpeg.run_capture([ffmpeg.ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error"] + args,
                               timeout=240, text=False)
        raw = p.stdout or b""
        n = len(raw) // (W * H)
        if n <= 0:
            return None
        return np.frombuffer(raw[: n * W * H], dtype=np.uint8).reshape(n, H, W)

    frames = None
    dur = max(0.5, info.duration)
    # 方案 A：只解码关键帧（快）
    frames = grab(["-skip_frame", "nokey", "-i", info.path, "-map", "0:v:0", "-vf",
                   f"scale={W}:{H}:flags=area,format=gray", "-fps_mode", "passthrough",
                   "-f", "rawvideo", "-"])
    if frames is None or len(frames) < 10:
        # 方案 B：全解码按时间均匀抽帧
        n = max_frames
        frames = grab(["-i", info.path, "-map", "0:v:0", "-vf",
                       f"fps={n / dur:.5f},scale={W}:{H}:flags=area,format=gray", "-f", "rawvideo", "-"])
    if frames is None or len(frames) < 4:
        return []
    if len(frames) > max_frames:
        idx = np.linspace(0, len(frames) - 1, max_frames).astype(int)
        frames = frames[idx]

    f = frames.astype(np.int16)
    gx = np.abs(f[:, :, 1:] - f[:, :, :-1])[:, :-1, :]
    gy = np.abs(f[:, 1:, :] - f[:, :-1, :])[:, :, :-1]
    g = np.maximum(gx, gy)
    persist = (g > 22).mean(axis=0)              # 边缘出现频率
    std = frames.astype(np.float32).std(axis=0)[:-1, :-1]
    moving_frac = float((std > 12).mean())       # 画面运动程度
    mask = persist > 0.72
    # 去掉贯穿画面的长直线（静态背景线条、黑边边界），避免和水印连成一片
    hh_, ww_ = mask.shape
    _remove_long_runs(mask, axis=0, max_run=max(12, int(hh_ * 0.22)))
    _remove_long_runs(mask, axis=1, max_run=max(16, int(ww_ * 0.35)))
    # 去掉单像素噪点：3x3 邻域计数
    m = mask.astype(np.uint8)
    pad = np.pad(m, 1)
    neigh = sum(pad[dy:dy + m.shape[0], dx:dx + m.shape[1]] for dy in range(3) for dx in range(3))
    mask = (m == 1) & (neigh >= 3)

    # 块化（每 6px 一个格）后做连通域
    cell = 6
    gh, gw = mask.shape[0] // cell, mask.shape[1] // cell
    if gh < 4 or gw < 4:
        return []
    grid = mask[: gh * cell, : gw * cell].reshape(gh, cell, gw, cell).sum(axis=(1, 3)) >= 2
    # 膨胀 1 格，连接文字笔画
    gp = np.pad(grid, 1)
    dil = np.zeros_like(grid)
    for dy in range(3):
        for dx in range(3):
            dil |= gp[dy:dy + gh, dx:dx + gw]
    labels = np.zeros((gh, gw), dtype=np.int32)
    comps = []
    cur = 0
    for yy in range(gh):
        for xx in range(gw):
            if dil[yy, xx] and not labels[yy, xx]:
                cur += 1
                stack = [(yy, xx)]
                labels[yy, xx] = cur
                ys, xs = [], []
                while stack:
                    cy, cx = stack.pop()
                    ys.append(cy)
                    xs.append(cx)
                    for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                        if 0 <= ny < gh and 0 <= nx < gw and dil[ny, nx] and not labels[ny, nx]:
                            labels[ny, nx] = cur
                            stack.append((ny, nx))
                comps.append((min(ys), min(xs), max(ys), max(xs), len(ys)))
    total_cells = gh * gw
    results = []
    for (y0, x0, y1, x1, n) in comps:
        bw, bh = x1 - x0 + 1, y1 - y0 + 1
        area = bw * bh
        if n < 3 or area > total_cells * 0.12 or bw > gw * 0.7 or bh > gh * 0.5:
            continue
        fill = n / area
        cxn = (x0 + x1 + 1) / 2 / gw
        cyn = (y0 + y1 + 1) / 2 / gh
        edge_dist = min(cxn, 1 - cxn, cyn, 1 - cyn)
        border_bonus = 1.0 if edge_dist < 0.22 else 0.55
        seg = persist[y0 * cell:(y1 + 1) * cell, x0 * cell:(x1 + 1) * cell]
        strength = float(seg.mean()) if seg.size else 0.0
        score = (0.5 + strength) * border_bonus * (0.6 + 0.4 * min(1.0, fill * 2)) * (0.5 + 0.5 * moving_frac)
        padx, pady = 0.012, 0.010
        x = max(0.0, x0 / gw - padx)
        y = max(0.0, y0 / gh - pady)
        w = min(1.0 - x, bw / gw + padx * 2)
        h = min(1.0 - y, bh / gh + pady * 2)
        results.append({"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4),
                        "score": round(score, 3)})
    results.sort(key=lambda r: -r["score"])
    if log:
        log(f"自动识别：画面运动度 {moving_frac:.2f}，候选 {len(results)} 处")
    return results[:5]
