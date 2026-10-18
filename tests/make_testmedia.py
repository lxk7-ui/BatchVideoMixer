"""生成测试素材（全部用 ffmpeg 合成，冒烟测试和截图脚本都依赖这些文件）。

用法：python tests/make_testmedia.py [testmedia|edge|dedup|natural|all]   （默认 all）

  _work/testmedia/   常规素材：横屏带水印、竖屏无音轨、720p60 单声道、竖屏短片、4:3 带空格文件名、BGM、产品图、风景照
  _work/edge/        边界素材：手机竖拍旋转、HDR HLG、奇数分辨率、59.94fps、损坏文件、0.8 秒超短、特殊字符文件名、webm
  _work/dedup_media/ 查重素材：5 个原素材 + 2 个拼接合集 + 内部重复 + 镜像竖屏版 + 重复导入的副本
  _work/natural/     误判测试：8 段不同风景照的推拉镜头（用 Windows 自带壁纸，找不到时用合成图）

需要 ffmpeg（与程序相同的查找规则：_deps/ffmpeg、环境变量 BM_FFMPEG_DIR 或 PATH）。
"""
import glob
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from PIL import Image, ImageDraw, ImageFilter, ImageFont  # noqa: E402

from mixer import ffmpeg  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WORK = os.environ.get("BVM_WORK_DIR") or os.path.join(ROOT, "_work")   # 可指定其它目录试生成
FONT = r"C\:/Windows/Fonts/msyh.ttc"          # drawtext 用（Windows 自带微软雅黑）
FONT_PIL = r"C:\Windows\Fonts\msyhbd.ttc"
WALLPAPERS = [r"C:\Windows\Web\Screen\img100.jpg", r"C:\Windows\Web\Screen\img102.jpg",
              r"C:\Windows\Web\Screen\img103.jpg", r"C:\Windows\Web\Wallpaper\Spotlight\img50.jpg",
              r"C:\Windows\Web\Wallpaper\ThemeB\img25.jpg", r"C:\Windows\Web\Wallpaper\ThemeB\img27.jpg",
              r"C:\Windows\Web\Wallpaper\ThemeA\img21.jpg", r"C:\Windows\Web\Wallpaper\ThemeA\img23.jpg"]


