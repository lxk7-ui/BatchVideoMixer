// 生成《使用文档.docx》：读取 docs/使用文档.md，转换为排版好的 Word 文档（封面 + 目录 + 正文 + 页眉页脚）
// 用法：node make_manual.js [输出路径]   （默认输出到 交付/使用文档.docx，之后用 finalize.ps1 更新目录）
const fs = require("fs");
const path = require("path");
const { Document, Packer } = require("docx");
const K = require("./common");
const { parseMarkdown } = require("./md2docx");

const ROOT = path.resolve(__dirname, "..", "..");
const DOCS = path.join(ROOT, "docs");
const MD = path.join(DOCS, "使用文档.md");
const OUT = process.argv[2] || path.join(ROOT, "交付", "使用文档.docx");
const VER = "1.2.0";

async function main() {
  const body = parseMarkdown(fs.readFileSync(MD, "utf8"), DOCS);
  const cover = K.cover({
    logo: path.join(DOCS, "images", "logo.png"),
    subtitle: "使 用 文 档",
    versionLine: `版本 v${VER}　｜　2026 年 10 月`,
    picture: path.join(DOCS, "使用文档_图片", "20_成片示例.jpg"),
    lines: [
      ["批量导入素材 · 时长上限控制 · 素材查重防重复 · 去水印/去贴纸/去遮挡 · 滤镜贴纸花字 · 一键批量成片", 21, K.C.text],
      ["适用系统：Windows 10 / 11 64 位（含 Windows 10 IoT 企业版 LTSC 21H2）", 19, K.C.sub],
      ["安装版 / 单文件免安装版 · 内置 FFmpeg、字体与贴纸库 · 完全离线运行", 19, K.C.sub],
    ],
  });
  const doc = new Document({
    creator: "批量视频混剪工具", title: "批量视频混剪工具 使用文档", description: `v${VER} 使用文档`,
    styles: K.styles(), numbering: K.numbering(),
    features: { updateFields: false },
    sections: [{
      properties: { page: { size: { width: K.PAGE.width, height: K.PAGE.height }, margin: K.PAGE.margin }, titlePage: true },
      headers: { default: K.header("批量视频混剪工具 · 使用文档"), first: K.emptyHeader() },
      footers: { default: K.footer(), first: K.emptyFooter() },
      children: [...cover, ...K.tocPage(), ...body],
    }],
  });
  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, await Packer.toBuffer(doc));
  console.log("ok", OUT, body.length, "blocks");
}

main().catch((e) => { console.error(e); process.exit(1); });
