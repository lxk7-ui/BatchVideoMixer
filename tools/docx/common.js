// 两份 Word 文档共用的样式与构件
const fs = require("fs");
const path = require("path");
const {
  Paragraph, TextRun, ImageRun, Table, TableRow, TableCell, WidthType, ShadingType, BorderStyle,
  AlignmentType, HeadingLevel, LevelFormat, Header, Footer, PageNumber, PageBreak, ExternalHyperlink,
  TableLayoutType, VerticalAlign, TableOfContents,
} = require("docx");

const FONT = "Microsoft YaHei";
const MONO = "Consolas";
const C = {
  accent: "1677FF", h1: "0B4FBF", text: "1F2329", sub: "646A73", line: "D9DEE7",
  head: "EAF1FF", noteBg: "FFF8E6", noteBar: "FAAD14", tipBg: "EEF6FF", tipBar: "1677FF",
  codeBg: "F3F5F8", red: "E8473F", green: "16A05D",
};
// A4，左右 2cm，上下 2.2cm
const PAGE = { width: 11906, height: 16838, margin: { top: 1247, bottom: 1247, left: 1134, right: 1134, header: 600, footer: 600 } };
const CONTENT_W = PAGE.width - PAGE.margin.left - PAGE.margin.right; // 9638 DXA

const font = (f = FONT) => ({ ascii: f, eastAsia: FONT, hAnsi: f, cs: f });

function styles() {
  return {
    default: {
      document: { run: { font: font(), size: 21, color: C.text }, paragraph: { spacing: { after: 100, line: 320 } } },
    },
    paragraphStyles: [
      {
        id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: font(), size: 32, bold: true, color: C.h1 },
        paragraph: {
          spacing: { before: 420, after: 180, line: 300 }, keepNext: true, keepLines: true, outlineLevel: 0,
          border: { bottom: { style: BorderStyle.SINGLE, size: 8, color: C.accent, space: 4 } },
        },
      },
      {
        id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: font(), size: 26, bold: true, color: C.text },
        paragraph: {
          spacing: { before: 300, after: 120, line: 300 }, keepNext: true, keepLines: true, outlineLevel: 1,
          border: { left: { style: BorderStyle.SINGLE, size: 24, color: C.accent, space: 8 } },
          indent: { left: 60 },
        },
      },
      {
        id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: font(), size: 23, bold: true, color: C.h1 },
        paragraph: { spacing: { before: 200, after: 80 }, keepNext: true, outlineLevel: 2 },
      },
      {
        id: "Caption2", name: "Figure Caption", basedOn: "Normal", next: "Normal",
        run: { font: font(), size: 18, color: C.sub },
        paragraph: { alignment: AlignmentType.CENTER, spacing: { before: 40, after: 200 } },
      },
    ],
  };
}

function numbering() {
  const bulletLevels = [
    { level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
      style: { paragraph: { indent: { left: 440, hanging: 280 } }, run: { color: C.accent } } },
    { level: 1, format: LevelFormat.BULLET, text: "◦", alignment: AlignmentType.LEFT,
      style: { paragraph: { indent: { left: 880, hanging: 280 } } } },
  ];
  const stepLevels = [
    { level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
      style: { paragraph: { indent: { left: 440, hanging: 340 } }, run: { bold: true, color: C.accent } } },
  ];
  return { config: [{ reference: "bullets", levels: bulletLevels }, { reference: "steps", levels: stepLevels }] };
}

