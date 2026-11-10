"""Arabic quotation check by letter-set key (NOT_DONE 29), model-free.

The Arabic text layers of U01 and U08 carry ligature-order defects, so a model that quotes correctly spelled Arabic
fails the exact string check even though it quoted the right words. A letter-set check accepts such a quotation, but only
as a READING: the citation is kept and marked reading_only, and rule R1 stays as it was (an Arabic citation cannot carry
ALLOW or DENY; an Arabic passage supports a reading or an open question in a dossier, never a stated requirement).
"""
import shutil

import pytest

from gapcourt.bilingual import check_quotation_letter_set
from gapcourt.chunking import ChunkParams
from gapcourt.dossier import evaluate_spec, render_markdown
from gapcourt.embed import HashingEmbedder
from gapcourt.gate import apply_gate
from gapcourt.index import build_kb
from gapcourt.retrieve import HybridRetriever
from gapcourt.verify import check_quotation

DEFECTIVE = "عىل املتحكم إخطار املكتب بأي اخرتاق أو انتهاك للبيانات فور علمه به."  # as extracted (passage text)
CORRECT = "على المتحكم إخطار المكتب بأي اختراق أو انتهاك للبيانات"  # the same words, correctly spelled
EXACT = "عىل املتحكم إخطار املكتب بأي اخرتاق أو انتهاك للبيانات"
EN_QUOTE = "must not use the records for a different purpose"


@pytest.fixture
def kb(fixtures_dir, tmp_path):
    root = tmp_path / "corpus"
    (root / "uae").mkdir(parents=True)
    (root / "global").mkdir()
    shutil.copy(fixtures_dir / "S01.md", root / "uae" / "S01.md")
    shutil.copy(fixtures_dir / "S02.md", root / "global" / "S02.md")
    shutil.copy(fixtures_dir / "S03_ar.md", root / "uae" / "S03_ar.md")
    return build_kb([root], HashingEmbedder(dim=256), ChunkParams(max_chars=300))


def _hits(kb, k=8):
    return HybridRetriever(kb, ["kb_uae"], k=k).search("recipient records different purpose breach notify office controller")


def _hit(hits, doc, loc):
    return next(h for h in hits if h.chunk.doc_id == doc and h.chunk.locator == loc)


# ---- the check itself ---------------------------------------------------------------------------------------------------

def test_the_exact_check_rejects_correct_spelling_and_the_letter_set_check_accepts_it():
    assert not check_quotation(CORRECT, DEFECTIVE).verified
    assert check_quotation_letter_set(CORRECT, DEFECTIVE).verified


def test_the_letter_set_check_accepts_the_defective_spelling_too_and_diacritics():
    assert check_quotation_letter_set(EXACT, DEFECTIVE).verified
    assert check_quotation_letter_set("عَلى المُتحكم إخطار المكتب بأي اختراق", DEFECTIVE).verified


def test_a_changed_word_a_swapped_order_or_a_missing_word_is_not_accepted():
    assert not check_quotation_letter_set("على المتحكم إخطار المكتب بأي اختراق أو إتلاف للبيانات", DEFECTIVE).verified
    assert not check_quotation_letter_set("إخطار المكتب على المتحكم بأي اختراق أو انتهاك", DEFECTIVE).verified
    assert not check_quotation_letter_set("على المتحكم المكتب بأي اختراق", DEFECTIVE).verified  # a word dropped inside the run


def test_a_quotation_must_be_at_least_four_arabic_words():
    r = check_quotation_letter_set("على المتحكم إخطار", DEFECTIVE)
    assert not r.verified and r.reason == "too_short"
    assert not check_quotation_letter_set("", DEFECTIVE).verified


def test_english_words_do_not_count_toward_the_minimum_or_pass_by_letter_set():
    r = check_quotation_letter_set("must not use the records", "The recipient must not use the records for a different purpose.")
    assert not r.verified and r.reason == "too_short"  # the key only applies to Arabic-script words


