"""Findings of the independent review of Session 6 (docs/SESSION_REPORT_2026-11-01.md, Session 7), each as a test.

Model-free. Rule R1: a ruling (ALLOW or DENY, which becomes the outcome ANSWER) needs a verbatim quotation from a
BINDING source in a language the quotation check was built for; an Arabic passage can only support a reading.
"""
import json
import shutil

import pytest

from gapcourt.chunking import ChunkParams
from gapcourt.dossier import evaluate_spec, render_markdown
from gapcourt.embed import HashingEmbedder
from gapcourt.gate import apply_gate
from gapcourt.index import build_kb
from gapcourt.llm import ScriptedModel
from gapcourt.retrieve import HybridRetriever
from gapcourt.roles import RoleAgent, RoleSpec


@pytest.fixture
def kb(fixtures_dir, tmp_path):
    root = tmp_path / "corpus"
    (root / "uae").mkdir(parents=True)
    (root / "global").mkdir()
    shutil.copy(fixtures_dir / "S01.md", root / "uae" / "S01.md")
    shutil.copy(fixtures_dir / "S02.md", root / "global" / "S02.md")
    shutil.copy(fixtures_dir / "S03_ar.md", root / "uae" / "S03_ar.md")
    return build_kb([root], HashingEmbedder(dim=256), ChunkParams(max_chars=300))


AR_QUOTE = "عىل املتحكم إخطار املكتب بأي اخرتاق أو انتهاك للبيانات"
AR_CLAIM = {"id": "C3", "doc_id": "S03", "locator": "Article 3", "quote": AR_QUOTE}
EN_CLAIM = {"id": "C1", "doc_id": "S01", "locator": "Article 3",
            "quote": "The recipient must not use the records for a different purpose without a written basis"}
NB_CLAIM = {"id": "C2", "doc_id": "S02", "locator": "Principle 1",
            "quote": "will seek human oversight of consequential automated decisions"}
BREACH_Q = "Must a controller notify the office of a personal data breach?"


def _spec(claims, findings, searches=None):
    return {"id": "T", "title": "T", "requirement": "r", "claims": claims, "findings": findings, "searches": searches or []}


def _reply(citations=None, decision="ESCALATE"):
    return json.dumps({"decision": decision, "rule_applied": "r", "rationale": "r", "citations": citations or [],
                       "gap": None, "applicability": [], "revision_reason": None})


def _agent(kb, model):
    return RoleAgent(RoleSpec(name="adviser", page_role="adviser", collections=["kb_uae"], doc_ids=None, focus="",
                              instructions="i"), kb, model)


# ---- finding 1: R1 is enforced by the gate, not only by the documentation -------------------------------------------

def test_a_verbatim_arabic_quotation_cannot_carry_an_allow_or_deny(kb):
    hits = HybridRetriever(kb, ["kb_uae"], k=5).search(BREACH_Q)
    ar = next(h for h in hits if h.chunk.doc_id == "S03" and h.chunk.locator == "Article 3")
    raw = {"decision": "DENY", "citations": [{"chunk_id": ar.chunk.chunk_id, "quote": AR_QUOTE}]}
    g = apply_gate(raw, hits, ["kb_uae"], kb)
    assert g.kept_citations and g.kept_citations[0]["binding"] is True  # the Arabic quotation is a real substring
    assert g.kept_citations[0]["language"] == "ar"
    assert g.decision == "ESCALATE"
    assert any("Arabic" in n for n in g.notes)


def test_an_english_binding_citation_still_carries_a_ruling_next_to_an_arabic_one(kb):
    hits = HybridRetriever(kb, ["kb_uae"], k=8).search("recipient records different purpose breach notify office controller")
    en = next(h for h in hits if h.chunk.doc_id == "S01" and h.chunk.locator == "Article 3")
    ar = next(h for h in hits if h.chunk.doc_id == "S03" and h.chunk.locator == "Article 3")
    raw = {"decision": "DENY", "citations": [
        {"chunk_id": ar.chunk.chunk_id, "quote": AR_QUOTE},
        {"chunk_id": en.chunk.chunk_id, "quote": "must not use the records for a different purpose"}]}
    g = apply_gate(raw, hits, ["kb_uae"], kb)
    assert g.decision == "DENY"


def test_the_gate_record_names_the_language_of_every_kept_citation(kb):
    hits = HybridRetriever(kb, ["kb_uae"], k=5).search(BREACH_Q)
    ar = next(h for h in hits if h.chunk.doc_id == "S03" and h.chunk.locator == "Article 3")
    out = _agent(kb, ScriptedModel([_reply([{"chunk_id": ar.chunk.chunk_id, "quote": AR_QUOTE}])])).assess(1, [BREACH_Q], [])
    assert out.gate_record()["kept"][0]["language"] == "ar"


