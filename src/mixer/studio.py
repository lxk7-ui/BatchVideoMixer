"""素材处理工作台：去水印/去遮挡 + 滤镜/调节 + 贴纸/文字 → 导出新素材；以及界面用的单帧预览。"""
import hashlib
import os
import random
import shutil
import tempfile

from . import cleanup as cln
from . import encoders, ffmpeg, paths, probe
from . import filters_lib as fl
from . import overlays as ovl
from . import render as rd
from .models import CleanupProfile, StylePreset


def _style_graph(inl, outl, style: StylePreset, W, H):
    return rd.style_graph(inl, outl, style.filter_name, style.filter_intensity, style.adjust_obj(), 0, False, W, H,
                          random.Random(1), uid="sv")


def build_graph(info, profile: CleanupProfile, style: StylePreset, workdir, duration, first_input=1,
                ctx_vars=None, preview_scale=None):
    """返回 (额外输入参数, 滤镜图, 输出宽, 输出高)。"""
    W, H = info.width, info.height
    graph = []
    cur = "[0:v]"
    if info.hdr and info.kind == "video":
        graph.append(f"{cur}{rd.HDR_CHAIN}[hd]")
        cur = "[hd]"
    if profile is not None and not profile.is_empty():
        g, _w, _h = cln.cleanup_graph(cur, "[cl]", profile, W, H, uid="c", seg_start=0.0, seg_dur=None,
                                      workdir=workdir, restore_size=True)
        graph.append(g)
        cur = "[cl]"
    style = style or StylePreset()
    graph.append(_style_graph(cur, "[sty]", style, W, H))
    cur = "[sty]"
    inputs = []
    items = style.overlay_objs()
    if items:
        prepared = ovl.prepare(items, W, H, max(0.5, duration), workdir, ctx=ctx_vars or {},
                               rng=random.Random(7))
        inputs, ov_graph, cur = ovl.build_graph(prepared, cur, first_input, W, H, int(round(info.fps or 30)))
        graph += ov_graph
    tail = "format=yuv420p" if not preview_scale else f"scale={preview_scale}:-2:flags=bilinear,format=rgb24"
    graph.append(f"{cur}{tail}[vout]")
    return inputs, ";".join(graph), W, H