def test_ellipsis_fragments_must_each_be_two_words_and_in_order():
    assert check_quotation_letter_set("على المتحكم [...] بأي اختراق أو انتهاك", DEFECTIVE).verified
    assert not check_quotation_letter_set("بأي اختراق أو انتهاك [...] على المتحكم", DEFECTIVE).verified
    assert not check_quotation_letter_set("على المتحكم [...] اختراق [...] انتهاك للبيانات", DEFECTIVE).verified


# ---- the gate: a letter-set quotation is kept as a reading and R1 stays intact ---------------------------------------

def test_the_gate_keeps_a_correctly_spelled_arabic_quotation_as_a_reading_only(kb):
    hits = _hits(kb)
    ar = _hit(hits, "S03", "Article 3")
    g = apply_gate({"decision": "ESCALATE", "citations": [{"chunk_id": ar.chunk.chunk_id, "quote": CORRECT}]}, hits, ["kb_uae"], kb)
    assert not g.dropped and len(g.kept_citations) == 1
    c = g.kept_citations[0]
    assert c["quotation_verified"] is True and c["quotation_check"] == "letter_set" and c["reading_only"] is True
    assert c["language"] == "ar"


def test_an_exact_arabic_copy_is_also_reading_only_and_an_english_citation_is_not(kb):
    hits = _hits(kb)
    ar, en = _hit(hits, "S03", "Article 3"), _hit(hits, "S01", "Article 3")
    g = apply_gate({"decision": "ESCALATE", "citations": [{"chunk_id": ar.chunk.chunk_id, "quote": EXACT},
                                                           {"chunk_id": en.chunk.chunk_id, "quote": EN_QUOTE}]}, hits, ["kb_uae"], kb)
    by_doc = {c["doc_id"]: c for c in g.kept_citations}
    assert by_doc["S03"]["quotation_check"] == "exact" and by_doc["S03"]["reading_only"] is True
    assert by_doc["S01"]["quotation_check"] == "exact" and by_doc["S01"]["reading_only"] is False


def test_r1_a_letter_set_quotation_cannot_carry_allow_or_deny(kb):
    hits = _hits(kb)
    ar = _hit(hits, "S03", "Article 3")
    for decision in ("ALLOW", "DENY"):
        g = apply_gate({"decision": decision, "citations": [{"chunk_id": ar.chunk.chunk_id, "quote": CORRECT}]}, hits, ["kb_uae"], kb)
        assert g.kept_citations and g.decision == "ESCALATE", decision
        assert any("Arabic" in n for n in g.notes)


def test_r1_an_english_binding_citation_still_carries_a_ruling_next_to_a_letter_set_one(kb):
    hits = _hits(kb)
    ar, en = _hit(hits, "S03", "Article 3"), _hit(hits, "S01", "Article 3")
    g = apply_gate({"decision": "DENY", "citations": [{"chunk_id": ar.chunk.chunk_id, "quote": CORRECT},
                                                      {"chunk_id": en.chunk.chunk_id, "quote": EN_QUOTE}]}, hits, ["kb_uae"], kb)
    assert g.decision == "DENY"


def test_the_letter_set_check_is_for_arabic_passages_only(kb):
    hits = _hits(kb)
    en = _hit(hits, "S01", "Article 3")
    g = apply_gate({"decision": "ESCALATE", "citations": [{"chunk_id": en.chunk.chunk_id, "quote": CORRECT},
                                                           {"chunk_id": en.chunk.chunk_id, "quote": EN_QUOTE.upper()}]}, hits, ["kb_uae"], kb)
    assert not g.kept_citations and len(g.dropped) == 2  # an Arabic quote against English text; a case change on English text


def test_a_changed_word_in_an_arabic_quotation_is_still_dropped(kb):
    hits = _hits(kb)
    ar = _hit(hits, "S03", "Article 3")
    g = apply_gate({"decision": "ESCALATE", "citations": [{"chunk_id": ar.chunk.chunk_id,
                    "quote": "على المتحكم إخطار المكتب بأي إتلاف أو انتهاك للبيانات"}]}, hits, ["kb_uae"], kb)
    assert not g.kept_citations and g.dropped[0]["reason"].startswith("quotation_")


