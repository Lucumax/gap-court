import pytest

from gapcourt.corpus import CorpusError, load_document, parse_document
from gapcourt.chunking import ChunkParams, chunk_document


def test_front_matter_parsed_with_literals(fixtures_dir):
    doc = load_document(fixtures_dir / "S01.md")
    assert doc.meta["doc_id"] == "S01"
    assert doc.meta["binding"] is True
    assert doc.meta["supersedes"] is None
    assert doc.meta["official_translation"] is None
    assert doc.meta["republish"] == "yes"


def test_required_keys_enforced():
    with pytest.raises(CorpusError):
        parse_document("---\ndoc_id: X1\n---\n### Article 1\ntext")


def test_missing_front_matter_rejected():
    with pytest.raises(CorpusError):
        parse_document("### Article 1\nno front matter")


def test_sections_have_locators_and_pages(fixtures_dir):
    doc = load_document(fixtures_dir / "S01.md")
    locs = [s.locator for s in doc.sections]
    assert locs == ["Preamble", "Article 1", "Article 2", "Article 3"]
    art2 = doc.sections[2]
    assert art2.page_start == 1 and art2.page_end == 2


def test_page_marker_not_left_in_text(fixtures_dir):
    doc = load_document(fixtures_dir / "S01.md")
    assert all("<!--page" not in s.text for s in doc.sections)


def test_chunk_ids_are_stable_across_runs(fixtures_dir):
    d = load_document(fixtures_dir / "S01.md")
    a = [c.chunk_id for c in chunk_document(d, ChunkParams())]
    b = [c.chunk_id for c in chunk_document(load_document(fixtures_dir / "S01.md"), ChunkParams())]
    assert a == b and len(set(a)) == len(a)


def test_chunk_id_changes_when_version_changes(fixtures_dir):
    text = (fixtures_dir / "S01.md").read_text(encoding="utf-8")
    d1 = parse_document(text)
    d2 = parse_document(text.replace("version: v1", "version: v2"))
    ids1 = {c.chunk_id for c in chunk_document(d1, ChunkParams())}
    ids2 = {c.chunk_id for c in chunk_document(d2, ChunkParams())}
    assert ids1.isdisjoint(ids2)


def test_chunk_carries_locator_page_and_hash(fixtures_dir):
    d = load_document(fixtures_dir / "S01.md")
    chunks = chunk_document(d, ChunkParams())
    c = next(c for c in chunks if c.locator == "Article 2")
    assert c.doc_id == "S01" and c.page_start == 1 and c.page_end == 2
    assert len(c.text_sha256) == 64
    assert "government agencies" in c.text


def test_long_section_splits_on_sentence_boundaries_never_mid_word(fixtures_dir):
    d = load_document(fixtures_dir / "S01.md")
    chunks = [c for c in chunk_document(d, ChunkParams(max_chars=90)) if c.locator == "Article 3"]
    assert len(chunks) >= 2
    joined = " ".join(c.text for c in chunks)
    assert "must record the purpose of the sharing." in joined
    for c in chunks:
        assert c.text.endswith((".", ";", ":")), c.text
        assert not c.text.startswith(" ")
    # reassembly loses nothing
    original = d.sections[3].text
    assert " ".join(original.split()) == " ".join(joined.split())


def test_single_oversize_sentence_is_kept_whole(fixtures_dir):
    d = load_document(fixtures_dir / "S01.md")
    chunks = [c for c in chunk_document(d, ChunkParams(max_chars=20)) if c.locator == "Article 1"]
    assert len(chunks) == 1
    assert chunks[0].text.endswith("synthetic state.")


def test_ordinals_restart_per_locator(fixtures_dir):
    d = load_document(fixtures_dir / "S01.md")
    chunks = chunk_document(d, ChunkParams(max_chars=90))
    first = {}
    for c in chunks:
        first.setdefault(c.locator, c.ordinal)
    assert set(first.values()) == {0}


def test_cite_string_is_human_readable(fixtures_dir):
    d = load_document(fixtures_dir / "S01.md")
    c = chunk_document(d, ChunkParams())[1]
    assert c.cite == "S01 Article 1"


def test_repeated_locators_are_made_unique_so_chunk_ids_never_collide():
    raw = ("---\n" + "\n".join(f"{k}: x" for k in (
        "doc_id", "instrument_id", "title", "issuer", "instrument_type", "binding", "jurisdiction", "language",
        "official_translation", "source_url", "retrieved_at", "version", "effective_date", "supersedes", "status",
        "terms_note", "sha256")) + "\nrepublish: yes\n---\n"
        "### Article 1\nFirst body of the resolution.\n### Article 2\nSecond body.\n"
        "### Part: Annex\n### Article 1\nFirst body of the annex document.\n")
    doc = parse_document(raw)
    assert [s.locator for s in doc.sections] == ["Article 1", "Article 2", "Part: Annex", "Article 1 [2]"]
    ids = [c.chunk_id for c in chunk_document(doc, ChunkParams())]
    assert len(ids) == len(set(ids))
