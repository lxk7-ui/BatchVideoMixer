// Markdown → Word 段落（使用文档、上手文档共用）。只支持这两份文档用到的写法：
//   ## 一级标题、### 二级标题；第一个 ## 之前的内容（GitHub 上的标题和简介）以及“## 目录”一节不进 Word
//   （Word 有封面和自动目录）
//   表格；- 列表；1. 编号列表（编号不从 1 开始时接续上一个列表）；``` 代码块；*整行斜体* 作为居中小字
//   图片：![说明](路径)，紧跟的 *斜体* 一行作为图注；行尾 <!-- w=520 --> 指定显示宽度（像素，默认 600）
//   引用：> 开头；首行 [!TIP] / [!NOTE] 为蓝色提示框，其余（含 [!WARNING] / [!IMPORTANT] / [!CAUTION]）为黄色
//   <!-- pagebreak -->：下一个一级标题从新的一页开始
const path = require("path");
const { Paragraph, TextRun, HeadingLevel, ShadingType } = require("docx");
const K = require("./common");

const IMG_RE = /^!\[([^\]]*)\]\(([^)\s]+)\)\s*(?:<!--\s*w=(\d+)\s*-->)?$/;
const ALERT_RE = /^\[!(TIP|NOTE|IMPORTANT|WARNING|CAUTION)\]$/i;

function parseMarkdown(md, baseDir) {
  const lines = md.split(/\r?\n/);
  const out = [];
  let i = 0;
  let started = false;
  let skipping = false;
  let listInst = 0;
  let inNumbered = false;
  let breakNext = true;   // 正文第一章从新的一页开始（目录之后）
  while (i < lines.length) {
    const line = lines[i];
    const t = line.trim();
    if (t === "<!-- pagebreak -->") { breakNext = true; i++; continue; }
    if (/^## /.test(line)) {
      const title = line.slice(3).trim();
      started = true;
      skipping = title === "目录";
      if (!skipping) {
        out.push(new Paragraph({ heading: HeadingLevel.HEADING_1, pageBreakBefore: breakNext, children: [new TextRun(title)] }));
        breakNext = false;
      }
      inNumbered = false;
      i++;
      continue;
    }
    if (!started || skipping) { i++; continue; }
    if (/^### /.test(line)) {
      out.push(new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun(line.slice(4).trim())] }));
      inNumbered = false; i++; continue;
    }
    if (t === "---" || t === "") { if (t === "") inNumbered = inNumbered && /^\d+\. /.test((lines[i + 1] || "").trim()); i++; continue; }
    if (t.startsWith("```")) {
      i++;
      const code = [];
      while (i < lines.length && !lines[i].trim().startsWith("```")) { code.push(lines[i]); i++; }
      i++;
      code.forEach((c, k) => out.push(new Paragraph({
        children: [new TextRun({ text: c.length ? c : " ", font: K.font(K.MONO), size: 18 })],
        shading: { type: ShadingType.CLEAR, fill: K.C.codeBg, color: "auto" },
        spacing: { before: k === 0 ? 120 : 0, after: k === code.length - 1 ? 160 : 0, line: 280 },
        indent: { left: 120, right: 120 },
      })));
      continue;
    }
    if (t.startsWith("|")) {
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        const cells = lines[i].trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        if (!cells.every((c) => /^:?-{2,}:?$/.test(c))) rows.push(cells);
        i++;
      }
      out.push(K.table(rows));
      out.push(K.spacer(80));
      continue;
    }
    let m = t.match(IMG_RE);
    if (m) {
      const file = path.join(baseDir, decodeURIComponent(m[2]));
      const w = m[3] ? parseInt(m[3], 10) : 600;
      // 紧随其后的 *说明* 作为图注
      let cap = null;
      let j = i + 1;
      while (j < lines.length && lines[j].trim() === "") j++;
      const capm = (lines[j] || "").trim().match(/^\*([^*].*)\*$/);
      if (capm) { cap = capm[1]; i = j; }
      out.push(...K.image(file, w, cap));
      i++;
      continue;
    }
    if (t.startsWith(">")) {
      const block = [];
      while (i < lines.length && lines[i].trim().startsWith(">")) {
        const s = lines[i].trim().replace(/^>\s?/, "");
        if (s) block.push(s);
        i++;
      }
      let kind = "note";
      const am = (block[0] || "").match(ALERT_RE);
      if (am) {
        kind = /^(TIP|NOTE)$/i.test(am[1]) ? "tip" : "note";
        block.shift();
      }
      out.push(...K.callout(block.map((s) => s.replace(/^- /, "· ")), kind));
      continue;
    }
    if (/^- /.test(t)) {
      const level = line.startsWith("  ") ? 1 : 0;
      out.push(K.bullet(t.slice(2), level));
      i++;
      continue;
    }
    m = t.match(/^(\d+)\. (.*)$/);
    if (m) {
      // 编号不是 1（如表格后面接着写“3.”）时接续上一个列表，而不是重新从 1 编号
      if (!inNumbered) { if (m[1] === "1" || listInst === 0) listInst++; inNumbered = true; }
      out.push(K.step(m[2], listInst));
      i++;
      continue;
    }
    m = t.match(/^\*([^*].*)\*$/);
    if (m) { out.push(new Paragraph({ style: "Caption2", children: K.inline(m[1]) })); i++; continue; }
    out.push(new Paragraph({ children: K.inline(t), spacing: { after: 100 } }));
    inNumbered = false;
    i++;
  }
  return out;
}

module.exports = { parseMarkdown };
