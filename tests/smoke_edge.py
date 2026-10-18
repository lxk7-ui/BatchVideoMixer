import os, sys, glob, time, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from mixer import probe, ffmpeg
from mixer.models import MixConfig, CleanupProfile, Region
from mixer.batch import BatchRunner
ED = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_work", "edge"))
OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_work", "out_edge")); os.makedirs(OUT, exist_ok=True)
mats = sorted(glob.glob(os.path.join(ED, "*.*")))
def ev(e, d):
    if e == "log" and d["level"] in ("warn", "error"): print("LOG", d["msg"][:400])
    if e == "finished": print("FINISHED", {k: v for k, v in d.items() if k != "dir"})
def check(res, limit):
    bad = 0
    for f in sorted(glob.glob(os.path.join(res["dir"], "成片", "*.mp4"))):
        i = probe.probe(f, use_cache=False)
        flag = "" if (i.has_audio and (not limit or i.duration <= limit + 0.05)) else "  <<<< 问题"
        bad += bool(flag)
        print(f"   {os.path.basename(f)} {i.duration:.2f}s {i.res_text()} audio={i.has_audio}{flag}")
    return bad
mode = sys.argv[1]
t = time.time()
if mode == "seg":
    rot = [m for m in mats if "旋转" in m][0]
    cfg = MixConfig(name="边界片段", materials=mats, count=4, mode="segment", limit_minutes=0.4, limit_float=25,
                    seg_min=1.0, seg_max=3.0, transition_mode="mixed", transition_prob=70, canvas="1080x1920", fit="smart",
                    fps=30, output_root=OUT, cover=True, speed_enabled=True, filter_mode="random", variant_level=2,
                    cleanup_map={rot: CleanupProfile(regions=[Region(x=0.05, y=0.05, w=0.3, h=0.08, method="blur", t0=1, t1=4).to_dict()], crop_b=0.08).to_dict()})
    lim = 24
elif mode == "whole":
    cfg = MixConfig(name="边界整段", materials=mats, count=5, mode="whole", limit_enabled=False, clips_min=1, clips_max=4,
                    transition_mode="xfade", transition_prob=100, canvas="1280x720", fit="pad", fps=25, output_root=OUT,
                    keep_audio="keep")
    lim = 0
elif mode == "long":
    TM = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_work", "testmedia"))
    tm = [os.path.join(TM, f) for f in os.listdir(TM) if f.endswith(".mp4")]
    cfg = MixConfig(name="长成片", materials=tm, bgms=[os.path.join(TM, "bgm1.mp3")], count=1, mode="segment", limit_minutes=5,
                    limit_float=5, seg_min=1.5, seg_max=3, transition_mode="mixed", transition_prob=50, canvas="720x1280",
                    fps=30, output_root=OUT, allow_repeat=True, keep_audio="lower")
    lim = 300
r = BatchRunner(cfg, callback=ev, record_history=False)
res = r.run()
print("RESULT", res.get("ok"), res.get("failed"), f"{time.time()-t:.1f}s")
plan = json.load(open(os.path.join(res["dir"], "数据", "plan.json"), encoding="utf-8"))
print("   segments per item:", [len(i["segments"]) for i in plan["items"]], "durations:", [i["duration"] for i in plan["items"]])
print("CHECK", "PASS" if check(res, lim) == 0 and res.get("failed") == 0 else "FAIL")