// ---------------------------------------------------------------- 行内格式：**粗体** `代码` [文字](链接)
function inline(text, base = {}) {
  const runs = [];
  const re = /(\*\*([^*]+)\*\*|`([^`]+)`|\[([^\]]+)\]\(([^)]+)\))/g;
  let last = 0;
  let m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) runs.push(new TextRun({ text: text.slice(last, m.index), ...base }));
    if (m[2] !== undefined) {
      runs.push(new TextRun({ text: m[2], bold: true, ...base }));
    } else if (m[3] !== undefined) {
      runs.push(new TextRun({ text: m[3], font: font(MONO), size: (base.size || 21) - 2, shading: { type: ShadingType.CLEAR, fill: C.codeBg, color: "auto" }, ...base, }));
    } else if (m[4] !== undefined) {
      const href = m[5];
      if (/^https?:/.test(href)) {
        runs.push(new ExternalHyperlink({ link: href, children: [new TextRun({ text: m[4], color: C.accent, underline: {}, ...base })] }));
      } else {
        runs.push(new TextRun({ text: m[4], color: C.accent, ...base }));
      }
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) runs.push(new TextRun({ text: text.slice(last), ...base }));
  return runs;
}

const P = (text, opts = {}) => new Paragraph({ children: inline(text, opts.run || {}), ...(opts.para || {}) });

function bullet(text, level = 0) {
  return new Paragraph({ numbering: { reference: "bullets", level }, children: inline(text), spacing: { after: 60 } });
}

function step(text, instance) {
  return new Paragraph({ numbering: { reference: "steps", level: 0, instance }, children: inline(text), spacing: { after: 70 } });
}

// 提示框（左侧色条 + 浅底色）
function callout(lines, kind = "note") {
  const bg = kind === "tip" ? C.tipBg : C.noteBg;
  const bar = kind === "tip" ? C.tipBar : C.noteBar;
  return lines.map((t, i) => new Paragraph({
    children: inline(t),
    shading: { type: ShadingType.CLEAR, fill: bg, color: "auto" },
    border: { left: { style: BorderStyle.SINGLE, size: 24, color: bar, space: 8 } },
    indent: { left: 160, right: 160 },
    spacing: { before: i === 0 ? 120 : 0, after: i === lines.length - 1 ? 160 : 0, line: 320 },
  }));
}

// ---------------------------------------------------------------- 图片
function imageSize(buf) {
  if (buf[0] === 0x89 && buf[1] === 0x50) return { w: buf.readUInt32BE(16), h: buf.readUInt32BE(20), type: "png" };
  let i = 2;
  while (i < buf.length) {
    if (buf[i] !== 0xff) { i++; continue; }
    const marker = buf[i + 1];
    const len = buf.readUInt16BE(i + 2);
    if (marker >= 0xc0 && marker <= 0xcf && ![0xc4, 0xc8, 0xcc].includes(marker)) {
      return { h: buf.readUInt16BE(i + 5), w: buf.readUInt16BE(i + 7), type: "jpg" };
    }
    i += 2 + len;
  }
  throw new Error("unknown image");
}

// width 为页面上的显示宽度（像素，96dpi；内容区约 640px）
function image(file, width = 600, caption = null, opts = {}) {
  const buf = fs.readFileSync(file);
  const s = imageSize(buf);
  const w = Math.min(width, 640);
  const h = Math.round(s.h * w / s.w);
  const out = [new Paragraph({
    alignment: AlignmentType.CENTER, keepNext: !!caption, spacing: { before: 100, after: caption ? 0 : 160 },
    children: [new ImageRun({ type: s.type, data: buf, transformation: { width: w, height: h },
      altText: { title: caption || path.basename(file), description: caption || path.basename(file), name: path.basename(file) } })],
    ...opts,
  })];
  if (caption) out.push(new Paragraph({ style: "Caption2", children: inline(caption) }));
  return out;
}

// ---------------------------------------------------------------- 表格
function textWidth(s) {
  let n = 0;
  for (const ch of s.replace(/\*\*|`/g, "")) n += ch.charCodeAt(0) > 255 ? 2 : 1;
  return n;
}

