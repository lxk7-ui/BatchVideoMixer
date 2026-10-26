"""音画同步测试：素材里画面在第 1/3/5/7 秒闪白、声音在同一时刻“嘀”一声，跑完整流程后检查成片里两者是否对齐。

两种容易出错的素材：
  音频晚开始.mp4   声音比画面晚 0.4 秒开始（录屏、手机视频常见）
  音频断档.mkv     声音在 3.2~3.7 秒之间缺了一段（时间戳跳空），后面的声音必须仍然对齐

用法：python tests/smoke_avsync.py [--src 源码目录]     （--src 可指向旧版本源码做对比）
"""
import argparse
import os
import subprocess
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--src", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
args = ap.parse_args()
sys.path.insert(0, os.path.abspath(args.src))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np  # noqa: E402

from mixer import ffmpeg, probe  # noqa: E402
from mixer import render as rd  # noqa: E402
from mixer.batch import BatchRunner  # noqa: E402
from mixer.models import MixConfig  # noqa: E402
from mixer.planner import PlanItem  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WORK = os.path.join(ROOT, "_work", "avsync")
os.makedirs(WORK, exist_ok=True)
FF = ffmpeg.ffmpeg_bin()
FLASH = "drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='lt(mod(t+1,2),0.066)'"
FPS = 30


def run(cmd):
    p = subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y"] + cmd, cwd=WORK, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if p.returncode:
        raise RuntimeError(p.stderr[-1500:])


def make_media():
    late = os.path.join(WORK, "音频晚开始.mp4")
    if not os.path.isfile(late):
        # 画面 0~8 秒；声音从 0.4 秒才开始（-itsoffset），“嘀”声在成片时间 1/3/5/7 秒
        run(["-f", "lavfi", "-i", f"color=black:s=720x1280:r={FPS}:d=8,{FLASH}",
             "-itsoffset", "0.4", "-f", "lavfi",
             "-i", "aevalsrc='0.8*sin(2*PI*1000*t)*lt(mod(t+1.4,2),0.06)':s=48000:d=7.6",
             "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", late])
    gap = os.path.join(WORK, "音频断档.mkv")
    if not os.path.isfile(gap):
        # 声音在 3.2~3.7 秒之间缺一段（aselect 保留原时间戳，mkv 里留下时间戳空档）
        run(["-f", "lavfi", "-i", f"color=black:s=720x1280:r={FPS}:d=8,{FLASH}",
             "-f", "lavfi", "-i", "aevalsrc='0.8*sin(2*PI*1000*t)*lt(mod(t+1,2),0.06)':s=48000:d=8",
             "-filter_complex", "[1:a]aselect='not(between(t,3.2,3.7))'[a]",
             "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "pcm_s16le", gap])
    return late, gap


def markers(video):
    """返回 (闪白时刻列表, “嘀”声开始时刻列表)，单位秒。"""
    p = subprocess.run([FF, "-v", "error", "-i", video, "-vf", "scale=32:32,format=gray", "-f", "rawvideo", "pipe:1"],
                       capture_output=True)
    fr = np.frombuffer(p.stdout, dtype=np.uint8).reshape(-1, 32 * 32).mean(axis=1)
    info = probe.probe(video, use_cache=False)
    fps = info.fps or FPS
    flashes, prev = [], False
    for k, v in enumerate(fr):
        on = v > 128
        if on and not prev:
            flashes.append(k / fps)
        prev = on
    p = subprocess.run([FF, "-v", "error", "-i", video, "-ac", "1", "-ar", "48000", "-f", "s16le", "pipe:1"],
                       capture_output=True)
    a = np.abs(np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768.0)
    win = 48   # 1 毫秒
    env = a[: len(a) // win * win].reshape(-1, win).max(axis=1)
    beeps, prev = [], False
    for k, v in enumerate(env):
        on = v > 0.25
        if on and not prev:
            beeps.append(k / 1000.0)
        prev = on
    return flashes, beeps


def check(name, video):
    flashes, beeps = markers(video)
    diffs = []
    for f in flashes:
        if beeps:
            b = min(beeps, key=lambda x: abs(x - f))
            diffs.append(round((b - f) * 1000))
    worst = max((abs(d) for d in diffs), default=9999)
    ok = len(flashes) >= 2 and len(diffs) == len(flashes) and worst <= 45
    print(f"   {name}: 闪白 {[round(x, 2) for x in flashes]}  嘀声 {[round(x, 2) for x in beeps]}  "
          f"声音-画面偏差(毫秒) {diffs}  {'对齐' if ok else '不同步'}")
    return ok


def main():
    late, gap = make_media()
    infos = {p: probe.probe(p, use_cache=False) for p in (late, gap)}
    print("素材：", {os.path.basename(p): f"音频起点 {i.extra.get('audio_start', '?')}" for p, i in infos.items()}
          if all(hasattr(i, "extra") for i in infos.values()) else "")
    results = []
    # 1) 整段拼接（从头开始的完整素材）
    out_root = os.path.join(WORK, "out")
    cfg = MixConfig(name="音画同步", materials=[late, gap], count=1, mode="whole", limit_enabled=False,
                    clips_min=2, clips_max=2, transition_mode="none", canvas="720x1280", fps=FPS, cover=False,
                    loudnorm=False, audio_fade=False, dedup_enabled=False, dedup_postcheck=False, keep_audio="keep",
                    output_root=out_root, encoder="cpu", seed=7)
    res = BatchRunner(cfg, callback=lambda e, d: None, record_history=False).run()
    for f in sorted(os.listdir(os.path.join(res["dir"], "成片"))):
        results.append(check("整段拼接 " + f, os.path.join(res["dir"], "成片", f)))
    # 2) 片段：从素材中间开始（跳转路径），含变速
    enc = rd.EncoderState("cpu")
    ctx = rd.RenderCtx(W=720, H=1280, fps=FPS, fit="smart", keep_audio=True, infos=infos, cleanup_map={},
                       parts_dir=os.path.join(WORK, "parts"), enc=enc, threads=4)
    for name, src, start, speed in (("片段 音频晚开始@2.2s", late, 2.2, 1.0), ("片段 音频断档@4.1s", gap, 4.1, 1.0),
                                    ("片段 音频晚开始@0s 变速1.1", late, 0.0, 1.1)):
        frames = int(3.6 * FPS)
        item = PlanItem(index=1, name="seg", segments=[{"src": src, "start": start, "frames": frames, "speed": speed,
                                                        "kind": "clip", "effect": ""}],
                        transitions=[], trans_frames=0, seed=1, fps=FPS, duration=frames / FPS)
        parts = [rd.render_part(p, ctx) for p in rd.item_parts(item, ctx)]
        out = os.path.join(WORK, f"seg_{len(results)}.mp4")
        cfg2 = MixConfig(loudnorm=False, audio_fade=False, keep_audio="keep", metadata=False)
        rd.compose(item, parts, out, cfg2, ctx, job_dir=os.path.join(WORK, f"job_{len(results)}"))
        results.append(check(name, out))
    print("RESULT", "PASS" if all(results) else "FAIL", f"{sum(results)}/{len(results)}")


if __name__ == "__main__":
    main()