# ---- finding 2: the Arabic quota is visible in the saved record ----------------------------------------------------

def test_a_passage_that_arrived_through_the_arabic_quota_is_marked_in_the_logged_passages(kb):
    agent = _agent(kb, ScriptedModel([_reply()]))
    out = agent.assess(1, [BREACH_Q], [])
    slots = {h.chunk.chunk_id for h in agent.retriever.search(out.query) if h.arabic_slot}
    assert slots
    for p in out.passages:
        if p["chunk_id"] in slots:
            assert p["via"] == "arabic_quota"
        else:
            assert "via" not in p


# ---- finding 5: the dossier checker's bars do not depend on flags the spec chooses to set ----------------------------

def test_a_requirement_finding_needs_a_binding_source_even_without_the_duty_flag(kb):
    res = evaluate_spec(kb, _spec([NB_CLAIM], [{"id": "F1", "kind": "requirement", "statement": "Oversight is required.",
                                                "claims": ["C2"]}]))
    assert res["findings"]["F1"]["status"] == "flagged" and "non-binding" in res["findings"]["F1"]["reason"]


def test_arabic_passage_supports_only_a_reading_or_an_unresolved_question(kb):
    for kind in ("context", "requirement", "comparator"):
        res = evaluate_spec(kb, _spec([AR_CLAIM], [{"id": "F1", "kind": kind, "statement": "x", "claims": ["C3"]}]))
        assert res["findings"]["F1"]["status"] == "flagged" and "Arabic" in res["findings"]["F1"]["reason"], kind
    res = evaluate_spec(kb, _spec([AR_CLAIM], [{"id": "F1", "kind": "reading", "statement": "x", "claims": ["C3"]}]))
    assert res["findings"]["F1"]["status"] == "supported"


def test_an_unknown_language_value_is_flagged_not_waved_through(kb, monkeypatch):
    rows = kb.coverage_table()
    for r in rows:
        if r["doc_id"] == "S03":
            r["language"] = "ar-EN"
    monkeypatch.setattr(kb, "coverage_table", lambda: rows)
    res = evaluate_spec(kb, _spec([AR_CLAIM], [{"id": "F1", "kind": "reading", "statement": "x", "claims": ["C3"]}]))
    assert res["findings"]["F1"]["status"] == "flagged" and "language" in res["findings"]["F1"]["reason"]


# ---- finding 7: duplicate ids and prose that leans on a flagged finding ----------------------------------------------

def test_duplicate_claim_finding_and_search_ids_are_rejected_before_evaluation(kb):
    dup_f = [{"id": "F1", "kind": "requirement", "statement": "bad", "claims": []},
             {"id": "F1", "kind": "requirement", "statement": "good", "claims": ["C1"]}]
    with pytest.raises(ValueError, match="duplicate"):
        evaluate_spec(kb, _spec([EN_CLAIM], dup_f))
    with pytest.raises(ValueError, match="duplicate"):
        evaluate_spec(kb, _spec([EN_CLAIM, EN_CLAIM], []))
    with pytest.raises(ValueError, match="duplicate"):
        evaluate_spec(kb, _spec([EN_CLAIM], [], searches=[{"id": "S1", "collections": ["kb_uae"], "query": "a"},
                                                          {"id": "S1", "collections": ["kb_uae"], "query": "b"}]))


def test_the_rendered_dossier_warns_when_a_finding_failed_because_the_prose_was_written_before_the_check(kb):
    bad = {**EN_CLAIM, "id": "C1x", "quote": "an invented sentence that is not in the passage at all"}
    spec = _spec([EN_CLAIM, bad], [{"id": "F1", "kind": "requirement", "statement": "ok", "claims": ["C1"]},
                                   {"id": "F2", "kind": "requirement", "statement": "gone", "claims": ["C1x"]}])
    spec["conclusion"] = "The conclusion leans on F2."
    head = render_markdown(spec, evaluate_spec(kb, _spec_of(spec))).split("## Requirement at issue")[0]
    assert "1 finding failed" in head and "F2" in head and "Requirement" in head and "Conclusion" in head
    clean = _spec([EN_CLAIM], [{"id": "F1", "kind": "requirement", "statement": "ok", "claims": ["C1"]}])
    assert "failed" not in render_markdown(clean, evaluate_spec(kb, clean)).split("## Requirement at issue")[0]


def _spec_of(spec):
    return spec


# ---- finding 11: a long quotation from link-only text is not a verification quote ------------------------------------

