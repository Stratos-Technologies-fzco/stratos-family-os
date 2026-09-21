// Regenerates docs/Stratos_CLI_Progress_Log.docx from docs/progress_log.json.
// To record new work: append an entry to progress_log.json, then run
//   node scripts/build_progress_log.js
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, HeadingLevel,
  AlignmentType, WidthType, ShadingType, BorderStyle, LevelFormat, Footer, PageNumber,
} = require("docx");

const root = path.join(__dirname, "..");
const log = JSON.parse(fs.readFileSync(path.join(root, "docs", "progress_log.json"), "utf8"));

const NAVY = "1F3A5F";
const GREY = "F2F4F7";
const FONT = "Calibri";
const W = 9360; // content width, US Letter with 1" margins
const border = { style: BorderStyle.SINGLE, size: 4, color: "C9CED6" };
const borders = { top: border, bottom: border, left: border, right: border };

const run = (text, o = {}) => new TextRun({ text, font: FONT, ...o });
const para = (text, o = {}) =>
  new Paragraph({ spacing: { after: 120 }, children: [run(text, o.run)], ...o.para });
const h = (text, level) => new Paragraph({ heading: level, children: [run(text)] });
const bullet = (text) =>
  new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 80 }, children: [run(text)] });

function cell(text, width, o = {}) {
  return new TableCell({
    width: { size: width, type: WidthType.DXA },
    borders,
    margins: { top: 80, bottom: 80, left: 120, right: 120 },
    shading: o.fill ? { fill: o.fill, type: ShadingType.CLEAR, color: "auto" } : undefined,
    children: [new Paragraph({ children: [run(text, { bold: o.bold, color: o.color })] })],
  });
}

// Summary table
const cols = [700, 1900, 2400, 3060, 1300];
const header = ["No.", "Date", "Area", "Change", "Status"];
const summary = new Table({
  width: { size: W, type: WidthType.DXA },
  columnWidths: cols,
  rows: [
    new TableRow({
      tableHeader: true,
      children: header.map((t, i) => cell(t, cols[i], { fill: NAVY, bold: true, color: "FFFFFF" })),
    }),
    ...log.entries.map((e) =>
      new TableRow({
        children: [String(e.id), e.date, e.area, e.title, e.status].map((t, i) => cell(t, cols[i])),
      })
    ),
  ],
});

function entryBlocks(e) {
  const out = [
    h(`Entry ${e.id}: ${e.title}`, HeadingLevel.HEADING_2),
    new Paragraph({
      spacing: { after: 160 },
      children: [
        run("Date: ", { bold: true }), run(e.date + "     "),
        run("Area: ", { bold: true }), run(e.area + "     "),
        run("Status: ", { bold: true }), run(e.status),
      ],
    }),
    para(e.summary),
    h("What was delivered", HeadingLevel.HEADING_3),
    ...e.delivered.map(bullet),
    h("Why it matters to the business", HeadingLevel.HEADING_3),
    para(e.value),
  ];
  if (e.quality) out.push(h("How we know it works", HeadingLevel.HEADING_3), para(e.quality));
  if (e.decisions?.length)
    out.push(h("Open points for a decision", HeadingLevel.HEADING_3), ...e.decisions.map(bullet));
  if (e.next) out.push(h("What comes next", HeadingLevel.HEADING_3), para(e.next));
  return out;
}

const last = log.entries[log.entries.length - 1];
const doc = new Document({
  creator: "Stratos Technologies FZCO",
  title: log.title,
  styles: {
    default: { document: { run: { font: FONT, size: 22 } } },
    paragraphStyles: [
      { id: "Title", name: "Title", basedOn: "Normal", run: { size: 48, bold: true, color: NAVY, font: FONT }, paragraph: { spacing: { after: 80 } } },
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 32, bold: true, color: NAVY, font: FONT }, paragraph: { spacing: { before: 320, after: 160 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 28, bold: true, color: NAVY, font: FONT }, paragraph: { spacing: { before: 360, after: 120 }, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 24, bold: true, color: "3B5B85", font: FONT }, paragraph: { spacing: { before: 200, after: 80 }, outlineLevel: 2 } },
    ],
  },
  numbering: {
    config: [{ reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
      style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] }],
  },
  sections: [{
    properties: { page: { size: { width: 12240, height: 15840 }, margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } } },
    footers: {
      default: new Footer({
        children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [
          run("Stratos CLI Progress Log  ·  Page ", { size: 18, color: "6B7280" }),
          new TextRun({ children: [PageNumber.CURRENT], font: FONT, size: 18, color: "6B7280" }),
        ] })],
      }),
    },
    children: [
      new Paragraph({ style: "Title", children: [run(log.title)] }),
      para(log.subtitle, { run: { color: "6B7280", size: 24 } }),
      para(`Latest update: ${last.date}  ·  Entries: ${log.entries.length}`, { run: { bold: true } }),
      h("About this document", HeadingLevel.HEADING_1),
      para(log.intro),
      h("Summary of changes", HeadingLevel.HEADING_1),
      summary,
      h("Detailed entries", HeadingLevel.HEADING_1),
      ...log.entries.flatMap(entryBlocks),
    ],
  }],
});

const out = path.join(root, "docs", "Stratos_CLI_Progress_Log.docx");
Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync(out, buf);
  console.log("Wrote", out, `(${log.entries.length} entries)`);
});
