import pytest

from gapcourt.corpus import parse_document
from gapcourt.ingest import render_corpus_md, split_into_sections

PAGES_EN = [
    "Running header\nPreamble words here.\nArticle (1)\nDefinitions\nIn this Law the words mean what follows.",
    "Running header\nAnd more definitions.\nArticle (2)\nScope\n1. This Law applies to X.\n2. It does not apply to Y.",
]


def test_english_articles_become_sections_with_pages():
    body = split_into_sections(PAGES_EN, lang="en", drop_lines=[r"^Running header$"])
    assert "### Article 1" in body and "### Article 2" in body
    assert body.index("<!--page:1-->") < body.index("### Article 1") < body.index("<!--page:2-->") < body.index("### Article 2") or \
        body.index("<!--page:2-->") < body.index("### Article 2")
    assert "Running header" not in body


def test_arabic_heading_forms_detected():
    pages = ["x\nالمادة )1(\nالتعاريف\nنص", "المادة )2(\nنطاق السريان\nنص آخر"]
    body = split_into_sections(pages, lang="ar")
    assert "### Article 1" in body and "### Article 2" in body


def test_rendered_file_round_trips_through_parser():
    meta = {
        "doc_id": "T1", "instrument_id": "T-1", "title": "Test", "issuer": "Test", "instrument_type": "law",
        "binding": True, "jurisdiction": "XX", "language": "en", "official_translation": None,
        "source_url": "https://example.invalid", "retrieved_at": "2026-10-27T00:00:00Z", "version": "v1",
        "effective_date": "2020-01-01", "supersedes": None, "status": "in force", "terms_note": "n/a",
        "republish": "link_only", "sha256": "ab" * 32,
    }
    md = render_corpus_md(meta, split_into_sections(PAGES_EN, lang="en"))
    doc = parse_document(md)
    assert [s.locator for s in doc.sections][-2:] == ["Article 1", "Article 2"]
    assert doc.meta["binding"] is True and doc.meta["supersedes"] is None


def test_render_rejects_multiline_values():
    with pytest.raises(ValueError):
        render_corpus_md({"doc_id": "a\nb"}, "")
