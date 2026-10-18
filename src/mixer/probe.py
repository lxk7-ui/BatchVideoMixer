"""媒体探测（ffprobe），带缓存。以 ffprobe 结果为准，不信文件后缀。"""
import json
import os
import threading
from dataclasses import dataclass, asdict, field

from . import ffmpeg

VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".flv", ".wmv", ".m4v", ".webm", ".ts", ".mts", ".m2ts",
              ".3gp", ".mpg", ".mpeg", ".vob", ".f4v", ".rmvb", ".rm", ".asf", ".dav"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff", ".jfif"}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".wma", ".opus", ".amr", ".ape"}
ANIM_EXTS = {".gif"}


def ext_of(path):
    return os.path.splitext(path)[1].lower()


def guess_kind(path):
    e = ext_of(path)
    if e in VIDEO_EXTS:
        return "video"
    if e in IMAGE_EXTS:
        return "image"
    if e in AUDIO_EXTS:
        return "audio"
    if e in ANIM_EXTS:
        return "image"
    return ""


@dataclass
class MediaInfo:
    path: str
    ok: bool = False
    error: str = ""
    kind: str = ""          # video / image / audio
    duration: float = 0.0
    width: int = 0          # 显示宽（已考虑旋转）
    height: int = 0
    fps: float = 0.0
    has_audio: bool = False
    has_video: bool = False
    vcodec: str = ""
    acodec: str = ""
    rotation: int = 0
    hdr: bool = False
    pix_fmt: str = ""
    size: int = 0
    mtime: float = 0.0
    bitrate: int = 0
    extra: dict = field(default_factory=dict)

    @property
    def sig(self):
        return f"{self.size}-{int(self.mtime)}"

    @property
    def name(self):
        return os.path.basename(self.path)

    @property
    def stem(self):
        return os.path.splitext(os.path.basename(self.path))[0]

    def to_dict(self):
        return asdict(self)

    def res_text(self):
        if self.width and self.height:
            return f"{self.width}×{self.height}"
        return "-"


_cache = {}
_lock = threading.Lock()


def file_sig(path):
    try:
        st = os.stat(path)
        return st.st_size, st.st_mtime
    except OSError:
        return 0, 0.0


def _parse_rate(s):
    try:
        if not s or s in ("0/0", "N/A"):
            return 0.0
        if "/" in s:
            a, b = s.split("/")
            a, b = float(a), float(b)
            return a / b if b else 0.0
        return float(s)
    except Exception:
        return 0.0


