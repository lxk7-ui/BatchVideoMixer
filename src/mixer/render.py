"""渲染：两阶段流水线（内存占用低、可并行、可缓存、帧精确）。

阶段一（片段）：每个片段单独一条 ffmpeg —— 精确seek → 去水印(源分辨率) → 适配画布(模糊背景/黑边/裁剪)
              → 变速 → 统一帧率 → 精确帧数 → 入场特效；音频统一 48k 立体声 PCM，样本级对齐。
              有叠化转场的边界额外渲染一个“衔接片”（前段尾 + 后段头做 xfade/acrossfade）。
阶段二（合成）：concat 顺序读取全部片段（同一时刻只解码一个）→ 滤镜/调节/画面变体 → 贴纸/文字/角标
              → 原声+BGM 混音、响度统一、淡出 → 最终编码。

实测：30 段一次性 xfade 需 4.6GB 内存，而本方案峰值约 0.7GB。
"""
import hashlib
import json
import math
import os
import random
import shutil
import threading
from dataclasses import dataclass, field

from . import cleanup as cln
from . import encoders
from . import ffmpeg
from . import filters_lib as fl
from . import overlays as ovl
from .models import CleanupProfile, OverlayItem, StylePreset, Adjust
from .planner import PlanItem, Seg

PART_VERSION = 4
AR = 48000  # 统一音频采样率
COLOR_SET = "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709"


