"""查重测试：剪辑拼接素材之间/内部的重复画面识别 + 成片无重复验证。"""
import os, sys, glob, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from mixer import probe, dedup
DM = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_work", "dedup_media"))
files = sorted(glob.glob(os.path.join(DM, "*.mp4")))
infos = [probe.probe(f) for f in files]
level = sys.argv[1] if len(sys.argv) > 1 else "standard"
t = time.time()
idx = dedup.analyze_library(infos, level, log=print)
print(f"analyze {time.time()-t:.1f}s")
for pa, a0, a1, pb, b0, b1 in idx.runs:
    print(f"  {os.path.basename(pa):22s} {a0:5.1f}-{a1:5.1f}  ≈  {os.path.basename(pb):22s} {b0:5.1f}-{b1:5.1f}")
print("full dups:", [(os.path.basename(p), os.path.basename(q), r) for p, q, r in idx.full_dups])
print("ratio:", {os.path.basename(p): round(r, 2) for p, r in idx.ratio.items()})
