"""贴纸库：内置原创贴纸 + Fluent 3D 表情（MIT）+ 用户自定义贴纸（支持 PNG/JPG/WebP/GIF/APNG 动图）。

用户贴纸放在 data/我的贴纸/<分类>/ 下即可被识别（直接拖文件进去也行），子文件夹名即分类名。
动图贴纸会被自动转成内部统一格式（MOV/PNG 带透明），保证合成稳定（避免 APNG 循环卡死等问题）。
"""
import hashlib
import json
import os
import shutil
import tempfile
import threading
from dataclasses import dataclass

from . import paths

STATIC_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
ANIM_EXTS = {".gif", ".apng", ".webp", ".png"}

BUILTIN_ORDER = ["电商标签", "互动引导", "箭头指示", "装饰元素", "对话气泡",
                 "表情_表情", "表情_手势", "表情_爱心符号", "表情_购物美食", "表情_萌宠动物"]
DISPLAY = {
    "表情_表情": "3D表情", "表情_手势": "3D手势", "表情_爱心符号": "3D符号",
    "表情_购物美食": "3D购物美食", "表情_萌宠动物": "3D萌宠",
}
USER_PREFIX = "★"


@dataclass
class Sticker:
    path: str
    name: str
    category: str
    builtin: bool
    animated: bool = False
    anim_hint: str = ""


_lock = threading.Lock()
_lib_cache = None


def display_name(cat):
    return DISPLAY.get(cat, cat)


def _is_animated(path):
    ext = os.path.splitext(path)[1].lower()
    if ext not in ANIM_EXTS:
        return False
    try:
        from PIL import Image
        with Image.open(path) as im:
            return getattr(im, "n_frames", 1) > 1
    except Exception:
        return False


def _scan_dir(root, builtin):
    cats = {}
    if not os.path.isdir(root):
        return cats
    entries = sorted(os.listdir(root))
    # 根目录散放的文件归入“我的贴纸”
    loose = [e for e in entries if os.path.isfile(os.path.join(root, e))]
    dirs = [e for e in entries if os.path.isdir(os.path.join(root, e))]
    groups = []
    if loose and not builtin:
        groups.append(("我的贴纸", root, loose))
    for d in dirs:
        full = os.path.join(root, d)
        groups.append((d, full, sorted(os.listdir(full))))
    for cat, folder, files in groups:
        meta = {}
        mp = os.path.join(folder, "meta.json")
        if os.path.isfile(mp):
            try:
                meta = json.load(open(mp, encoding="utf-8"))
            except Exception:
                meta = {}
        items = []
        for f in files:
            fp = os.path.join(folder, f)
            ext = os.path.splitext(f)[1].lower()
            if not os.path.isfile(fp) or (ext not in STATIC_EXTS and ext not in ANIM_EXTS):
                continue
            animated = (not builtin) and _is_animated(fp)
            items.append(Sticker(path=fp, name=os.path.splitext(f)[0], category=cat, builtin=builtin,
                                 animated=animated, anim_hint=(meta.get(f) or {}).get("anim", "")))
        if items:
            key = cat if builtin else USER_PREFIX + cat
            cats.setdefault(key, []).extend(items)
    return cats


def library(refresh=False):
    """返回有序 dict：分类 → [Sticker]。用户分类以 ★ 开头排在最前。"""
    global _lib_cache
    with _lock:
        if _lib_cache is not None and not refresh:
            return _lib_cache
        user = _scan_dir(paths.user_sticker_dir(), builtin=False)
        builtin = _scan_dir(paths.assets_dir("stickers"), builtin=True)
        ordered = {}
        for k in sorted(user.keys()):
            ordered[k] = user[k]
        for k in BUILTIN_ORDER:
            if k in builtin:
                ordered[k] = builtin.pop(k)
        for k in sorted(builtin.keys()):
            ordered[k] = builtin[k]
        _lib_cache = ordered
        return ordered


def all_stickers():
    out = []
    for items in library().values():
        out.extend(items)
    return out


def find_by_path(path):
    for s in all_stickers():
        if os.path.normcase(s.path) == os.path.normcase(path):
            return s
    return None