def test_a_link_only_quotation_over_the_limit_is_refused_and_open_text_is_not_limited(kb, monkeypatch):
    from gapcourt.export import MAX_QUOTE_WORDS
    assert MAX_QUOTE_WORDS >= 40
    chunk = next(e.chunk for e in kb.entries if e.chunk.doc_id == "S01" and e.chunk.locator == "Article 3")
    long_quote = " ".join(chunk.text.split())
    long_claim = {**EN_CLAIM, "quote": long_quote}
    spec = _spec([long_claim], [{"id": "F1", "kind": "requirement", "statement": "x", "claims": ["C1"]}])
    rows = kb.coverage_table()
    for r in rows:
        r["republish"] = "link_only"
    monkeypatch.setattr(kb, "coverage_table", lambda: rows)
    monkeypatch.setattr("gapcourt.dossier.MAX_QUOTE_WORDS", 5)
    refused = evaluate_spec(kb, spec)
    assert refused["claims"]["C1"]["status"] == "quote_too_long_for_link_only"
    assert refused["findings"]["F1"]["status"] == "flagged"
    open_rows = kb.coverage_table()
    for r in open_rows:
        r["republish"] = "yes"
    monkeypatch.setattr(kb, "coverage_table", lambda: open_rows)
    assert evaluate_spec(kb, spec)["claims"]["C1"]["status"] == "verified"


# ---- finding 9: report facts are re-derived from the records beneath the aggregates, or the report refuses ------------

def _kb(tmp_path):
    d = tmp_path / "kb"
    d.mkdir(exist_ok=True)
    (d / "KB_MANIFEST.json").write_text(json.dumps({"kb_version": "kb-test123456", "chunk_count": 1, "embedder": {"name": "h", "dim": 8},
                                                    "documents": []}), encoding="utf-8")
    return d


def test_report_refuses_dossier_counts_that_disagree_with_the_claims_beneath_them(tmp_path):
    from gapcourt.report import collect_facts
    ev = {"counts": {"claims": 2, "claims_verified": 2, "findings": 1, "findings_supported": 1},
          "claims": {"C1": {"status": "verified"}, "C2": {"status": "quote_not_found"}},
          "findings": {"F1": {"status": "supported"}}}
    p = tmp_path / "dossier.json"
    p.write_text(json.dumps(ev), encoding="utf-8")
    with pytest.raises(ValueError, match="disagree"):
        collect_facts(runs_root=tmp_path / "runs", kb_dir=_kb(tmp_path), dossier_evidence=p)
    ev["counts"]["claims_verified"] = 1
    p.write_text(json.dumps(ev), encoding="utf-8")
    facts = collect_facts(runs_root=tmp_path / "runs", kb_dir=_kb(tmp_path), dossier_evidence=p)
    assert facts["dossier.claims_verified"].value == "1"


def test_report_refuses_retrieval_totals_that_disagree_with_the_per_question_rows(tmp_path):
    from gapcourt.report import collect_facts
    on = {"probes": {"hybrid": {"hit": 2, "n": 2, "by_probe": {"B01": {"hit": True}, "B02": {"hit": False}}}},
          "dev": {"hybrid": {"expected_found": 1, "scored": 2, "by_case": {"PC01": {"expected_found": True}, "PC03": {"expected_found": False}}}}}
    off = {"probes": {"hybrid": {"hit": 0, "n": 2, "by_probe": {"B01": {"hit": False}, "B02": {"hit": False}}}},
           "dev": {"hybrid": {"expected_found": 1, "scored": 2, "by_case": {"PC01": {"expected_found": True}, "PC03": {"expected_found": False}}}}}
    p = tmp_path / "retr.json"
    p.write_text(json.dumps({"runs": {"off": off, "on": on}}), encoding="utf-8")
    with pytest.raises(ValueError, match="disagree"):
        collect_facts(runs_root=tmp_path / "runs", kb_dir=_kb(tmp_path), retrieval_results=p)
    on["probes"]["hybrid"]["hit"] = 1
    p.write_text(json.dumps({"runs": {"off": off, "on": on}}), encoding="utf-8")
    assert collect_facts(runs_root=tmp_path / "runs", kb_dir=_kb(tmp_path), retrieval_results=p)["retrieval.probes_after"].value == "1"


def test_the_report_template_does_not_point_to_scored_runs_that_do_not_exist():
    from pathlib import Path
    tpl = (Path(__file__).resolve().parent.parent / "report" / "TEMPLATE_V3_FILLABLE.md").read_text(encoding="utf-8")
    assert "from the scored runs named above" not in tpl
    assert "no scored case tests it" in tpl
    assert "adjusted" in tpl.split("Retrieval check, offline", 1)[1].split("\n", 1)[0]  # the map was adjusted after probes missed
