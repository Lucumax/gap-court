"""Recorded transforms for corpus files (corpus/PREP.json) and the verbatim audit for the rest.

Every corpus file must be either rebuilt byte for byte from its hash-verified raw file by a recorded transform
(extractor, options, structural patch) or pass the verbatim audit: each content line is found in the raw
file's text under a named, limited normalization, and every line that is not is a registered edit.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from gapcourt.prepare import (
    apply_patch,
    check_corpus,
    extract_lines,
    rebuild_all,
)

ROOT = Path(__file__).resolve().parents[1]


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def lsha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def write_doc(tmp_path, raw_name="x.txt", raw_text="alpha\nbeta\ngamma\ndelta\n", prep=None, corpus_body=None,
              republish="link_only", collection="uae"):
    raw = tmp_path / "corpus_local/_raw/X01" / raw_name
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(raw_text.encode("utf-8"))
    meta = [["doc_id", "X01"], ["title", "Synthetic doc"], ["sha256", sha(raw.read_bytes())], ["republish", republish]]
    entry = {"path": f"corpus_local/{collection}/X01.md", "raw_name": raw_name, "extractor": "text_lines",
             "options": {}, "meta": meta, "patch": []}
    if prep:
        entry.update(prep)
    (tmp_path / "corpus").mkdir(exist_ok=True)
    (tmp_path / "corpus/PREP.json").write_text(json.dumps({"version": 1, "docs": {"X01": entry}, "accepted_edits": {}}), encoding="utf-8")
    out = tmp_path / entry["path"]
    out.parent.mkdir(parents=True, exist_ok=True)
    if corpus_body is not None:
        head = "---\n" + "\n".join(f"{k}: {v}" for k, v in meta) + "\n---\n"
        out.write_text(head + corpus_body, encoding="utf-8", newline="\n")
    return raw, out, entry


# ---- extractors and patches ---------------------------------------------------

def test_text_lines_extractor_and_options(tmp_path):
    raw, _, _ = write_doc(tmp_path, raw_text="a  \n\nb\nHEADER 1\nc\n")
    assert extract_lines(raw, "text_lines", {}) == ["a  ", "", "b", "HEADER 1", "c"]
    opts = {"rstrip": True, "drop_blank": True, "drop_regex": ["^HEADER \\d$"]}
    assert extract_lines(raw, "text_lines", opts) == ["a", "b", "c"]


def test_apply_patch_inserts_and_deletes_with_guards():
    lines = ["a", "b", "c", "d", "e"]
    patch = [
        {"at": 1, "delete": 0, "insert": ["### Article 1"], "expect_before": lsha("b"), "class": "structural_heading"},
        {"at": 3, "delete": 2, "delete_sha256": lsha("d\ne"), "insert": ["[...]"], "class": "excerpt_selection"},
    ]
    assert apply_patch(lines, patch) == ["a", "### Article 1", "b", "c", "[...]"]


def test_apply_patch_refuses_when_the_source_line_changed():
    lines = ["a", "b", "c"]
    with pytest.raises(ValueError, match="guard"):
        apply_patch(lines, [{"at": 1, "delete": 1, "delete_sha256": lsha("zzz"), "insert": [], "class": "excerpt_selection"}])
    with pytest.raises(ValueError, match="guard"):
        apply_patch(lines, [{"at": 1, "delete": 0, "insert": ["x"], "expect_before": lsha("nope"), "class": "structural_heading"}])


def test_apply_patch_rejects_unclassified_hunks():
    with pytest.raises(ValueError, match="class"):
        apply_patch(["a"], [{"at": 0, "delete": 0, "insert": ["x"]}])


# ---- check_corpus with a recorded transform --------------------------------------

def test_recorded_transform_reproduces_a_corpus_file_byte_for_byte(tmp_path):
    patch = [{"at": 1, "delete": 0, "insert": ["### Part: B"], "expect_before": lsha("beta"), "class": "structural_heading"}]
    write_doc(tmp_path, prep={"patch": patch}, corpus_body="alpha\n### Part: B\nbeta\ngamma\ndelta\n")
    res = check_corpus(tmp_path)
    assert [(r.doc_id, r.status) for r in res] == [("X01", "REPRODUCED")]
    assert "text_lines" in res[0].detail


def test_hand_edit_after_the_transform_is_flagged_as_differs(tmp_path):
    write_doc(tmp_path, corpus_body="alpha\nbeta\ngamma\nDELTA\n")
    r = check_corpus(tmp_path)[0]
    assert r.status == "DIFFERS" and "DELTA" in r.detail


def test_rebuild_all_creates_missing_corpus_files_from_raw_and_prep(tmp_path):
    raw, out, entry = write_doc(tmp_path, corpus_body=None)
    assert not out.exists()
    written = rebuild_all(tmp_path, recipes={})
    assert [p.name for p in written] == ["X01.md"]
    assert check_corpus(tmp_path, recipes={})[0].status == "REPRODUCED"
    assert out.read_text(encoding="utf-8").startswith("---\ndoc_id: X01\ntitle: Synthetic doc\n")


def test_link_only_transform_may_not_store_source_text_in_the_patch(tmp_path):
    patch = [{"at": 1, "delete": 1, "delete_sha256": lsha("beta"), "insert": ["a rewritten sentence of the law"], "class": "rewrite"}]
    write_doc(tmp_path, prep={"patch": patch}, corpus_body="alpha\na rewritten sentence of the law\ngamma\ndelta\n")
    r = check_corpus(tmp_path)[0]
    assert r.status == "RECIPE_ERROR" and "source text" in r.detail


# ---- the verbatim audit -----------------------------------------------------------

def audit(tmp_path, raw_text, body, accepted=None):
    write_doc(tmp_path, raw_text=raw_text, corpus_body=body)
    prep = json.loads((tmp_path / "corpus/PREP.json").read_text(encoding="utf-8"))
    prep["docs"]["X01"] = {k: v for k, v in prep["docs"]["X01"].items() if k in ("path", "meta")}  # no recipe: audit only
    prep["accepted_edits"] = accepted or {}
    (tmp_path / "corpus/PREP.json").write_text(json.dumps(prep), encoding="utf-8")
    return check_corpus(tmp_path)[0]


def test_audit_accepts_wrapped_lines_typography_enumerators_and_labels(tmp_path):
    raw_text = ("The Party shall adopt\nmeasures to ensure oversight.\n"
                "It" + chr(0x2019) + "s a " + chr(0x2013) + " dash line\n"
                "Notice (sections 6.2.1) Plain language notice posted\n"
                "Level I Level II\n")
    body = ("<!--page:1-->\n### Article 3\n"
            "The Party shall adopt measures to ensure oversight.\n"
            "It's a - dash line\n"
            "1. The Party shall adopt measures to ensure oversight.\n"
            "Level I: Plain language notice posted\n"
            "[...]\n")
    r = audit(tmp_path, raw_text, body)
    assert r.status == "AUDITED", r.detail
    assert r.audit["tiers"]["typography"] == 1 and r.audit["tiers"]["enumerator"] == 1 and r.audit["tiers"]["labelled"] == 1
    assert r.audit["structural"] == {"page_markers": 1, "headings": 1, "omission_marks": 1}


def test_audit_flags_a_line_that_is_not_in_the_raw_file_without_printing_it(tmp_path):
    r = audit(tmp_path, "alpha\nbeta\n", "alpha\nan invented sentence\n")
    assert r.status == "UNMATCHED"
    assert "line 2" in r.detail and "invented" not in r.detail
    assert r.audit["unmatched"][0]["line_sha256"] == lsha("an invented sentence")


def test_audit_accepts_a_registered_edit_and_counts_it(tmp_path):
    accepted = {"X01": [{"line_sha256": lsha("an invented sentence"), "kind": "editor_note", "note": "provenance: reviewer"}]}
    r = audit(tmp_path, "alpha\nbeta\n", "alpha\nan invented sentence\n", accepted)
    assert r.status == "AUDITED" and r.audit["registered_edits"] == 1


def test_audit_reports_headings_whose_number_is_not_in_the_raw_text(tmp_path):
    r = audit(tmp_path, "Article (1)\nbody\n", "### Article 1\nArticle (1)\nbody\n### Article 9\n")
    assert r.audit["headings_unverified"] == ["Article 9"]


# ---- extractors for real file types ---------------------------------------------

def test_pymupdf_lines_marks_pages_and_applies_options(tmp_path):
    fitz = pytest.importorskip("fitz")
    pdf = tmp_path / "t.pdf"
    d = fitz.open()
    for text in ("RUNNING HEADER\nArticle (1)\nfirst page", "RUNNING HEADER\nsecond page"):
        d.new_page().insert_text((72, 72), text)
    d.save(str(pdf))
    got = extract_lines(pdf, "pymupdf_lines", {"rstrip": True, "drop_blank": True, "drop_regex": ["^RUNNING HEADER$"]})
    assert got[0] == "<!--page:1-->" and "<!--page:2-->" in got
    assert "RUNNING HEADER" not in got and "second page" in got


def test_html_blocks_joins_table_cells_and_normalizes_whitespace(tmp_path):
    pytest.importorskip("bs4")
    h = tmp_path / "t.html"
    h.write_bytes("<html><body><p>One&nbsp;line\n  wrapped</p><table><tr><td>Term</td><td>Meaning here</td></tr></table>"
                  "<ul><li>item <b>bold</b>.</li></ul></body></html>".encode("utf-8"))
    got = extract_lines(h, "html_blocks", {})
    assert got == ["<!--page:1-->", "One line wrapped", "Term Meaning here", "item bold ."]


# ---- the real corpus ------------------------------------------------------------

@pytest.mark.skipif(not (ROOT / "corpus_local" / "uae" / "U01.md").exists(), reason="link-only corpus not present")
def test_real_corpus_every_file_is_reproduced_or_audited_and_none_is_unaccounted():
    res = {r.doc_id: r for r in check_corpus(ROOT)}
    assert len(res) == 20
    bad = {d: (r.status, r.detail) for d, r in res.items() if r.status not in ("REPRODUCED", "AUDITED")}
    assert not bad, bad
    for d in ("U01", "U01EN", "U06", "U08", "U11", "U12", "U13", "U15", "U16"):
        assert res[d].status == "REPRODUCED", (d, res[d].detail)


def test_rebuild_all_skips_a_document_whose_raw_file_is_missing_and_says_so(tmp_path):
    raw, out, entry = write_doc(tmp_path, corpus_body=None)
    prep = json.loads((tmp_path / "corpus/PREP.json").read_text(encoding="utf-8"))
    other = json.loads(json.dumps(entry))
    other["path"] = "corpus_local/uae/X02.md"
    other["meta"] = [["doc_id", "X02"], ["sha256", "f" * 64], ["republish", "link_only"]]
    prep["docs"]["X02"] = other
    (tmp_path / "corpus/PREP.json").write_text(json.dumps(prep), encoding="utf-8")
    skipped: list = []
    written = rebuild_all(tmp_path, recipes={}, skipped=skipped)
    assert [p.name for p in written] == ["X01.md"]
    assert skipped and skipped[0][0] == "X02" and "raw file" in skipped[0][1]


# ---- Review fixes: audit source and recipe checks ------------------------------

def _add_sibling(tmp_path, name, text, listed):
    folder = tmp_path / "corpus_local/_raw/X01"
    p = folder / name
    p.write_bytes(text.encode("utf-8"))
    if listed:
        with open(folder / "SHA256SUMS.txt", "a", encoding="utf-8", newline="\n") as f:
            f.write(f"{sha(p.read_bytes())} *./{name}\n")


def audit_with_sibling(tmp_path, body, sibling_text, listed):
    write_doc(tmp_path, raw_text="alpha\nbeta\n", corpus_body=body)
    _add_sibling(tmp_path, "extra.txt", sibling_text, listed)
    prep = json.loads((tmp_path / "corpus/PREP.json").read_text(encoding="utf-8"))
    prep["docs"]["X01"] = {k: v for k, v in prep["docs"]["X01"].items() if k in ("path", "meta")}
    (tmp_path / "corpus/PREP.json").write_text(json.dumps(prep), encoding="utf-8")
    return check_corpus(tmp_path)[0]


def test_audit_does_not_trust_an_unhashed_sibling_file(tmp_path):
    r = audit_with_sibling(tmp_path, "alpha\nan invented sentence\n", "an invented sentence\n", listed=False)
    assert r.status == "UNMATCHED", r.detail
    assert r.audit["unverified_raw_files_ignored"] == ["extra.txt"]


def test_audit_uses_a_sibling_whose_hash_is_listed_and_verified(tmp_path):
    r = audit_with_sibling(tmp_path, "alpha\nan amending sentence\n", "an amending sentence\n", listed=True)
    assert r.status == "AUDITED", r.detail
    assert "extra.txt" in r.audit["raw_files"]


def test_audit_ignores_a_listed_sibling_whose_content_no_longer_matches_its_hash(tmp_path):
    write_doc(tmp_path, raw_text="alpha\nbeta\n", corpus_body="alpha\nan amending sentence\n")
    _add_sibling(tmp_path, "extra.txt", "an amending sentence\n", listed=True)
    (tmp_path / "corpus_local/_raw/X01/extra.txt").write_text("changed after listing\n", encoding="utf-8")
    prep = json.loads((tmp_path / "corpus/PREP.json").read_text(encoding="utf-8"))
    prep["docs"]["X01"] = {k: v for k, v in prep["docs"]["X01"].items() if k in ("path", "meta")}
    (tmp_path / "corpus/PREP.json").write_text(json.dumps(prep), encoding="utf-8")
    assert check_corpus(tmp_path)[0].status == "UNMATCHED"


def test_audit_records_which_raw_file_supplied_each_matched_line(tmp_path):
    # the audit says where every match came from
    r = audit_with_sibling(tmp_path, "alpha\nan amending sentence\nbeta\n", "an amending sentence\n", listed=True)
    assert r.status == "AUDITED", r.detail
    by = r.audit["matches_by_source"]
    assert by["extra.txt"] == 1 and sum(by.values()) == r.audit["content_lines"] - r.audit["registered_edits"]
    assert set(by) <= set(r.audit["raw_files"])


def test_audit_does_not_stitch_a_line_together_from_two_raw_files(tmp_path):
    r = audit_with_sibling(tmp_path, "alpha amending\n", "amending\n", listed=True)
    assert r.status == "UNMATCHED", r.detail


@pytest.mark.parametrize("body", ["", "\n\n", "### Article 1\n### Whatever\n", "<!--page:1-->\n[...]\n"])
def test_audit_cannot_pass_with_no_content_lines(tmp_path, body):
    r = audit(tmp_path, "alpha\nbeta\n", body)
    assert r.status == "EMPTY", (r.status, r.detail)


def test_prepare_cli_fails_when_there_is_no_corpus_file_at_all(tmp_path, capsys):
    from gapcourt.cli import main

    (tmp_path / "corpus").mkdir()
    (tmp_path / "corpus/PREP.json").write_text(json.dumps({"version": 1, "docs": {}, "accepted_edits": {}}), encoding="utf-8")
    assert main(["prepare", "--root", str(tmp_path)]) == 1
    assert "no corpus files" in capsys.readouterr().out.lower()
