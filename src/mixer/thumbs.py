"""素材缩略图（按文件签名缓存）。"""
import hashlib
import os

from . import ffmpeg, paths


def thumb(info, width=192):
    key = hashlib.md5(f"{info.path}|{info.sig}|{width}".encode("utf-8")).hexdigest()[:20]
    out = os.path.join(paths.cache_dir("thumbs"), key + ".jpg")
    if os.path.isfile(out) and os.path.getsize(out) > 0:
        return out
    if not info.ok:
        return None
    args = [ffmpeg.ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
    if info.kind == "video":
        t = min(max(0.0, info.duration * 0.15), max(0.0, info.duration - 0.1))
        args += ["-ss", f"{t:.2f}"]
    args += ["-i", info.path, "-frames:v", "1", "-vf", f"scale={width}:-2:flags=bilinear", "-q:v", "4", out]
    try:
        ffmpeg.run_capture(args, timeout=30)
    except Exception:
        return None
    return out if os.path.isfile(out) else None
