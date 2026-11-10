"""Pre-freeze fixes of 2026-11-05 (one test per behaviour).

1. paired comparison table (full system against retrieval plus abstain, same cases)
2. applicability gate for decisive recommendations
3. one meaningful revision, recorded with previous recommendation, new fact, revised recommendation and reason
"""
import json
import re

import pytest
from test_score_addstep import QUOTE, kb, make_case_run, reply, write_labels  # noqa: F401  (kb is a fixture)

from gapcourt.llm import ModelResponse

PAIRED_LABELS = [
    {"case_id": "C01", "split": "dev", "category": "answerable", "status": "APPROVED",
     "question": "may the recipient agency use the shared records for a different purpose",
     "expected": [{"doc_id": "S01", "locator": "Article 3"}], "expected_outcome": "ANSWER"},
    {"case_id": "C02", "split": "dev", "category": "candidate_gap", "status": "APPROVED",
     "question": "must a resident be told an automated tool assessed them", "expected": [], "expected_outcome": "GAP"},
    {"case_id": "C03", "split": "dev", "category": "candidate_gap", "status": "APPROVED",
     "question": "may the recipient agency use the shared records for a different purpose again",
     "expected": [], "expected_outcome": "GAP"},
    {"case_id": "C04", "split": "dev", "category": "ambiguity", "status": "APPROVED",
     "question": "does the recipient agency record the purpose of sharing", "expected": [], "expected_outcome": "ESCALATE"},
]


def cite_deny(system, user):
    cid = None
    for m in re.finditer(r'<passage id="([A-Z0-9]+-[0-9a-f]{10})"[^>]*>\n(.*?)\n</passage>', user, re.S):
        if QUOTE in m.group(2):
            cid = m.group(1)
    return reply("DENY", [{"chunk_id": cid, "quote": QUOTE}],
                 applicability=[{"doc_id": "S01", "applies": "yes", "chunk_id": cid, "quote": "An agency that shares benefit records with another agency"}])


REFUSED = ModelResponse(text="", model_id="scripted-test-model", stop_reason="refusal", rejection={"reason": "refused"})


def paired_rows(kb, tmp_path):
    from gapcourt.score import score_runs

    lp = write_labels(tmp_path, PAIRED_LABELS)
    # C01 correct answer; C02 model failure; C03 decisive answer where the label says GAP; C04 escalation (correct)
    make_case_run(kb, lp, "dev", tmp_path / "runs" / "dev1", [cite_deny, REFUSED, cite_deny, reply("ESCALATE")])
    res = score_runs(kb, lp, tmp_path / "runs", "dev", allow_test_double=True)
    return res


def test_paired_table_counts_correct_unsupported_decisive_escalations_and_model_failures(kb, tmp_path):
    p = paired_rows(kb, tmp_path)["rows"]["paired"]
    s = p["system"]
    assert (s["n"], s["correct"], s["decisive"], s["unsupported_decisive"], s["escalated"], s["model_failures"]) == (4, 2, 2, 1, 1, 1)
    assert p["case_ids"] == ["C01", "C02", "C03", "C04"]


def test_paired_baseline_is_scored_on_the_same_cases_and_has_no_model_failures(kb, tmp_path):
    p = paired_rows(kb, tmp_path)["rows"]["paired"]
    b = p["baseline"]
    assert b["n"] == p["system"]["n"] == 4 and b["model_failures"] == 0 and b["escalated"] == 0
    assert b["decisive"] + b["abstained"] == 4  # every case lands in one of the baseline's two outcomes
    assert 0 <= b["unsupported_decisive"] <= b["decisive"]


def test_results_markdown_shows_the_two_sides_in_one_table(kb, tmp_path):
    from gapcourt.score import write_results

    write_results(paired_rows(kb, tmp_path), tmp_path / "out")
    md = (tmp_path / "out" / "results.md").read_text(encoding="utf-8")
    sec = md.split("Paired comparison")[1].split("\n## ")[0]
    for word in ("outcome agreement", "unsupported decisive", "escalations", "model failures"):
        assert word in sec
    assert "correct recommendations" not in sec
    assert "| full system | retrieval plus abstain |" in sec


# ---- 2. applicability gate ----------------------------------------------------

