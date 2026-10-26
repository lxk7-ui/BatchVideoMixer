"""ffmpeg / ffprobe 统一封装。

所有外部调用都经过这里，统一处理：
- 不弹黑窗（CREATE_NO_WINDOW）、stdin 置空（-nostdin）
- 进度解析（-progress pipe:1）、取消、无进度看门狗（防卡死）、总超时
- 子进程挂到 Windows Job Object：主程序退出/崩溃时 ffmpeg 自动被结束，不留僵尸进程
- 失败原因分类
"""
import collections
import glob
import os
import re
import shutil
import subprocess
import threading
import time

from . import paths

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class FFmpegError(Exception):
    def __init__(self, msg, cmd=None, stderr="", category="其他"):
        super().__init__(msg)
        self.cmd = cmd or []
        self.stderr = stderr or ""
        self.category = category


class Cancelled(Exception):
    """用户主动停止。"""


# ----------------------------------------------------------------------------
# 定位 ffmpeg
# ----------------------------------------------------------------------------
_custom_dir = None
_cache = {}


def set_custom_dir(path):
    """设置页指定的 ffmpeg 目录（为空表示自动）。"""
    global _custom_dir
    _custom_dir = path or None
    _cache.clear()


def _candidate_dirs():
    dirs = []
    if _custom_dir:
        dirs.append(_custom_dir)
    env = os.environ.get("BM_FFMPEG_DIR")
    if env:
        dirs.append(env)
    dirs.append(os.path.join(paths.exe_dir(), "ffmpeg"))
    dirs.append(os.path.join(paths.resource_dir(), "ffmpeg"))
    if not paths.is_frozen():
        dirs.append(os.path.join(paths.project_root(), "_deps", "ffmpeg"))
    return dirs


def find_tool(name: str):
    if name in _cache:
        return _cache[name]
    exe = name + (".exe" if os.name == "nt" else "")
    found = None
    env_single = os.environ.get("BM_" + name.upper())
    if env_single and os.path.isfile(env_single):
        found = env_single
    if not found:
        for d in _candidate_dirs():
            p = os.path.join(d, exe)
            if os.path.isfile(p):
                found = p
                break
    if not found:
        found = shutil.which(name)
    _cache[name] = found
    return found


def ffmpeg_bin():
    p = find_tool("ffmpeg")
    if not p:
        raise FFmpegError("找不到 ffmpeg.exe，请检查程序目录下的 ffmpeg 文件夹或在设置中指定。", category="环境")
    return p


def ffprobe_bin():
    p = find_tool("ffprobe")
    if not p:
        raise FFmpegError("找不到 ffprobe.exe，请检查程序目录下的 ffmpeg 文件夹或在设置中指定。", category="环境")
    return p


def ffplay_bin():
    return find_tool("ffplay")


_version_cache = {}


def version_info():
    """返回 (版本字符串, 主版本号)。"""
    b = ffmpeg_bin()
    if b in _version_cache:
        return _version_cache[b]
    try:
        p = run_capture([b, "-hide_banner", "-version"], timeout=20)
        first = (p.stdout or "").splitlines()[0] if p.stdout else ""
        m = re.search(r"version\s+n?(\d+)\.(\d+)", first)
        major = int(m.group(1)) if m else 0
        res = (first, major)
    except Exception as e:  # noqa
        res = (f"未知 ({e})", 0)
    _version_cache[b] = res
    return res


def filter_script_args(script_path: str):
    """ffmpeg>=7 使用 -/filter_complex 文件；老版本用 -filter_complex_script。"""
    major = version_info()[1]
    if major >= 7 or major == 0:
        return ["-/filter_complex", script_path]
    return ["-filter_complex_script", script_path]


# ----------------------------------------------------------------------------
# Windows Job Object：主进程退出时自动杀掉所有 ffmpeg 子进程
# ----------------------------------------------------------------------------
_job_handle = None
_job_lock = threading.Lock()


def _get_job():
    global _job_handle
    if os.name != "nt":
        return None
    with _job_lock:
        if _job_handle is not None:
            return _job_handle
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [(n, ctypes.c_ulonglong) for n in (
                    "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                    "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

            class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_longlong),
                    ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            kernel32.CreateJobObjectW.restype = wintypes.HANDLE
            h = kernel32.CreateJobObjectW(None, None)
            if not h:
                _job_handle = 0
                return 0
            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            kernel32.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info))
            _apply_cpu_cap(h)
            _job_handle = h
        except Exception:
            _job_handle = 0
        return _job_handle


def _assign_to_job(proc):
    h = _get_job()
    if not h:
        return
    try:
        import ctypes
        ctypes.windll.kernel32.AssignProcessToJobObject(h, int(proc._handle))
    except Exception:
        pass