def test_a_scope_finding_on_an_arabic_passage_by_letter_set_is_kept_as_a_reading(kb):
    hits = HybridRetriever(kb, ["kb_uae"], k=10).search("does the law apply to government entities that control personal data")
    ar = _hit(hits, "S03", "Article 2")
    raw = {"decision": "ESCALATE", "citations": [], "applicability": [
        {"chunk_id": ar.chunk.chunk_id, "applies": "no", "quote": "وال ترسي عىل الجهات الحكومية املتحكمة بالبيانات"}]}
    g = apply_gate(raw, hits, ["kb_uae"], kb)
    assert len(g.scope_findings) == 1
    assert g.scope_findings[0]["reading_only"] is True and g.scope_findings[0]["language"] == "ar"
    raw["applicability"][0]["quote"] = "ولا تسري على الجهات الحكومية المتحكمة بالبيانات"  # correctly spelled
    g2 = apply_gate(raw, hits, ["kb_uae"], kb)
    f = g2.scope_findings[0]
    assert f["quotation_check"] == "letter_set" and f["reading_only"] is True


# ---- the dossier checker -----------------------------------------------------------------------------------------------

def _spec(claims, findings):
    return {"id": "T", "title": "T", "requirement": "r", "claims": claims, "findings": findings, "searches": []}


def test_a_dossier_claim_checked_by_letter_set_is_verified_as_a_reading_and_says_so(kb):
    claim = {"id": "C3", "doc_id": "S03", "locator": "Article 3", "quote": CORRECT}
    spec = _spec([claim], [{"id": "F1", "kind": "reading", "statement": "x", "claims": ["C3"]}])
    res = evaluate_spec(kb, spec)
    assert res["claims"]["C3"]["status"] == "verified" and res["claims"]["C3"]["check"] == "letter_set"
    assert res["findings"]["F1"]["status"] == "supported"
    assert "letter-set match, reading only" in render_markdown(spec, res)
    for kind in ("requirement", "context", "comparator"):
        spec2 = _spec([claim], [{"id": "F1", "kind": kind, "statement": "x", "claims": ["C3"]}])
        assert evaluate_spec(kb, spec2)["findings"]["F1"]["status"] == "flagged", kind


def test_an_exact_claim_is_marked_exact_and_prints_no_letter_set_flag(kb):
    claim = {"id": "C3", "doc_id": "S03", "locator": "Article 3", "quote": EXACT}
    spec = _spec([claim], [{"id": "F1", "kind": "reading", "statement": "x", "claims": ["C3"]}])
    res = evaluate_spec(kb, spec)
    assert res["claims"]["C3"]["check"] == "exact"
    assert "letter-set" not in render_markdown(spec, res)


def test_a_letter_set_claim_with_a_changed_word_is_not_verified(kb):
    claim = {"id": "C3", "doc_id": "S03", "locator": "Article 3", "quote": "على المتحكم إخطار المكتب بأي إتلاف أو انتهاك للبيانات"}
    res = evaluate_spec(kb, _spec([claim], [{"id": "F1", "kind": "reading", "statement": "x", "claims": ["C3"]}]))
    assert res["claims"]["C3"]["status"] != "verified" and res["findings"]["F1"]["status"] == "flagged"


# ---- verification notes (Session 8: comparators re-opened on the primary source) -----------------------------------------

def test_a_dossier_renders_its_primary_source_verification_notes_before_the_disposition(kb):
    claim = {"id": "C1", "doc_id": "S01", "locator": "Article 3", "quote": "must not use the records for a different purpose"}
    spec = _spec([claim], [{"id": "F1", "kind": "requirement", "statement": "x", "claims": ["C1"]}])
    spec["verification_notes"] = ["C17: found verbatim on the primary source on 2026-11-02.", "Not verified: the English readings."]
    md = render_markdown(spec, evaluate_spec(kb, spec))
    assert "## Primary-source verification notes" in md and "- C17: found verbatim on the primary source" in md
    assert md.index("## Primary-source verification notes") < md.index("## Disposition")
    assert "Primary-source verification notes" not in render_markdown(_spec([claim], []), evaluate_spec(kb, _spec([claim], [])))