from gapcourt.gate import apply_gate  # noqa: E402
from gapcourt.retrieve import HybridRetriever  # noqa: E402

DENY_QUOTE = "must not use the records for a different purpose"
WHO_QUOTE = "An agency that shares benefit records with another agency"
APPROVED = {"approved": {"S01"}, "sha256": "test"}


def art3(kb):
    hits = [h for h in HybridRetriever(kb, ["kb_uae"], k=5).search("agency shares benefit records with another agency purpose")
            if h.chunk.locator == "Article 3"]
    return hits


def decide(kb, hits, applicability, record):
    kb.applicability = record
    raw = {"decision": "DENY", "citations": [{"chunk_id": hits[0].chunk.chunk_id, "quote": DENY_QUOTE}],
           "applicability": applicability}
    return apply_gate(raw, hits, ["kb_uae"], kb)


def entry(hits, applies, quote=WHO_QUOTE):
    return {"doc_id": "S01", "applies": applies, "chunk_id": hits[0].chunk.chunk_id, "quote": quote}


def test_decisive_recommendation_kept_with_verified_quote_approved_record_and_recorded_yes(kb):
    hits = art3(kb)
    g = decide(kb, hits, [entry(hits, "yes")], APPROVED)
    assert g.decision == "DENY" and g.applicability[0]["doc_id"] == "S01" and g.applicability[0]["quote"] == WHO_QUOTE


def test_decisive_recommendation_escalates_when_the_instrument_has_no_approved_record(kb):
    hits = art3(kb)
    g = decide(kb, hits, [entry(hits, "yes")], {"approved": set(), "sha256": "test"})
    assert g.decision == "ESCALATE" and any("S01" in n and "approved" in n for n in g.notes)


def test_decisive_recommendation_escalates_when_no_applicability_is_recorded(kb):
    hits = art3(kb)
    g = decide(kb, hits, [], APPROVED)
    assert g.decision == "ESCALATE" and any("no recorded applicability" in n for n in g.notes)


def test_decisive_recommendation_escalates_when_the_quote_of_the_yes_entry_is_not_in_the_passage(kb):
    hits = art3(kb)
    g = decide(kb, hits, [entry(hits, "yes", "A private company that sells loyalty cards")], APPROVED)
    assert g.decision == "ESCALATE"


@pytest.mark.parametrize("applies", ["no", "unclear"])
def test_authentic_quotation_for_an_actor_the_model_marks_outside_or_unclear_escalates(kb, applies):
    hits = art3(kb)
    g = decide(kb, hits, [entry(hits, "yes"), entry(hits, applies)], APPROVED)
    assert g.decision == "ESCALATE" and any(applies in n for n in g.notes)


def test_gate_is_unchanged_when_no_applicability_regime_is_loaded(kb):
    hits = art3(kb)
    g = decide(kb, hits, [], None)
    assert g.decision == "DENY"


def test_only_records_marked_approved_by_a_reviewer_count_as_approved(tmp_path):
    from gapcourt.gate import load_applicability

    p = tmp_path / "A.json"
    p.write_text(json.dumps([
        {"doc_id": "U06", "covers": "Art. 3 scope", "approval": "approved by Test Reviewer on 2026-11-06"},
        {"doc_id": "U11", "covers": "Art. 2", "approval": "agent-drafted 2026-10-27; not yet approved by Walter (W5)"},
        {"doc_id": "U12", "covers": "", "approval": "approved by Test Reviewer on 2026-11-06"}]), encoding="utf-8")
    a = load_applicability(p)
    assert a["approved"] == {"U06"} and len(a["sha256"]) == 64
    assert load_applicability(tmp_path / "missing.json")["approved"] == set()


def test_approve_applicability_marks_only_the_named_instruments(tmp_path):
    from gapcourt.gate import approve_applicability

    p = tmp_path / "A.json"
    rows = [{"doc_id": d, "in_kb": True, "covers": "x", "approval": "agent-drafted 2026-10-27; not yet approved by Walter (W5)"} for d in ("U06", "U11")]
    p.write_text(json.dumps(rows), encoding="utf-8")
    approve_applicability(p, ["U06"], "Test Reviewer")
    got = {r["doc_id"]: r["approval"] for r in json.loads(p.read_text(encoding="utf-8"))}
    assert got["U06"].startswith("approved by Test Reviewer on ") and got["U11"].startswith("agent-drafted")
    with pytest.raises(ValueError, match="U99"):
        approve_applicability(p, ["U99"], "Test Reviewer")