# ----------------------------------------------------------------------------
# 生成时的电脑负载（批量跑时不卡电脑）
# ----------------------------------------------------------------------------
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
IDLE_PRIORITY_CLASS = 0x00000040
PERF_MODES = {
    # cpu：全部 ffmpeg 进程合计最多占用的 CPU 比例（Windows 作业对象硬上限，界面和其他软件始终有余量）
    # priority：进程优先级；background：同时调低内存和磁盘读写优先级，减少卡顿
    "smooth": {"label": "流畅优先", "cpu": 0.6, "priority": IDLE_PRIORITY_CLASS, "background": True},
    "balanced": {"label": "均衡", "cpu": 0.8, "priority": BELOW_NORMAL_PRIORITY_CLASS, "background": True},
    "fast": {"label": "速度优先", "cpu": 1.0, "priority": BELOW_NORMAL_PRIORITY_CLASS, "background": False},
}
_perf_mode = "smooth"


def set_perf_mode(mode: str):
    global _perf_mode
    _perf_mode = mode if mode in PERF_MODES else "smooth"
    h = _get_job()
    if h:
        _apply_cpu_cap(h)


def perf_mode():
    return _perf_mode


def set_low_priority(flag: bool):
    """兼容旧设置：开 = 流畅优先，关 = 速度优先。"""
    set_perf_mode("smooth" if flag else "fast")


def _apply_cpu_cap(h):
    """给作业对象设置 CPU 总占用硬上限（Windows 8 及以上；速度优先时取消上限）。"""
    try:
        import ctypes
        from ctypes import wintypes

        class CPU_RATE_CONTROL(ctypes.Structure):
            _fields_ = [("ControlFlags", wintypes.DWORD), ("CpuRate", wintypes.DWORD)]
        frac = PERF_MODES[_perf_mode]["cpu"]
        info = CPU_RATE_CONTROL()
        if frac < 0.999:
            info.ControlFlags = 0x1 | 0x4      # JOB_OBJECT_CPU_RATE_CONTROL_ENABLE | HARD_CAP
            info.CpuRate = max(100, min(10000, int(frac * 10000)))
        ctypes.windll.kernel32.SetInformationJobObject(wintypes.HANDLE(h), 15, ctypes.byref(info),
                                                      ctypes.sizeof(info))
    except Exception:
        pass


def _lower_io_memory(proc):
    """流畅 / 均衡模式：把子进程的内存优先级和磁盘读写优先级调低（失败不影响运行）。"""
    if os.name != "nt" or not PERF_MODES[_perf_mode]["background"]:
        return
    try:
        import ctypes
        from ctypes import wintypes
        h = wintypes.HANDLE(int(proc._handle))
        mem = wintypes.ULONG(2)                # MEMORY_PRIORITY_LOW
        k32 = ctypes.windll.kernel32
        k32.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.SetProcessInformation(h, 0, ctypes.byref(mem), ctypes.sizeof(mem))      # ProcessMemoryPriority
        io = wintypes.ULONG(1)                 # IoPriorityLow
        ctypes.windll.ntdll.NtSetInformationProcess(h, 33, ctypes.byref(io), ctypes.sizeof(io))  # ProcessIoPriority
    except Exception:
        pass


def _creationflags():
    f = CREATE_NO_WINDOW
    if os.name == "nt":
        f |= PERF_MODES[_perf_mode]["priority"]
    return f


# ----------------------------------------------------------------------------
# 运行
# ----------------------------------------------------------------------------
def run_capture(args, timeout=120, cwd=None, text=True):
    """短命令：一次性捕获输出（探测、抽帧等）。"""
    try:
        p = subprocess.Popen(
            args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=cwd, creationflags=_creationflags())
    except FileNotFoundError as e:
        raise FFmpegError(f"无法启动：{e}", cmd=args, category="环境")
    _assign_to_job(p)
    _lower_io_memory(p)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        out, err = p.communicate()
        raise FFmpegError(f"命令超时（>{timeout}s）", cmd=args, stderr=_dec(err), category="超时")
    if text:
        out = _dec(out)
        err = _dec(err)
    return subprocess.CompletedProcess(args, p.returncode, out, err)


def _dec(b):
    if b is None:
        return ""
    if isinstance(b, str):
        return b
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("utf-8", errors="replace")


_time_re = re.compile(r"out_time_(?:us|ms)=(\d+)")


