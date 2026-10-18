"""后台任务：在线程里跑耗时函数，结果通过 Qt 信号回到界面线程（Qt 控件只能在主线程操作）。"""
import threading
import traceback

from PySide6.QtCore import QObject, Signal, QTimer

_alive = set()
_alive_lock = threading.Lock()


class _Relay(QObject):
    done = Signal(object)
    failed = Signal(object)


def run_bg(fn, *args, on_done=None, on_error=None, **kwargs):
    """后台执行 fn(*args, **kwargs)。on_done(result)/on_error(exc) 在主线程回调。"""
    relay = _Relay()
    with _alive_lock:
        _alive.add(relay)

    def finish():
        with _alive_lock:
            _alive.discard(relay)

    if on_done:
        relay.done.connect(on_done)
    if on_error:
        relay.failed.connect(on_error)
    relay.done.connect(lambda *_: finish())
    relay.failed.connect(lambda *_: finish())

    def target():
        try:
            res = fn(*args, **kwargs)
        except Exception as e:  # noqa
            e.tb = traceback.format_exc()
            relay.failed.emit(e)
            return
        relay.done.emit(res)

    th = threading.Thread(target=target, daemon=True)
    th.start()
    return th


class EventBridge(QObject):
    """引擎回调（任意线程）→ 主线程信号。"""
    event = Signal(str, object)

    def __call__(self, ev, data):
        self.event.emit(ev, data)


class Debouncer(QObject):
    """合并频繁触发（如拖动滑杆）后只执行最后一次。"""

    def __init__(self, ms, fn, parent=None):
        super().__init__(parent)
        self.fn = fn
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(ms)
        self.timer.timeout.connect(self.fn)

    def __call__(self, *a):
        self.timer.start()


class LatestOnly:
    """只保留最新一次请求的结果（预览渲染用，避免旧结果覆盖新结果）。"""

    def __init__(self):
        self.seq = 0
        self.busy = False
        self.pending = None

    def submit(self, fn, on_done, on_error=None):
        self.seq += 1
        my = self.seq
        if self.busy:
            self.pending = (fn, on_done, on_error)
            return
        self.busy = True

        def done(res):
            self.busy = False
            if my == self.seq:
                on_done(res)
            self._next()

        def err(e):
            self.busy = False
            if on_error and my == self.seq:
                on_error(e)
            self._next()
        run_bg(fn, on_done=done, on_error=err)

    def _next(self):
        if self.pending:
            fn, d, e = self.pending
            self.pending = None
            self.submit(fn, d, e)