def test_role_agent_records_the_applicability_file_hash_in_its_configuration_hash(kb):
    from gapcourt.llm import ScriptedModel
    from gapcourt.roles import RoleAgent, RoleSpec

    spec = RoleSpec(name="adviser", page_role="adviser", collections=["kb_uae"], focus="", instructions="Rule.")
    def cfg_hash():
        a = RoleAgent(spec, kb, ScriptedModel([reply()])).assess(1, ["m"], [])
        return a.config_sha256
    kb.applicability = None
    off = cfg_hash()
    kb.applicability = APPROVED
    assert cfg_hash() != off


def test_prompt_contract_asks_for_a_yes_entry_with_words_naming_the_actor():
    from gapcourt.roles import CONTRACT

    assert 'applies "yes"' in CONTRACT and "ESCALATE" in CONTRACT.split('applies "yes"')[1][:400]


def test_command_line_loads_the_approved_applicability_and_the_freeze_binds_its_hash(kb, tmp_path, monkeypatch):
    from gapcourt.cli import _load_kb
    from gapcourt.gate import load_applicability
    from test_score_addstep import LABELS, freeze_for, scenario_file

    ap = tmp_path / "A.json"  # the real records stay unapproved in the repository; a freeze needs at least one approved
    ap.write_text(json.dumps([{"doc_id": "S01", "covers": "x", "approval": "approved by Test Reviewer on 2026-11-06"}]), encoding="utf-8")
    monkeypatch.setattr("gapcourt.gate.APPLICABILITY_FILE", ap)
    kb.save(tmp_path / "kb")
    loaded = _load_kb(tmp_path / "kb")
    assert loaded.applicability == load_applicability() and loaded.applicability["sha256"] != "missing"
    lp = write_labels(tmp_path, LABELS)
    fr = freeze_for(tmp_path, loaded, lp, scenario_file(tmp_path))
    assert fr["applicability_sha256"] == loaded.applicability["sha256"]


# ---- 3. one meaningful revision ------------------------------------------------

from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def test_revision_record_holds_previous_recommendation_new_fact_revised_recommendation_and_reason(kb, tmp_path):
    from test_score_addstep import make_scenario_run, scenario_file

    sp = scenario_file(tmp_path)
    def deny_with_reason(system, user):
        d = json.loads(cite_deny(system, user))
        d["revision_reason"] = "the recipient now wants a different purpose"
        return json.dumps(d)
    s = make_scenario_run(kb, sp, tmp_path / "runs" / "s1", [reply("ESCALATE"), deny_with_reason])
    rev = s["revisions"][0]
    assert (rev["from"], rev["to"], rev["reason"]) == ("ESCALATE", "DENY", "the recipient now wants a different purpose")
    assert rev["new_fact"] == "Step 2. The recipient agency wants the records for a different purpose."
    assert rev["prior_record_hash"]


def test_development_demonstration_scenario_changes_the_candidate_ruling_on_a_new_fact_and_is_marked():
    d = json.loads((ROOT / "scenarios" / "S-04.json").read_text(encoding="utf-8"))
    assert d["scenario_id"] == "S-04" and d["synthetic"] is True and d["development_demonstration"] is True
    s1, s2 = d["steps"]
    assert s1["hidden"]["expected_candidate"] != s2["hidden"]["expected_candidate"]
    assert s2["hidden"]["contradicts_prior"] is True and s1["hidden"]["contradicts_prior"] is False
    assert s1["addressees"] == s2["addressees"] == ["adviser"]
    assert "written consent" in s2["text"] and "written consent" not in s1["text"]  # the new fact arrives in step 2