def ff(*args, cwd):
    p = subprocess.run([ffmpeg.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", *args], cwd=cwd,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode:
        raise RuntimeError(f"ffmpeg 生成 {args[-1]} 失败：{p.stderr[-2000:]}")


def wallpaper(i, out_dir):
    """第 i 张风景图：优先用 Windows 自带壁纸，没有就合成一张（渐变 + 随机图形，保证每张都不一样）。"""
    if i < len(WALLPAPERS) and os.path.isfile(WALLPAPERS[i]):
        return WALLPAPERS[i]
    import random
    rnd = random.Random(i * 7919)
    im = Image.new("RGB", (2560, 1440))
    d = ImageDraw.Draw(im)
    c1 = tuple(rnd.randrange(256) for _ in range(3))
    c2 = tuple(rnd.randrange(256) for _ in range(3))
    for y in range(1440):
        t = y / 1439
        d.line([(0, y), (2560, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(c1, c2)))
    for _ in range(40):
        x, y, r = rnd.randrange(2560), rnd.randrange(1440), rnd.randrange(40, 300)
        d.ellipse((x - r, y - r, x + r, y + r), fill=tuple(rnd.randrange(256) for _ in range(3)))
    p = os.path.join(out_dir, f"_wall_{i}.jpg")
    im.filter(ImageFilter.GaussianBlur(2)).save(p, quality=92)
    return p


def testmedia():
    d = os.path.join(WORK, "testmedia")
    os.makedirs(d, exist_ok=True)
    ff("-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=20", "-f", "lavfi", "-i", "sine=f=440:sample_rate=44100:d=20",
       "-vf", f"drawtext=fontfile='{FONT}':text='抖音号 WM123456':fontcolor=white@0.85:fontsize=44:x=w-tw-40:y=40:"
              "borderw=2:bordercolor=black@0.5,drawbox=x=60:y=900:w=220:h=120:color=red@0.9:t=fill",
       "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-shortest", "A_横屏_水印.mp4", cwd=d)
    ff("-f", "lavfi", "-i", "mandelbrot=s=1080x1920:r=25", "-t", "15", "-c:v", "libx264", "-preset", "veryfast",
       "-crf", "23", "B_竖屏_无音轨.mp4", cwd=d)
    ff("-f", "lavfi", "-i", "smptehdbars=s=1280x720:r=60:d=12", "-f", "lavfi", "-i", "anoisesrc=d=12:c=pink:a=0.1:r=48000",
       "-vf", f"drawtext=fontfile='{FONT}':text='素材C %{{pts\\:hms}}':fontcolor=yellow:fontsize=60:x=(w-tw)/2:y=(h-th)/2",
       "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-ac", "1", "C_720p60_单声道.mp4", cwd=d)
    ff("-f", "lavfi", "-i", "testsrc=s=720x1280:r=30:d=8", "-f", "lavfi", "-i", "sine=f=660:d=8", "-c:v", "libx264",
       "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "D_竖屏短.mp4", cwd=d)
    ff("-f", "lavfi", "-i", "rgbtestsrc=s=640x480:r=24:d=10", "-f", "lavfi", "-i", "sine=f=330:d=10", "-c:v", "libx264",
       "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "E 4比3 带空格.mp4", cwd=d)
    ff("-f", "lavfi", "-i", "aevalsrc='0.2*sin(2*PI*220*t)*(1+sin(2*PI*2*t))/2+0.15*sin(2*PI*330*t)':s=44100:d=40",
       "-c:a", "libmp3lame", "-b:a", "128k", "bgm1.mp3", cwd=d)
    im = Image.new("RGBA", (600, 600), (255, 255, 255, 255))
    dr = ImageDraw.Draw(im)
    dr.rounded_rectangle((80, 120, 520, 520), radius=60, fill=(30, 144, 255, 255))
    dr.ellipse((220, 40, 380, 200), fill=(255, 180, 0, 255))
    dr.text((300, 330), "新品", font=ImageFont.truetype(FONT_PIL, 90), fill="white", anchor="mm")
    im.save(os.path.join(d, "product_white_bg.png"))
    ph = Image.open(wallpaper(3, d))
    ph.thumbnail((1600, 1600))
    ph.convert("RGB").save(os.path.join(d, "photo_landscape.jpg"))
    print("testmedia ok", d)


def edge():
    d = os.path.join(WORK, "edge")
    os.makedirs(d, exist_ok=True)
    tm = os.path.join(WORK, "testmedia")
    if not os.path.isfile(os.path.join(tm, "A_横屏_水印.mp4")):
        testmedia()
    # 手机竖拍：编码为横向 1920x1080，带 90° 旋转元数据
    ff("-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=6", "-f", "lavfi", "-i", "sine=f=500:d=6", "-c:v", "libx264",
       "-preset", "ultrafast", "-c:a", "aac", "-shortest", "rot_tmp.mp4", cwd=d)
    ff("-display_rotation", "90", "-i", "rot_tmp.mp4", "-c", "copy", "手机竖拍_旋转.mp4", cwd=d)
    os.remove(os.path.join(d, "rot_tmp.mp4"))
    # HDR HLG 10bit HEVC
    ff("-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=5", "-vf", "format=yuv420p10le", "-c:v", "libx265",
       "-preset", "ultrafast", "-x265-params", "colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc:log-level=error",
       "-color_primaries", "bt2020", "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc", "HDR_HLG.mp4", cwd=d)
    # 奇数分辨率 + 59.94fps（yuv444p 才能保留奇数尺寸）
    ff("-f", "lavfi", "-i", "testsrc=s=853x481:r=60000/1001:d=5", "-f", "lavfi", "-i", "sine=f=300:d=5", "-c:v", "libx264",
       "-preset", "ultrafast", "-pix_fmt", "yuv444p", "-c:a", "aac", "-shortest", "奇数分辨率_5994.mp4", cwd=d)
    ff("-f", "lavfi", "-i", "testsrc=s=854x480:r=60000/1001:d=5", "-f", "lavfi", "-i", "sine=f=300:d=5", "-c:v", "libx264",
       "-preset", "ultrafast", "-c:a", "aac", "-shortest", "5994fps.mp4", cwd=d)
    # 损坏文件：截断的 mp4
    with open(os.path.join(tm, "A_横屏_水印.mp4"), "rb") as fh, open(os.path.join(d, "损坏文件.mp4"), "wb") as out:
        out.write(fh.read(300000))
    # 0.8 秒超短素材
    ff("-f", "lavfi", "-i", "testsrc2=s=720x1280:r=30:d=0.8", "-c:v", "libx264", "-preset", "ultrafast", "超短0.8秒.mp4", cwd=d)
    # 特殊字符文件名
    shutil.copy2(os.path.join(tm, "C_720p60_单声道.mp4"), os.path.join(d, "它's #1 & [测试] (副本) 100%.mp4"))
    # webm VP9 + opus
    ff("-f", "lavfi", "-i", "mandelbrot=s=640x360:r=25", "-f", "lavfi", "-i", "sine=f=200:d=6", "-t", "6", "-c:v", "libvpx-vp9",
       "-deadline", "realtime", "-cpu-used", "8", "-b:v", "1M", "-c:a", "libopus", "网页格式.webm", cwd=d)
    print("edge ok", d)


def dedup_media():
    d = os.path.join(WORK, "dedup_media")
    os.makedirs(d, exist_ok=True)
    enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-shortest"]
    ff("-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=20", "-f", "lavfi", "-i", "sine=f=440:d=20", *enc, "S1_原素材.mp4", cwd=d)
    ff("-f", "lavfi", "-i", "mandelbrot=s=720x1280:r=25", "-f", "lavfi", "-i", "sine=f=550:d=20", "-t", "20", *enc,
       "S2_原素材.mp4", cwd=d)
    ff("-f", "lavfi", "-i", "life=s=640x480:r=25:mold=10:ratio=0.15:death_color=#202040:life_color=#F0C040,"
       "scale=1280:960:flags=neighbor", "-t", "20", "-f", "lavfi", "-i", "sine=f=330:d=20", *enc, "S3_原素材.mp4", cwd=d)
    ff("-loop", "1", "-framerate", "30", "-t", "20", "-i", wallpaper(3, d), "-f", "lavfi", "-i", "sine=f=660:d=20",
       "-vf", "scale=2560:-2,zoompan=z='1+0.0015*on':x='iw/2-(iw/zoom/2)+on*2':y='ih/2-(ih/zoom/2)':d=1:s=1080x1920:fps=30",
       "-t", "20", *enc, "S4_原素材.mp4", cwd=d)
    ff("-f", "lavfi", "-i", "gradients=s=1280x720:r=30:speed=0.04:n=5:d=20", "-f", "lavfi", "-i", "sine=f=770:d=20", *enc,
       "S5_原素材.mp4", cwd=d)
    cat = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac"]
    # X = S1[2:6] + S3[3:8] + S5[1:5]
    ff("-i", "S1_原素材.mp4", "-i", "S3_原素材.mp4", "-i", "S5_原素材.mp4", "-filter_complex",
       "[0:v]trim=2:6,setpts=PTS-STARTPTS,scale=1280:720,fps=30,setsar=1[v0];[0:a]atrim=2:6,asetpts=PTS-STARTPTS[a0];"
       "[1:v]trim=3:8,setpts=PTS-STARTPTS,scale=1280:720,fps=30,setsar=1[v1];[1:a]atrim=3:8,asetpts=PTS-STARTPTS[a1];"
       "[2:v]trim=1:5,setpts=PTS-STARTPTS,scale=1280:720,fps=30,setsar=1[v2];[2:a]atrim=1:5,asetpts=PTS-STARTPTS[a2];"
       "[v0][a0][v1][a1][v2][a2]concat=n=3:v=1:a=1[v][a]", "-map", "[v]", "-map", "[a]", *cat, "X_拼接合集.mp4", cwd=d)
    # Y = S2[5:9] + S1[10:14] + S1[3:5]（最后一段调过色）
    ff("-i", "S2_原素材.mp4", "-i", "S1_原素材.mp4", "-filter_complex",
       "[0:v]trim=5:9,setpts=PTS-STARTPTS,scale=1280:720:force_original_aspect_ratio=decrease,"
       "pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps=30,setsar=1[v0];[0:a]atrim=5:9,asetpts=PTS-STARTPTS[a0];"
       "[1:v]trim=10:14,setpts=PTS-STARTPTS,fps=30,setsar=1[v1];[1:a]atrim=10:14,asetpts=PTS-STARTPTS[a1];"
       "[1:v]trim=3:5,setpts=PTS-STARTPTS,fps=30,setsar=1,eq=saturation=1.3:brightness=0.04[v2];"
       "[1:a]atrim=3:5,asetpts=PTS-STARTPTS[a2];[v0][a0][v1][a1][v2][a2]concat=n=3:v=1:a=1[v][a]",
       "-map", "[v]", "-map", "[a]", *cat, "Y_拼接合集.mp4", cwd=d)
    # Z = 镜像 + 竖屏模糊背景版的 X
    ff("-i", "X_拼接合集.mp4", "-filter_complex",
       "[0:v]hflip,split=2[f][b];[b]scale=135:240:force_original_aspect_ratio=increase,crop=135:240,boxblur=10:3,"
       "scale=1080:1920[bb];[f]scale=1080:1920:force_original_aspect_ratio=decrease[ff];[bb][ff]overlay=(W-w)/2:(H-h)/2[v]",
       "-map", "[v]", "-map", "0:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-c:a", "aac",
       "Z_镜像竖屏盗版.mp4", cwd=d)
    # W = S4[0:4] + S5[8:12] + S4[0:4]（素材内部重复）
    ff("-i", "S4_原素材.mp4", "-i", "S5_原素材.mp4", "-filter_complex",
       "[0:v]trim=0:4,setpts=PTS-STARTPTS,scale=1080:1920,fps=30,setsar=1,split=2[v0][v2];"
       "[0:a]atrim=0:4,asetpts=PTS-STARTPTS,asplit=2[a0][a2];"
       "[1:v]trim=8:12,setpts=PTS-STARTPTS,scale=1080:1920:force_original_aspect_ratio=decrease,"
       "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,fps=30,setsar=1[v1];[1:a]atrim=8:12,asetpts=PTS-STARTPTS[a1];"
       "[v0][a0][v1][a1][v2][a2]concat=n=3:v=1:a=1[v][a]", "-map", "[v]", "-map", "[a]", *cat, "W_内部重复.mp4", cwd=d)
    shutil.copy2(os.path.join(d, "S3_原素材.mp4"), os.path.join(d, "S3_副本(重复导入).mp4"))
    for p in glob.glob(os.path.join(d, "_wall_*.jpg")):
        os.remove(p)
    print("dedup_media ok", d)


def natural():
    d = os.path.join(WORK, "natural")
    os.makedirs(d, exist_ok=True)
    for i in range(8):
        ff("-loop", "1", "-framerate", "25", "-t", "8", "-i", wallpaper(i, d), "-f", "lavfi", "-i", f"sine=f={250 + i * 50}:d=8",
           "-vf", "scale=2560:-2,zoompan=z='1+0.002*on':x='iw/2-(iw/zoom/2)+on*3':y='ih/2-(ih/zoom/2)':d=1:s=1280x720:fps=25",
           "-t", "8", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-c:a", "aac", "-shortest", f"自然_{i + 1}.mp4", cwd=d)
    for p in glob.glob(os.path.join(d, "_wall_*.jpg")):
        os.remove(p)
    print("natural ok", d)


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    jobs = {"testmedia": testmedia, "edge": edge, "dedup": dedup_media, "natural": natural}
    for name, fn in jobs.items():
        if which in ("all", name):
            fn()
