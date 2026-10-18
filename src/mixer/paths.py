"""路径管理：开发态 / PyInstaller 打包态统一定位资源、数据、ffmpeg。"""
import os
import sys

from . import APP_ID


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def exe_dir() -> str:
    """exe 所在目录（打包态）或 src 目录（开发态）。"""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_dir() -> str:
    """只读资源根目录：打包态为 _internal（sys._MEIPASS），开发态为 src。"""
    if is_frozen():
        return getattr(sys, "_MEIPASS", exe_dir())
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def assets_dir(*parts) -> str:
    return os.path.join(resource_dir(), "assets", *parts)


def project_root() -> str:
    """开发态的工程根目录（src 的上一级）。"""
    return os.path.dirname(exe_dir()) if not is_frozen() else exe_dir()


def _writable(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        test = os.path.join(path, f".write_test_{os.getpid()}")   # 带进程号，多个进程同时启动也不会互相干扰
        with open(test, "w", encoding="utf-8") as fh:
            fh.write("ok")
    except OSError:
        return False
    try:
        os.remove(test)
    except OSError:
        pass    # 杀毒软件可能正在扫描刚写的文件导致删不掉；能写入就说明目录可写
    return True


_DATA_DIR = None


def data_dir() -> str:
    """用户数据目录（设置、方案、模板、我的贴纸、任务记录、缓存）。

    - 安装版 / 单文件版：%APPDATA%/BatchVideoMixer（升级、重装都不会丢）
    - 便携模式：程序旁边已有 data 文件夹或 portable.flag 时，数据跟着程序走（兼容 v1.0 绿色版）
    """
    global _DATA_DIR
    if _DATA_DIR:
        return _DATA_DIR
    candidates = []
    if is_frozen():
        side = os.path.join(exe_dir(), "data")
        if os.path.isdir(side) or os.path.isfile(os.path.join(exe_dir(), "portable.flag")):
            candidates.append(side)
    else:
        candidates.append(os.path.join(project_root(), "_work", "data"))
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    candidates.append(os.path.join(appdata, APP_ID))
    for c in candidates:
        if _writable(c):
            _DATA_DIR = c
            return c
    _DATA_DIR = candidates[-1]
    return _DATA_DIR


def data_path(*parts, mkdir=False) -> str:
    p = os.path.join(data_dir(), *parts)
    if mkdir:
        os.makedirs(p, exist_ok=True)
    return p


def cache_dir(*parts) -> str:
    p = os.path.join(data_dir(), "cache", *parts)
    os.makedirs(p, exist_ok=True)
    return p


def user_sticker_dir() -> str:
    return data_path("我的贴纸", mkdir=True)


def templates_dir() -> str:
    return data_path("模板", mkdir=True)


def presets_dir() -> str:
    return data_path("方案", mkdir=True)


def default_output_root() -> str:
    """默认输出根目录：桌面\\混剪输出。"""
    home = os.path.expanduser("~")
    desktop = os.path.join(home, "Desktop")
    try:
        import ctypes
        from ctypes import wintypes
        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        # CSIDL_DESKTOPDIRECTORY = 0x10
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) == 0 and buf.value:
            desktop = buf.value
    except Exception:
        pass
    return os.path.join(desktop, "混剪输出")
