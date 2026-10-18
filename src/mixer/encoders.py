"""视频编码器：真实可用性探测（实测编码一小段）+ 统一参数生成 + 自动回退。

GPU 只有“实测能编码”才会启用，绝不只看显卡名字（修复原软件“GPU 假加速”问题）。
"""
import os
import threading

from . import ffmpeg

ENCODERS = {
    "cpu": {"label": "CPU 软件编码 (x264，兼容性最好)", "codec": "libx264", "short": "CPU"},
    "nvenc": {"label": "NVIDIA 显卡加速 (NVENC)", "codec": "h264_nvenc", "short": "NVIDIA"},
    "qsv": {"label": "Intel 核显加速 (QSV)", "codec": "h264_qsv", "short": "Intel QSV"},
    "amf": {"label": "AMD 显卡加速 (AMF)", "codec": "h264_amf", "short": "AMD"},
}

# 画质档位：x264 CRF / NVENC CQ / QSV global_quality / AMF QP，及码率上限（按 1080p 计）
QUALITY = {
    "high": {"label": "高清", "crf": 19, "cq": 21, "gq": 21, "qp": 21, "maxrate": 14, "x264preset": "fast"},
    "standard": {"label": "标准", "crf": 22, "cq": 24, "gq": 24, "qp": 24, "maxrate": 9, "x264preset": "veryfast"},
    "small": {"label": "省空间", "crf": 26, "cq": 28, "gq": 28, "qp": 28, "maxrate": 5, "x264preset": "veryfast"},
}

_detect_lock = threading.Lock()
_detected = None


def _scale_rate(mbps, width, height):
    """码率上限按分辨率面积缩放（以 1080×1920 为基准）。"""
    area = max(1, width * height)
    k = area / (1080 * 1920)
    k = max(0.35, min(k, 2.5))
    return max(1, int(round(mbps * k)))


def video_args(enc: str, quality: str = "standard", width=1080, height=1920, fps=30, threads=None,
               purpose="final"):
    """生成视频编码参数（列表）。purpose='intermediate' 为中间片段（高质量、快速）。"""
    q = QUALITY.get(quality, QUALITY["standard"])
    gop = max(1, int(round(fps * 4)))
    if purpose == "intermediate":
        # 中间片段：接近无损、速度优先，只做临时文件
        if enc == "nvenc":
            return ["-c:v", "h264_nvenc", "-preset", "p3", "-rc", "vbr", "-cq", "16", "-b:v", "0",
                    "-pix_fmt", "yuv420p", "-g", str(gop)]
        if enc == "qsv":
            return ["-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality", "16", "-g", str(gop)]
        if enc == "amf":
            return ["-c:v", "h264_amf", "-usage", "transcoding", "-quality", "speed", "-rc", "cqp",
                    "-qp_i", "16", "-qp_p", "18", "-g", str(gop)]
        a = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", "-g", str(gop)]
        if threads:
            a += ["-threads", str(threads)]
        return a

    maxrate = _scale_rate(q["maxrate"], width, height)
    if enc == "nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(q["cq"]), "-b:v", "0",
                "-maxrate", f"{maxrate}M", "-bufsize", f"{maxrate * 2}M", "-profile:v", "high",
                "-pix_fmt", "yuv420p", "-g", str(gop)]
    if enc == "qsv":
        return ["-c:v", "h264_qsv", "-preset", "medium", "-global_quality", str(q["gq"]),
                "-profile:v", "high", "-g", str(gop)]
    if enc == "amf":
        return ["-c:v", "h264_amf", "-usage", "transcoding", "-quality", "balanced", "-rc", "cqp",
                "-qp_i", str(q["qp"]), "-qp_p", str(q["qp"] + 2), "-profile:v", "high", "-g", str(gop)]
    a = ["-c:v", "libx264", "-preset", q["x264preset"], "-crf", str(q["crf"]),
         "-maxrate", f"{maxrate}M", "-bufsize", f"{maxrate * 2}M",
         "-profile:v", "high", "-pix_fmt", "yuv420p", "-g", str(gop)]
    if threads:
        a += ["-threads", str(threads)]
    return a


COLOR_ARGS = ["-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]


def _test_encoder(enc):
    args = ["-f", "lavfi", "-i", "color=c=black:s=640x360:r=30:d=0.4", "-vf", "format=yuv420p"]
    args += video_args(enc, "standard", 640, 360, 30)
    args += ["-f", "null", "-"]
    try:
        p = ffmpeg.run_capture([ffmpeg.ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error"] + args,
                               timeout=25)
        return p.returncode == 0
    except Exception:
        return False


def detect(force=False):
    """返回 {enc: bool}，结果缓存（进程内）。"""
    global _detected
    with _detect_lock:
        if _detected is not None and not force:
            return dict(_detected)
        res = {"cpu": False, "nvenc": False, "qsv": False, "amf": False}
        try:
            p = ffmpeg.run_capture([ffmpeg.ffmpeg_bin(), "-hide_banner", "-encoders"], timeout=20)
            listing = p.stdout or ""
        except Exception:
            listing = ""
        for enc, meta in ENCODERS.items():
            if meta["codec"] not in listing:
                continue
            res[enc] = _test_encoder(enc)
        _detected = res
        return dict(res)


def resolve(pref: str):
    """把用户选择（auto/cpu/nvenc/qsv/amf）解析成实际可用的编码器。"""
    avail = detect()
    if pref and pref != "auto":
        if avail.get(pref):
            return pref
        return "cpu"
    for enc in ("nvenc", "amf", "qsv"):
        if avail.get(enc):
            return enc
    return "cpu"


def describe(avail=None):
    avail = avail or detect()
    names = [ENCODERS[k]["short"] for k in ("nvenc", "qsv", "amf") if avail.get(k)]
    if names:
        return "可用硬件编码：" + "、".join(names)
    return "未检测到可用的显卡硬件编码，将使用 CPU 编码"


def cpu_count():
    return os.cpu_count() or 4