def run_ffmpeg(args, duration=None, on_progress=None, cancel=None, cwd=None,
               stall_timeout=180, total_timeout=None, log=None, label=""):
    """运行一条 ffmpeg 命令（args 不含 ffmpeg 本身）。

    duration: 预计输出时长（秒），用于计算百分比。
    on_progress(frac): 0~1 进度回调。
    cancel: threading.Event，置位后立即结束进程并抛 Cancelled。
    stall_timeout: 多少秒无任何进度/输出视为卡死。
    """
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", "-loglevel", "warning",
           "-progress", "pipe:1", "-stats_period", "0.5"] + list(args)
    t0 = time.time()
    if log:
        log(f"$ {format_cmd(cmd)}", level="debug")
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=cwd, creationflags=_creationflags())
    except FileNotFoundError as e:
        raise FFmpegError(f"无法启动 ffmpeg：{e}", cmd=cmd, category="环境")
    _assign_to_job(proc)
    _lower_io_memory(proc)

    state = {"last": time.time(), "frac": 0.0}
    err_tail = collections.deque(maxlen=200)

    def read_out():
        for raw in iter(proc.stdout.readline, b""):
            state["last"] = time.time()
            line = _dec(raw).strip()
            m = _time_re.match(line)
            if m and duration and duration > 0:
                sec = int(m.group(1)) / 1_000_000.0
                frac = max(0.0, min(1.0, sec / duration))
                if frac > state["frac"]:
                    state["frac"] = frac
                    if on_progress:
                        try:
                            on_progress(frac)
                        except Exception:
                            pass

    def read_err():
        for raw in iter(proc.stderr.readline, b""):
            state["last"] = time.time()
            s = _dec(raw).rstrip()
            if s:
                err_tail.append(s)

    th1 = threading.Thread(target=read_out, daemon=True)
    th2 = threading.Thread(target=read_err, daemon=True)
    th1.start()
    th2.start()
    killed_reason = None
    while True:
        try:
            rc = proc.wait(timeout=0.25)
            break
        except subprocess.TimeoutExpired:
            pass
        now = time.time()
        if cancel is not None and cancel.is_set():
            killed_reason = "cancel"
        elif stall_timeout and now - state["last"] > stall_timeout:
            killed_reason = "stall"
        elif total_timeout and now - t0 > total_timeout:
            killed_reason = "timeout"
        if killed_reason:
            try:
                proc.kill()
            except Exception:
                pass
            rc = proc.wait()
            break
    th1.join(timeout=2)
    th2.join(timeout=2)
    stderr = "\n".join(err_tail)
    elapsed = time.time() - t0
    if log:
        log(f"  ↳ rc={rc} 用时 {elapsed:.1f}s {label}", level="debug")
    if killed_reason == "cancel":
        raise Cancelled()
    if killed_reason == "stall":
        raise FFmpegError(f"ffmpeg 超过 {stall_timeout}s 无进度，已强制结束（可能素材异常）", cmd=cmd,
                          stderr=stderr, category="卡死")
    if killed_reason == "timeout":
        raise FFmpegError(f"ffmpeg 运行超时（>{total_timeout:.0f}s）", cmd=cmd, stderr=stderr, category="超时")
    if rc != 0:
        raise FFmpegError(summarize_error(stderr) or f"ffmpeg 失败（rc={rc}）", cmd=cmd, stderr=stderr,
                          category=classify_error(stderr))
    if on_progress:
        try:
            on_progress(1.0)
        except Exception:
            pass
    return elapsed


def format_cmd(cmd):
    out = []
    for a in cmd:
        a = str(a)
        if not a or any(c in a for c in ' "\'&|<>^;[]'):
            out.append('"' + a.replace('"', '\\"') + '"')
        else:
            out.append(a)
    return " ".join(out)


def classify_error(stderr: str) -> str:
    s = (stderr or "").lower()
    if "no space left" in s or "disk full" in s or "there is not enough space" in s:
        return "磁盘空间不足"
    if "permission denied" in s or "access is denied" in s:
        return "无权限/文件被占用"
    encoder_hit = (
        "error while opening encoder" in s
        or "cannot load nvcuda" in s
        or ("nvenc" in s and "error" in s)
        or ("amf" in s and "failed" in s)
        or ("qsv" in s and "error" in s)
    )
    if encoder_hit:
        return "编码器失败"
    if ("moov atom not found" in s or "invalid data found" in s or "error while decoding" in s
            or "corrupt" in s):
        return "素材损坏"
    if "no such file" in s:
        return "文件不存在"
    if "cannot allocate memory" in s or "out of memory" in s:
        return "内存不足"
    return "其他"


def summarize_error(stderr: str) -> str:
    lines = [l for l in (stderr or "").splitlines() if l.strip()]
    if not lines:
        return ""
    keys = ("error", "invalid", "failed", "cannot", "no such", "unable", "not found", "denied")
    picked = [l for l in lines if any(k in l.lower() for k in keys)]
    use = picked[-3:] if picked else lines[-3:]
    return " | ".join(x.strip()[:200] for x in use)


# ----------------------------------------------------------------------------
# 工具
# ----------------------------------------------------------------------------
def escape_filter_value(v: str) -> str:
    """filtergraph 选项值转义（用于 drawtext 文本等）。"""
    v = str(v)
    for ch in ("\\", "'", ":", ",", ";", "[", "]", "="):
        v = v.replace(ch, "\\" + ch)
    return v


def concat_list_line(path: str) -> str:
    p = os.path.abspath(path).replace("\\", "/").replace("'", "'\\''")
    return f"file '{p}'\n"


def which_ffmpeg_dirs():
    return [d for d in _candidate_dirs() if glob.glob(os.path.join(d, "ffmpeg*"))]
