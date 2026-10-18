"""一键打包：PyInstaller onedir + 随包 FFmpeg + 文档。

用法（在工程根目录运行）：
  python tools/build.py             只生成 dist/批量视频混剪工具/（开发自测用）
  python tools/build.py --package   另外生成 交付/批量视频混剪工具_安装包_v<版本>.exe（Inno Setup）
                                    和 交付/批量视频混剪工具_免安装版_v<版本>.exe（单文件，C# 启动器 + 内嵌程序包）
  python tools/build.py --zip       另外生成 交付/批量视频混剪工具_v<版本>_Win10-x64.zip（绿色版压缩包）
"""
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
BUILD = os.path.join(ROOT, "build")
DIST = os.path.join(ROOT, "dist")
NAME = "批量视频混剪工具"
FFMPEG_DIR = os.path.join(ROOT, "_deps", "ffmpeg")
DELIVER = os.path.join(ROOT, "交付")

sys.path.insert(0, SRC)
from mixer import VERSION  # noqa: E402

VER_TUPLE = tuple(int(x) for x in VERSION.split(".")) + (0,)

VERSION_INFO = f"""
VSVersionInfo(
  ffi=FixedFileInfo(filevers={VER_TUPLE}, prodvers={VER_TUPLE}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('080404b0', [
      StringStruct('CompanyName', '内部工具'),
      StringStruct('FileDescription', '{NAME}'),
      StringStruct('FileVersion', '{VERSION}'),
      StringStruct('InternalName', 'BatchVideoMixer'),
      StringStruct('LegalCopyright', '仅限处理自有或已授权素材'),
      StringStruct('OriginalFilename', '{NAME}.exe'),
      StringStruct('ProductName', '{NAME}'),
      StringStruct('ProductVersion', '{VERSION}')])]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"""

SPEC = r'''# -*- mode: python ; coding: utf-8 -*-
import os
SRC = {src!r}
a = Analysis(
    [os.path.join(SRC, 'main.py')],
    pathex=[SRC],
    binaries=[],
    datas=[(os.path.join(SRC, 'assets'), 'assets')],
    hiddenimports=['mixer.selftest'],
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', '_tkinter', 'unittest', 'pydoc', 'doctest', 'lib2to3', 'test', 'xmlrpc', 'pdb',
              'PySide6.QtNetwork', 'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtSql', 'PySide6.QtTest',
              'PySide6.QtDBus', 'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets', 'PySide6.QtPrintSupport',
              'PySide6.QtXml', 'PySide6.QtConcurrent', 'PySide6.QtDesigner', 'PySide6.QtUiTools', 'PySide6.QtHelp',
              'PySide6.QtSvgWidgets', 'PIL._avif', 'PIL.AvifImagePlugin'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name={name!r},
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon={icon!r},
    version={ver!r},
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, upx_exclude=[], name={name!r})
'''


def run(cmd, **kw):
    print("$", " ".join(cmd))
    subprocess.check_call(cmd, **kw)