def even(v):
    return max(2, int(v) // 2 * 2)


# ----------------------------------------------------------------------------
# 编码器状态（GPU 失败自动回退 CPU）
# ----------------------------------------------------------------------------
class EncoderState:
    def __init__(self, pref="auto", log=None):
        self.lock = threading.Lock()
        self.log = log
        self.enc = encoders.resolve(pref)
        self.gpu_sem = threading.Semaphore(3)

    @property
    def is_gpu(self):
        return self.enc != "cpu"

    def fallback(self, reason=""):
        with self.lock:
            if self.enc != "cpu":
                if self.log:
                    self.log(f"! 显卡编码失败，已自动切换为 CPU 编码。{reason}", level="warn")
                self.enc = "cpu"


@dataclass
class RenderCtx:
    W: int
    H: int
    fps: int
    fit: str
    keep_audio: bool
    infos: dict
    cleanup_map: dict
    parts_dir: str
    enc: EncoderState
    threads: int = 4
    cancel: object = None
    log: object = None
    stall_timeout: int = 240


# ----------------------------------------------------------------------------
# 滤镜链构造
# ----------------------------------------------------------------------------
def _in_matrix(info):
    cs = (info.extra or {}).get("color_space", "") if info else ""
    if cs in ("bt709",):
        return "bt709"
    if cs in ("bt470bg", "smpte170m", "bt601"):
        return "bt601"
    if cs.startswith("bt2020"):
        return "bt2020"
    # 未标注：高清按 709、标清按 601（与主流播放器一致）
    if info and info.height and min(info.width, info.height) >= 700:
        return "bt709"
    return "bt601"


def _in_range(info):
    r = (info.extra or {}).get("color_range", "") if info else ""
    return "full" if r in ("pc", "jpeg", "full") else "tv"


def _scale_opts(info):
    return f"in_color_matrix={_in_matrix(info)}:out_color_matrix=bt709:in_range={_in_range(info)}:out_range=tv"


def fit_graph(inl, outl, info, W, H, fit, uid, src_w=None, src_h=None):
    sw = src_w or (info.width if info else W)
    sh = src_h or (info.height if info else H)
    if fit == "smart":
        diff = abs(sw / max(1, sh) - W / H) / (W / H)
        fit = "crop" if diff <= 0.18 else "blur"
    so = _scale_opts(info)
    if fit == "crop":
        return f"{inl}scale={W}:{H}:force_original_aspect_ratio=increase:flags=bicubic:{so},crop={W}:{H},setsar=1{outl}"
    if fit == "pad":
        return (f"{inl}scale={W}:{H}:force_original_aspect_ratio=decrease:flags=bicubic:{so},"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1{outl}")
    if fit == "stretch":
        return f"{inl}scale={W}:{H}:flags=bicubic:{so},setsar=1{outl}"
    # blur
    bw, bh = even(W // 8), even(H // 8)
    return (f"{inl}split=2[{uid}f][{uid}b];"
            f"[{uid}b]scale={bw}:{bh}:force_original_aspect_ratio=increase:{so},crop={bw}:{bh},boxblur=10:3,"
            f"scale={W}:{H}:flags=bilinear,eq=brightness=-0.04,setsar=1[{uid}bb];"
            f"[{uid}f]scale={W}:{H}:force_original_aspect_ratio=decrease:flags=bicubic:{so},setsar=1[{uid}ff];"
            f"[{uid}bb][{uid}ff]overlay=(W-w)/2:(H-h)/2{outl}")


HDR_CHAIN = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
             "zscale=t=bt709:m=bt709:r=tv,format=yuv420p")


def effect_chain(effect, W, H):
    if effect == "flash_white":
        return "fade=t=in:st=0:d=0.25:color=white"
    if effect == "flash_black":
        return "fade=t=in:st=0:d=0.3:color=black"
    if effect == "zoom_punch":
        Z = "if(lt(t,0.3),1.15-0.15*t/0.3,1)"
        return (f"scale=w='trunc({W}*{Z}/2)*2':h='trunc({H}*{Z}/2)*2':eval=frame,"
                f"crop={W}:{H}:x='(iw-{W})/2':y='(ih-{H})/2'")
    if effect == "shake":
        W2, H2 = even(W * 1.04), even(H * 1.04)
        A = (W2 - W) / 2 * 0.9
        return (f"scale={W2}:{H2},crop={W}:{H}:x='{(W2 - W) / 2:.1f}+if(lt(t,0.4),{A:.1f}*sin(t*70),0)'"
                f":y='{(H2 - H) / 2:.1f}+if(lt(t,0.4),{A:.1f}*cos(t*55),0)'")
    if effect == "blur_in":
        return "gblur=sigma=18:enable='lt(t,0.12)',gblur=sigma=7:enable='between(t,0.12,0.28)'"
    return ""


def _atempo_chain(speed):
    parts = []
    s = speed
    while s > 2.0:
        parts.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        parts.append("atempo=0.5")
        s /= 0.5
    parts.append(f"atempo={s:.5f}")
    return ",".join(parts)


def _nsamples(frames, fps):
    return int(round(frames * AR / float(fps)))


def source_video_graph(inl, outl, rng_spec, ctx: RenderCtx, uid, workdir, effect=""):
    """单个源区间 → 画布规格、精确帧数。"""
    src = rng_spec["src"]
    info = ctx.infos.get(src)
    frames = int(rng_spec["frames"])
    speed = float(rng_spec.get("speed") or 1.0)
    fps = ctx.fps
    W, H = ctx.W, ctx.H
    parts = []
    cur = inl
    is_image = info is not None and info.kind == "image"
    if info is not None and info.hdr and not is_image:
        parts.append(f"{cur}{HDR_CHAIN}[{uid}hdr]")
        cur = f"[{uid}hdr]"
    # 去水印（源分辨率）
    prof = None
    cm = ctx.cleanup_map.get(src) if ctx.cleanup_map else None
    if cm:
        prof = CleanupProfile.from_dict(cm)
        if prof.is_empty():
            prof = None
    sw, sh = (info.width, info.height) if info else (W, H)
    if prof is not None and not is_image:
        seg_start = float(rng_spec.get("start") or 0)
        seg_dur = frames / fps * speed
        g, sw, sh = cln.cleanup_graph(cur, f"[{uid}cl]", prof, info.width, info.height, uid=f"{uid}c",
                                      seg_start=seg_start, seg_dur=seg_dur, workdir=workdir)
        parts.append(g)
        cur = f"[{uid}cl]"
    elif prof is not None and is_image:
        g, sw, sh = cln.cleanup_graph(cur, f"[{uid}cl]", prof, info.width, info.height, uid=f"{uid}c",
                                      workdir=workdir)
        parts.append(g)
        cur = f"[{uid}cl]"
    # 适配画布
    if is_image:
        # 图片：放大到 2 倍画布后做缓慢推拉（Ken Burns），减少抖动
        parts.append(fit_graph(cur, f"[{uid}fit]", info, W * 2 // 2 * 2, H * 2 // 2 * 2,
                               ctx.fit, f"{uid}f", src_w=sw, src_h=sh))
        kb_from = int(rng_spec.get("kb_from", 0))
        kb_total = max(1, int(rng_spec.get("kb_total", frames)))
        zoom_in = (int(rng_spec.get("kb_dir", 0)) % 2) == 0
        z0, z1 = (1.0, 1.08) if zoom_in else (1.08, 1.0)
        zexpr = f"{z0}+({z1 - z0:.3f})*(on+{kb_from})/{kb_total}"
        parts.append(f"[{uid}fit]zoompan=z='{zexpr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                     f":d=1:s={W}x{H}:fps={fps},setsar=1[{uid}kb]")
        cur = f"[{uid}kb]"
        post = []
    else:
        parts.append(fit_graph(cur, f"[{uid}fit]", info, W, H, ctx.fit, f"{uid}f", src_w=sw, src_h=sh))
        cur = f"[{uid}fit]"
        post = []
        if abs(speed - 1.0) > 1e-3:
            post.append(f"setpts=(PTS-STARTPTS)/{speed:.5f}")
        else:
            post.append("setpts=PTS-STARTPTS")
    post.append(f"fps={fps}")
    post.append("tpad=stop_mode=clone:stop=-1")
    post.append(f"trim=end_frame={frames}")
    post.append(f"setpts=N/({fps}*TB)")
    ef = effect_chain(effect, W, H) if effect else ""
    if ef:
        post.append(ef)
    post.append("format=yuv420p")
    post.append(COLOR_SET)
    parts.append(f"{cur}{','.join(post)}{outl}")
    return ";".join(parts)


def source_audio_graph(inl, outl, rng_spec, ctx: RenderCtx, has_audio):
    frames = int(rng_spec["frames"])
    speed = float(rng_spec.get("speed") or 1.0)
    ns = _nsamples(frames, ctx.fps)
    dur = frames / float(ctx.fps)
    if not has_audio:
        return f"{inl}aformat=sample_fmts=s16:channel_layouts=stereo,atrim=end_sample={ns}{outl}"
    chain = ["asetpts=PTS-STARTPTS"]
    if abs(speed - 1.0) > 1e-3:
        chain.append(_atempo_chain(speed))
    chain += [f"aresample={AR}", "aformat=sample_fmts=s16:channel_layouts=stereo", "afade=t=in:d=0.012",
              "apad", f"atrim=end_sample={ns}"]
    if dur > 0.05:
        chain.append(f"afade=t=out:st={max(0.0, dur - 0.015):.4f}:d=0.015")
    return f"{inl}{','.join(chain)}{outl}"


def _input_args(rng_spec, ctx: RenderCtx):
    """单个源区间的输入参数，返回 (args, has_audio)。"""
    src = rng_spec["src"]
    info = ctx.infos.get(src)
    frames = int(rng_spec["frames"])
    speed = float(rng_spec.get("speed") or 1.0)
    out_dur = frames / float(ctx.fps)
    if info is not None and info.kind == "image":
        return ["-loop", "1", "-framerate", str(ctx.fps), "-t", f"{out_dur + 0.5:.3f}", "-i", src], False
    start = max(0.0, float(rng_spec.get("start") or 0))
    need = out_dur * speed + 0.3
    args = []
    if start > 0.001:
        args += ["-ss", f"{start:.3f}"]
    args += ["-t", f"{need:.3f}", "-i", src]
    has_audio = bool(info and info.has_audio and ctx.keep_audio)
    return args, has_audio


# ----------------------------------------------------------------------------
# 片段（body / junction）
# ----------------------------------------------------------------------------
def item_parts(item: PlanItem, ctx: RenderCtx):
    """把成片计划拆成有序的片段规格列表。"""
    fps = ctx.fps
    segs = item.seg_objs()
    T = int(item.trans_frames or 0)
    trans = list(item.transitions or [])
    parts = []
    for i, s in enumerate(segs):
        left = T if (i > 0 and i - 1 < len(trans) and trans[i - 1]) else 0
        right = T if (i < len(segs) - 1 and i < len(trans) and trans[i]) else 0
        body_frames = s.frames - left - right
        kb_dir = (item.seed + i) % 2
        base = {"src": s.src, "speed": s.speed, "kb_total": s.frames, "kb_dir": kb_dir}
        if body_frames > 0:
            parts.append({"kind": "body", "a": dict(base, start=s.start + left / fps * s.speed, frames=body_frames,
                                                    kb_from=left),
                          "effect": s.effect if left == 0 and i > 0 else ""})
        if right:
            n = segs[i + 1]
            a = dict(base, start=s.start + (s.frames - right) / fps * s.speed, frames=right,
                     kb_from=s.frames - right)
            b = {"src": n.src, "speed": n.speed, "start": n.start, "frames": right, "kb_from": 0,
                 "kb_total": n.frames, "kb_dir": (item.seed + i + 1) % 2}
            parts.append({"kind": "junction", "a": a, "b": b, "transition": trans[i]})
    return parts


def part_key(spec, ctx: RenderCtx):
    srcs = [spec["a"]["src"]] + ([spec["b"]["src"]] if spec.get("b") else [])
    sigs = []
    for s in srcs:
        info = ctx.infos.get(s)
        sigs.append([s, info.sig if info else "", ctx.cleanup_map.get(s) if ctx.cleanup_map else None])
    payload = {"v": PART_VERSION, "spec": spec, "sigs": sigs, "W": ctx.W, "H": ctx.H, "fps": ctx.fps,
               "fit": ctx.fit, "audio": ctx.keep_audio}
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


_part_locks = {}
_part_locks_guard = threading.Lock()


def _lock_for(key):
    with _part_locks_guard:
        lk = _part_locks.get(key)
        if lk is None:
            lk = threading.Lock()
            _part_locks[key] = lk
        return lk


def render_part(spec, ctx: RenderCtx, on_progress=None):
    """渲染一个片段，返回 mov 路径（命中缓存直接返回；多条成片共用的片段只渲染一次）。"""
    key = part_key(spec, ctx)
    with _lock_for(key):
        return _render_part_locked(spec, ctx, key, on_progress)


def _render_part_locked(spec, ctx: RenderCtx, key, on_progress=None):
    out = os.path.join(ctx.parts_dir, key + ".mov")
    if os.path.isfile(out) and os.path.getsize(out) > 1024:
        if on_progress:
            on_progress(1.0)
        return out
    os.makedirs(ctx.parts_dir, exist_ok=True)
    workdir = os.path.join(ctx.parts_dir, "w_" + key)
    os.makedirs(workdir, exist_ok=True)
    tmp = os.path.join(ctx.parts_dir, key + ".tmp.mov")
    args = []
    graph = []
    a = spec["a"]
    a_in, a_has_audio = _input_args(a, ctx)
    args += a_in
    idx_null = 1
    if spec["kind"] == "junction":
        b = spec["b"]
        b_in, b_has_audio = _input_args(b, ctx)
        args += b_in
        idx_null = 2
    args += ["-f", "lavfi", "-i", f"anullsrc=r={AR}:cl=stereo"]
    frames = int(a["frames"])
    if spec["kind"] == "body":
        graph.append(source_video_graph("[0:v]", "[v]", a, ctx, "a", workdir, effect=spec.get("effect", "")))
        ain = "[0:a]" if a_has_audio else f"[{idx_null}:a]"
        graph.append(source_audio_graph(ain, "[a]", a, ctx, a_has_audio))
    else:
        graph.append(source_video_graph("[0:v]", "[va]", a, ctx, "a", workdir))
        graph.append(source_video_graph("[1:v]", "[vb]", spec["b"], ctx, "b", workdir))
        T = frames / float(ctx.fps)
        tr = spec.get("transition") or "fade"
        if tr not in fl.XFADE_TYPES:
            tr = "fade"
        graph.append(f"[va][vb]xfade=transition={tr}:duration={T:.4f}:offset=0,trim=end_frame={frames},"
                     f"setpts=N/({ctx.fps}*TB),format=yuv420p,{COLOR_SET}[v]")
        ain_a = "[0:a]" if a_has_audio else f"[{idx_null}:a]"
        ain_b = "[1:a]" if b_has_audio else f"[{idx_null}:a]"
        if not a_has_audio and not b_has_audio:
            graph.append(f"[{idx_null}:a]aformat=sample_fmts=s16:channel_layouts=stereo,"
                         f"atrim=end_sample={_nsamples(frames, ctx.fps)}[a]")
        else:
            graph.append(source_audio_graph(ain_a, "[aa]", a, ctx, a_has_audio))
            graph.append(source_audio_graph(ain_b, "[ab]", spec["b"], ctx, b_has_audio))
            graph.append(f"[aa][ab]acrossfade=d={T:.4f}:c1=tri:c2=tri,apad,"
                         f"atrim=end_sample={_nsamples(frames, ctx.fps)}[a]")
    script = os.path.join(workdir, "graph.txt")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(";\n".join(graph))
    # 片段很短，显卡编码器每次初始化要 1~2 秒，反而更慢；片段统一用 CPU(x264)，显卡留给最终合成
    venc_name = "cpu"
    use_gpu = False
    venc = encoders.video_args(venc_name, purpose="intermediate", width=ctx.W, height=ctx.H, fps=ctx.fps,
                               threads=ctx.threads)
    cmd = args + ffmpeg.filter_script_args(script) + ["-map", "[v]", "-map", "[a]"] + venc + \
        encoders.COLOR_ARGS + ["-c:a", "pcm_s16le", "-ar", str(AR), "-frames:v", str(frames), tmp]
    dur = frames / float(ctx.fps)

    def _run(c):
        return ffmpeg.run_ffmpeg(c, duration=dur, on_progress=on_progress, cancel=ctx.cancel, cwd=workdir,
                                 stall_timeout=ctx.stall_timeout, log=ctx.log, label="片段")
    try:
        if use_gpu:
            with ctx.enc.gpu_sem:
                _run(cmd)
        else:
            _run(cmd)
    except ffmpeg.FFmpegError as e:
        if use_gpu and e.category in ("编码器失败", "其他"):
            ctx.enc.fallback(str(e)[:120])
            venc = encoders.video_args("cpu", purpose="intermediate", width=ctx.W, height=ctx.H, fps=ctx.fps,
                                       threads=ctx.threads)
            cmd = args + ffmpeg.filter_script_args(script) + ["-map", "[v]", "-map", "[a]"] + venc + \
                encoders.COLOR_ARGS + ["-c:a", "pcm_s16le", "-ar", str(AR), "-frames:v", str(frames), tmp]
            _run(cmd)
        else:
            raise
    os.replace(tmp, out)
    shutil.rmtree(workdir, ignore_errors=True)
    return out


# ----------------------------------------------------------------------------
# 成片合成
# ----------------------------------------------------------------------------
def style_graph(inl, outl, filter_name, intensity, adjust: Adjust, variant_level, mirror, W, H, rng, uid="st"):
    """滤镜 + 调节 + 画面变体。返回滤镜图（可能为 null 直通）。"""
    pieces = []
    cur = inl
    if filter_name and filter_name in fl.FILTERS:
        pieces.append(fl.filter_graph(cur, f"[{uid}1]", filter_name, intensity, uid=f"{uid}x"))
        cur = f"[{uid}1]"
    tail = []
    ac = fl.adjust_chain(adjust) if adjust else ""
    if ac:
        tail.append(ac)
    vc = fl.variant_chain(int(variant_level or 0), rng, W, H, mirror=mirror)
    if vc:
        tail.append(vc)
    if tail:
        pieces.append(f"{cur}{','.join(tail)}{outl}")
    elif pieces:
        last = pieces[-1]
        pieces[-1] = last[: last.rfind(cur)] + outl
    else:
        pieces.append(f"{inl}null{outl}")
    return ";".join(pieces)


def build_item_overlays(item: PlanItem, cfg, W, H):
    """成片包装：模板叠加层 + 标题 + 产品角标。"""
    items = []
    st = cfg.style_obj()
    for o in st.overlay_objs():
        items.append(o)
    if item.title:
        style = dict(cfg.title_style or {})
        if not style:
            from . import textstyle
            style = textstyle.preset_style("白字黑边", size=78).to_dict()
        cy = {"top": 0.14, "center": 0.5, "bottom": 0.8}.get(cfg.title_pos, 0.14)
        t1 = float(cfg.title_duration or 0)
        items.append(OverlayItem(kind="text", text=item.title, style=style, cx=0.5, cy=cy, w=0,
                                 t0=0.0, t1=t1 if t1 > 0 else 0.0, anim_in="pop", name="标题"))
    if item.badge:
        rng = random.Random(item.seed + 17)
        items.append(OverlayItem(kind="image", src=item.badge, w=round(rng.uniform(0.18, 0.28), 3),
                                 corner_random=True, anim_in=cfg.badge_anim or "fade", anim_loop="none",
                                 remove_bg=ovl.detect_plain_bg(item.badge), name="角标"))
    return items


def compose(item: PlanItem, part_files, out_path, cfg, ctx: RenderCtx, on_progress=None, job_dir=None):
    W, H, fps = ctx.W, ctx.H, ctx.fps
    D = item.duration
    job_dir = job_dir or os.path.join(os.path.dirname(ctx.parts_dir), "jobs", f"{item.index:04d}")
    os.makedirs(job_dir, exist_ok=True)
    lst = os.path.join(job_dir, "list.txt")
    with open(lst, "w", encoding="utf-8") as fh:
        for p in part_files:
            fh.write(ffmpeg.concat_list_line(p))
    rng = random.Random(item.seed + 101)
    args = ["-reinit_filter", "0", "-f", "concat", "-safe", "0", "-i", lst]
    graph = []
    next_idx = 1
    bgm_idx = None
    if item.bgm and os.path.isfile(item.bgm):
        if item.bgm_offset and item.bgm_offset > 0:
            args += ["-ss", f"{item.bgm_offset:.2f}"]
        args += ["-stream_loop", "-1", "-i", item.bgm]
        bgm_idx = next_idx
        next_idx += 1
    # 视频：风格
    st = cfg.style_obj()
    filter_name = item.filter_name or st.filter_name
    intensity = cfg.filter_intensity if item.filter_name else st.filter_intensity
    graph.append(style_graph("[0:v]", "[vs]", filter_name, intensity, st.adjust_obj(), item.variant,
                             cfg.mirror, W, H, rng))
    # 叠加层
    from datetime import datetime
    first = next((s for s in item.seg_objs() if s.kind in ("clip", "image")), None)
    tctx = {"序号": f"{item.index:03d}", "日期": datetime.now().strftime("%Y-%m-%d"), "任务名": cfg.name,
            "素材名": os.path.splitext(os.path.basename(first.src))[0] if first else "", "时长": f"{int(D)}秒"}
    ov_items = build_item_overlays(item, cfg, W, H)
    prepared = ovl.prepare(ov_items, W, H, D, job_dir, ctx=tctx, rng=random.Random(item.seed + 23))
    in_args, ov_graph, vlabel = ovl.build_graph(prepared, "[vs]", next_idx, W, H, fps)
    args += in_args
    next_idx += len(prepared)
    graph += ov_graph
    graph.append(f"{vlabel}format=yuv420p,{COLOR_SET}[vout]")
    # 音频
    mode = cfg.keep_audio
    ov = 1.0 if mode == "keep" else (0.3 if mode == "lower" else 0.0)
    ov *= float(cfg.orig_volume if cfg.orig_volume is not None else 1.0)
    a_chain = [f"[0:a]aformat=sample_fmts=fltp:sample_rates={AR}:channel_layouts=stereo,volume={ov:.3f}"]
    if bgm_idx is not None:
        graph.append(",".join(a_chain) + "[a0]")
        bv = float(cfg.bgm_volume if cfg.bgm_volume is not None else 0.35)
        graph.append(f"[{bgm_idx}:a]aformat=sample_fmts=fltp:sample_rates={AR}:channel_layouts=stereo,"
                     f"volume={bv:.3f},afade=t=in:d=0.8[bg]")
        graph.append("[a0][bg]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[am]")
        cur = "[am]"
    else:
        graph.append(",".join(a_chain) + "[am]")
        cur = "[am]"
    post = []
    if cfg.loudnorm and (ov > 0 or bgm_idx is not None):
        post.append("loudnorm=I=-16:TP=-1.5:LRA=11")
        post.append(f"aresample={AR}")
    if cfg.audio_fade and D > 3:
        post.append("afade=t=in:d=0.05")
        post.append(f"afade=t=out:st={max(0.0, D - 1.2):.3f}:d=1.2")
    post.append(f"atrim=end={D:.4f}")
    graph.append(f"{cur}{','.join(post)}[aout]")
    script = os.path.join(job_dir, "compose.txt")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(";\n".join(graph))
    tmp = out_path + ".tmp.mp4"
    meta = []
    if cfg.metadata:
        meta = ["-metadata", f"title={item.name}", "-metadata",
                f"comment=批量视频混剪工具 {datetime.now().strftime('%Y-%m-%d %H:%M')}"]

    def build_cmd(enc_name):
        venc = encoders.video_args(enc_name, cfg.quality, W, H, fps, threads=None)
        return args + ffmpeg.filter_script_args(script) + ["-map", "[vout]", "-map", "[aout]"] + venc + \
            encoders.COLOR_ARGS + ["-c:a", "aac", "-b:a", "192k", "-ar", str(AR), "-ac", "2",
                                   "-t", f"{D:.3f}", "-movflags", "+faststart"] + meta + [tmp]

    enc_name = ctx.enc.enc

    def _run(c):
        return ffmpeg.run_ffmpeg(c, duration=D, on_progress=on_progress, cancel=ctx.cancel, cwd=job_dir,
                                 stall_timeout=ctx.stall_timeout, log=ctx.log, label="合成")
    try:
        if enc_name != "cpu":
            with ctx.enc.gpu_sem:
                _run(build_cmd(enc_name))
        else:
            _run(build_cmd("cpu"))
    except ffmpeg.FFmpegError as e:
        if enc_name != "cpu" and e.category in ("编码器失败", "其他"):
            ctx.enc.fallback(str(e)[:120])
            _run(build_cmd("cpu"))
        else:
            raise
    os.replace(tmp, out_path)
    return out_path


# ----------------------------------------------------------------------------
# 成片复查后删除重复段
# ----------------------------------------------------------------------------
def output_boundaries(item: PlanItem, fps):
    """成片时间轴上各片段的起止时刻（秒），用于把剪切点对齐到片段边界。"""
    segs = item.seg_objs()
    T = int(item.trans_frames or 0)
    trans = list(item.transitions or [])
    t = 0
    out = {0.0}
    for i, s in enumerate(segs):
        end = t + s.frames
        out.add(round(end / float(fps), 3))
        if i < len(segs) - 1:
            t = end - (T if (i < len(trans) and trans[i]) else 0)
            out.add(round(t / float(fps), 3))
    return sorted(out)


def output_segments(item: PlanItem, fps):
    """成片时间轴上每个片段的 (起, 止, Seg)（秒，含转场重叠），用于把成片时刻换算回素材时刻。"""
    segs = item.seg_objs()
    T = int(item.trans_frames or 0)
    trans = list(item.transitions or [])
    out = []
    t = 0
    for i, s in enumerate(segs):
        out.append((t / float(fps), (t + s.frames) / float(fps), s))
        t = t + s.frames - (T if (i < len(trans) and trans[i]) else 0)
    return out


def remove_ranges(video, cuts, cfg, ctx: RenderCtx, duration):
    """从成片中删除若干时间段（重新编码一次），返回新时长。"""
    keep = []
    cur = 0.0
    for a, b in sorted(cuts):
        if a - cur >= 0.2:
            keep.append((cur, a))
        cur = max(cur, b)
    if duration - cur >= 0.2:
        keep.append((cur, duration))
    if not keep:
        return duration
    K = len(keep)
    g = [f"[0:v]split={K}" + "".join(f"[sv{k}]" for k in range(K)),
         f"[0:a]asplit={K}" + "".join(f"[sa{k}]" for k in range(K))]
    for k, (a, b) in enumerate(keep):
        d = b - a
        g.append(f"[sv{k}]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{k}]")
        g.append(f"[sa{k}]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS,afade=t=in:d=0.02,"
                 f"afade=t=out:st={max(0.0, d - 0.03):.3f}:d=0.03[a{k}]")
    g.append("".join(f"[v{k}][a{k}]" for k in range(K)) + f"concat=n={K}:v=1:a=1[cv][ca]")
    g.append(f"[cv]format=yuv420p,{COLOR_SET}[vout]")
    work = os.path.dirname(os.path.abspath(video))
    script = video + ".cut.txt"
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(";\n".join(g))
    tmp = video + ".cut.mp4"
    new_dur = sum(b - a for a, b in keep)

    def build(enc_name):
        return ["-i", video] + ffmpeg.filter_script_args(script) + ["-map", "[vout]", "-map", "[ca]"] + \
            encoders.video_args(enc_name, cfg.quality, ctx.W, ctx.H, ctx.fps) + encoders.COLOR_ARGS + \
            ["-c:a", "aac", "-b:a", "192k", "-ar", str(AR), "-ac", "2", "-map_metadata", "0",
             "-movflags", "+faststart", tmp]
    try:
        enc_name = ctx.enc.enc
        try:
            ffmpeg.run_ffmpeg(build(enc_name), duration=new_dur, cancel=ctx.cancel, cwd=work,
                              stall_timeout=ctx.stall_timeout, log=ctx.log, label="删除重复段")
        except ffmpeg.FFmpegError as e:
            if enc_name != "cpu" and e.category in ("编码器失败", "其他"):
                ctx.enc.fallback(str(e)[:120])
                ffmpeg.run_ffmpeg(build("cpu"), duration=new_dur, cancel=ctx.cancel, cwd=work,
                                  stall_timeout=ctx.stall_timeout, log=ctx.log, label="删除重复段(CPU)")
            else:
                raise
        os.replace(tmp, video)
    finally:
        for f in (script, tmp):
            try:
                os.remove(f)
            except OSError:
                pass
    return round(new_dur, 3)


# ----------------------------------------------------------------------------
# 封面（自动挑选清晰、亮度适中、色彩丰富的一帧）
# ----------------------------------------------------------------------------
def pick_cover_time(video, duration, n=12, min_t=0.0):
    import numpy as np
    w, h = 160, 284
    if duration <= 0.5:
        return 0.0
    try:
        p = ffmpeg.run_capture([ffmpeg.ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", video,
                                "-vf", f"fps={n / duration:.5f},scale={w}:{h}:force_original_aspect_ratio=disable",
                                "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], timeout=120, text=False)
        raw = p.stdout or b""
    except Exception:
        return duration * 0.3
    k = len(raw) // (w * h * 3)
    if k <= 0:
        return duration * 0.3
    arr = np.frombuffer(raw[: k * w * h * 3], dtype=np.uint8).reshape(k, h, w, 3).astype(np.float32)
    best, bt = -1e9, duration * 0.3
    for i in range(k):
        f = arr[i]
        g = f.mean(axis=2)
        lap = np.abs(4 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]).mean()
        bright = g.mean()
        sat = (f.max(axis=2) - f.min(axis=2)).mean()
        score = lap * 1.0 + sat * 0.15 - abs(bright - 125) * 0.12
        t = (i + 0.5) * duration / k
        if t < 0.3 or t > duration - 0.3:
            score -= 5
        if min_t and t < min_t:
            score -= 1000
        if score > best:
            best, bt = score, t
    return min(max(0.0, bt), max(0.0, duration - 0.1))


def make_cover(video, duration, out_jpg, title="", title_style=None, W=1080, min_t=0.0):
    t = pick_cover_time(video, duration, min_t=min_t if min_t < duration - 0.5 else 0.0)
    tmp = out_jpg + ".tmp.jpg"
    ffmpeg.run_capture([ffmpeg.ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                        "-ss", f"{t:.3f}", "-i", video, "-frames:v", "1", "-q:v", "2", tmp], timeout=60)
    if not os.path.isfile(tmp):
        return None
    if title:
        try:
            from PIL import Image
            from . import textstyle
            im = Image.open(tmp).convert("RGBA")
            st = textstyle.TextStyle.from_dict(title_style) if title_style else textstyle.preset_style(
                "黄字黑边", size=96)
            st.size = int(max(st.size, 90))
            txt = textstyle.render_text(title, st, canvas_w=im.width)
            if txt.width > im.width * 0.92:
                r = im.width * 0.92 / txt.width
                txt = txt.resize((int(txt.width * r), int(txt.height * r)))
            im.alpha_composite(txt, ((im.width - txt.width) // 2, int(im.height * 0.16)))
            im.convert("RGB").save(tmp, quality=92)
        except Exception:
            pass
    os.replace(tmp, out_jpg)
    return out_jpg