@pytest.mark.skipif(not (ROOT / "corpus_local" / "uae" / "U12.md").exists(), reason="link-only corpus not present")
def test_demonstration_basis_quotations_are_verbatim_in_the_stored_passages():
    from gapcourt.corpus import load_document
    from gapcourt.verify import check_quotation

    d = json.loads((ROOT / "scenarios" / "S-04.json").read_text(encoding="utf-8"))
    for b in d["basis"]:
        doc = load_document(ROOT / "corpus_local" / "uae" / f"{b['doc_id']}.md")
        text = next(s.text for s in doc.sections if s.locator == b["locator"])
        assert check_quotation(b["quote"], text).verified, b


def test_score_marks_the_development_demonstration_row_so_it_is_reported_separately(kb, tmp_path):
    from gapcourt.score import score_runs, write_results
    from test_score_addstep import LABELS, make_scenario_run, scenario_file

    sp = scenario_file(tmp_path)
    d = json.loads(sp.read_text(encoding="utf-8"))
    d["development_demonstration"] = True
    sp.write_text(json.dumps(d), encoding="utf-8")
    make_scenario_run(kb, sp, tmp_path / "runs" / "demo" / "s1", [reply("ESCALATE"), cite_deny])
    lp = write_labels(tmp_path, LABELS)
    res = score_runs(kb, lp, tmp_path / "runs", "dev", scenarios_dir=sp.parent, allow_test_double=True)
    assert res["rows"]["scenario_steps"][0]["development_demonstration"] is True
    write_results(res, tmp_path / "out")
    assert "development demonstration" in (tmp_path / "out" / "results.md").read_text(encoding="utf-8")


def test_report_counts_demonstration_runs_apart_from_the_live_scenario_runs(tmp_path):
    from gapcourt.report import collect_facts, fill_template
    from test_report_fill import make_kb, make_run

    runs = tmp_path / "runs"
    make_run(runs, "S-01/r1", revisions=2)
    make_run(runs, "demo/S-04/r1", revisions=1)
    facts = collect_facts(runs_root=runs, kb_dir=make_kb(tmp_path))
    assert fill_template("{{fact:live.revisions}}|{{fact:demo.revisions}}|{{fact:total.revisions}}", facts) == \
        "2 [measured]|1 [measured]|3 [measured]"


def test_dev_session_of_the_live_script_also_runs_the_demonstration_scenario():
    text = (ROOT / "scripts" / "walter_live_runs.ps1").read_text(encoding="utf-8")
    dev = text[text.index("function Run-Dev"):text.index("function Run-Heldout")]
    assert "scenarios/S-04.json" in dev and "$runsRoot/demo/S-04" in dev


# ---- 4. pre-freeze review fixes -------------------------------------------------

from test_score_addstep import LABELS, freeze_for, make_case_run as _case_run, scenario_file  # noqa: E402

from gapcourt.eval import EvalError  # noqa: E402


def frozen_with_approvals(kb, tmp_path):
    kb.applicability = APPROVED
    lp = write_labels(tmp_path, LABELS)
    fr = freeze_for(tmp_path, kb, lp, scenario_file(tmp_path))
    fp = tmp_path / "FREEZE.json"
    fp.write_text(json.dumps(fr), encoding="utf-8")
    return lp, fr, fp


@pytest.mark.parametrize("runtime", [{"approved": {"S01"}, "sha256": "edited"}, None])
def test_held_out_calls_are_refused_when_the_approvals_hash_differs_from_or_is_missing_against_the_freeze(kb, tmp_path, runtime):
    lp, fr, _ = frozen_with_approvals(kb, tmp_path)
    kb.applicability = runtime
    with pytest.raises(EvalError, match="applicability"):
        _case_run(kb, lp, "heldout", tmp_path / "runs" / "h1", [reply("ESCALATE")] * 2, freeze=fr)
    assert not (tmp_path / "runs" / "h1" / "log.jsonl").exists()


def test_held_out_calls_proceed_when_the_approvals_hash_matches_the_freeze(kb, tmp_path):
    lp, fr, _ = frozen_with_approvals(kb, tmp_path)
    s = _case_run(kb, lp, "heldout", tmp_path / "runs" / "h1", [reply("ESCALATE")] * 2, freeze=fr)
    assert s["applicability_sha256"] == fr["applicability_sha256"]


