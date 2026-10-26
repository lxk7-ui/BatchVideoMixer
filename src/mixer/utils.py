"""杂项：防休眠、磁盘/内存检测、打开文件夹、时间格式化。"""
import os
import shutil
import subprocess
import sys


def prevent_sleep(on=True):
    """批量处理期间阻止系统自动睡眠（不阻止关屏）。"""
    if os.name != "nt":
        return
    try:
        import ctypes
        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0)
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
    except Exception:
        pass


def free_disk_gb(path):
    try:
        p = path
        while p and not os.path.exists(p):
            p = os.path.dirname(p)
        return shutil.disk_usage(p or ".").free / (1024 ** 3)
    except Exception:
        return 999.0


def memory_gb():
    """返回 (总内存GB, 可用内存GB)。"""
    try:
        import ctypes
        from ctypes import wintypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        return st.ullTotalPhys / 1024 ** 3, st.ullAvailPhys / 1024 ** 3
    except Exception:
        return 8.0, 4.0


def cpu_budget():
    """按“生成时电脑负载”设置，可用于渲染的 CPU 线程数（流畅优先约 60%，均衡约 80%，速度优先全部）。"""
    from . import ffmpeg
    cores = os.cpu_count() or 4
    frac = ffmpeg.PERF_MODES.get(ffmpeg.perf_mode(), {}).get("cpu", 1.0)
    return max(1, int(round(cores * frac)))


def recommend_concurrency(W=1080, H=1920, gpu=False):
    """并发推荐：按可用 CPU（负载设置）与可用内存自适应，限制 1~4。"""
    cores = cpu_budget()
    base = max(1, min(4, cores // 4))
    if W * H > 1920 * 1080 * 1.5:
        base = max(1, base - 1)
    if gpu:
        base = min(4, base + 1)
    _, avail = memory_gb()
    per = 0.9 if W * H <= 1920 * 1080 else 1.6
    base = max(1, min(base, int(max(1.0, avail) / per)))
    return base


def recommend_part_workers(W=1080, H=1920, concurrency=1):
    """片段渲染并行数：按核数（每路约 3~4 线程）与可用内存（每路约 0.6GB@1080p）自适应，限制 2~8。"""
    cores = cpu_budget()
    n = max(1, min(8, cores // 3))
    _, avail = memory_gb()
    per = 0.6 if W * H <= 1920 * 1080 else 1.1
    return max(1, min(n, int(max(1.0, avail - 0.5) / per)))


def threads_per_job(concurrency):
    return max(1, min(16, cpu_budget() // max(1, concurrency)))


def final_threads(concurrency):
    """最终合成每路的线程数：速度优先时不限制（0），其余按可用 CPU 平分。"""
    from . import ffmpeg
    if ffmpeg.perf_mode() == "fast":
        return 0
    return max(2, cpu_budget() // max(1, concurrency))


def open_path(path, select=False):
    try:
        if select and os.path.exists(path):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            os.startfile(os.path.normpath(path))  # noqa
    except Exception:
        pass


def fmt_secs(s):
    s = int(round(max(0, s or 0)))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


def fmt_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"
