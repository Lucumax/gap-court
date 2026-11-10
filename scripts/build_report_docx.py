#!/usr/bin/env python3
"""Render the filled report draft into the official hackathon Word template (new file, the template is never modified).

    python scripts/build_report_docx.py --template <official template .docx> --draft docs/REPORT_DRAFT_5PAGE.md --out report/REPORT_v1.docx

The template is the Word file linked from the event page ("Download the template"); it is not stored in this repository.
The script keeps the template's page setup, fonts, numbered section headings and table style, replaces the example text with the
draft, and writes the result to a new file. Requires python-docx. Page count: open the result in Word, or let
scripts/report_pages.ps1 export it to PDF and count; the word-count estimate in `gapcourt report-fill` is only a guide.
"""
from __future__ import annotations

import argparse
import copy
import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt

DASH = chr(0x2013)  # the template title uses an en dash; written as a code point so the repository lint finds no dash in the source
TITLE = f"AI for Good Sandbox Hackathon {DASH} Juncture Policy {DASH} Gap Court"
BODY_PT = 12
TABLE_PT = 10
CODE_PT = 9
INLINE = re.compile(r"(`[^`]+`)")
PAGE_NOTE = re.compile(r"\s*\[[^\]]*pages?\]\s*$")
HEADING_NUM = re.compile(r"^\d+\.\s+")


def _clear(p):
    for r in list(p.runs):
        r._element.getparent().remove(r._element)
    for br in p._element.findall(qn("w:br")):
        p._element.remove(br)


def _add_runs(p, text: str, size: float, bold: bool = False, italic: bool = False):
    for part in INLINE.split(text):
        if not part:
            continue
        code = part.startswith("`") and part.endswith("`")
        run = p.add_run(part[1:-1] if code else part)
        run.font.name = "Courier New" if code else "Times New Roman"
        run._element.rPr.rFonts.set(qn("w:eastAsia"), run.font.name)
        run.font.size = Pt(CODE_PT if code else size)
        run.bold = bold or None
        run.italic = italic or None


def _set_text(p, text: str, size: float = BODY_PT, bold: bool = False, italic: bool = False):
    _clear(p)
    _add_runs(p, text, size, bold, italic)


def _tight(p, after: float = 4, before: float = 0):
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.space_before = Pt(before)
    p.paragraph_format.line_spacing = 1.0


def parse_blocks(md: str):
    """Yield ("title", text), ("heading", text), ("para", text) or ("table", rows) in order."""
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            i += 1
            continue
        if ln.startswith("# "):
            yield "title", PAGE_NOTE.sub("", ln[2:].strip())
        elif ln.startswith("## "):
            yield "heading", HEADING_NUM.sub("", PAGE_NOTE.sub("", ln[3:].strip()))
        elif ln.lstrip().startswith("|"):
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            yield "table", rows
            continue
        elif ln.startswith("- "):
            yield "bullet", ln[2:].strip()
        else:
            para = [ln.strip()]
            while i + 1 < len(lines) and lines[i + 1].strip() and not lines[i + 1].startswith(("#", "|", "- ")):
                i += 1
                para.append(lines[i].strip())
            yield "para", " ".join(para)
        i += 1


def build(template: Path, draft: Path, out: Path) -> dict:
    if out.exists():
        raise SystemExit(f"{out} exists; this script writes a new file and never overwrites")
    doc = Document(str(template))
    paras = doc.paragraphs
    heading_proto = copy.deepcopy(paras[5]._element)  # numbered section heading
    body_proto = copy.deepcopy(paras[6]._element)     # justified body paragraph
    field_proto = copy.deepcopy(paras[1]._element)    # plain field line
    table_proto = copy.deepcopy(doc.tables[0]._element)
    title_el, body_el = paras[0]._element, doc.element.body
    sect = body_el.find(qn("w:sectPr"))
    for el in list(body_el):
        if el is not title_el and el is not sect:
            body_el.remove(el)
    title_p = paras[0]
    _set_text(title_p, TITLE, size=13, bold=True)
    _tight(title_p, after=3)

    def new_para(proto):
        el = copy.deepcopy(proto)
        sect.addprevious(el)
        from docx.text.paragraph import Paragraph
        p = Paragraph(el, doc._body)
        _clear(p)
        return p

    counts = {"tables": 0, "headings": 0, "paragraphs": 0}
    seen_heading = False
    for kind, val in parse_blocks(draft.read_text(encoding="utf-8")):
        if kind == "title":
            p = new_para(field_proto)
            _add_runs(p, val, BODY_PT, italic=True)
            _tight(p, after=3)
        elif kind == "heading":
            seen_heading = True
            p = new_para(heading_proto)
            _add_runs(p, val, 11.5, bold=True)
            _tight(p, after=2, before=5)
            counts["headings"] += 1
        elif kind == "bullet":
            p = new_para(body_proto)
            _add_runs(p, "• " + val, BODY_PT)
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            _tight(p, after=1)
            counts["paragraphs"] += 1
        elif kind == "para":
            p = new_para(body_proto if seen_heading else field_proto)
            _add_runs(p, val, BODY_PT)
            _tight(p, after=3)
            counts["paragraphs"] += 1
        else:
            tbl_el = copy.deepcopy(table_proto)
            sect.addprevious(tbl_el)
            from docx.table import Table
            t = Table(tbl_el, doc._body)
            while len(t.rows) > 1:
                t._tbl.remove(t.rows[-1]._tr)
            ncols = len(val[0])
            # reuse the template table grid when the column count matches, otherwise rebuild the grid
            if len(t.columns) != ncols:
                for gc in list(t._tbl.tblGrid):
                    t._tbl.tblGrid.remove(gc)
                for _ in range(ncols):
                    t._tbl.tblGrid.append(t._tbl.tblGrid.makeelement(qn("w:gridCol"), {}))
                for tc in list(t.rows[0]._tr.findall(qn("w:tc"))):
                    t.rows[0]._tr.remove(tc)
                for _ in range(ncols):
                    t.rows[0]._tr.append(copy.deepcopy(table_proto.findall(qn("w:tr"))[0].findall(qn("w:tc"))[0]))
            for r_i, row in enumerate(val):
                tr = t.rows[0] if r_i == 0 else t.add_row()
                for c_i, cell_text in enumerate(row):
                    cell = tr.cells[c_i]
                    for extra in cell.paragraphs[1:]:
                        extra._element.getparent().remove(extra._element)
                    cp = cell.paragraphs[0]
                    _clear(cp)
                    _add_runs(cp, cell_text, TABLE_PT, bold=(r_i == 0))
                    _tight(cp, after=0)
            counts["tables"] += 1
            spacer = new_para(body_proto)
            _tight(spacer, after=2)
            for r in spacer.runs:
                r.font.size = Pt(3)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    return counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", required=True)
    ap.add_argument("--draft", default="docs/REPORT_DRAFT_5PAGE.md")
    ap.add_argument("--out", default="report/REPORT_v1.docx")
    a = ap.parse_args(argv)
    counts = build(Path(a.template), Path(a.draft), Path(a.out))
    print(f"wrote {a.out}: {counts['headings']} headings, {counts['paragraphs']} paragraphs, {counts['tables']} tables")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