def main():
    os.makedirs(BUILD, exist_ok=True)
    icon = os.path.join(BUILD, "app.ico")
    if not os.path.isfile(icon):
        run([sys.executable, os.path.join(ROOT, "tools", "make_icon.py"), icon])
    ver = os.path.join(BUILD, "version_info.txt")
    with open(ver, "w", encoding="utf-8") as fh:
        fh.write(VERSION_INFO)
    spec = os.path.join(BUILD, "app.spec")
    with open(spec, "w", encoding="utf-8") as fh:
        fh.write(SPEC.format(src=SRC, name=NAME, icon=icon, ver=ver))
    out_dir = os.path.join(DIST, NAME)
    for attempt in range(10):  # 杀软扫描/资源管理器可能短暂占用文件
        if not os.path.isdir(out_dir):
            break
        try:
            shutil.rmtree(out_dir)
        except OSError as e:
            print("等待文件释放…", e)
            import time
            time.sleep(3)
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", DIST,
         "--workpath", os.path.join(BUILD, "pyi"), spec])
    trim(out_dir)
    # 随包 FFmpeg（放在 exe 同级 ffmpeg 文件夹，便于替换/升级）
    ff_out = os.path.join(out_dir, "ffmpeg")
    os.makedirs(ff_out, exist_ok=True)
    for f in os.listdir(FFMPEG_DIR):
        if f.lower().endswith((".exe", ".dll", ".txt")):
            shutil.copy2(os.path.join(FFMPEG_DIR, f), os.path.join(ff_out, f))
    # 文档（由 docs/使用文档.md 生成）
    run([sys.executable, os.path.join(ROOT, "tools", "make_doc.py")])
    p = os.path.join(DELIVER, "使用文档.html")
    if os.path.isfile(p):
        shutil.copy2(p, os.path.join(out_dir, "使用文档.html"))
    lic = os.path.join(out_dir, "第三方授权说明.txt")
    with open(lic, "w", encoding="utf-8") as fh:
        fh.write(THIRD_PARTY)
    print("build ok:", out_dir)
    total = 0
    for dp, dn, fn in os.walk(out_dir):
        for f in fn:
            total += os.path.getsize(os.path.join(dp, f))
    print(f"size: {total / 1024 / 1024:.1f} MB")
    if "--package" in sys.argv:
        package(out_dir, icon)
    if "--zip" in sys.argv:
        make_zip(out_dir)


