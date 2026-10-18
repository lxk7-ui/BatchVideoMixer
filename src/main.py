"""批量视频混剪工具 - 程序入口。"""
import datetime
import os
import sys
import traceback


def _crash_log(text):
    try:
        from mixer import paths
        p = paths.data_path("crash.log")
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(f"\n===== {datetime.datetime.now():%Y-%m-%d %H:%M:%S} =====\n{text}\n")
        return p
    except Exception:
        return ""


def selftest_cli():
    """命令行自检：批量视频混剪工具.exe --selftest （结果写入 data/selftest.txt，退出码 0=正常）"""
    from mixer import settings, selftest
    settings.apply()
    ok, report = selftest.run(log=lambda *a: None)
    return 0 if ok else 1


def main():
    if "--selftest" in sys.argv:
        return selftest_cli()
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("BatchVideoMixer.App")
        except Exception:
            pass
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont, QGuiApplication, QPixmap
    from PySide6.QtWidgets import QApplication, QMessageBox, QSplashScreen

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("批量视频混剪工具")
    try:  # Qt 标准对话框中文化（确定/取消/是/否）
        from PySide6.QtCore import QTranslator, QLibraryInfo, QLocale
        tr = QTranslator(app)
        if tr.load(QLocale(QLocale.Chinese, QLocale.China), "qtbase", "_",
                   QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
            app.installTranslator(tr)
    except Exception:
        pass
    f = QFont("Microsoft YaHei UI")
    f.setPointSizeF(9.5)
    app.setFont(f)

    from mixer import settings
    from ui import theme
    settings.apply()
    from PySide6.QtGui import QFontDatabase
    if "Microsoft YaHei UI" not in QFontDatabase.families():
        # 非中文系统没有雅黑时，界面改用内置思源黑体
        from mixer import paths
        fid = QFontDatabase.addApplicationFont(paths.assets_dir("fonts", "NotoSansSC-VF.ttf"))
        fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
        if fams:
            f = QFont(fams[0])
            f.setPointSizeF(9.5)
            app.setFont(f)
    theme.set_palette(settings.get().get("theme", "dark"))
    app.setStyleSheet(theme.qss())
    app.setWindowIcon(theme.app_icon())

    splash = QSplashScreen(theme.app_icon().pixmap(160, 160))
    splash.showMessage("正在加载…", Qt.AlignBottom | Qt.AlignHCenter, Qt.white)
    splash.show()
    app.processEvents()

    def excepthook(t, v, tb):
        text = "".join(traceback.format_exception(t, v, tb))
        p = _crash_log(text)
        try:
            QMessageBox.critical(None, "程序出错", f"发生了未处理的错误（已记录到 {p}）：\n\n{v}")
        except Exception:
            pass
    sys.excepthook = excepthook

    from ui.main_window import MainWindow
    win = MainWindow()
    win.show()
    splash.finish(win)
    snap = os.environ.get("BVM_SNAPSHOT")
    if snap:  # 打包后自动化检查用：几秒后截图并退出
        from PySide6.QtCore import QTimer

        def _snap():
            win.grab().save(snap)
            app.quit()
        QTimer.singleShot(int(os.environ.get("BVM_SNAPSHOT_MS", "5000")), _snap)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
