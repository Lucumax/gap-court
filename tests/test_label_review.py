"""Label review for the freeze: one card per case with the source passage inline, then one approval command."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from gapcourt.chunking import ChunkParams
from gapcourt.embed import HashingEmbedder
from gapcourt.eval import EvalError, load_labels
from gapcourt.index import build_kb
from gapcourt.labelreview import approve_labels, render_cards


@pytest.fixture
def kb(fixtures_dir, tmp_path):
    root = tmp_path / "corpus"
    (root / "uae").mkdir(parents=True)
    shutil.copy(fixtures_dir / "S01.md", root / "uae" / "S01.md")
    k = build_kb([root], HashingEmbedder(dim=64), ChunkParams(max_chars=400))
    k.applicability = {"approved": {"S01"}, "sha256": "test-approvals"}  # test fixture, never the real corpus file
    return k


def first_chunk(kb):
    c = kb.entries[0].chunk
    return c.doc_id, c.locator, c.text


def labels(kb, quote=None, tmp_path=None):
    doc, loc, text = first_chunk(kb)
    q = quote if quote is not None else " ".join(text.split()[:6])
    rows = [
        {"case_id": "PC01", "split": "dev", "category": "answerable", "question": "What does the first article say <b>?",
         "expected": [{"doc_id": doc, "locator": loc}], "expected_outcome": "ANSWER", "basis_quote": q,
         "status": "DRAFT_UNAPPROVED", "drafted_by": "LLM-drafted candidate; unapproved; single reviewer pending"},
        {"case_id": "PC02", "split": "heldout", "category": "missing_document", "question": "Is there a rule on X?", "expected": [],
         "expected_outcome": "GAP", "basis_quote": "", "status": "DRAFT_UNAPPROVED", "drafted_by": "LLM"},
        {"case_id": "PC03", "split": "heldout", "category": "answerable", "question": "Second", "expected": [{"doc_id": doc, "locator": loc}],
         "expected_outcome": "ANSWER", "basis_quote": q, "status": "DRAFT_UNAPPROVED", "drafted_by": "LLM"},
    ]
    p = tmp_path / "draft.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return p


def test_cards_show_question_expected_outcome_and_the_passage_text_inline(kb, tmp_path):
    p = labels(kb, tmp_path=tmp_path)
    html = render_cards(load_labels(p), kb)
    doc, loc, text = first_chunk(kb)
    assert "PC01" in html and "ANSWER" in html and "heldout" in html
    assert text.split(".")[0][:40] in html
    assert "quote found in the expected passage" in html
    assert "&lt;b&gt;" in html and "<b>?" not in html


def test_cards_flag_a_basis_quote_that_is_not_in_the_expected_passage(kb, tmp_path):
    p = labels(kb, quote="words that are not in the passage at all", tmp_path=tmp_path)
    html = render_cards(load_labels(p), kb)
    assert "QUOTE NOT FOUND" in html


def test_cards_for_gap_cases_say_that_no_governing_passage_is_expected(kb, tmp_path):
    html = render_cards(load_labels(labels(kb, tmp_path=tmp_path)), kb)
    assert "no governing passage is expected" in html


def test_cards_carry_no_model_answers_and_no_scripts(kb, tmp_path):
    html = render_cards(load_labels(labels(kb, tmp_path=tmp_path)), kb)
    assert "<script" not in html and "model answer" not in html.lower().replace("without seeing model answers", "")
    assert "without seeing model answers" in html


def test_approve_all_marks_every_case_approved_with_a_named_reviewer(kb, tmp_path):
    src = labels(kb, tmp_path=tmp_path)
    out = tmp_path / "labels_v1.jsonl"
    approve_labels(src, out, reviewer="W. Reviewer", approve="all")
    rows = load_labels(out)
    assert {r["status"] for r in rows} == {"APPROVED"} and all(r["reviewed_by"] == "W. Reviewer" for r in rows)


def test_approve_some_leaves_the_rest_unapproved(kb, tmp_path):
    src = labels(kb, tmp_path=tmp_path)
    out = tmp_path / "labels_v1.jsonl"
    approve_labels(src, out, reviewer="W. Reviewer", approve=["PC01", "PC03"])
    st = {r["case_id"]: r["status"] for r in load_labels(out)}
    assert st == {"PC01": "APPROVED", "PC02": "DRAFT_UNAPPROVED", "PC03": "APPROVED"}


def test_corrections_change_fields_and_mark_the_case_corrected(kb, tmp_path):
    src = labels(kb, tmp_path=tmp_path)
    out = tmp_path / "labels_v1.jsonl"
    approve_labels(src, out, reviewer="W. Reviewer", approve="all", corrections={"PC02": {"expected_outcome": "ABSTAIN"}})
    rows = {r["case_id"]: r for r in load_labels(out)}
    assert rows["PC02"]["status"] == "CORRECTED" and rows["PC02"]["expected_outcome"] == "ABSTAIN"
    assert rows["PC02"]["original"]["expected_outcome"] == "GAP" and rows["PC01"]["status"] == "APPROVED"


def test_approve_refuses_unknown_ids_empty_reviewer_bad_corrections_and_overwrite(kb, tmp_path):
    src = labels(kb, tmp_path=tmp_path)
    out = tmp_path / "labels_v1.jsonl"
    with pytest.raises(EvalError, match="PC99"):
        approve_labels(src, out, reviewer="R", approve=["PC99"])
    with pytest.raises(EvalError, match="reviewer"):
        approve_labels(src, out, reviewer=" ", approve="all")
    with pytest.raises(EvalError, match="split|category|expected_outcome"):
        approve_labels(src, out, reviewer="R", approve="all", corrections={"PC01": {"expected_outcome": "MAYBE"}})
    assert not out.exists()
    approve_labels(src, out, reviewer="R", approve="all")
    with pytest.raises(EvalError, match="exists"):
        approve_labels(src, out, reviewer="R", approve="all")


def test_approved_file_changes_the_label_hash_so_the_freeze_records_the_review(kb, tmp_path):
    from gapcourt.eval import labels_sha256

    src = labels(kb, tmp_path=tmp_path)
    out = tmp_path / "labels_v1.jsonl"
    approve_labels(src, out, reviewer="R", approve="all")
    assert labels_sha256(src) != labels_sha256(out)


def test_a_corrected_heldout_label_can_be_frozen(kb, tmp_path):
    from gapcourt.score import make_freeze

    src = labels(kb, tmp_path=tmp_path)
    out = tmp_path / "labels_v1.jsonl"
    approve_labels(src, out, reviewer="R", approve="all", corrections={"PC02": {"expected_outcome": "ABSTAIN"}})
    fr = make_freeze(out, kb, [], {"model_id": "m"}, "R", [], True, "t")
    assert fr["heldout_case_ids"] == ["PC02", "PC03"] and fr["reviewer"] == "R"


def test_cards_open_with_the_meaning_of_each_outcome_word_as_the_adviser_is_told(kb, tmp_path):
    html = render_cards(load_labels(labels(kb, tmp_path=tmp_path)), kb)
    legend = html.split("<div class='card'>", 1)[0]
    for word in ("ANSWER", "ABSTAIN", "GAP", "ESCALATE", "SCOPE_FINDING"):
        assert f"<b>{word}</b>" in legend
    assert "the document that would govern it is not among them" in legend
    assert "cover the subject but no passage states a rule" in legend


def test_held_out_cards_come_first_under_their_own_heading_and_blocked_cases_say_so(kb, tmp_path):
    rows = load_labels(labels(kb, tmp_path=tmp_path))
    rows[1] = {**rows[1], "blocked_on": "English text of U01"}
    html = render_cards(rows, kb)
    assert html.index("Held-out cases") < html.index("PC02") < html.index("Development cases") < html.index("PC01")
    assert "BLOCKED" in html and "English text of U01" in html


# ---- proposed corrections shown on the cards (D2, 2026-11-09) -------------------------------------------------------

def test_cards_show_a_proposed_correction_with_its_passage_and_do_not_apply_it(kb, tmp_path):
    doc, loc, text = first_chunk(kb)
    rows = load_labels(labels(kb, tmp_path=tmp_path))
    rows[1]["blocked_on"] = "waiting for a source"
    q = " ".join(text.split()[:6])
    corr = {"PC02": {"blocked_on": None, "expected_outcome": "SCOPE_FINDING", "expected": [{"doc_id": doc, "locator": loc}],
                     "basis_quote": q}}
    html = render_cards(rows, kb, corr, "corr.json")
    card = html.split("PC02")[1].split("PC03")[0]
    assert "Proposed correction" in card and "NOT applied" in card and "corr.json" in card
    assert "GAP &rarr; SCOPE_FINDING" in card and "clears the blocker" in card
    assert "quote found in the proposed passage" in card
    assert "BLOCKED: waiting for a source" in card  # the draft itself is unchanged
    assert rows[1]["expected_outcome"] == "GAP" and rows[1]["blocked_on"] == "waiting for a source"
    assert "Proposed correction" not in html.split("PC03")[1]  # no correction, no box


def test_cards_without_corrections_are_unchanged(kb, tmp_path):
    rows = load_labels(labels(kb, tmp_path=tmp_path))
    assert render_cards(rows, kb) == render_cards(rows, kb, None, None)
    assert "Proposed correction" not in render_cards(rows, kb)
