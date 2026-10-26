"""整段拼接测试：素材完整使用（从第一帧到最后一帧，不拆分、不截断）、不超时长上限、转场不吃画面、查重整段跳过。

1) 规划：40 条计划逐条检查——每个视频素材都从 0 秒开始、帧数等于完整时长；成片时长 ≤ 上限；
   时长 = 素材帧数之和 + 转场帧数（定格过渡不重叠）。
2) 查重：拼接合集素材里，同一条成片中任意两个素材都没有相同的内容编号（有重复就整个素材跳过）。
3) 实际生成 3 条：成片时长与计划一致（没有在合成时被截掉），有声音。
用法：python tests/smoke_whole.py
"""
import glob
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from mixer import dedup, probe  # noqa: E402
from mixer.batch import BatchRunner  # noqa: E402
from mixer.models import MixConfig  # noqa: E402
from mixer.planner import Planner, total_frames  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TM = os.path.join(ROOT, "_work", "testmedia")
DM = os.path.join(ROOT, "_work", "dedup_media")
OUT = os.path.join(ROOT, "_work", "out_whole")
FPS = 30
bad = []


def full_frames(info):
    return int(math.floor(info.duration * FPS + 1e-6))


def check_plans(cfg, infos, idx=None, label=""):
    pl = Planner(cfg, infos, {}, log=lambda *a, **k: None, dup=idx)
    plans, _ = pl.build()
    lim = cfg.limit_seconds()
    n_seg = n_trans = 0
    for it in plans:
        segs = it.seg_objs()
        for s in segs:
            info = infos[s.src]
            if s.kind == "clip":
                n_seg += 1
                if abs(s.start) > 1e-6 or s.frames != full_frames(info):
                    bad.append(f"{label}{it.index}: {os.path.basename(s.src)} 没有完整使用 "
                               f"(起点 {s.start}, {s.frames}/{full_frames(info)} 帧)")
        n_trans += sum(1 for t in it.transitions if t)
        nfr = total_frames(segs, it.transitions, it.trans_frames, it.trans_hold)
        if abs(nfr / FPS - it.duration) > 0.01:
            bad.append(f"{label}{it.index}: 时长 {it.duration} 与帧数 {nfr} 不符")
        if lim and it.duration > lim + 1e-6:
            bad.append(f"{label}{it.index}: 时长 {it.duration} 超过上限 {lim}")
        if idx is not None:
            seen = {}
            for k, s in enumerate(segs):
                ids = idx.ids_in(s.src, s.start, s.start + s.frames / FPS)
                for j, other in seen.items():
                    if ids & other:
                        bad.append(f"{label}{it.index}: 第 {j + 1} 和第 {k + 1} 个素材有重复画面")
                seen[k] = ids
    durs = [p.duration for p in plans]
    print(f"   {label}{len(plans)} 条，素材 {n_seg} 个、定格过渡 {n_trans} 个，时长 {min(durs):.1f}~{max(durs):.1f}s")
    return plans


def main():
    mats = [os.path.join(TM, f) for f in ("A_横屏_水印.mp4", "B_竖屏_无音轨.mp4", "C_720p60_单声道.mp4",
                                          "D_竖屏短.mp4", "E 4比3 带空格.mp4", "photo_landscape.jpg")]
    infos = {p: probe.probe(p) for p in mats}
    print("1) 规划（上限 0.5 分钟、混合转场）")
    cfg = MixConfig(materials=mats, count=40, mode="whole", limit_enabled=True, limit_minutes=0.5, limit_float=20,
                    clips_min=2, clips_max=4, transition_mode="mixed", transition_prob=100, transition_dur=0.5,
                    fps=FPS, seed=11, dedup_enabled=False)
    check_plans(cfg, infos, label="上限 ")
    cfg2 = MixConfig(materials=mats, count=20, mode="whole", limit_enabled=False, clips_min=2, clips_max=3,
                     transition_mode="xfade", transition_prob=100, fps=FPS, seed=12, dedup_enabled=False)
    check_plans(cfg2, infos, label="不限时长 ")
    print("2) 查重（拼接合集素材，素材间有大量重复）")
    dmats = sorted(glob.glob(os.path.join(DM, "*.mp4")))
    dinfos = {p: probe.probe(p) for p in dmats}
    idx = dedup.analyze_library(list(dinfos.values()), "standard")
    cfg3 = MixConfig(materials=dmats, count=30, mode="whole", limit_enabled=True, limit_minutes=1.0, limit_float=20,
                     clips_min=2, clips_max=5, transition_mode="xfade", transition_prob=60, fps=FPS, seed=13)
    check_plans(cfg3, dinfos, idx, label="查重 ")
    print("3) 实际生成 3 条")
    cfg4 = MixConfig(name="整段测试", materials=mats, count=3, mode="whole", limit_enabled=True, limit_minutes=0.5,
                     limit_float=20, clips_min=2, clips_max=4, transition_mode="mixed", transition_prob=100,
                     canvas="720x1280", fps=FPS, output_root=OUT, cover=False, encoder="cpu", seed=21)
    res = BatchRunner(cfg4, callback=lambda e, d: None, record_history=False).run()
    plan = json.load(open(os.path.join(res["dir"], "数据", "plan.json"), encoding="utf-8"))
    for d in plan["items"]:
        f = os.path.join(res["dir"], "成片", d["name"] + ".mp4")
        info = probe.probe(f, use_cache=False)
        ok = abs(info.duration - d["duration"]) <= 0.07 and info.has_audio and info.duration <= 30.05
        if not ok:
            bad.append(f"成片 {d['name']}: 实际 {info.duration:.2f}s / 计划 {d['duration']}s，音轨 {info.has_audio}")
        print(f"   {d['name']}: 计划 {d['duration']:.2f}s 实际 {info.duration:.2f}s，素材 {len(d['segments'])} 个，"
              f"转场 {sum(1 for t in d['transitions'] if t)} 个")
    print("问题：", bad[:10] if bad else "无")
    print("RESULT", "PASS" if not bad else "FAIL")


if __name__ == "__main__":
    main()
