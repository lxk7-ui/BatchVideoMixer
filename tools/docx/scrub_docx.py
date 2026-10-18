"""清理 Word 文档属性里的个人信息：作者、最后修改人统一为程序名，去掉公司、经理、上次打印时间等。

Word 保存文档时会写入本机 Office 账户名（docProps/core.xml 的 lastModifiedBy），对外发布前必须清理。
用法：python tools/docx/scrub_docx.py <a.docx> [b.docx ...]
"""
import os
import re
import shutil
import sys
import tempfile
import zipfile

NAME = "批量视频混剪工具"


def scrub_core(xml):
    for tag in ("dc:creator", "cp:lastModifiedBy"):
        if re.search(rf"<{tag}\b", xml):
            xml = re.sub(rf"<{tag}\b[^>]*?(/>|>.*?</{tag}>)", f"<{tag}>{NAME}</{tag}>", xml, flags=re.S)
        else:
            xml = xml.replace("</cp:coreProperties>", f"<{tag}>{NAME}</{tag}></cp:coreProperties>")
    xml = re.sub(r"<cp:lastPrinted\b[^>]*?(/>|>.*?</cp:lastPrinted>)", "", xml, flags=re.S)
    return xml


def scrub_app(xml):
    for tag in ("Company", "Manager", "HyperlinkBase"):
        xml = re.sub(rf"<{tag}\b[^>]*?(/>|>.*?</{tag}>)", "", xml, flags=re.S)
    return xml


def scrub(path):
    fd, tmp = tempfile.mkstemp(suffix=".docx", dir=os.path.dirname(os.path.abspath(path)))
    os.close(fd)
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "docProps/core.xml":
                data = scrub_core(data.decode("utf-8")).encode("utf-8")
            elif item.filename == "docProps/app.xml":
                data = scrub_app(data.decode("utf-8")).encode("utf-8")
            zout.writestr(item, data)
    shutil.move(tmp, path)
    print("已清理文档属性:", path)


if __name__ == "__main__":
    for p in sys.argv[1:]:
        scrub(p)