def test_scoring_excludes_a_run_made_under_other_approvals_than_the_frozen_ones(kb, tmp_path, monkeypatch):
    from gapcourt.score import score_runs

    lp, fr, fp = frozen_with_approvals(kb, tmp_path)
    runs = tmp_path / "runs"
    monkeypatch.setattr("gapcourt.cases.check_freeze_for_heldout", lambda *a, **k: None)  # a run that got past the pre-call check
    kb.applicability = {"approved": {"S01"}, "sha256": "edited"}
    _case_run(kb, lp, "heldout", runs / "h1", [reply("ESCALATE")] * 2, freeze=fr)
    res = score_runs(kb, lp, runs, "heldout", freeze_path=fp, scenarios_dir=tmp_path / "scenarios", allow_test_double=True)
    assert res["rows"]["outcome_agreement"]["system"]["n"] == 0
    assert any("applicability" in x["reason"] for x in res["excluded_runs"])


def test_freeze_is_refused_when_no_applicability_record_is_approved(kb, tmp_path):
    kb.applicability = {"approved": set(), "sha256": "x"}
    lp = write_labels(tmp_path, LABELS)
    with pytest.raises(EvalError, match="applicability"):
        freeze_for(tmp_path, kb, lp, scenario_file(tmp_path))


def kb_two_instruments(fixtures_dir, tmp_path):
    import shutil

    from gapcourt.chunking import ChunkParams
    from gapcourt.embed import HashingEmbedder
    from gapcourt.index import build_kb

    root = tmp_path / "corpus2"
    (root / "uae").mkdir(parents=True)
    shutil.copy(fixtures_dir / "S01.md", root / "uae" / "S01.md")
    t = (fixtures_dir / "S01.md").read_text(encoding="utf-8").replace("doc_id: S01", "doc_id: S09").replace("SYNTH-LAW-1", "SYNTH-LAW-9")
    (root / "uae" / "S09.md").write_text(t, encoding="utf-8")
    return build_kb([root], HashingEmbedder(dim=128), ChunkParams(max_chars=300))


def two_doc_decision(kb2, applicability, approved):
    hits = [h for h in HybridRetriever(kb2, ["kb_uae"], k=10).search("agency shares benefit records with another agency purpose")
            if h.chunk.locator == "Article 3"]
    by_doc = {h.chunk.doc_id: h for h in hits}
    assert set(by_doc) == {"S01", "S09"}
    kb2.applicability = {"approved": approved, "sha256": "t"}
    raw = {"decision": "DENY", "applicability": applicability(by_doc),
           "citations": [{"chunk_id": by_doc[d].chunk.chunk_id, "quote": DENY_QUOTE} for d in ("S01", "S09")]}
    return apply_gate(raw, hits, ["kb_uae"], kb2), by_doc


def yes_for(h, doc_id=None):
    return {"doc_id": doc_id or h.chunk.doc_id, "applies": "yes", "chunk_id": h.chunk.chunk_id, "quote": WHO_QUOTE}


def test_one_passing_instrument_does_not_keep_a_ruling_that_also_cites_an_unapproved_instrument(fixtures_dir, tmp_path):
    kb2 = kb_two_instruments(fixtures_dir, tmp_path)
    g, _ = two_doc_decision(kb2, lambda b: [yes_for(b["S01"]), yes_for(b["S09"])], {"S01"})
    assert g.decision == "ESCALATE" and any("S09" in n and "approved" in n for n in g.notes)


def test_one_passing_instrument_does_not_keep_a_ruling_when_another_cited_instrument_is_marked_unclear(fixtures_dir, tmp_path):
    kb2 = kb_two_instruments(fixtures_dir, tmp_path)
    def entries(b):
        return [yes_for(b["S01"]), yes_for(b["S09"]), {**yes_for(b["S09"]), "applies": "unclear"}]
    g, _ = two_doc_decision(kb2, entries, {"S01", "S09"})
    assert g.decision == "ESCALATE" and any("S09" in n and "unclear" in n for n in g.notes)


def test_ruling_is_kept_when_every_cited_instrument_has_an_approved_record_and_a_recorded_yes(fixtures_dir, tmp_path):
    kb2 = kb_two_instruments(fixtures_dir, tmp_path)
    g, _ = two_doc_decision(kb2, lambda b: [yes_for(b["S01"]), yes_for(b["S09"])], {"S01", "S09"})
    assert g.decision == "DENY" and {a["doc_id"] for a in g.applicability} == {"S01", "S09"}


