"""全局设置（data/settings.json）。"""
import os

from . import ffmpeg, paths
from .models import load_json, save_json

DEFAULTS = {
    "theme": "dark",
    "ffmpeg_dir": "",
    "output_root": "",
    "low_priority": True,
    "perf_mode": "",          # smooth 流畅优先 / balanced 均衡 / fast 速度优先；空 = 按旧的 low_priority
    "default_encoder": "auto",
    "studio_out_dir": "",
    "last_config": None,
    "first_run": True,
    "agree": False,
}

_settings = None


def _file():
    return paths.data_path("settings.json")


def get():
    global _settings
    if _settings is None:
        data = load_json(_file(), {}) or {}
        s = dict(DEFAULTS)
        s.update(data)
        _settings = s
    return _settings


def save():
    if _settings is not None:
        try:
            save_json(_file(), _settings)
        except OSError:
            pass


def set(key, value, persist=True):  # noqa: A001
    get()[key] = value
    if persist:
        save()


def apply():
    s = get()
    ffmpeg.set_custom_dir(s.get("ffmpeg_dir") or None)
    ffmpeg.set_perf_mode(perf_mode())


def perf_mode():
    s = get()
    return s.get("perf_mode") or ("smooth" if s.get("low_priority", True) else "fast")


def output_root():
    return get().get("output_root") or paths.default_output_root()


def thumbs_cache_size():
    total = 0
    root = paths.cache_dir()
    for dp, dn, fn in os.walk(root):
        for f in fn:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return total
