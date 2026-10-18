"""把 docs/使用文档.md 转成单文件 HTML（图片内嵌，双击即可用浏览器打开/打印成 PDF），输出到 交付/使用文档.html。

打包时 tools/build.py 会先调用本脚本，再把 HTML 放进程序目录（“帮助 → 打开完整使用文档”）。
"""
import base64
import os
import re
import sys

import markdown
from markdown.extensions.toc import slugify_unicode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC_DIR = os.path.join(ROOT, "docs")
MD = os.path.join(DOC_DIR, "使用文档.md")
OUT = os.path.join(ROOT, "交付", "使用文档.html")
ALERTS = {"TIP": "提示", "NOTE": "说明", "IMPORTANT": "重要", "WARNING": "注意", "CAUTION": "警告"}

CSS = """
:root { --accent: #1677ff; --text: #1f2329; --sub: #646a73; --line: #e5e6eb; --bg: #f7f8fa; }
* { box-sizing: border-box; }
body { margin: 0; font-family: "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", sans-serif; color: var(--text);
       background: var(--bg); line-height: 1.75; font-size: 15px; }
.wrap { max-width: 1040px; margin: 0 auto; padding: 36px 44px 80px; background: #fff; min-height: 100vh;
        box-shadow: 0 0 24px rgba(0,0,0,.05); }
h1 { font-size: 30px; margin: 8px 0 18px; padding-bottom: 14px; border-bottom: 3px solid var(--accent); }
h2 { font-size: 22px; margin: 44px 0 14px; padding: 6px 0 6px 12px; border-left: 5px solid var(--accent);
     background: linear-gradient(90deg, #eef4ff, #fff 60%); }
h3 { font-size: 17px; margin: 28px 0 10px; color: #0b4fbf; }
p { margin: 10px 0; }
a { color: var(--accent); text-decoration: none; } a:hover { text-decoration: underline; }
blockquote { margin: 14px 0; padding: 10px 16px; background: #fffbe6; border-left: 4px solid #faad14; color: #614700;
             border-radius: 4px; }
blockquote p { margin: 4px 0; }
table { border-collapse: collapse; width: 100%; margin: 14px 0; font-size: 14px; }
th, td { border: 1px solid var(--line); padding: 8px 10px; vertical-align: top; }
th { background: #f2f5fa; text-align: left; white-space: nowrap; }
tr:nth-child(even) td { background: #fbfcfd; }
code { font-family: Consolas, "Microsoft YaHei UI", monospace; background: #f2f3f5; padding: 1px 5px; border-radius: 4px;
       font-size: 13px; }
pre { background: #1f2329; color: #e8eaed; padding: 14px 16px; border-radius: 8px; overflow-x: auto; }
pre code { background: none; color: inherit; padding: 0; }
img { max-width: 100%; border: 1px solid var(--line); border-radius: 8px; box-shadow: 0 4px 18px rgba(0,0,0,.10);
      margin: 10px 0 4px; }
em { color: var(--sub); font-style: normal; font-size: 13px; }
hr { border: none; border-top: 1px solid var(--line); margin: 30px 0; }
ul, ol { padding-left: 24px; }
li { margin: 4px 0; }
.toc-note { color: var(--sub); }
@media print { body { background: #fff; } .wrap { box-shadow: none; padding: 0; max-width: none; }
  h2 { page-break-after: avoid; } img, table { page-break-inside: avoid; } }
"""


def embed_images(html):
    def rep(m):
        src = m.group(1)
        path = os.path.join(DOC_DIR, src.replace("/", os.sep))
        if not os.path.isfile(path):
            return m.group(0)
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"
        data = base64.b64encode(open(path, "rb").read()).decode("ascii")
        return f'src="data:{mime};base64,{data}"'
    return re.sub(r'src="([^"]+)"', rep, html)


def github_alerts(text):
    """GitHub 的提示框写法 > [!TIP] → 普通引用加粗标题（python-markdown 不认识这种写法）。"""
    return re.sub(r"^>\s*\[!(TIP|NOTE|IMPORTANT|WARNING|CAUTION)\]\s*$",
                  lambda m: f"> **{ALERTS[m.group(1).upper()]}**", text, flags=re.M | re.I)


def main():
    text = github_alerts(open(MD, encoding="utf-8").read())
    body = markdown.markdown(text, extensions=["tables", "fenced_code", "toc", "sane_lists"],
                             extension_configs={"toc": {"slugify": slugify_unicode, "permalink": False}})
    body = embed_images(body)
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>批量视频混剪工具 · 使用文档</title><style>{CSS}</style></head>
<body><div class="wrap">{body}</div></body></html>"""
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(html)
    print("ok", OUT, f"{os.path.getsize(OUT) / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()
