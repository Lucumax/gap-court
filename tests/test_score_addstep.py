"""E1 score command (saved runs only), case runs, freeze rules, and E2 judge add-step.

All runs here use the scripted test double or TestDoubleAnthropicClient. Nothing is model output; the
scoring is exercised with allow_test_double=True and the results carry the TEST DOUBLE stamp.
"""
import json
import re
import shutil
from pathlib import Path

import pytest

from gapcourt.chunking import ChunkParams
from gapcourt.embed import HashingEmbedder
from gapcourt.eval import EvalError, labels_sha256
from gapcourt.index import build_kb
from gapcourt.llm import ScriptedModel

ROOT = Path(__file__).resolve().parents[1]
QUOTE = "must not use the records for a different purpose"


@pytest.fixture
def kb(fixtures_dir, tmp_path):
    root = tmp_path / "corpus"
    (root / "uae").mkdir(parents=True)
    shutil.copy(fixtures_dir / "S01.md", root / "uae" / "S01.md")
    k = build_kb([root], HashingEmbedder(dim=128), ChunkParams(max_chars=300))
    k.applicability = {"approved": {"S01"}, "sha256": "test-approvals"}  # test fixture, never the real corpus file
    return k


WHO_QUOTE = "An agency that shares benefit records with another agency"


def reply(decision="ESCALATE", citations=None, revision_reason=None, gap=None, applicability=None):
    return json.dumps({"decision": decision, "rule_applied": "r", "rationale": "x", "citations": citations or [],
                       "applicability": applicability or [], "gap": gap, "revision_reason": revision_reason})


def cite_deny(system, user):
    cid = None
    for m in re.finditer(r'<passage id="([A-Z0-9]+-[0-9a-f]{10})"[^>]*>\n(.*?)\n</passage>', user, re.S):
        if QUOTE in m.group(2):
            cid = m.group(1)
    return reply("DENY", [{"chunk_id": cid, "quote": QUOTE}],
                 applicability=[{"doc_id": "S01", "applies": "yes", "chunk_id": cid, "quote": WHO_QUOTE}])


LABELS = [
    {"case_id": "C01", "split": "dev", "category": "answerable", "status": "DRAFT_UNAPPROVED",
     "question": "may the recipient agency use the shared records for a different purpose",
     "expected": [{"doc_id": "S01", "locator": "Article 3"}], "expected_outcome": "ANSWER"},
    {"case_id": "C02", "split": "dev", "category": "missing_document", "status": "DRAFT_UNAPPROVED",
     "question": "submarine cable landing licence fees", "expected": [], "expected_outcome": "ABSTAIN"},
    {"case_id": "H01", "split": "heldout", "category": "answerable", "status": "APPROVED",
     "question": "may the recipient agency use the shared records for a different purpose",
     "expected": [{"doc_id": "S01", "locator": "Article 3"}], "expected_outcome": "ANSWER"},
    {"case_id": "H02", "split": "heldout", "category": "candidate_gap", "status": "APPROVED",
     "question": "must a resident be told an automated tool assessed them", "expected": [], "expected_outcome": "GAP"},
]


def write_labels(tmp_path, rows=LABELS, name="labels.jsonl"):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return p


def scenario_file(tmp_path):
    d = {"scenario_id": "S-T", "title": "synthetic", "synthetic": True, "steps": [
        {"step_id": "T-1", "addressees": ["adviser"], "text": "Step 1. A service asks whether it may use records.",
         "hidden": {"contradicts_prior": False, "expected_candidate": "ESCALATE"}},
        {"step_id": "T-2", "addressees": ["adviser"],
         "text": "Step 2. The recipient agency wants the records for a different purpose.",
         "hidden": {"contradicts_prior": True, "expected_candidate": "DENY"}}]}
    p = tmp_path / "scenarios" / "S-T.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d), encoding="utf-8")
    return p


def make_case_run(kb, labels_path, split, out, script, freeze=None):
    from gapcourt.cases import run_cases
    from gapcourt.eval import load_labels

    return run_cases(kb, load_labels(labels_path), split, ScriptedModel(script), out, real=False,
                     labels_sha=labels_sha256(labels_path), freeze=freeze, code_version="test")