def test_applicability_entry_naming_another_document_than_its_chunk_is_rejected(kb):
    hits = art3(kb)
    g = decide(kb, hits, [entry(hits, "yes") | {"doc_id": "S99"}], APPROVED)
    assert g.decision == "ESCALATE" and any(d["reason"] == "doc_id_mismatch" for d in g.dropped)


def test_contrary_entry_naming_another_document_than_its_chunk_is_rejected_not_attributed_to_the_chunk(kb):
    hits = art3(kb)
    g = decide(kb, hits, [entry(hits, "yes"), entry(hits, "unclear") | {"doc_id": "S99"}], APPROVED)
    assert any(d["reason"] == "doc_id_mismatch" for d in g.dropped)
    assert g.decision == "DENY"


def test_outcome_keeps_an_escalation_as_escalate_even_with_a_scope_finding():
    from gapcourt.cases import outcome_of

    assert outcome_of("ESCALATE", [{"doc_id": "U12"}]) == "ESCALATE"


def test_outcome_counts_a_decisive_ruling_as_answer_even_with_a_scope_finding_for_another_instrument():
    from gapcourt.cases import outcome_of

    assert outcome_of("DENY", [{"doc_id": "U01"}]) == "ANSWER" and outcome_of("ALLOW", [{"doc_id": "U01"}]) == "ANSWER"


def test_outcome_is_scope_finding_when_a_gap_or_abstention_carries_a_verified_scope_finding():
    from gapcourt.cases import outcome_of

    assert outcome_of("GAP", [{"doc_id": "U01"}]) == "SCOPE_FINDING" and outcome_of("GAP", []) == "GAP"


# ---- 5. no bypass through a missing approvals hash (re-review) -----------------------------

def refrozen(fr, **changes):
    import hashlib

    from gapcourt.audit import canonical

    body = {k: v for k, v in fr.items() if k != "freeze_sha256"} | changes
    return {**body, "freeze_sha256": hashlib.sha256(canonical(body)).hexdigest()}


@pytest.mark.parametrize("runtime", [None, {"approved": set(), "sha256": "x"}, {"approved": {"S01"}, "sha256": "missing"}, {"approved": {"S01"}, "sha256": ""}])
def test_freeze_is_refused_when_applicability_is_unset_empty_or_without_a_hash(kb, tmp_path, runtime):
    kb.applicability = runtime
    lp = write_labels(tmp_path, LABELS)
    with pytest.raises(EvalError, match="applicability"):
        freeze_for(tmp_path, kb, lp, scenario_file(tmp_path))


def test_held_out_calls_are_refused_when_neither_the_freeze_nor_the_run_has_an_approvals_hash(kb, tmp_path):
    lp, fr, _ = frozen_with_approvals(kb, tmp_path)
    kb.applicability = None
    with pytest.raises(EvalError, match="applicability"):
        _case_run(kb, lp, "heldout", tmp_path / "runs" / "h1", [reply("ESCALATE")] * 2, freeze=refrozen(fr, applicability_sha256=None))


def test_scoring_excludes_a_run_when_neither_the_freeze_nor_the_run_has_an_approvals_hash(kb, tmp_path, monkeypatch):
    from gapcourt.score import score_runs

    lp, fr, _ = frozen_with_approvals(kb, tmp_path)
    fr2 = refrozen(fr, applicability_sha256=None)
    fp = tmp_path / "FREEZE2.json"
    fp.write_text(json.dumps(fr2), encoding="utf-8")
    monkeypatch.setattr("gapcourt.cases.check_freeze_for_heldout", lambda *a, **k: None)
    kb.applicability = None
    _case_run(kb, lp, "heldout", tmp_path / "runs" / "h1", [reply("ESCALATE")] * 2, freeze=fr2)
    res = score_runs(kb, lp, tmp_path / "runs", "heldout", freeze_path=fp, scenarios_dir=tmp_path / "scenarios", allow_test_double=True)
    assert res["rows"]["outcome_agreement"]["system"]["n"] == 0
    assert any("applicability" in x["reason"] for x in res["excluded_runs"])
