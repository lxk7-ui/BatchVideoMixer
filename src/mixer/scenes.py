"""镜头切分（场景检测）：让片段尽量从镜头切点开始/结束，混剪更自然。结果按文件签名缓存。"""
import hashlib
import json
import os
import re
import threading

from . import ffmpeg, paths

_lock = threading.Lock()
_mem = {}
_re = re.compile(r"lavfi\.scd\.time:\s*([0-9.]+)")


def _cache_file(info):
    key = hashlib.md5(f"{info.path}|{info.sig}|v2".encode("utf-8")).hexdigest()[:20]
    return os.path.join(paths.cache_dir("scenes"), key + ".json")


def detect(info, threshold=10.0, cancel=None):
    """返回切点时间列表（秒，升序，不含 0 与结尾）。失败返回 []。"""
    if info.kind != "video" or info.duration < 3:
        return []
    cf = _cache_file(info)
    with _lock:
        if cf in _mem:
            return list(_mem[cf])
    if os.path.isfile(cf):
        try:
            data = json.load(open(cf, encoding="utf-8"))
            cuts = [float(x) for x in data.get("cuts", [])]
            with _lock:
                _mem[cf] = cuts
            return list(cuts)
        except Exception:
            pass
    # 低分辨率 + 降帧解码，速度快
    sample_fps = 10 if info.duration <= 600 else 5
    try:
        p = ffmpeg.run_capture([ffmpeg.ffmpeg_bin(), "-hide_banner", "-nostdin", "-i", info.path, "-map", "0:v:0",
                                "-vf", f"fps={sample_fps},scale=192:-2,scdet=threshold={threshold}", "-an",
                                "-f", "null", "-"], timeout=max(120, int(info.duration * 2)))
        text = p.stderr or ""
    except Exception:
        return []
    cuts = []
    for m in _re.finditer(text):
        t = float(m.group(1))
        if 0.3 < t < info.duration - 0.3:
            if not cuts or t - cuts[-1] > 0.4:
                cuts.append(round(t, 3))
    try:
        with open(cf, "w", encoding="utf-8") as fh:
            json.dump({"path": info.path, "cuts": cuts}, fh)
    except OSError:
        pass
    with _lock:
        _mem[cf] = cuts
    return list(cuts)


def shots(info, cuts):
    """切点 → 镜头区间列表 [(start, end)]。"""
    bounds = [0.0] + [c for c in cuts if 0 < c < info.duration] + [info.duration]
    out = []
    for a, b in zip(bounds, bounds[1:]):
        if b - a > 0.05:
            out.append((a, b))
    return out