def make_scenario_run(kb, scen_path, out, script, freeze_sha=None):
    from gapcourt.journey import JOURNEY_ROLES, load_scenario, run_and_save

    roles = [r for r in JOURNEY_ROLES if r.name == "adviser"]
    m = ScriptedModel(script)
    sha = __import__("hashlib").sha256(Path(scen_path).read_bytes()).hexdigest()
    return run_and_save(kb, load_scenario(scen_path), {"adviser": m}, out, real=False, code_version="test",
                        roles=roles, extra={"freeze_sha256": freeze_sha, "scenario_file_sha256": sha})


def freeze_for(tmp_path, kb, labels_path, scen_path, exposed=()):
    from gapcourt.score import make_freeze

    return make_freeze(labels_path, kb, scenario_paths=[scen_path], model_config={"model_id": "scripted-test-model"},
                       reviewer="Test Reviewer", exposed_case_ids=list(exposed), attest_independent=True,
                       code_version="test")


# ---- case runs and freeze ---------------------------------------------------

def test_case_run_saves_outcomes_baseline_and_a_verifiable_log(kb, tmp_path):
    from gapcourt.audit import AuditLog, verify_records

    lp = write_labels(tmp_path)
    s = make_case_run(kb, lp, "dev", tmp_path / "runs" / "dev1", [cite_deny, reply("GAP", gap={"type": "unresolved", "requirement": "x"})])
    rows = [json.loads(x) for x in (tmp_path / "runs" / "dev1" / "cases.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["case_id"] for r in rows] == ["C01", "C02"]
    assert rows[0]["system_outcome"] == "ANSWER" and rows[1]["system_outcome"] == "GAP"
    assert {r["baseline_outcome"] for r in rows} <= {"ANSWER", "ABSTAIN"}
    assert s["kind"] == "cases" and s["split"] == "dev" and s["labels_sha256"] == labels_sha256(lp)
    digest = json.loads((tmp_path / "runs" / "dev1" / "digest.json").read_text(encoding="utf-8"))
    assert verify_records(AuditLog.load_records(tmp_path / "runs" / "dev1" / "log.jsonl"), expected=digest).ok
    # the label never reaches the prompt
    assert "expected_outcome" not in (tmp_path / "runs" / "dev1" / "log.jsonl").read_text(encoding="utf-8")


def test_run_cases_heldout_requires_freeze(kb, tmp_path):
    lp = write_labels(tmp_path)
    with pytest.raises(EvalError, match="freeze"):
        make_case_run(kb, lp, "heldout", tmp_path / "runs" / "h", [reply()] * 2)


def test_freeze_refuses_unapproved_heldout_labels_and_requires_attestation(kb, tmp_path):
    from gapcourt.score import make_freeze

    bad = [dict(r, status="DRAFT_UNAPPROVED") for r in LABELS]
    lp = write_labels(tmp_path, bad)
    sp = scenario_file(tmp_path)
    with pytest.raises(EvalError, match="APPROVED"):
        freeze_for(tmp_path, kb, lp, sp)
    lp2 = write_labels(tmp_path, LABELS, "ok.jsonl")
    with pytest.raises(EvalError, match="without seeing model answers"):
        make_freeze(lp2, kb, scenario_paths=[sp], model_config={}, reviewer="R", exposed_case_ids=[],
                    attest_independent=False, code_version="t")


# ---- score ------------------------------------------------------------------

def test_score_counts_from_saved_runs(kb, tmp_path):
    from gapcourt.score import score_runs

    lp = write_labels(tmp_path)
    sp = scenario_file(tmp_path)
    runs = tmp_path / "runs"
    make_case_run(kb, lp, "dev", runs / "dev1", [cite_deny, reply("GAP", gap={"type": "unresolved", "requirement": "x"})])
    make_scenario_run(kb, sp, runs / "s1", [reply("ESCALATE"), cite_deny])
    res = score_runs(kb, lp, runs, "dev", scenarios_dir=sp.parent, allow_test_double=True)
    rows = res["rows"]
    assert rows["outcome_agreement"]["system"]["agree"] == 1 and rows["outcome_agreement"]["system"]["n"] == 2
    assert rows["outcome_agreement"]["baseline"]["n"] == 2
    assert rows["false_findings"]["system"]["false_gap"] == {"k": 0, "n": 1}  # one ANSWER-labelled case
    sc = rows["scenario_steps"][0]
    assert sc["scenario_id"] == "S-T" and (sc["matched"], sc["n"]) == (2, 2) and sc["frozen"] is False
    rv = rows["revisions"][0]
    assert (rv["revised"], rv["contradicting"], rv["stale"]) == (1, 1, 0)
    assert rows["quotations"]["kept"] >= 2
    assert set(rows["retrieval_top5"]) == {"bm25", "vector", "hybrid"}
    assert rows["retrieval_top5"]["bm25"]["all"]["n"] == 1  # only C01 has an expected passage
    assert rows["model_rejections"]["by_reason"] == {}
    assert res["stamp"]["labels_sha256"] == labels_sha256(lp) and res["stamp"]["kb_version"] == kb.version
    assert res["stamp"]["run_ids"] and all("@" in r for r in res["stamp"]["run_ids"])
    assert "TEST DOUBLE" in res["stamp"]["banner"] and "DRAFT" in res["stamp"]["label_status"]


def test_score_refuses_unfrozen_labels_for_heldout(kb, tmp_path):
    from gapcourt.score import ScoreRefused, score_runs

    lp = write_labels(tmp_path)
    with pytest.raises(ScoreRefused, match="frozen"):
        score_runs(kb, lp, tmp_path / "runs", "heldout", allow_test_double=True)


def test_score_refuses_heldout_when_labels_differ_from_the_freeze_record(kb, tmp_path):
    from gapcourt.score import ScoreRefused, score_runs

    lp = write_labels(tmp_path)
    sp = scenario_file(tmp_path)
    fr = freeze_for(tmp_path, kb, lp, sp)
    fp = tmp_path / "FREEZE.json"
    fp.write_text(json.dumps(fr), encoding="utf-8")
    changed = [dict(r, question=r["question"] + " (edited)") if r["case_id"] == "H01" else r for r in LABELS]
    lp.write_text("\n".join(json.dumps(r) for r in changed) + "\n", encoding="utf-8")
    with pytest.raises(ScoreRefused, match="hash"):
        score_runs(kb, lp, tmp_path / "runs", "heldout", freeze_path=fp, allow_test_double=True)


def test_score_heldout_uses_first_frozen_run_and_excludes_exposed_cases(kb, tmp_path):
    from gapcourt.score import score_runs

    lp = write_labels(tmp_path)
    sp = scenario_file(tmp_path)
    fr = freeze_for(tmp_path, kb, lp, sp, exposed=["H02"])
    fp = tmp_path / "FREEZE.json"
    fp.write_text(json.dumps(fr), encoding="utf-8")
    runs = tmp_path / "runs"
    make_case_run(kb, lp, "heldout", runs / "h1", [cite_deny, reply("GAP", gap={"type": "unresolved", "requirement": "x"})], freeze=fr)
    make_case_run(kb, lp, "heldout", runs / "h2", [reply("ESCALATE"), reply("ESCALATE")], freeze=fr)
    res = score_runs(kb, lp, runs, "heldout", freeze_path=fp, scenarios_dir=sp.parent, allow_test_double=True)
    oa = res["rows"]["outcome_agreement"]
    assert oa["system"] == {"agree": 1, "n": 1, "by_category": {"answerable": {"agree": 1, "n": 1}}}
    assert oa["excluded_exposed"] == ["H02"]
    assert res["rows"]["outcome_agreement"]["run_id"].startswith("h1@")
    assert any(x["run"].startswith("h2") and "later" in x["reason"] for x in res["excluded_runs"])
    assert res["stamp"]["freeze_sha256"] == fr["freeze_sha256"]


def test_score_dev_prefers_the_full_dev_run_over_a_later_smoke_subset(kb, tmp_path):
    from gapcourt.cases import run_cases
    from gapcourt.eval import load_labels
    from gapcourt.score import score_runs

    lp = write_labels(tmp_path)
    runs = tmp_path / "runs"
    make_case_run(kb, lp, "dev", runs / "full", [cite_deny, reply()])
    run_cases(kb, load_labels(lp), "dev", ScriptedModel([reply()]), runs / "smoke", real=False,
              labels_sha=labels_sha256(lp), case_ids=["C01"], code_version="t")
    res = score_runs(kb, lp, runs, "dev", allow_test_double=True)
    assert res["rows"]["outcome_agreement"]["run_id"].startswith("full@")
    assert res["rows"]["outcome_agreement"]["system"]["n"] == 2


def test_score_excludes_test_double_runs_unless_allowed(kb, tmp_path):
    from gapcourt.score import score_runs

    lp = write_labels(tmp_path)
    runs = tmp_path / "runs"
    make_case_run(kb, lp, "dev", runs / "dev1", [reply()] * 2)
    res = score_runs(kb, lp, runs, "dev")
    assert res["rows"]["outcome_agreement"]["system"]["n"] == 0
    assert any("test double" in x["reason"] for x in res["excluded_runs"])
    assert res["stamp"]["banner"] is None


def test_score_excludes_runs_that_fail_log_verification(kb, tmp_path):
    from gapcourt.score import score_runs

    lp = write_labels(tmp_path)
    runs = tmp_path / "runs"
    make_case_run(kb, lp, "dev", runs / "dev1", [reply()] * 2)
    log = runs / "dev1" / "log.jsonl"
    lines = log.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[1])
    rec["decision"] = "ALLOW" if rec.get("decision") else rec.get("decision")
    rec["rationale_summary"] = "tampered"
    lines[1] = json.dumps(rec)
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    res = score_runs(kb, lp, runs, "dev", allow_test_double=True)
    assert any("verification" in x["reason"] for x in res["excluded_runs"])
    assert res["rows"]["outcome_agreement"]["system"]["n"] == 0


def test_score_marks_scenario_rows_unfrozen_or_exposed(kb, tmp_path):
    from gapcourt.score import score_runs

    lp = write_labels(tmp_path)
    sp = scenario_file(tmp_path)
    runs = tmp_path / "runs"
    make_scenario_run(kb, sp, runs / "s0", [reply("ESCALATE"), cite_deny])  # before the freeze
    fr = freeze_for(tmp_path, kb, lp, sp)
    fp = tmp_path / "FREEZE.json"
    fp.write_text(json.dumps(fr), encoding="utf-8")
    make_scenario_run(kb, sp, runs / "s1", [reply("ESCALATE"), cite_deny], freeze_sha=fr["freeze_sha256"])
    res = score_runs(kb, lp, runs, "dev", freeze_path=fp, scenarios_dir=sp.parent, allow_test_double=True)
    by = {x["run_id"].split("@")[0]: x for x in res["rows"]["scenario_steps"]}
    assert by["s0"]["frozen"] is False and by["s1"]["frozen"] is True
    assert by["s1"]["exposed"] is True  # a run of this scenario predates the freeze


def test_write_results_stamps_json_and_markdown(kb, tmp_path):
    from gapcourt.score import score_runs, write_results

    lp = write_labels(tmp_path)
    runs = tmp_path / "runs"
    make_case_run(kb, lp, "dev", runs / "dev1", [cite_deny, reply()])
    res = score_runs(kb, lp, runs, "dev", allow_test_double=True)
    out = tmp_path / "results"
    write_results(res, out)
    md = (out / "results.md").read_text(encoding="utf-8")
    js = json.loads((out / "results.json").read_text(encoding="utf-8"))
    assert js["stamp"]["labels_sha256"] in md and "TEST DOUBLE" in md
    assert " of " in md and "accuracy" not in md.lower()


def test_score_cli_refuses_heldout_without_freeze(kb, tmp_path, capsys):
    from gapcourt.cli import main

    kb.save(tmp_path / "kb")
    lp = write_labels(tmp_path)
    rc = main(["score", "--kb", str(tmp_path / "kb"), "--labels", str(lp), "--runs", str(tmp_path / "runs"),
               "--split", "heldout", "--out", str(tmp_path / "res"), "--freeze", str(tmp_path / "none.json")])
    assert rc == 2 and "frozen" in capsys.readouterr().err


# ---- judge add-step ---------------------------------------------------------

def test_add_step_runs_after_released_steps_and_logs(kb, tmp_path):
    from gapcourt.journey import JOURNEY_ROLES, add_step, load_scenario, run_and_save

    sp = scenario_file(tmp_path)
    sc = add_step(load_scenario(sp), "Step 3. The resident consented in writing to sharing for benefit checks.", ["adviser"])
    assert [s.step_id for s in sc.steps][-1] == "S-T-J1" and sc.steps[-1].hidden == {"judge_added": True}
    m = ScriptedModel([reply(), reply(), reply()])
    roles = [r for r in JOURNEY_ROLES if r.name == "adviser"]
    out = run_and_save(kb, sc, {"adviser": m}, tmp_path / "judge", real=False, code_version="t", roles=roles,
                       extra={"judge_added_step": {"step_id": "S-T-J1", "to": ["adviser"]}})
    # the added step is released last, and the adviser sees the earlier steps addressed to it plus the new one
    last_prompt = m.calls[-1]["user"]
    assert "[step 1]" in last_prompt and "[step 3] Step 3. The resident consented" in last_prompt
    assert len(m.calls) == 3 and out["rulings"][-1]["step_id"] == "S-T-J1"
    log = (tmp_path / "judge" / "log.jsonl").read_text(encoding="utf-8")
    assert '"step_id": "S-T-J1"' in log and (tmp_path / "judge" / "trace.html").exists()
    assert out["judge_added_step"]["step_id"] == "S-T-J1"


def test_add_step_rejects_unknown_addressee(kb, tmp_path):
    from gapcourt.journey import add_step, load_scenario

    with pytest.raises(ValueError, match="addressee"):
        add_step(load_scenario(scenario_file(tmp_path)), "text", ["mayor"])


def test_add_step_cli_offline_rehearsal(kb, tmp_path, capsys):
    from gapcourt.cli import main

    kb.save(tmp_path / "kb")
    sp = scenario_file(tmp_path)
    out = tmp_path / "runs" / "judge1"
    rc = main(["add-step", "--kb", str(tmp_path / "kb"), "--scenario", str(sp), "--to", "adviser",
               "--text", "Step 3. A new fact.", "--out", str(out), "--test-double", "--runs-root", str(tmp_path / "runs")])
    assert rc == 0 and "TEST DOUBLE" in capsys.readouterr().out
    s = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert s["judge_added_step"]["to"] == ["adviser"] and s["test_double"] is True
    assert (out / "scenario_with_added_step.json").exists()


# ---- live-run script and README ---------------------------------------------

def test_live_run_script_holds_no_key_and_never_sets_one():
    text = (ROOT / "scripts" / "walter_live_runs.ps1").read_text(encoding="utf-8")
    assert "sk-ant" not in text
    assert not re.search(r"\$env:ANTHROPIC_API_KEY\s*=", text)
    assert "ANTHROPIC_API_KEY" in text  # it checks that the key is set
    assert "--test-double" in text  # rehearsal path exists
    for session in ("probe", "smoke", "dev", "heldout", "repeats", "status"):
        assert session in text


def test_readme_has_no_stale_remote_line_and_template_says_four_roles():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "no remote" not in readme
    assert (ROOT / "NOT_DONE.md").exists()
    tpl = (ROOT / "report" / "TEMPLATE_V3_FILLABLE.md").read_text(encoding="utf-8")
    assert "five roles" not in tpl and "four model-backed roles" in tpl


# ---- Scoring integrity -----------------------------------------------------------

def _frozen_setup(kb, tmp_path, exposed=()):
    lp = write_labels(tmp_path)
    sp = scenario_file(tmp_path)
    fr = freeze_for(tmp_path, kb, lp, sp, exposed=list(exposed))
    fp = tmp_path / "FREEZE.json"
    fp.write_text(json.dumps(fr), encoding="utf-8")
    return lp, sp, fr, fp


def test_score_ignores_an_edited_cases_sidecar_and_excludes_the_run(kb, tmp_path):
    # F1: cases.jsonl is not covered by the digest; scored outcomes must come from the verified log
    from gapcourt.score import score_runs

    lp, sp, fr, fp = _frozen_setup(kb, tmp_path)
    runs = tmp_path / "runs"
    make_case_run(kb, lp, "heldout", runs / "h1", [reply("ESCALATE"), reply("ESCALATE")], freeze=fr)
    side = runs / "h1" / "cases.jsonl"
    rows = [json.loads(x) for x in side.read_text(encoding="utf-8").splitlines()]
    for r in rows:
        r["system_outcome"] = {"H01": "ANSWER", "H02": "GAP"}[r["case_id"]]  # make the system look perfect
    side.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    res = score_runs(kb, lp, runs, "heldout", freeze_path=fp, scenarios_dir=sp.parent, allow_test_double=True)
    assert res["rows"]["outcome_agreement"]["system"]["agree"] == 0
    assert any("sidecar" in x["reason"] for x in res["excluded_runs"])


def test_score_reads_outcomes_from_the_log_when_the_sidecar_is_missing(kb, tmp_path):
    from gapcourt.score import score_runs

    lp, sp, fr, fp = _frozen_setup(kb, tmp_path)
    runs = tmp_path / "runs"
    make_case_run(kb, lp, "heldout", runs / "h1", [cite_deny, reply("GAP", gap={"type": "unresolved", "requirement": "x"})], freeze=fr)
    (runs / "h1" / "cases.jsonl").unlink()
    res = score_runs(kb, lp, runs, "heldout", freeze_path=fp, scenarios_dir=sp.parent, allow_test_double=True)
    assert res["rows"]["outcome_agreement"]["system"]["agree"] == 2


def test_summary_that_disagrees_with_the_log_about_the_freeze_is_excluded(kb, tmp_path):
    from gapcourt.score import score_runs

    lp, sp, fr, fp = _frozen_setup(kb, tmp_path)
    runs = tmp_path / "runs"
    make_case_run(kb, lp, "heldout", runs / "h1", [reply(), reply()], freeze=fr)
    sj = runs / "h1" / "summary.json"
    s = json.loads(sj.read_text(encoding="utf-8"))
    s["freeze_sha256"] = "0" * 64
    sj.write_text(json.dumps(s), encoding="utf-8")
    res = score_runs(kb, lp, runs, "heldout", freeze_path=fp, scenarios_dir=sp.parent, allow_test_double=True)
    assert res["rows"]["outcome_agreement"]["system"]["n"] == 0
    assert res["excluded_runs"]


def test_legacy_freeze_without_attestation_is_refused(kb, tmp_path):
    # F2: eval.freeze_record has no reviewer or attestation
    from gapcourt.eval import freeze_record
    from gapcourt.score import ScoreRefused, score_runs

    lp = write_labels(tmp_path)
    legacy = freeze_record(lp, kb_version=kb.version, top_k=5, fusion={}, abstain_threshold=0.35, model_config={}, code_version="t")
    fp = tmp_path / "FREEZE.json"
    fp.write_text(json.dumps(legacy), encoding="utf-8")
    with pytest.raises(ScoreRefused, match="attest"):
        score_runs(kb, lp, tmp_path / "runs", "heldout", freeze_path=fp, allow_test_double=True)


def test_heldout_run_refuses_a_different_knowledge_base_than_the_freeze(kb, tmp_path, fixtures_dir):
    from gapcourt.cases import run_cases
    from gapcourt.eval import load_labels

    lp, sp, fr, fp = _frozen_setup(kb, tmp_path)
    root = tmp_path / "other"
    (root / "uae").mkdir(parents=True)
    shutil.copy(fixtures_dir / "S02.md", root / "uae" / "S02.md")
    kb2 = build_kb([root], HashingEmbedder(dim=128), ChunkParams(max_chars=300))
    assert kb2.version != kb.version
    with pytest.raises(EvalError, match="knowledge base"):
        run_cases(kb2, load_labels(lp), "heldout", ScriptedModel([reply()] * 2), tmp_path / "runs" / "h", real=False,
                  labels_sha=labels_sha256(lp), freeze=fr, code_version="t")


def test_eval_command_refuses_heldout_retrieval_before_a_freeze(kb, tmp_path, capsys):
    from gapcourt.cli import main

    kb.save(tmp_path / "kb")
    lp = write_labels(tmp_path)
    rc = main(["eval-retrieval", "--kb", str(tmp_path / "kb"), "--labels", str(lp), "--collections", "kb_uae", "--split", "heldout",
               "--freeze", str(tmp_path / "none.json")])
    assert rc == 2 and "freeze" in capsys.readouterr().err
    rc = main(["eval-retrieval", "--kb", str(tmp_path / "kb"), "--labels", str(lp), "--collections", "kb_uae", "--freeze", str(tmp_path / "none.json")])
    out = capsys.readouterr().out
    assert rc == 0 and "withheld" in out.lower()


def test_partial_heldout_run_reports_missing_cases_and_a_retry_fills_only_those(kb, tmp_path):
    from gapcourt.cases import run_cases
    from gapcourt.eval import load_labels
    from gapcourt.score import score_runs

    lp, sp, fr, fp = _frozen_setup(kb, tmp_path)
    runs = tmp_path / "runs"
    run_cases(kb, load_labels(lp), "heldout", ScriptedModel([cite_deny]), runs / "h1", real=False,
              labels_sha=labels_sha256(lp), freeze=fr, code_version="t", case_ids=["H01"])
    res = score_runs(kb, lp, runs, "heldout", freeze_path=fp, scenarios_dir=sp.parent, allow_test_double=True)
    oa = res["rows"]["outcome_agreement"]
    assert oa["missing_cases"] == ["H02"] and oa["system"]["n"] == 1
    run_cases(kb, load_labels(lp), "heldout", ScriptedModel([reply("ESCALATE"), reply("GAP", gap={"type": "unresolved", "requirement": "x"})]),
              runs / "h2", real=False, labels_sha=labels_sha256(lp), freeze=fr, code_version="t")
    res = score_runs(kb, lp, runs, "heldout", freeze_path=fp, scenarios_dir=sp.parent, allow_test_double=True)
    oa = res["rows"]["outcome_agreement"]
    assert oa["missing_cases"] == [] and oa["system"]["n"] == 2
    # H01 keeps its first outcome (DENY -> ANSWER, agrees); H02 comes from the retry (GAP, agrees)
    assert oa["system"]["agree"] == 2


def test_blocked_scenario_step_is_not_in_the_scored_denominator():
    from gapcourt.score import _scenario_rows

    scen = {"steps": [
        {"step_id": "A", "hidden": {"expected_candidate": "ESCALATE"}},
        {"step_id": "B", "hidden": {"expected_candidate": "SCOPE_FINDING: x", "blocked_on": "English text of U01"}}]}
    st, _ = _scenario_rows([], scen)
    assert st["n"] == 1 and [s["step_id"] for s in st["steps"]] == ["A"]
    assert st["blocked"] == [{"step_id": "B", "blocked_on": "English text of U01"}]


def test_blocked_labels_are_not_run_approved_or_frozen(kb, tmp_path):
    from gapcourt.cases import run_cases
    from gapcourt.eval import load_labels
    from gapcourt.labelreview import approve_labels
    from gapcourt.score import make_freeze

    rows = [dict(r) for r in LABELS]
    rows.append({"case_id": "H03", "split": "heldout", "category": "ambiguity", "status": "DRAFT_UNAPPROVED",
                 "question": "does the federal law cover this pre-check", "expected": [], "expected_outcome": "SCOPE_FINDING",
                 "blocked_on": "English text of U01"})
    lp = write_labels(tmp_path, rows)
    with pytest.raises(EvalError, match="blocked"):
        approve_labels(lp, tmp_path / "x.jsonl", "R", approve=["H03"])
    approved = approve_labels(lp, tmp_path / "ok.jsonl", "R", approve="all")
    assert {r["case_id"]: r["status"] for r in approved}["H03"] == "DRAFT_UNAPPROVED"
    fr = make_freeze(tmp_path / "ok.jsonl", kb, scenario_paths=[], model_config={}, reviewer="R", exposed_case_ids=[],
                     attest_independent=True, code_version="t")
    assert "H03" not in fr["heldout_case_ids"] and fr["blocked_case_ids"] == ["H03"]
    s = run_cases(kb, load_labels(tmp_path / "ok.jsonl"), "heldout", ScriptedModel([reply()] * 5), tmp_path / "runs" / "h",
                  real=False, labels_sha=labels_sha256(tmp_path / "ok.jsonl"), freeze=fr, code_version="t")
    assert "H03" not in s["case_ids"]


def test_unexpected_error_mid_run_still_saves_log_cost_and_summary(kb, tmp_path):
    # an unexpected exception used to end a paid run before digest and cost files were written
    from gapcourt.audit import AuditLog, verify_records
    from gapcourt.cases import run_cases
    from gapcourt.eval import load_labels

    def boom(system, user):
        raise RuntimeError("synthetic failure")

    lp = write_labels(tmp_path)
    s = run_cases(kb, load_labels(lp), "dev", ScriptedModel([cite_deny, boom]), tmp_path / "runs" / "d", real=False,
                  labels_sha=labels_sha256(lp), code_version="t")
    out = tmp_path / "runs" / "d"
    assert s["stopped"]["reason"] == "unexpected error" and "RuntimeError" in s["stopped"]["detail"]
    assert (out / "cost.json").exists() and (out / "summary.json").exists()
    digest = json.loads((out / "digest.json").read_text(encoding="utf-8"))
    assert verify_records(AuditLog.load_records(out / "log.jsonl"), expected=digest).ok
    assert s["case_ids"] == ["C01"]


def test_unexpected_error_in_a_scenario_run_is_saved_as_a_stop(kb, tmp_path):
    sp = scenario_file(tmp_path)

    def boom(system, user):
        raise RuntimeError("synthetic failure")

    out = tmp_path / "runs" / "s"
    summary = make_scenario_run(kb, sp, out, [reply("ESCALATE"), boom])
    assert summary["stopped"]["reason"] == "unexpected error"
    assert (out / "cost.json").exists() and (out / "digest.json").exists()


def test_heldout_run_whose_logged_model_differs_from_the_frozen_model_is_excluded(kb, tmp_path):
    # the model id in the hash-chained log, not only in summary.json, must match the freeze
    from gapcourt.score import make_freeze, score_runs

    lp = write_labels(tmp_path)
    sp = scenario_file(tmp_path)
    fr = make_freeze(lp, kb, scenario_paths=[sp], model_config={"model_id": "claude-haiku-5-5"}, reviewer="R",
                     exposed_case_ids=[], attest_independent=True, code_version="t")
    fp = tmp_path / "FREEZE.json"
    fp.write_text(json.dumps(fr), encoding="utf-8")
    runs = tmp_path / "runs"
    make_case_run(kb, lp, "heldout", runs / "h1", [reply(), reply()], freeze=fr)  # logged model: scripted-test-model
    sj = runs / "h1" / "summary.json"
    sm = json.loads(sj.read_text(encoding="utf-8"))
    sm["models"] = {"adviser": {"model_id": "claude-haiku-5-5"}}  # the summary claims the frozen model
    sj.write_text(json.dumps(sm), encoding="utf-8")
    res = score_runs(kb, lp, runs, "heldout", freeze_path=fp, scenarios_dir=sp.parent, allow_test_double=True)
    assert res["rows"]["outcome_agreement"]["system"]["n"] == 0
    # the edit to summary.json is caught by the digest binding first; the log's own model id is still checked after it
    assert any("model" in x["reason"] or "summary" in x["reason"] for x in res["excluded_runs"])