def preview_frame(path, t, profile=None, style=None, max_w=960, out_png=None):
    """渲染单帧预览（与导出效果完全一致），返回 PNG 路径。"""
    info = probe.probe(path)
    if not info.ok:
        raise ffmpeg.FFmpegError(info.error or "素材无效")
    work = os.path.join(paths.cache_dir("preview"), "w")
    os.makedirs(work, exist_ok=True)
    out_png = out_png or os.path.join(paths.cache_dir("preview"), "frame.png")
    pw = min(max_w, info.width) // 2 * 2
    inputs, graph, W, H = build_graph(info, profile, style, work, max(1.0, info.duration), preview_scale=pw)
    if info.kind == "image":
        src_args = ["-i", path]
    else:
        t = max(0.0, min(float(t), max(0.0, info.duration - 0.05)))
        src_args = ["-ss", f"{t:.3f}", "-i", path]
    # 预览时叠加层时间按 t 对齐：简单起见用 setpts 偏移
    if style is not None and style.overlays and info.kind == "video":
        graph = graph.replace("[0:v]", f"[0:v]setpts=PTS-STARTPTS+{t:.3f}/TB,", 1)
    script = os.path.join(work, "pv.txt")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(graph)
    cmd = [ffmpeg.ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y"] + src_args + inputs + \
        ffmpeg.filter_script_args(script) + ["-map", "[vout]", "-frames:v", "1", "-update", "1", out_png]
    p = ffmpeg.run_capture(cmd, timeout=60, cwd=work)
    if p.returncode != 0 or not os.path.isfile(out_png):
        raise ffmpeg.FFmpegError(ffmpeg.summarize_error(p.stderr) or "预览失败", cmd=cmd, stderr=p.stderr)
    return out_png


def raw_frame(path, t, max_w=1280, out_png=None):
    """原始帧（不做任何处理），用于编辑画布。"""
    info = probe.probe(path)
    out_png = out_png or os.path.join(paths.cache_dir("preview"), "raw.png")
    args = [ffmpeg.ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
    if info.kind == "video":
        t = max(0.0, min(float(t), max(0.0, info.duration - 0.05)))
        args += ["-ss", f"{t:.3f}"]
    w = min(max_w, info.width or max_w) // 2 * 2
    args += ["-i", path, "-frames:v", "1", "-vf", f"scale={w}:-2:flags=bicubic", "-update", "1", out_png]
    p = ffmpeg.run_capture(args, timeout=60)
    if p.returncode != 0 or not os.path.isfile(out_png):
        raise ffmpeg.FFmpegError(ffmpeg.summarize_error(p.stderr) or "取帧失败", stderr=p.stderr)
    return out_png


def filter_thumbs(frame_png, names, width=150, out_dir=None):
    """一次 ffmpeg 调用生成所有滤镜缩略图。返回 {名称: png 路径}。"""
    out_dir = out_dir or paths.cache_dir("filter_thumbs")
    key = hashlib.md5(f"{frame_png}|{os.path.getmtime(frame_png)}|{width}".encode()).hexdigest()[:10]
    res = {}
    todo = []
    for n in names:
        p = os.path.join(out_dir, f"{key}_{hashlib.md5(n.encode()).hexdigest()[:8]}.png")
        res[n] = p
        if not os.path.isfile(p):
            todo.append(n)
    if not todo:
        return res
    parts = [f"[0:v]scale={width}:-2,format=yuv420p,split={len(todo)}" + "".join(f"[i{k}]" for k in range(len(todo)))]
    maps = []
    for k, n in enumerate(todo):
        parts.append(fl.filter_graph(f"[i{k}]", f"[o{k}]", n, 1.0, uid=f"u{k}"))
        maps += ["-map", f"[o{k}]", "-frames:v", "1", "-update", "1", res[n]]
    script = os.path.join(out_dir, f"{key}_graph.txt")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(";\n".join(parts))
    ffmpeg.run_capture([ffmpeg.ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", frame_png]
                       + ffmpeg.filter_script_args(script) + maps, timeout=120)
    return res


def export(path, out_path, profile=None, style=None, quality="high", encoder="auto", cancel=None,
           on_progress=None, log=None):
    """导出处理后的素材（视频或图片）。"""
    info = probe.probe(path)
    if not info.ok:
        raise ffmpeg.FFmpegError(info.error or "素材无效", category="素材损坏")
    work = tempfile.mkdtemp(prefix="exp_", dir=paths.cache_dir("tmp"))
    try:
        inputs, graph, W, H = build_graph(info, profile, style, work, max(1.0, info.duration),
                                          ctx_vars={"素材名": info.stem})
        script = os.path.join(work, "graph.txt")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(graph)
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        if info.kind == "image":
            tmp = out_path + ".tmp" + os.path.splitext(out_path)[1]
            cmd = [ffmpeg.ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", path] + inputs + \
                ffmpeg.filter_script_args(script) + ["-map", "[vout]", "-frames:v", "1", "-update", "1", "-q:v", "2",
                                                     tmp]
            p = ffmpeg.run_capture(cmd, timeout=120, cwd=work)
            if p.returncode != 0:
                raise ffmpeg.FFmpegError(ffmpeg.summarize_error(p.stderr), cmd=cmd, stderr=p.stderr,
                                         category=ffmpeg.classify_error(p.stderr))
            os.replace(tmp, out_path)
            return out_path
        enc = encoders.resolve(encoder)
        tmp = out_path + ".tmp.mp4"

        def cmd_for(e):
            fps = info.fps or 30
            return ["-i", path] + inputs + ffmpeg.filter_script_args(script) + \
                ["-map", "[vout]", "-map", "0:a?"] + encoders.video_args(e, quality, W, H, fps) + \
                ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", tmp]
        try:
            ffmpeg.run_ffmpeg(cmd_for(enc), duration=info.duration, on_progress=on_progress, cancel=cancel, cwd=work,
                              log=log, label="导出")
        except ffmpeg.FFmpegError as e:
            if enc != "cpu" and e.category in ("编码器失败", "其他"):
                ffmpeg.run_ffmpeg(cmd_for("cpu"), duration=info.duration, on_progress=on_progress, cancel=cancel,
                                  cwd=work, log=log, label="导出(CPU)")
            else:
                raise
        os.replace(tmp, out_path)
        return out_path
    finally:
        shutil.rmtree(work, ignore_errors=True)


def preview_clip(path, start, seconds, profile=None, style=None, out_path=None, width=540):
    """渲染一小段低分辨率预览视频（带声音），用于播放查看动画效果。"""
    info = probe.probe(path)
    work = os.path.join(paths.cache_dir("preview"), "clip")
    os.makedirs(work, exist_ok=True)
    out_path = out_path or os.path.join(paths.cache_dir("preview"), "preview_clip.mp4")
    inputs, graph, W, H = build_graph(info, profile, style, work, seconds)
    graph = graph.replace("format=yuv420p[vout]", f"scale={width}:-2,format=yuv420p[vout]")
    script = os.path.join(work, "graph.txt")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(graph)
    src = ["-i", path] if info.kind == "image" else ["-ss", f"{max(0.0, start):.3f}", "-t", f"{seconds:.3f}", "-i",
                                                       path]
    if info.kind == "image":
        src = ["-loop", "1", "-framerate", "30", "-t", f"{seconds:.3f}", "-i", path]
    cmd = src + inputs + ffmpeg.filter_script_args(script) + ["-map", "[vout]", "-map", "0:a?", "-t",
                                                              f"{seconds:.3f}", "-c:v", "libx264", "-preset",
                                                              "ultrafast", "-crf", "26", "-c:a", "aac", out_path]
    ffmpeg.run_ffmpeg(cmd, duration=seconds, cwd=work, label="预览")
    return out_path


def detect_black_bars(path):
    """自动检测上下/左右黑边，返回裁切比例 (l, t, r, b)。"""
    import re
    info = probe.probe(path)
    if not info.ok or info.kind != "video":
        return (0.0, 0.0, 0.0, 0.0)
    best = None
    for frac in (0.2, 0.5, 0.8):
        t = info.duration * frac
        p = ffmpeg.run_capture([ffmpeg.ffmpeg_bin(), "-hide_banner", "-nostdin", "-ss", f"{t:.2f}", "-i", path,
                                "-vf", "cropdetect=limit=24:round=2:reset=0", "-frames:v", "12", "-an", "-f", "null",
                                "-"], timeout=60)
        m = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", p.stderr or "")
        if not m:
            continue
        w, h, x, y = map(int, m[-1])
        if best is None:
            best = [x, y, x + w, y + h]
        else:  # 取并集（保守，避免裁掉有效画面）
            best = [min(best[0], x), min(best[1], y), max(best[2], x + w), max(best[3], y + h)]
    if not best:
        return (0.0, 0.0, 0.0, 0.0)
    # cropdetect 基于未旋转的编码尺寸；旋转素材不做处理
    if info.rotation in (90, 270):
        return (0.0, 0.0, 0.0, 0.0)
    W, H = info.width, info.height
    l = best[0] / W
    t = best[1] / H
    r = max(0.0, (W - best[2]) / W)
    b = max(0.0, (H - best[3]) / H)
    return tuple(round(v, 4) if v > 0.01 else 0.0 for v in (l, t, r, b))


def canvas_frame(path, t, W, H, fit="smart", out_png=None, scale=0.5):
    """按成片画布规格（含模糊背景等适配方式）渲染一帧，用作包装设计的背景。"""
    info = probe.probe(path)
    out_png = out_png or os.path.join(paths.cache_dir("preview"), "canvas_bg.png")
    Wd, Hd = rd.even(W * scale), rd.even(H * scale)
    g = rd.fit_graph("[0:v]", "[o]", info, Wd, Hd, fit, "f")
    args = [ffmpeg.ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
    if info.kind == "video":
        args += ["-ss", f"{max(0.0, min(t, info.duration - 0.1)):.2f}"]
    args += ["-i", path, "-filter_complex", g, "-map", "[o]", "-frames:v", "1", "-update", "1", out_png]
    p = ffmpeg.run_capture(args, timeout=60)
    if p.returncode != 0:
        raise ffmpeg.FFmpegError(ffmpeg.summarize_error(p.stderr) or "生成背景失败", stderr=p.stderr)
    return out_png
