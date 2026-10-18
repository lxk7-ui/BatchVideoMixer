"""覆盖测试：全部 23 种叠化转场 + 5 种切换特效 + 全部贴纸动画组合。"""
import os, sys, glob, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from mixer import probe, render as rd, filters_lib as fl, ffmpeg, overlays as ovl
from mixer.models import MixConfig, OverlayItem, StylePreset, ANIM_IN, ANIM_LOOP
from mixer.planner import PlanItem
TM = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_work", "testmedia"))
OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_work", "out_anim")); os.makedirs(OUT, exist_ok=True)
A = os.path.join(TM, "A_横屏_水印.mp4"); B = os.path.join(TM, "B_竖屏_无音轨.mp4"); P = os.path.join(TM, "photo_landscape.jpg")
infos = {p: probe.probe(p) for p in (A, B, P)}
enc = rd.EncoderState("cpu")
ctx = rd.RenderCtx(W=360, H=640, fps=25, fit="blur", keep_audio=True, infos=infos, cleanup_map={},
                   parts_dir=os.path.join(OUT, "parts"), enc=enc, threads=4)
bad = []
t = time.time()
for i, tr in enumerate(fl.XFADE_TYPES):
    a = {"src": A if i % 2 else P, "start": 1.0, "frames": 12, "speed": 1.0, "kb_from": 0, "kb_total": 50, "kb_dir": 0}
    b = {"src": B if i % 3 else A, "start": 2.0, "frames": 12, "speed": 1.1, "kb_from": 0, "kb_total": 50, "kb_dir": 1}
    try:
        rd.render_part({"kind": "junction", "a": a, "b": b, "transition": tr}, ctx)
    except Exception as e:
        bad.append(("xfade", tr, str(e)[:200]))
for ef in fl.CUT_EFFECTS:
    try:
        rd.render_part({"kind": "body", "a": {"src": A, "start": 3.0, "frames": 30, "speed": 1.0}, "effect": ef}, ctx)
    except Exception as e:
        bad.append(("effect", ef, str(e)[:200]))
print("parts", f"{time.time()-t:.1f}s", "bad:", bad)
# 贴纸动画：每种入场 × 循环组合放在一条成片里
stk = sorted(glob.glob(os.path.join(os.path.dirname(__file__), "..", "src", "assets", "stickers", "电商标签", "*.png")))
items = []
k = 0
for ain in ANIM_IN:
    for al in ANIM_LOOP:
        items.append(OverlayItem(kind="sticker", src=stk[k % len(stk)], cx=0.1 + 0.11 * (k % 8), cy=0.08 + 0.12 * (k // 8) % 0.96,
                                 w=0.1, anim_in=ain, anim_loop=al, anim_out="fade" if k % 2 else "none",
                                 t0=0.2 * (k % 3), t1=-0.5 if k % 4 == 0 else 0, rotation=15 if k % 5 == 0 else 0,
                                 opacity=0.8 if k % 6 == 0 else 1.0).to_dict())
        k += 1
items.append(OverlayItem(kind="text", text="第{序号}条", style={"font": "得意黑", "size": 60}, cx=0.5, cy=0.95, anim_in="slide_down").to_dict())
print("overlay items", len(items))
cfg = MixConfig(name="动画", style=StylePreset(filter_name="港风", filter_intensity=0.6, adjust={"sharpen": 0.2, "vignette": 0.3},
                overlays=items).to_dict(), keep_audio="keep", loudnorm=True, variant_level=3, mirror=True)
item = PlanItem(index=1, name="anim", segments=[{"src": A, "start": 0.0, "frames": 125, "speed": 1.0, "kind": "clip", "effect": ""}],
                transitions=[], trans_frames=0, seed=5, fps=25, duration=5.0)
parts = [rd.render_part(p, ctx) for p in rd.item_parts(item, ctx)]
out = os.path.join(OUT, "anim.mp4")
try:
    rd.compose(item, parts, out, cfg, ctx)
    i = probe.probe(out, use_cache=False); print("compose ok", i.duration, i.res_text(), i.has_audio)
except Exception as e:
    print("compose FAIL", e, getattr(e, "stderr", "")[-2000:])
for tt in (0.1, 0.5, 2.5, 4.8):
    ffmpeg.run_capture([ffmpeg.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", "-ss", str(tt), "-i", out, "-frames:v", "1", os.path.join(OUT, f"f_{tt}.png")])
print("ALL", "PASS" if not bad else "FAIL")
