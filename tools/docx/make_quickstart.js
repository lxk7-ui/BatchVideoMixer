// 生成《上手文档.docx》：读取 docs/上手文档.md（面向第一次使用的同事，配编号标注截图）
// 用法：node make_quickstart.js [输出路径]   （默认输出到 交付/上手文档.docx，之后用 finalize.ps1 更新目录）
const fs = require("fs");
const path = require("path");
const { Document, Packer } = require("docx");
const K = require("./common");
const { parseMarkdown } = require("./md2docx");

const ROOT = path.resolve(__dirname, "..", "..");
const DOCS = path.join(ROOT, "docs");
const MD = path.join(DOCS, "上手文档.md");
const OUT = process.argv[2] || path.join(ROOT, "交付", "上手文档.docx");
const VER = "1.2.0";

async function main() {
  const body = parseMarkdown(fs.readFileSync(MD, "utf8"), DOCS);
  const cover = K.cover({
    logo: path.join(DOCS, "images", "logo.png"),
    subtitle: "上 手 指 南",
    versionLine: `10 分钟学会批量出片　｜　v${VER}　｜　2026 年 10 月`,
    picture: path.join(DOCS, "上手文档_图片", "q01_混剪主界面.jpg"),
    top: 1300,
    lines: [
      ["导入素材 → 设好时长上限 → 点“开始生成”，一次产出几十上百条不同的成片", 21, K.C.text],
      ["截图里的红色编号①②③④与正文步骤一一对应", 19, K.C.sub],
    ],
  });
  const doc = new Document({
    creator: "批量视频混剪工具", title: "批量视频混剪工具 上手指南", description: `v${VER} 上手指南`,
    styles: K.styles(), numbering: K.numbering(),
    features: { updateFields: false },
    sections: [{
      properties: { page: { size: { width: K.PAGE.width, height: K.PAGE.height }, margin: K.PAGE.margin }, titlePage: true },
      headers: { default: K.header("批量视频混剪工具 · 上手指南"), first: K.emptyHeader() },
      footers: { default: K.footer(), first: K.emptyFooter() },
      children: [...cover, ...K.tocPage(), ...body],
    }],
  });
  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, await Packer.toBuffer(doc));
  console.log("ok", OUT, body.length, "blocks");
}

main().catch((e) => { console.error(e); process.exit(1); });
