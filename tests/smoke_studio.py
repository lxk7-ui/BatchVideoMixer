import os, sys, time, glob
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from mixer import studio, probe, cleanup as cln, filters_lib as fl, textstyle
from mixer.models import CleanupProfile, Region, StylePreset, OverlayItem
TM = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_work", "testmedia"))
OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_work", "out_studio")); os.makedirs(OUT, exist_ok=True)
A = os.path.join(TM, "A_横屏_水印.mp4"); P = os.path.join(TM, "photo_landscape.jpg")
STK = glob.glob(os.path.join(os.path.dirname(__file__), "..", "src", "assets", "stickers", "电商标签", "爆款.png"))[0]
prof = CleanupProfile(regions=[Region(x=1420/1920, y=30/1080, w=470/1920, h=85/1080).to_dict()], crop_b=0.05)
style = StylePreset(filter_name="青橙", filter_intensity=0.8, adjust={"sharpen": 0.3},
                    overlays=[OverlayItem(kind="sticker", src=STK, cx=0.85, cy=0.25, w=0.15, anim_loop="pulse").to_dict(),
                              OverlayItem(kind="text", text="{素材名}", style=textstyle.preset_style("黄字黑边").to_dict(), cx=0.5, cy=0.85).to_dict()])
t = time.time(); f = studio.preview_frame(A, 3.0, prof, style, max_w=960, out_png=os.path.join(OUT, "pv.png")); print("preview", f, f"{time.time()-t:.2f}s")
t = time.time(); raw = studio.raw_frame(A, 3.0, out_png=os.path.join(OUT, "raw.png")); print("raw", raw, f"{time.time()-t:.2f}s")
t = time.time(); th = studio.filter_thumbs(raw, list(fl.FILTERS.keys()), out_dir=OUT); print("thumbs", len(th), f"{time.time()-t:.2f}s", all(os.path.isfile(p) for p in th.values()))
t = time.time(); out = studio.export(A, os.path.join(OUT, "A_处理后.mp4"), prof, style, encoder="cpu", on_progress=lambda f: None); print("export", out, f"{time.time()-t:.2f}s")
i = probe.probe(out, use_cache=False); print(" ->", i.width, i.height, i.duration, i.has_audio)
t = time.time(); out2 = studio.export(P, os.path.join(OUT, "photo_处理后.jpg"), None, style); print("export img", out2, f"{time.time()-t:.2f}s")
info = probe.probe(A)
t = time.time(); det = cln.detect_static_overlays(info); print("detect", det, f"{time.time()-t:.2f}s")
t = time.time(); clip = studio.preview_clip(A, 2, 3, prof, style, out_path=os.path.join(OUT, "clip.mp4")); print("clip", clip, f"{time.time()-t:.2f}s")
