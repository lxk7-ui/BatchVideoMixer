"""端到端：关闭/开启查重各生成一批，逐条复查成片内部是否还有重复画面。

判定（与软件“剪辑后复查”同一标准）：复查找到的相似画面逐个采样点换算回素材时刻，对照素材原始画面确认——
同一素材同一时刻、查重索引认定的同一画面、或不同素材原始指纹确实相近，才算“真重复”；
同一素材不同时刻的相似画面（口播、固定机位）、画布模糊背景/贴纸标题造成的“看起来像”不算。
整段拼接（whole）不拆素材：同一个素材内部的重复不算；复查发现不同素材之间重复时整段移除后出现的素材并重新合成。
用法：python tests/smoke_dedup_mix.py segment|whole
"""
import os, sys, glob, time, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from mixer import probe, dedup
from mixer import render as rd
from mixer.models import MixConfig
from mixer.batch import BatchRunner
from mixer.planner import PlanItem
DM = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_work", "dedup_media"))
OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_work", "out_dedup")); os.makedirs(OUT, exist_ok=True)
mats = sorted(glob.glob(os.path.join(DM, "*.mp4")))
IDX = dedup.analyze_library([probe.probe(p) for p in mats], "standard")
def ev(e, d):
    if e == "log" and ("查重" in d["msg"] or "复查" in d["msg"] or d["level"] in ("warn", "error")): print("   LOG", d["msg"][:220])
JUDGE = BatchRunner(MixConfig(materials=mats, dedup_level="standard"), record_history=False)
JUDGE.infos = {p: probe.probe(p) for p in mats}
JUDGE.dup = IDX
REMOVED = []
def is_real(segs, *r, whole=False):
    if whole:   # 整段拼接：同一个素材内部的重复不算（素材不拆分）
        idx = [next((k for k, (o0, o1, _s) in enumerate(segs) if o0 <= t < o1), None) for t in ((r[0] + r[1]) / 2, (r[2] + r[3]) / 2)]
        if idx[0] is None or idx[0] == idx[1]:
            return False
    return JUDGE._real_dup(segs, r)
def run(mode, on, count, post=False):
    tag = ("查重" if on else "不查重") + ("_复查" if post else "")
    cfg = MixConfig(name=f"{mode}_{tag}", materials=mats, count=count, mode=mode,
                    limit_enabled=(mode == "segment"), limit_minutes=0.5, limit_float=10, seg_min=2, seg_max=4,
                    clips_min=3, clips_max=5, canvas="540x960", fps=25, output_root=OUT, cover=False,
                    transition_mode="none", dedup_enabled=on, dedup_postcheck=post, encoder="cpu", seed=12345)
    # 复查单独做：先生成未复查的成片做判定（换算需要原时间轴），复查效果由下面的 postcheck 统计
    t = time.time(); res = BatchRunner(cfg, callback=ev, record_history=False).run()
    plan = json.load(open(os.path.join(res["dir"], "数据", "plan.json"), encoding="utf-8"))
    items = {d["name"]: PlanItem.from_dict(d) for d in plan["items"]}
    bad = 0; total_dup = 0.0; raw_n = 0; durs = []
    for f in sorted(glob.glob(os.path.join(res["dir"], "成片", "*.mp4"))):
        info = probe.probe(f, use_cache=False)
        it = items[os.path.splitext(os.path.basename(f))[0]]
        segs = rd.output_segments(it, cfg.fps)
        reps = dedup.check_output(info, "standard")
        real = [r for r in reps if is_real(segs, *r, whole=(mode == "whole"))]
        sec = sum(b1 - b0 for _, _, b0, b1 in real)
        bad += bool(real); total_dup += sec; raw_n += len(reps); durs.append(info.duration)
        print(f"   {os.path.basename(f)} {info.duration:5.1f}s  真重复 {len(real)} 处 {sec:.1f}s（相似 {len(reps)} 处） {real[:3]}")
    print(f"== {mode} {tag}: {res.get('ok')} 条，含真重复 {bad} 条，真重复总时长 {total_dup:.1f}s，"
          f"相似画面 {raw_n} 处，时长 {min(durs):.1f}~{max(durs):.1f}s，用时 {time.time()-t:.0f}s")
    return bad, res["dir"], cfg, items
def postcheck(dir_, cfg, items, dup):
    """在成片上跑一遍剪辑后复查，看它删了什么（dup=None 模拟只开“剪辑后复查”）。"""
    br = BatchRunner(cfg, callback=ev, record_history=False)
    br.batch_dir = dir_
    br.infos = {p: probe.probe(p) for p in mats}
    br.dup = dup
    from mixer.render import RenderCtx, EncoderState
    ctx = RenderCtx(W=540, H=960, fps=cfg.fps, fit=cfg.fit, keep_audio=True, infos=br.infos, cleanup_map={},
                    parts_dir=os.path.join(dir_, "_tmp", "parts"), enc=EncoderState("cpu"), threads=4,
                    cancel=br.cancel, log=br.log)
    cut = 0
    for name, it in items.items():
        f = os.path.join(dir_, "成片", name + ".mp4")
        d0 = probe.probe(f, use_cache=False).duration
        it.duration = d0
        br._postcheck(it, f, ctx)
        d1 = probe.probe(f, use_cache=False).duration
        cut += d0 - d1
        print(f"   复查 {name}: {d0:.1f}s → {d1:.1f}s")
    print(f"== 剪辑后复查共删除 {cut:.1f}s")
    return cut
mode = sys.argv[1]
b_off, d_off, cfg_off, items_off = run(mode, False, 6)
b_on, d_on, cfg_on, items_on = run(mode, True, 6)
if mode == "whole":
    def ev2(e, d, _orig=ev):
        _orig(e, d)
        if e == "log" and "整段移除" in d["msg"]:
            REMOVED.append(d["msg"])
    ev_old, ev = ev, ev2
    print("-- 只开剪辑后复查：不同素材之间的重复要整段移除（素材不拆分），最终成片不再含不同素材间的重复")
    b_post, _, _, _ = run(mode, False, 6, post=True)
    ev = ev_old
    print("RESULT", mode, "off:", b_off, "on:", b_on, "post:", b_post, f"整段移除 {len(REMOVED)} 次",
          "PASS" if b_on == 0 and b_off > 0 and b_post == 0 and REMOVED else "CHECK")
    sys.exit(0)
print("-- 开启剪辑前查重后的成片，再复查（应删除 0 秒）")
cut_on = postcheck(d_on, cfg_on, items_on, IDX)
print("-- 只开剪辑后复查：在未查重的成片上复查（应删除真重复，且无“仍有重复”警告）")
cut_off = postcheck(d_off, cfg_off, items_off, None)
print("RESULT", mode, "off:", b_off, "on:", b_on, f"复查误删 {cut_on:.1f}s，兜底删除 {cut_off:.1f}s",
      "PASS" if b_on == 0 and b_off > 0 and cut_on < 0.5 and cut_off > 0 else "CHECK")
