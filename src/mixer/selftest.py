"""环境自检：用 ffmpeg 生成测试素材，真实跑一遍“去水印 + 片段混剪 + 转场 + 滤镜 + 贴纸 + 标题 + BGM + 封面”，
校验每条成片时长不超过上限、有音视频流。用于新电脑部署后确认一切正常。"""
import datetime
import os
import shutil
import time

from . import ffmpeg, encoders, probe, paths, VERSION


def run(log=print, keep=False):
    lines = []
    ok_all = True

    def rec(ok, text):
        nonlocal ok_all
        ok_all = ok_all and ok
        line = ("√ " if ok else "× ") + text
        lines.append(line)
        log(line)

    t0 = time.time()
    lines.append(f"批量视频混剪工具 v{VERSION} 自检  {datetime.datetime.now():%Y-%m-%d %H:%M:%S}")
    work = os.path.join(paths.cache_dir("selftest"), datetime.datetime.now().strftime("%H%M%S"))
    os.makedirs(work, exist_ok=True)
    try:
        try:
            ff = ffmpeg.ffmpeg_bin()
            ffp = ffmpeg.ffprobe_bin()
            rec(True, f"FFmpeg：{ffmpeg.version_info()[0][:60]}")
        except Exception as e:
            rec(False, f"找不到 FFmpeg：{e}")
            return False, "\n".join(lines)
        av = encoders.detect(force=True)
        rec(True, encoders.describe(av))
        # 测试素材
        a = os.path.join(work, "横屏带水印.mp4")
        b = os.path.join(work, "竖屏无音轨.mp4")
        m = os.path.join(work, "bgm.mp3")
        c3 = os.path.join(work, "动态画面3.mp4")
        c4 = os.path.join(work, "动态画面4.mp4")
        cmds = [
            ["-f", "lavfi", "-i", "mandelbrot=s=1280x720:r=30", "-f", "lavfi", "-i", "sine=f=440:d=10", "-t", "10",
             "-vf", "drawbox=x=1000:y=20:w=240:h=50:color=white@0.9:t=fill", "-c:v", "libx264", "-preset",
             "ultrafast", "-c:a", "aac", "-shortest", a],
            ["-f", "lavfi", "-i", "life=s=360x640:r=25:mold=10:ratio=0.15,scale=720:1280:flags=neighbor", "-t", "10",
             "-c:v", "libx264", "-preset", "ultrafast", b],
            ["-f", "lavfi", "-i", "sine=f=330:d=20", "-c:a", "libmp3lame", m],
            ["-f", "lavfi", "-i", "cellauto=s=320x180:rule=110:r=25,scale=1280:720:flags=neighbor", "-t", "10",
             "-c:v", "libx264", "-preset", "ultrafast", c3],
            ["-f", "lavfi", "-i", "testsrc2=s=720x1280:r=25,hue=H=2*PI*t/3:s=1.5", "-t", "10",
             "-c:v", "libx264", "-preset", "ultrafast", c4],
        ]
        for c in cmds:
            ffmpeg.run_ffmpeg(c, stall_timeout=60)
        rec(all(probe.probe(p, use_cache=False).ok for p in (a, b, m, c3, c4)), "生成测试素材")
        # 混剪
        from .models import MixConfig, CleanupProfile, Region, StylePreset, OverlayItem
        from .batch import BatchRunner
        from . import textstyle
        stk_dir = paths.assets_dir("stickers", "电商标签")
        stk = ""
        if os.path.isdir(stk_dir):
            pngs = [f for f in os.listdir(stk_dir) if f.endswith(".png")]
            stk = os.path.join(stk_dir, pngs[0]) if pngs else ""
        style = StylePreset(overlays=[OverlayItem(kind="sticker", src=stk, cx=0.8, cy=0.25, w=0.25,
                                                  anim_loop="pulse").to_dict()] if stk else []).to_dict()
        clean = CleanupProfile(regions=[Region(x=1000 / 1280, y=20 / 720, w=240 / 1280, h=50 / 720).to_dict()])
        cfg = MixConfig(name="自检", materials=[a, b, c3, c4], bgms=[m], count=2, mode="segment", limit_minutes=0.25,
                        limit_float=20, seg_min=1.5, seg_max=3, transition_mode="mixed", transition_prob=80,
                        canvas="720x1280", fps=25, filter_mode="random", titles=["自检标题"],
                        title_style=textstyle.preset_style("综艺花字").to_dict(), style=style,
                        cleanup_map={a: clean.to_dict()}, output_root=os.path.join(work, "out"), cover=True,
                        keep_audio="lower", concurrency=2)
        res = BatchRunner(cfg, callback=lambda ev, d: None, record_history=False).run()
        rec(res.get("ok") == 2, f"批量混剪：成功 {res.get('ok', 0)}/2")
        outs = []
        if res.get("dir"):
            od = os.path.join(res["dir"], "成片")
            outs = [os.path.join(od, f) for f in os.listdir(od) if f.endswith(".mp4")] if os.path.isdir(od) else []
        for f in outs:
            info = probe.probe(f, use_cache=False)
            good = info.ok and info.has_audio and info.duration <= 15.05
            rec(good, f"成片 {os.path.basename(f)}：{info.duration:.2f}s（上限 15s），{info.res_text()}，"
                      f"{'有' if info.has_audio else '无'}音轨")
        # 素材查重：用 a、b 剪一个“拼接素材”，应能识别出与 a、b 的重复画面
        from . import dedup
        c = os.path.join(work, "拼接素材.mp4")
        ffmpeg.run_ffmpeg(["-i", a, "-i", b, "-filter_complex",
                           "[0:v]trim=1:4,setpts=PTS-STARTPTS,scale=720:1280,setsar=1,fps=25[x];"
                           "[1:v]trim=1:4,setpts=PTS-STARTPTS,setsar=1,fps=25[y];[x][y]concat=n=2:v=1:a=0[v]",
                           "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast", c], stall_timeout=60)
        idx = dedup.analyze_library([probe.probe(p, use_cache=False) for p in (a, b, c)], "standard")
        hits = {os.path.basename(x[0]) for x in idx.runs} | {os.path.basename(x[3]) for x in idx.runs}
        rec("拼接素材.mp4" in hits and len(idx.runs) >= 2, f"素材查重：识别出 {len(idx.runs)} 处重复画面")
        # 素材处理预览
        from . import studio
        png = studio.preview_frame(a, 2.0, clean, StylePreset(filter_name="清新"), max_w=640,
                                   out_png=os.path.join(work, "pv.png"))
        rec(os.path.isfile(png), "素材处理预览（去水印 + 滤镜）")
    except Exception as e:
        rec(False, f"自检出错：{e}")
    finally:
        lines.append(f"用时 {time.time() - t0:.1f} 秒，结论：{'全部正常' if ok_all else '存在问题，请把本报告发给技术人员'}")
        try:
            with open(paths.data_path("selftest.txt"), "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines))
        except OSError:
            pass
        if not keep:
            shutil.rmtree(work, ignore_errors=True)
    return ok_all, "\n".join(lines)