function table(rows, opts = {}) {
  // rows: 二维文本数组，第一行为表头
  const ncol = rows[0].length;
  let widths = opts.widths;
  if (!widths) {
    const need = [];
    for (let c = 0; c < ncol; c++) {
      let mx = 4;
      for (const r of rows) mx = Math.max(mx, Math.min(textWidth(r[c] || ""), 60));
      need.push(Math.sqrt(mx) * 10 + 8);
    }
    const sum = need.reduce((a, b) => a + b, 0);
    widths = need.map((x) => Math.floor(CONTENT_W * x / sum));
  }
  const total = widths.reduce((a, b) => a + b, 0);
  widths[widths.length - 1] += CONTENT_W - total;
  const border = { style: BorderStyle.SINGLE, size: 4, color: C.line };
  const borders = { top: border, bottom: border, left: border, right: border };
  const size = opts.size || 19;
  return new Table({
    width: { size: CONTENT_W, type: WidthType.DXA },
    columnWidths: widths,
    layout: TableLayoutType.FIXED,
    rows: rows.map((r, ri) => new TableRow({
      tableHeader: ri === 0,
      cantSplit: true,
      children: r.map((cell, ci) => new TableCell({
        width: { size: widths[ci], type: WidthType.DXA },
        borders,
        verticalAlign: VerticalAlign.CENTER,
        shading: ri === 0 ? { type: ShadingType.CLEAR, fill: C.head, color: "auto" }
          : (ri % 2 === 0 ? { type: ShadingType.CLEAR, fill: "FAFBFC", color: "auto" } : undefined),
        margins: { top: 60, bottom: 60, left: 110, right: 110 },
        children: [new Paragraph({ spacing: { after: 0, line: 290 },
          children: inline(cell || "", ri === 0 ? { bold: true, size } : { size }) })],
      })),
    })),
  });
}

function spacer(after = 120) {
  return new Paragraph({ children: [], spacing: { after } });
}

function pageBreak() {
  return new Paragraph({ children: [new PageBreak()] });
}

function header(title) {
  return new Header({
    children: [new Paragraph({
      alignment: AlignmentType.RIGHT,
      border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: C.line, space: 4 } },
      children: [new TextRun({ text: title, size: 17, color: "8A9099" })],
    })],
  });
}

function footer() {
  return new Footer({
    children: [new Paragraph({
      alignment: AlignmentType.CENTER,
      children: [new TextRun({ children: ["第 ", PageNumber.CURRENT, " 页 / 共 ", PageNumber.TOTAL_PAGES, " 页"], size: 17, color: "8A9099" })],
    })],
  });
}

// ---------------------------------------------------------------- 封面 / 目录页
// lines: [[文字, 字号(半磅), 颜色], ...]
function cover({ logo, subtitle, versionLine, picture, pictureWidth = 560, lines = [], top = 1500 }) {
  const ch = [new Paragraph({ children: [], spacing: { before: top, after: 0 } })];
  ch.push(...image(logo, 92));
  ch.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 200, after: 120 },
    children: [new TextRun({ text: "批量视频混剪工具", bold: true, size: 60, color: C.text })] }));
  ch.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 160 },
    children: [new TextRun({ text: subtitle, size: 36, color: C.accent, bold: true })] }));
  ch.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 360 },
    children: [new TextRun({ text: versionLine, size: 22, color: C.sub })] }));
  ch.push(...image(picture, pictureWidth));
  lines.forEach(([text, size, color], k) => ch.push(new Paragraph({ alignment: AlignmentType.CENTER,
    spacing: { before: k === 0 ? 240 : 0, after: 80 }, children: [new TextRun({ text, size, color })] })));
  return ch;
}

function tocPage() {
  return [
    new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 200, after: 300 }, pageBreakBefore: true,
      children: [new TextRun({ text: "目　录", bold: true, size: 36, color: C.text })] }),
    new TableOfContents("目录", { hyperlink: true, headingStyleRange: "1-2" }),
  ];
}

const emptyHeader = () => new Header({ children: [new Paragraph({ children: [] })] });
const emptyFooter = () => new Footer({ children: [new Paragraph({ children: [] })] });

module.exports = {
  FONT, MONO, C, PAGE, CONTENT_W, font, styles, numbering, inline, P, bullet, step, callout, image, imageSize, table,
  spacer, pageBreak, header, footer, emptyHeader, emptyFooter, cover, tocPage,
};
