"""Render the filled report draft into the official Word template (the template is a downloaded file; tests use a stand-in)."""
import importlib.util
import sys
from pathlib import Path

import pytest

docx = pytest.importorskip("docx")

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_report_docx", ROOT / "scripts" / "build_report_docx.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["build_report_docx"] = mod
spec.loader.exec_module(mod)


def stand_in_template(path: Path):
    d = docx.Document()
    d.add_paragraph("AI for Good Sandbox Hackathon - Team Name - Solution Name")
    d.add_paragraph("Team leader:")
    d.add_paragraph("Team members:")
    d.add_paragraph("Github link:")
    d.add_paragraph("")
    d.add_paragraph("Introduction", style="List Paragraph")
    d.add_paragraph("AI is increasingly used... smart pen example text")
    d.add_paragraph("NOTE: explain how your solution is supposed to solve the gaps")
    t = d.add_table(rows=2, cols=4)
    t.cell(0, 1).text = "Use case (e.g. smart classroom)"
    t.cell(1, 0).text = "SRC"
    d.save(str(path))


DRAFT = """# Gap Court: a prototype   [header block, 0.3 page]

Team leader: Walter Guevara. Contact details: [WALTER: email].

## 1. Introduction   [0.3 page]

First paragraph with `code` and a number 12 [offline].

- a bullet line

## 2. Mapping   [1.2 pages]

| Node | In Gap Court | Documents |
|---|---|---|
| SRC | public instruments | `corpus/SOURCES.json` |
| C | downloads | none |
"""


def test_parse_blocks_strips_page_notes_and_heading_numbers():
    blocks = list(mod.parse_blocks(DRAFT))
    kinds = [k for k, _ in blocks]
    assert kinds == ["title", "para", "heading", "para", "bullet", "heading", "table"]
    assert blocks[0][1] == "Gap Court: a prototype"
    assert blocks[2][1] == "Introduction" and blocks[5][1] == "Mapping"
    assert blocks[6][1][0] == ["Node", "In Gap Court", "Documents"] and len(blocks[6][1]) == 3


def test_build_replaces_the_template_example_text_and_keeps_the_structure(tmp_path):
    tpl = tmp_path / "template.docx"
    stand_in_template(tpl)
    draft = tmp_path / "draft.md"
    draft.write_text(DRAFT, encoding="utf-8")
    out = tmp_path / "REPORT.docx"
    counts = mod.build(tpl, draft, out)
    assert counts == {"tables": 1, "headings": 2, "paragraphs": 3}
    d = docx.Document(str(out))
    text = "\n".join(p.text for p in d.paragraphs)
    assert "smart pen" not in text and "NOTE:" not in text and "Team Name" not in text
    assert d.paragraphs[0].text.startswith("AI for Good Sandbox Hackathon")
    assert "Juncture Policy" in d.paragraphs[0].text and "Gap Court" in d.paragraphs[0].text
    heads = [p.text for p in d.paragraphs if p.style.name == "List Paragraph"]
    assert heads == ["Introduction", "Mapping"]
    assert len(d.tables) == 1 and [c.text for c in d.tables[0].rows[0].cells] == ["Node", "In Gap Court", "Documents"]
    assert [c.text for c in d.tables[0].rows[2].cells] == ["C", "downloads", "none"]
    assert "[WALTER: email]" in text  # Walter-only fields stay visible


def test_build_never_overwrites_and_never_touches_the_template(tmp_path):
    tpl = tmp_path / "template.docx"
    stand_in_template(tpl)
    before = tpl.read_bytes()
    draft = tmp_path / "draft.md"
    draft.write_text(DRAFT, encoding="utf-8")
    out = tmp_path / "REPORT.docx"
    mod.build(tpl, draft, out)
    assert tpl.read_bytes() == before
    with pytest.raises(SystemExit):
        mod.build(tpl, draft, out)
