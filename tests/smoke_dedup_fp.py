"""误判测试：8 段不同画面的素材（互不重复），开启查重后不应有大量“误删”。"""
import os, sys, glob
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from mixer import probe, dedup
from mixer.models import MixConfig
from mixer.batch import BatchRunner
NM = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_work", "natural"))
OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_work", "out_dedup")); os.makedirs(OUT, exist_ok=True)
mats = sorted(glob.glob(os.path.join(NM, "*.mp4")))
idx = dedup.analyze_library([probe.probe(m) for m in mats], "standard", log=print)
print("runs:", [(os.path.basename(a), a0, a1, os.path.basename(b), b0, b1) for a, a0, a1, b, b0, b1 in idx.runs])
print("static:", {os.path.basename(k): round(v, 2) for k, v in idx.static_ratio.items()})
cuts = []
def ev(e, d):
    if e == "log" and "复查" in d["msg"]: cuts.append(d["msg"]); print("  ", d["msg"][:200])
cfg = MixConfig(name="误判测试", materials=mats, count=4, mode="segment", limit_minutes=0.4, limit_float=10, seg_min=2, seg_max=3,
                canvas="540x960", fps=25, output_root=OUT, cover=False, transition_mode="none", encoder="cpu", seed=7)
res = BatchRunner(cfg, callback=ev, record_history=False).run()
for f in sorted(glob.glob(os.path.join(res["dir"], "成片", "*.mp4"))):
    i = probe.probe(f, use_cache=False); print("  ", os.path.basename(f), f"{i.duration:.1f}s")
print("RESULT", res.get("ok"), "cuts:", len(cuts))