def import_files(files, category="我的贴纸"):
    """复制贴纸到用户库。返回导入成功的新路径列表。"""
    category = (category or "我的贴纸").strip().replace("/", "_").replace("\\", "_") or "我的贴纸"
    dest = paths.user_sticker_dir() if category == "我的贴纸" else os.path.join(paths.user_sticker_dir(), category)
    os.makedirs(dest, exist_ok=True)
    out = []
    for f in files:
        ext = os.path.splitext(f)[1].lower()
        if ext not in STATIC_EXTS and ext not in ANIM_EXTS:
            continue
        base = os.path.basename(f)
        target = os.path.join(dest, base)
        i = 1
        while os.path.exists(target):
            stem, e = os.path.splitext(base)
            target = os.path.join(dest, f"{stem}_{i}{e}")
            i += 1
        try:
            shutil.copy2(f, target)
            out.append(target)
        except OSError:
            pass
    library(refresh=True)
    return out


def delete_user_sticker(path):
    root = os.path.normcase(os.path.abspath(paths.user_sticker_dir()))
    if not os.path.normcase(os.path.abspath(path)).startswith(root):
        return False
    try:
        os.remove(path)
    except OSError:
        return False
    library(refresh=True)
    return True


def _key(path, extra=""):
    try:
        st = os.stat(path)
        sig = f"{path}|{st.st_size}|{st.st_mtime}|{extra}"
    except OSError:
        sig = f"{path}|{extra}"
    return hashlib.md5(sig.encode("utf-8")).hexdigest()[:16]


def thumbnail(path, size=96):
    """缓存缩略图（动图取第一帧），返回 PNG 路径。"""
    from PIL import Image
    out = os.path.join(paths.cache_dir("sticker_thumbs"), _key(path, str(size)) + ".png")
    if os.path.isfile(out):
        return out
    try:
        with Image.open(path) as im:
            im.seek(0)
            im = im.convert("RGBA")
            im.thumbnail((size, size), Image.LANCZOS)
            im.save(out)
    except Exception:
        return path
    return out


def normalize_animated(path, max_side=512, fps=25):
    """动图 → MOV(PNG 编码，带透明)。返回 (mov 路径, 时长秒)。失败返回 (None, 0)。"""
    from PIL import Image
    from . import ffmpeg
    out = os.path.join(paths.cache_dir("sticker_anim"), _key(path, f"{max_side}-{fps}") + ".mov")
    if os.path.isfile(out) and os.path.getsize(out) > 0:
        try:
            with Image.open(path) as im:
                total = sum((im.seek(i) or im.info.get("duration", 100)) for i in range(getattr(im, "n_frames", 1)))
        except Exception:
            total = 1000
        return out, max(0.04, total / 1000.0)
    tmp = tempfile.mkdtemp(prefix="stk_", dir=paths.cache_dir("tmp"))
    try:
        with Image.open(path) as im:
            n = getattr(im, "n_frames", 1)
            if n <= 1:
                return None, 0
            frames, durs = [], []
            for i in range(n):
                im.seek(i)
                fr = im.convert("RGBA")
                if max(fr.size) > max_side:
                    fr.thumbnail((max_side, max_side), Image.LANCZOS)
                frames.append(fr.copy())
                d = im.info.get("duration", 100) or 100
                durs.append(max(20, int(d)))
        # 统一画布尺寸（部分 GIF 帧大小不一）
        W = max(f.width for f in frames) // 2 * 2 or 2
        H = max(f.height for f in frames) // 2 * 2 or 2
        total_ms = sum(durs)
        nout = max(1, int(round(total_ms / 1000.0 * fps)))
        bounds = []
        acc = 0
        for d in durs:
            acc += d
            bounds.append(acc)
        idx = 0
        for k in range(nout):
            tms = (k + 0.5) * 1000.0 / fps
            while idx < len(bounds) - 1 and tms > bounds[idx]:
                idx += 1
            canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            fr = frames[idx]
            canvas.paste(fr, ((W - fr.width) // 2, (H - fr.height) // 2))
            canvas.save(os.path.join(tmp, f"{k:05d}.png"))
        ffmpeg.run_capture([ffmpeg.ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                            "-framerate", str(fps), "-i", os.path.join(tmp, "%05d.png"),
                            "-c:v", "png", "-pix_fmt", "rgba", out], timeout=300)
        if os.path.isfile(out) and os.path.getsize(out) > 0:
            return out, nout / fps
        return None, 0
    except Exception:
        return None, 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
