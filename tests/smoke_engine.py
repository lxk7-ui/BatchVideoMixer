"""端到端冒烟测试：真实 ffmpeg 跑通 片段混剪(限时)/整段拼接/去水印/滤镜/贴纸/标题/角标/BGM/封面/断点续跑。"""
import json, os, sys, time, glob
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from mixer import ffmpeg, probe
from mixer.models import MixConfig, CleanupProfile, Region, OverlayItem, StylePreset
from mixer.batch import BatchRunner
from mixer import textstyle

TM = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_work", "testmedia"))
OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_work", "out"))
os.makedirs(OUT, exist_ok=True)
A = os.path.join(TM, "A_横屏_水印.mp4"); B = os.path.join(TM, "B_竖屏_无音轨.mp4"); C = os.path.join(TM, "C_720p60_单声道.mp4")
D = os.path.join(TM, "D_竖屏短.mp4"); E = os.path.join(TM, "E 4比3 带空格.mp4"); P = os.path.join(TM, "photo_landscape.jpg")
BGM = os.path.join(TM, "bgm1.mp3"); PROD = os.path.join(TM, "product_white_bg.png")
STK = glob.glob(os.path.join(os.path.dirname(__file__), "..", "src", "assets", "stickers", "电商标签", "爆款.png"))[0]
EMO = glob.glob(os.path.join(os.path.dirname(__file__), "..", "src", "assets", "stickers", "表情_手势", "点赞.png"))[0]

def events(ev, d):
    if ev == "log": print("LOG", d["level"], d["msg"][:300])
    elif ev == "stage": print("STAGE", d["text"])
    elif ev == "item" and d["status"] in ("ok", "failed"): print("ITEM", d["index"], d["status"], d.get("error", ""))
    elif ev == "finished": print("FINISHED", d)

def check_outputs(dirpath, limit):
    files = sorted(glob.glob(os.path.join(dirpath, "成片", "*.mp4")))
    ok = True
    for f in files:
        info = probe.probe(f, use_cache=False)
        p = ffmpeg.run_capture([ffmpeg.ffprobe_bin(), "-v", "error", "-show_entries", "stream=codec_type,duration,nb_frames",
                                "-of", "json", f])
        st = json.loads(p.stdout)["streams"]
        print(f"  {os.path.basename(f)}  {info.width}x{info.height} {info.fps:.2f}fps dur={info.duration:.3f}s audio={info.has_audio} streams={[(s['codec_type'], s.get('duration'), s.get('nb_frames')) for s in st]}")
        if limit and info.duration > limit + 0.05:
            print("  !!! 超过时长上限"); ok = False
        if not info.has_audio: print("  !!! 无音轨"); ok = False
    return files, ok

def main(which):
    t = time.time()
    style = StylePreset(overlays=[
        OverlayItem(kind="sticker", src=STK, cx=0.82, cy=0.3, w=0.28, anim_in="pop", anim_loop="pulse").to_dict(),
        OverlayItem(kind="sticker", src=EMO, cx=0.2, cy=0.72, w=0.2, anim_loop="bounce", t0=1.0, t1=-1.0).to_dict(),
        OverlayItem(kind="text", text="第{序号}条 · {日期}", style=textstyle.preset_style("红底白字", size=48).to_dict(),
                    cx=0.5, cy=0.93, w=0, anim_in="slide_up").to_dict(),
    ]).to_dict()
    clean = CleanupProfile(regions=[Region(x=1420/1920, y=30/1080, w=470/1920, h=85/1080, method="delogo").to_dict(),
                                    Region(x=60/1920, y=900/1080, w=220/1920, h=120/1080, method="mosaic", strength=20).to_dict()]).to_dict()
    if which == "segment":
        cfg = MixConfig(name="冒烟片段", materials=[A, B, C, D, E, P], openings=[D], bgms=[BGM], badges=[PROD],
                        count=3, mode="segment", limit_enabled=True, limit_minutes=0.5, limit_float=20, seg_min=1.5, seg_max=4,
                        transition_mode="mixed", transition_prob=80, canvas="720x1280", fit="smart", fps=30,
                        keep_audio="lower", filter_mode="random", titles=["爆款好物 限时抢购", "今日必入清单"],
                        title_style=textstyle.preset_style("综艺花字", size=80).to_dict(), style=style,
                        cleanup_map={A: clean}, speed_enabled=True, variant_level=1, output_root=OUT, cover=True,
                        encoder="cpu", concurrency=2)
        limit = 30
    else:
        cfg = MixConfig(name="冒烟整段", materials=[A, B, C, D, E], openings=[], bgms=[], count=4, mode="whole",
                        limit_enabled=True, limit_minutes=0.5, limit_float=10, clips_min=2, clips_max=3,
                        transition_mode="xfade", transition_prob=100, canvas="1080x1920", fit="blur", fps=25,
                        keep_audio="keep", output_root=OUT, cover=True, encoder=sys.argv[2] if len(sys.argv) > 2 else "auto",
                        concurrency=0)
        limit = 30
    r = BatchRunner(cfg, callback=events)
    res = r.run()
    print("RESULT", res, f"{time.time()-t:.1f}s")
    files, ok = check_outputs(res["dir"], limit)
    print("CHECK", "PASS" if ok and res["ok"] == cfg.count else "FAIL")

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "segment")
