"""The report fills itself from run output: every number carries a tag (measured, offline, pending-live)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from gapcourt.report import ReportError, collect_facts, fill_template, lint_numerals


def write_json(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj), encoding="utf-8")


def make_kb(tmp_path):
    kb = tmp_path / "kb"
    write_json(kb / "KB_MANIFEST.json", {
        "kb_version": "kb-test123456", "chunk_count": 12, "embedder": {"name": "hashing-lexical-v1", "dim": 8},
        "documents": [{"doc_id": "U1", "collection": "kb_uae"}, {"doc_id": "U2", "collection": "kb_uae"},
                      {"doc_id": "G1", "collection": "kb_global"}, {"doc_id": "F1", "collection": "kb_framework"}]})
    return kb


def make_run(runs, rel, *, test_double=False, kind="scenario", calls=2, cost=0.01, rejections=None, kept=1, dropped=1,
             revisions=1, stop="end_turn"):
    d = runs / rel
    cost_obj = {"test_double": test_double, "calls": calls, "input_tokens": 1000 * calls, "output_tokens": 200 * calls,
                "cost_usd": cost}
    summary = {"kind": kind, "scenario_id": "S-03", "kb_version": "kb-test123456", "code_version": "abc1234",
               "started_at": "2026-11-05T10:00:00Z", "model_rejections": rejections or [], "revisions": [{"step_id": "x"}] * revisions,
               "gaps": [], "rulings": [{"step_id": "s1", "decision": "ESCALATE"}], "cost": cost_obj}
    if kind == "cases":
        summary["test_double"] = test_double
    write_json(d / "cost.json", cost_obj)
    from gapcourt.audit import AuditLog, bind_summary

    log = AuditLog(path=d / "log.jsonl", code_version="abc1234")
    log.append_step(role="adviser", input={"step_id": "s1"}, query="q", scope={}, kb_version="kb-test123456", passages=[],
                    rule_applied="none", rationale="r", decision="ESCALATE", model_id="claude-haiku-5-5",
                    prompt_sha256="0" * 64, config_sha256="0" * 64,
                    model_calls=[{"stop_reason": stop, "usage": {"input_tokens": 1000, "output_tokens": 200}, "rejection": None}] * calls,
                    gate={"kept": [1] * kept, "dropped": [1] * dropped, "notes": [], "scope_findings": []})
    digest = bind_summary(log, summary)
    summary["digest"] = digest
    write_json(d / "summary.json", summary)
    write_json(d / "digest.json", digest)


def test_offline_facts_are_tagged_offline_and_come_from_the_kb_and_offline_file(tmp_path):
    kb = make_kb(tmp_path)
    write_json(tmp_path / "offline.json", {"tests_passed": 195, "tests_skipped": 1, "code_version": "abc1234", "date": "2026-11-01"})
    facts = collect_facts(runs_root=tmp_path / "runs", kb_dir=kb, offline_facts=tmp_path / "offline.json")
    out = fill_template("KB {{fact:kb.version}} has {{fact:kb.documents}} documents; tests {{fact:tests.passed}}.", facts)
    assert out == "KB kb-test123456 [offline] has 4 [offline] documents; tests 195 [offline]."


def test_without_live_runs_every_measured_number_is_pending_live(tmp_path):
    facts = collect_facts(runs_root=tmp_path / "runs", kb_dir=make_kb(tmp_path))
    out = fill_template("Calls {{fact:smoke.calls}}; probe model {{fact:probe.model}}; cost {{fact:total.cost_usd}}.", facts)
    assert out.count("[pending-live") == 3 and "measured" not in out
    assert "smoke session" in out and "probe session" in out


def test_test_double_runs_are_ignored_and_listed_as_ignored(tmp_path):
    runs = tmp_path / "runs"
    make_run(runs, "smoke/S-03-a", test_double=True)
    facts = collect_facts(runs_root=runs, kb_dir=make_kb(tmp_path))
    assert "[pending-live" in fill_template("{{fact:smoke.calls}}", facts)
    assert "TEST DOUBLE" in fill_template("{{block:evidence}}", facts) and "smoke/S-03-a" in fill_template("{{block:evidence}}", facts)


def test_live_smoke_runs_are_measured_with_run_ids_and_sums(tmp_path):
    runs = tmp_path / "runs"
    make_run(runs, "smoke/S-03-a", calls=2, cost=0.01, kept=3, dropped=1, revisions=0)
    make_run(runs, "smoke/dev3-a", kind="cases", calls=3, cost=0.02, kept=2, dropped=0, revisions=0)
    make_run(runs, "smoke/S-03-test", test_double=True, calls=99, cost=9.0)
    facts = collect_facts(runs_root=runs, kb_dir=make_kb(tmp_path))
    assert fill_template("{{fact:smoke.calls}}", facts) == "5 [measured]"
    assert fill_template("{{fact:smoke.cost_usd}}", facts) == "USD 0.0300 [measured]"
    assert fill_template("{{fact:smoke.statements_kept}}", facts) == "5 [measured]"
    assert fill_template("{{fact:smoke.statements_dropped}}", facts) == "1 [measured]"
    ev = fill_template("{{block:evidence}}", facts)
    assert "smoke/S-03-a" in ev and "smoke/dev3-a" in ev and "S-03-test" in ev and "measured" in ev


def test_rejections_are_counted_by_reason(tmp_path):
    runs = tmp_path / "runs"
    make_run(runs, "smoke/S-03-a", rejections=[{"reason": "truncated"}, {"reason": "truncated"}, {"reason": "refused"}])
    facts = collect_facts(runs_root=runs, kb_dir=make_kb(tmp_path))
    assert fill_template("{{fact:smoke.rejections}}", facts) == "3 [measured]"
    assert fill_template("{{fact:smoke.rejection_reasons}}", facts) == "refused 1, truncated 2 [measured]"


def test_probe_is_measured_from_the_probe_file(tmp_path):
    runs = tmp_path / "runs"
    write_json(runs / "_probe/20261101T151834Z/probe.json", {
        "model_id": "claude-haiku-5-5", "accepted": "as configured", "test_double": False,
        "attempts": [{"call": {"stop_reason": "end_turn", "usage": {"input_tokens": 222, "output_tokens": 9}, "cost_usd": 2.67e-05,
                               "request_id": "req_x"}}]})
    facts = collect_facts(runs_root=runs, kb_dir=make_kb(tmp_path))
    assert fill_template("{{fact:probe.model}} / {{fact:probe.accepted}}", facts) == "claude-haiku-5-5 [measured] / as configured [measured]"


def test_total_spent_includes_the_probe_calls_so_it_matches_the_ledger(tmp_path):
    # the first live session: smoke runs cost USD 0.0022, the two probes USD 0.00015, `gapcourt cost` said 0.0024 and the report 0.0022
    runs = tmp_path / "runs"
    make_run(runs, "smoke/S-03-a", calls=2, cost=0.01)
    write_json(runs / "_probe/20261101T151834Z/probe.json", {
        "model_id": "claude-haiku-5-5", "accepted": "as configured", "test_double": False,
        "attempts": [{"call": {"stop_reason": "end_turn", "usage": {"input_tokens": 222, "output_tokens": 9}, "cost_usd": 0.0005}}]})
    facts = collect_facts(runs_root=runs, kb_dir=make_kb(tmp_path))
    out = fill_template("{{fact:smoke.calls}} / {{fact:smoke.cost_usd}} / {{fact:total.calls}} / {{fact:total.cost_usd}}", facts)
    assert out == "2 [measured] / USD 0.0100 [measured] / 3 [measured] / USD 0.0105 [measured]"


def test_unknown_fact_key_is_an_error_not_a_blank(tmp_path):
    facts = collect_facts(runs_root=tmp_path / "runs", kb_dir=make_kb(tmp_path))
    with pytest.raises(ReportError, match="no.such"):
        fill_template("{{fact:no.such}}", facts)


def test_results_block_is_pending_until_a_score_output_exists_then_embedded_with_its_run_ids(tmp_path):
    kb = make_kb(tmp_path)
    facts = collect_facts(runs_root=tmp_path / "runs", kb_dir=kb)
    assert "[pending-live" in fill_template("{{block:results}}", facts)
    res = tmp_path / "results"
    res.mkdir()
    (res / "results.md").write_text("| row | K of N |\n| 1 | 7 of 10 |\n", encoding="utf-8")
    write_json(res / "results.json", {"stamp": {"run_ids": ["heldout/20261106T1"], "test_double": False, "scored_at": "2026-11-06T12:00:00Z"}})
    facts = collect_facts(runs_root=tmp_path / "runs", kb_dir=kb, results_dir=res)
    out = fill_template("{{block:results}}", facts)
    assert "7 of 10" in out and "[measured: gapcourt score" in out and "heldout/20261106T1" in out


def test_lint_flags_untagged_numerals_in_prose_but_not_tagged_facts_or_identifiers():
    tpl = "We ran 12 cases. KB {{fact:kb.version}} has {{fact:kb.documents}} documents. Y.3172 clause 8.1 and S-01, 2026, Article 2(2)(b), U01."
    assert lint_numerals(tpl) == ["12"]


def test_lint_accepts_a_hyphenated_memorandum_number_whole():
    # the identifier pattern used to take "M-25" and leave "21" as an untagged numeral (smoke session, 2026-11-02)
    assert lint_numerals("US OMB M-25-21 has no general notice duty; M-24-10 too.") == []
    assert lint_numerals("US OMB M-25-21 allows 12 cases") == ["12"]


def test_page_estimate_uses_words_and_flags_overrun(tmp_path):
    from gapcourt.report import estimate_pages

    assert estimate_pages("word " * 1000) == pytest.approx(2.0, abs=0.2)
    assert estimate_pages("word " * 3000) > 5.0


def test_results_from_a_test_double_score_are_not_embedded(tmp_path):
    res = tmp_path / "results"
    res.mkdir()
    (res / "results.md").write_text(chr(10).join(["| row | K of N |", "| 1 | 7 of 10 |", ""]), encoding="utf-8")
    write_json(res / "results.json", {"stamp": {"run_ids": ["x"], "test_double": True}})
    facts = collect_facts(runs_root=tmp_path / "runs", kb_dir=make_kb(tmp_path), results_dir=res)
    out = fill_template("{{block:results}}", facts)
    assert "7 of 10" not in out and "[pending-live" in out


def test_run_whose_log_fails_verification_is_not_measured(tmp_path):
    # summaries were aggregated without checking the hash chain
    runs = tmp_path / "runs"
    make_run(runs, "smoke/S-03-a", calls=2, cost=0.01)
    log = runs / "smoke" / "S-03-a" / "log.jsonl"
    log.write_text(log.read_text(encoding="utf-8").replace("ESCALATE", "ALLOW"), encoding="utf-8")
    facts = collect_facts(runs_root=runs, kb_dir=make_kb(tmp_path))
    assert "[pending-live" in fill_template("{{fact:smoke.calls}}", facts)
    ev = fill_template("{{block:evidence}}", facts)
    assert "failed verification" in ev and "[measured]" not in ev


def test_run_without_a_retained_digest_is_not_measured(tmp_path):
    runs = tmp_path / "runs"
    make_run(runs, "smoke/S-03-a")
    (runs / "smoke" / "S-03-a" / "digest.json").unlink()
    facts = collect_facts(runs_root=runs, kb_dir=make_kb(tmp_path))
    assert "[pending-live" in fill_template("{{fact:smoke.calls}}", facts)


def test_results_with_no_counted_runs_are_not_measured(tmp_path):
    res = tmp_path / "results"
    res.mkdir()
    (res / "results.md").write_text("| row | K of N |\n| 1 | 0 of 0 |\n", encoding="utf-8")
    write_json(res / "results.json", {"stamp": {"run_ids": [], "test_double": False, "scored_at": "2026-11-06T12:00:00Z"},
                                      "rows": {"outcome_agreement": {"run_id": None}}})
    facts = collect_facts(runs_root=tmp_path / "runs", kb_dir=make_kb(tmp_path), results_dir=res)
    out = fill_template("{{block:results}}", facts)
    assert "[measured" not in out and "pending-live" in out


# ---- wording fixes after the first dev run (2026-11-02) ---------------------------------------------

ROOT = Path(__file__).resolve().parents[1]


def test_dev_runs_have_their_own_measured_group_apart_from_smoke(tmp_path):
    runs = tmp_path / "runs"
    make_run(runs, "smoke/S-03-a", calls=2, cost=0.01)
    make_run(runs, "dev/20261102T102235Z", kind="cases", calls=10, cost=0.0075, kept=9, dropped=0)
    facts = collect_facts(runs_root=runs, kb_dir=make_kb(tmp_path))
    assert fill_template("{{fact:dev.calls}}", facts) == "10 [measured]"
    assert fill_template("{{fact:dev.statements_kept}}", facts) == "9 [measured]"
    assert fill_template("{{fact:smoke.calls}}", facts) == "2 [measured]"
    assert fill_template("{{fact:total.calls}}", facts) == "12 [measured]"
    assert "[pending-live" in fill_template("{{fact:dev.calls}}", collect_facts(runs_root=tmp_path / "none", kb_dir=make_kb(tmp_path)))


def test_contradicting_steps_run_are_counted_from_the_scenario_files(tmp_path):
    runs = tmp_path / "runs"
    make_run(runs, "smoke/S-03-a", revisions=0)  # logs step s1 of scenario S-03
    scen = tmp_path / "scenarios"
    write_json(scen / "S-03.json", {"scenario_id": "S-03", "steps": [
        {"step_id": "s1", "hidden": {"contradicts_prior": False}}, {"step_id": "s2", "hidden": {"contradicts_prior": True}}]})
    facts = collect_facts(runs_root=runs, kb_dir=make_kb(tmp_path), scenarios_dir=scen)
    assert fill_template("{{fact:total.contradicting_steps}}", facts) == "0 [measured]"  # s2 was never run
    write_json(scen / "S-03.json", {"scenario_id": "S-03", "steps": [{"step_id": "s1", "hidden": {"contradicts_prior": True}}]})
    facts = collect_facts(runs_root=runs, kb_dir=make_kb(tmp_path), scenarios_dir=scen)
    assert fill_template("{{fact:total.contradicting_steps}}", facts) == "1 [measured]"
    assert "[pending-live" in fill_template("{{fact:total.contradicting_steps}}", collect_facts(runs_root=tmp_path / "none", kb_dir=make_kb(tmp_path)))


def test_real_template_does_not_present_untested_revision_behaviour_as_tested(tmp_path):
    tpl = (ROOT / "report" / "TEMPLATE_V3_FILLABLE.md").read_text(encoding="utf-8")
    r4 = [l for l in tpl.splitlines() if l.startswith("| R4 ")][0]
    assert "{{fact:total.revisions}}" in r4 and "{{fact:total.contradicting_steps}}" in r4 and "contradicting" in r4
    assert "{{fact:live.revisions}}" not in tpl
    assert "saved runs counted so far" not in tpl and "saved run folders" in tpl  # the scorer counts fewer than the folders
    assert "{{fact:dev.calls}}" in tpl and "unapproved" in tpl.split("Development session", 1)[1].split("Request-settings", 1)[0]


def test_dossier_and_retrieval_facts_are_offline_and_come_from_their_files(tmp_path):
    write_json(tmp_path / "dossier.json", {"counts": {"claims": 22, "claims_verified": 22, "findings": 11, "findings_supported": 11}})
    write_json(tmp_path / "retr.json", {"kb_version": "kb-test123456", "runs": {
        "off": {"probes": {"hybrid": {"hit": 2, "n": 15}}, "dev": {"hybrid": {"expected_found": 3, "scored": 7}}},
        "on": {"probes": {"hybrid": {"hit": 15, "n": 15}}, "dev": {"hybrid": {"expected_found": 3, "scored": 7}}}}})
    facts = collect_facts(runs_root=tmp_path / "runs", kb_dir=make_kb(tmp_path), dossier_evidence=tmp_path / "dossier.json",
                          retrieval_results=tmp_path / "retr.json")
    out = fill_template("{{fact:dossier.claims_verified}} of {{fact:dossier.claims}}; {{fact:retrieval.probes_before}} to "
                        "{{fact:retrieval.probes_after}} of {{fact:retrieval.probes_n}}; dev {{fact:retrieval.dev_before}} and "
                        "{{fact:retrieval.dev_after}} of {{fact:retrieval.dev_scored}}; findings {{fact:dossier.findings_supported}} "
                        "of {{fact:dossier.findings}}", facts)
    assert out == ("22 [offline] of 22 [offline]; 2 [offline] to 15 [offline] of 15 [offline]; dev 3 [offline] and 3 [offline] of "
                   "7 [offline]; findings 11 [offline] of 11 [offline]")


def test_dossier_and_retrieval_facts_are_pending_not_blank_when_their_files_are_missing(tmp_path):
    facts = collect_facts(runs_root=tmp_path / "runs", kb_dir=make_kb(tmp_path))
    out = fill_template("{{fact:dossier.claims_verified}} {{fact:retrieval.probes_after}}", facts)
    assert out.count("[pending-offline") == 2


def test_real_template_carries_the_local_policy_requirements_table_and_the_dossier_facts():
    tpl = (ROOT / "report" / "TEMPLATE_V3_FILLABLE.md").read_text(encoding="utf-8")
    assert "Requirements mapping to local policies" in tpl
    assert "{{fact:dossier.claims_verified}}" in tpl and "{{fact:retrieval.probes_after}}" in tpl
    table = tpl.split("Requirements mapping to local policies", 1)[1].split("\n\n", 2)[1]
    assert table.startswith("| Requirement") and table.count("\n| ") >= 6


def test_lint_accepts_a_principle_number_as_an_identifier():
    assert lint_numerals("UAE Smart Data Framework Part 1, Principle 3 (non-binding)") == []
