"""Checks on the draft pilot cases against the local corpus. Skipped when the link-only files are absent
(they are not in the public tree)."""
import json
from collections import Counter
from pathlib import Path

import pytest

from gapcourt.corpus import load_document
from gapcourt.eval import load_labels
from gapcourt.verify import check_quotation

ROOT = Path(__file__).resolve().parents[1]
LABELS = ROOT / "evals" / "pilot_cases_DRAFT.jsonl"


def test_shape_21_cases_11_dev_10_heldout_all_unapproved():
    rows = load_labels(LABELS)
    assert len(rows) == 21
    assert Counter(r["split"] for r in rows) == {"dev": 11, "heldout": 10}
    assert all(r["status"] == "DRAFT_UNAPPROVED" for r in rows)
    assert Counter(r["category"] for r in rows) == {
        "answerable": 6, "paraphrase_trap": 4, "missing_document": 4, "ambiguity": 3, "candidate_gap": 3, "applicability_trap": 1}
    assert len({r["case_id"] for r in rows}) == 21


def test_the_adversarial_applicability_case_is_a_draft_development_case_that_expects_escalation():
    pc21 = next(r for r in load_labels(LABELS) if r["case_id"] == "PC21")
    assert pc21["split"] == "dev" and pc21["status"] == "DRAFT_UNAPPROVED" and pc21["expected_outcome"] == "ESCALATE"
    assert pc21["category"] == "applicability_trap" and "other actor" in pc21["note"]
    assert {(e["doc_id"], e["locator"]) for e in pc21["expected"]} == {("U12", "Article 3 [2]"), ("U12", "Article 9")}


def test_every_split_has_each_kind_of_case():
    rows = load_labels(LABELS)
    for split in ("dev", "heldout"):
        cats = {r["category"] for r in rows if r["split"] == split}
        assert {"answerable", "missing_document", "candidate_gap"} <= cats


def test_no_banned_phrases_in_questions():
    text = LABELS.read_text(encoding="utf-8").lower()
    assert ("the uae " + "should") not in text and ("years of " + "experience") not in text


@pytest.mark.skipif(not (ROOT / "corpus_local" / "uae" / "U06.md").exists(), reason="link-only corpus not present")
def test_expected_passages_exist_and_basis_quotes_are_verbatim():
    for r in load_labels(LABELS):
        for e in r["expected"]:
            if e["doc_id"] == "U01":  # Arabic text layer; blocked_on an English text
                assert r.get("blocked_on")
                continue
            path = ROOT / "corpus_local" / "uae" / f"{e['doc_id']}.md"
            doc = load_document(path)
            secs = {s.locator: s.text for s in doc.sections}
            assert e["locator"] in secs, (r["case_id"], e)
        if r.get("basis_quote"):
            texts = []
            for e in r["expected"]:
                if e["doc_id"] == "U01":
                    continue
                doc = load_document(ROOT / "corpus_local" / "uae" / f"{e['doc_id']}.md")
                texts += [s.text for s in doc.sections if s.locator == e["locator"]]
            assert any(check_quotation(r["basis_quote"], t).verified for t in texts), r["case_id"]