def package(out_dir, icon):
    """生成两种桌面版：安装包（推荐）+ 单文件免安装版，并清理旧的“文件夹版”交付物。"""
    import hashlib
    os.makedirs(DELIVER, exist_ok=True)
    for old in (os.path.join(DELIVER, NAME), os.path.join(DELIVER, f"{NAME}_v1.0.0_Win10-x64.zip")):
        if os.path.isdir(old):
            shutil.rmtree(old, ignore_errors=True)
        elif os.path.isfile(old):
            os.remove(old)
    # 1) 安装包（Inno Setup）
    iscc = next((p for p in (os.path.join(ROOT, "_deps", "innosetup", "ISCC.exe"),
                             os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Inno Setup 6", "ISCC.exe"),
                             os.path.join(os.environ.get("ProgramFiles", ""), "Inno Setup 6", "ISCC.exe"),
                             shutil.which("ISCC") or "") if p and os.path.isfile(p)), None)
    if not iscc:
        raise SystemExit("找不到 Inno Setup 6 的 ISCC.exe：请安装 Inno Setup 6，或把便携版放到 _deps\\innosetup")
    iss_src = os.path.join(ROOT, "tools", "installer.iss")
    iss = os.path.join(BUILD, "installer.iss")
    with open(iss_src, encoding="utf-8") as fh:
        text = fh.read()
    with open(iss, "w", encoding="utf-8-sig") as fh:   # Inno 需要带 BOM 的 UTF-8 才能正确识别中文
        fh.write(text)
    lang = os.path.join(ROOT, "tools", "ChineseSimplified.isl")
    run([iscc, "/Q", f"/DSrcDir={out_dir}", f"/DOutDir={DELIVER}", f"/DAppVer={VERSION}", f"/DIconFile={icon}",
         f"/DLangFile={lang}", iss])
    setup = os.path.join(DELIVER, f"{NAME}_安装包_v{VERSION}.exe")
    print("installer:", setup, f"{os.path.getsize(setup) / 1024 / 1024:.1f} MB")
    # 2) 单文件免安装版：程序本体打成 zip 嵌进 C# 启动器（Windows 自带 csc 编译）
    payload = os.path.join(BUILD, "payload.zip")
    if os.path.exists(payload):
        os.remove(payload)
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for dp, dn, fn in os.walk(out_dir):
            dn[:] = [d for d in dn if d != "data"]
            for f in fn:
                full = os.path.join(dp, f)
                z.write(full, os.path.relpath(full, out_dir))
    h = hashlib.sha1()
    with open(payload, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    stamp = f"{VERSION}-{h.hexdigest()[:16]}"
    with open(os.path.join(ROOT, "tools", "launcher", "Launcher.cs"), encoding="utf-8") as fh:
        cs = fh.read().replace("{VERSION}", VERSION).replace("{STAMP}", stamp)
    cs_gen = os.path.join(BUILD, "Launcher.gen.cs")
    with open(cs_gen, "w", encoding="utf-8-sig") as fh:
        fh.write(cs)
    fw = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Microsoft.NET", "Framework64", "v4.0.30319")
    single = os.path.join(DELIVER, f"{NAME}_免安装版_v{VERSION}.exe")
    run([os.path.join(fw, "csc.exe"), "/nologo", "/target:winexe", "/optimize+", "/codepage:65001",
         f"/win32icon:{icon}", f"/resource:{payload},payload.zip",
         f"/r:{os.path.join(fw, 'System.IO.Compression.dll')}",
         f"/r:{os.path.join(fw, 'System.IO.Compression.FileSystem.dll')}",
         "/r:System.Windows.Forms.dll", "/r:System.Drawing.dll", f"/out:{single}", cs_gen])
    print("single:", single, f"{os.path.getsize(single) / 1024 / 1024:.1f} MB", stamp)


def trim(out_dir):
    """删掉用不到的大文件：软件 OpenGL、非中文翻译、无用平台插件。"""
    q = os.path.join(out_dir, "_internal", "PySide6")
    for f in ("opengl32sw.dll",):
        p = os.path.join(q, f)
        if os.path.isfile(p):
            os.remove(p)
    tr = os.path.join(q, "translations")
    if os.path.isdir(tr):
        for f in os.listdir(tr):
            if not (f.startswith(("qtbase_zh_CN", "qt_zh_CN"))):
                os.remove(os.path.join(tr, f))
    for f in ("qdirect2d.dll", "qminimal.dll"):
        p = os.path.join(q, "plugins", "platforms", f)
        if os.path.isfile(p):
            os.remove(p)


def make_zip(out_dir):
    os.makedirs(DELIVER, exist_ok=True)
    zp = os.path.join(DELIVER, f"{NAME}_v{VERSION}_Win10-x64.zip")
    if os.path.exists(zp):
        os.remove(zp)
    base = os.path.dirname(out_dir)
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for dp, dn, fn in os.walk(out_dir):
            rel_dir = os.path.relpath(dp, base)
            if os.path.basename(dp) == "data" or "\\data\\" in dp + "\\":
                continue
            for f in fn:
                full = os.path.join(dp, f)
                z.write(full, os.path.join(rel_dir, f))
    print("zip:", zp, f"{os.path.getsize(zp) / 1024 / 1024:.1f} MB")


THIRD_PARTY = """本程序使用了以下第三方组件，均按其授权协议随附：

1. FFmpeg 8.1（GPL v3）—— https://ffmpeg.org ，以独立可执行程序方式放在 ffmpeg 文件夹中，
   完整协议见 ffmpeg\\LICENSE.txt。构建来源：BtbN/FFmpeg-Builds（win64-gpl-shared）。
2. Qt 6 / PySide6（LGPL v3）—— https://www.qt.io ，以动态库形式随附于 _internal 目录，可自行替换。
3. Python 3.12（PSF License）、NumPy（BSD）、Pillow（HPND / MIT-CMU）。
4. Microsoft Fluent Emoji（MIT License）—— https://github.com/microsoft/fluentui-emoji ，
   内置“3D表情/3D手势/3D符号/3D购物美食/3D萌宠”贴纸来自该项目。
5. 字体（SIL Open Font License 1.1，可免费商用）：
   思源黑体 Noto Sans SC（Google/Adobe）、得意黑 Smiley Sans（atelier-anchor）、
   站酷快乐体、站酷庆科黄油体（站酷）、马善政毛笔楷书（Ma Shan Zheng）。
6. 内置“电商标签/互动引导/箭头指示/装饰元素/对话气泡”贴纸、全部滤镜参数、花字预设为本工具原创。

说明：剪映（CapCut）贴纸素材版权归字节跳动及原作者所有，本程序未抓取或内置；
如有已授权的贴纸素材，可通过“贴纸库 → 导入贴纸”自行添加。
"""

if __name__ == "__main__":
    main()