def probe(path: str, use_cache=True) -> MediaInfo:
    path = os.path.abspath(path)
    size, mtime = file_sig(path)
    key = (path, size, mtime)
    if use_cache:
        with _lock:
            if key in _cache:
                return _cache[key]
    info = MediaInfo(path=path, size=size, mtime=mtime, kind=guess_kind(path))
    if not os.path.isfile(path):
        info.error = "文件不存在"
        return info
    if size <= 0:
        info.error = "空文件"
        return info
    try:
        p = ffmpeg.run_capture([ffmpeg.ffprobe_bin(), "-v", "error", "-print_format", "json",
                                "-show_format", "-show_streams", path], timeout=60)
        data = json.loads(p.stdout or "{}")
    except ffmpeg.FFmpegError as e:
        info.error = f"探测失败：{e}"
        return info
    except Exception as e:
        info.error = f"探测失败：{e}"
        return info

    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    vstreams = [s for s in streams if s.get("codec_type") == "video"
                and not (s.get("disposition") or {}).get("attached_pic")]
    astreams = [s for s in streams if s.get("codec_type") == "audio"]
    fmt_name = (fmt.get("format_name") or "").lower()

    try:
        info.duration = float(fmt.get("duration") or 0)
    except ValueError:
        info.duration = 0.0
    try:
        info.bitrate = int(fmt.get("bit_rate") or 0)
    except ValueError:
        info.bitrate = 0

    if vstreams:
        v = vstreams[0]
        info.has_video = True
        info.vcodec = v.get("codec_name", "")
        w, h = int(v.get("width") or 0), int(v.get("height") or 0)
        rot = 0
        for sd in v.get("side_data_list") or []:
            if "rotation" in sd:
                try:
                    rot = int(round(float(sd["rotation"])))
                except Exception:
                    pass
        if not rot:
            try:
                rot = int((v.get("tags") or {}).get("rotate", 0))
            except Exception:
                rot = 0
        rot = rot % 360
        info.rotation = rot
        if rot in (90, 270):
            w, h = h, w
        info.width, info.height = w, h
        fps = _parse_rate(v.get("avg_frame_rate")) or _parse_rate(v.get("r_frame_rate"))
        if fps <= 0 or fps > 240:
            fps = _parse_rate(v.get("r_frame_rate"))
        info.fps = fps if 0 < fps <= 240 else 30.0
        info.pix_fmt = v.get("pix_fmt", "")
        trc = (v.get("color_transfer") or "").lower()
        info.hdr = trc in ("smpte2084", "arib-std-b67")
        info.extra["color_space"] = (v.get("color_space") or "").lower()
        info.extra["color_range"] = (v.get("color_range") or "").lower()
        if info.pix_fmt.startswith("yuvj"):
            info.extra["color_range"] = "pc"
        if not info.duration:
            try:
                info.duration = float(v.get("duration") or 0)
            except ValueError:
                pass
    if astreams:
        info.has_audio = True
        info.acodec = astreams[0].get("codec_name", "")
        if not info.duration:
            try:
                info.duration = float(astreams[0].get("duration") or 0)
            except ValueError:
                pass

    is_image_fmt = any(k in fmt_name for k in ("image2", "png_pipe", "jpeg_pipe", "bmp_pipe", "webp_pipe",
                                               "tiff_pipe", "gif"))
    ext_kind = guess_kind(path)
    if info.has_video and (ext_kind == "image" or (is_image_fmt and info.duration <= 0.05)):
        # 静态图（gif 多帧则当作视频）
        nb = 0
        try:
            nb = int(vstreams[0].get("nb_frames") or 0)
        except Exception:
            nb = 0
        if ext_of(path) == ".gif" and info.duration > 0.2:
            info.kind = "video"
        elif nb > 1 and info.duration > 0.2 and ext_of(path) in (".webp",):
            info.kind = "video"
        else:
            info.kind = "image"
            info.duration = 0.0
    elif info.has_video:
        info.kind = "video"
    elif info.has_audio:
        info.kind = "audio"

    # 有效性校验
    if info.kind == "video":
        if info.width < 16 or info.height < 16:
            info.error = "无有效画面"
        elif info.duration <= 0.2:
            info.error = "时长无效（<0.2s 或无法读取）"
        else:
            info.ok = True
    elif info.kind == "image":
        if info.width < 8 or info.height < 8:
            info.error = "无效图片"
        else:
            info.ok = True
    elif info.kind == "audio":
        if info.duration <= 0.2:
            info.error = "音频时长无效"
        else:
            info.ok = True
    else:
        info.error = "无法识别的媒体文件"

    with _lock:
        _cache[key] = info
    return info


def probe_many(paths, workers=4, on_each=None):
    """并发探测。on_each(info) 在每个完成后回调（工作线程中）。"""
    from concurrent.futures import ThreadPoolExecutor
    results = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(probe, p): p for p in paths}
        for f in futs:
            info = f.result()
            results[futs[f]] = info
            if on_each:
                on_each(info)
    return results


def scan_paths(paths, kinds=("video", "image")):
    """展开文件/文件夹（递归），按类型过滤，去重并保持顺序。"""
    seen = set()
    out = []
    for p in paths:
        p = os.path.abspath(p)
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if not d.startswith((".", "_tmp"))]
                for f in sorted(files):
                    fp = os.path.join(root, f)
                    if guess_kind(fp) in kinds and fp not in seen:
                        seen.add(fp)
                        out.append(fp)
        elif os.path.isfile(p):
            if guess_kind(p) in kinds and p not in seen:
                seen.add(p)
                out.append(p)
    return out
