"""Session 3: summary.json and the sidecars are bound into the retained digest; the code a run used is compared
with the code the freeze names. Test doubles only; nothing here is model output."""
import json
import shutil
from pathlib import Path

import pytest

from gapcourt.audit import AuditLog, check_summary_binding, summary_sha256, verify_records
from gapcourt.chunking import ChunkParams
from gapcourt.embed import HashingEmbedder
from gapcourt.eval import EvalError, labels_sha256
from gapcourt.index import build_kb
from gapcourt.llm import ScriptedModel

QUOTE = "must not use the records for a different purpose"


@pytest.fixture
def kb(fixtures_dir, tmp_path):
    root = tmp_path / "corpus"
    (root / "uae").mkdir(parents=True)
    shutil.copy(fixtures_dir / "S01.md", root / "uae" / "S01.md")
    k = build_kb([root], HashingEmbedder(dim=128), ChunkParams(max_chars=300))
    k.applicability = {"approved": {"S01"}, "sha256": "test-approvals"}  # test fixture, never the real corpus file
    return k


def reply(decision="ESCALATE"):
    return json.dumps({"decision": decision, "rule_applied": "r", "rationale": "x", "citations": [],
                       "applicability": [], "gap": None, "revision_reason": None})


LABELS = [
    {"case_id": "C01", "split": "dev", "category": "answerable", "status": "DRAFT_UNAPPROVED",
     "question": "may the recipient agency use the shared records for a different purpose",
     "expected": [{"doc_id": "S01", "locator": "Article 3"}], "expected_outcome": "ANSWER"},
    {"case_id": "H01", "split": "heldout", "category": "answerable", "status": "APPROVED",
     "question": "may the recipient agency use the shared records for a different purpose",
     "expected": [{"doc_id": "S01", "locator": "Article 3"}], "expected_outcome": "ANSWER"},
    {"case_id": "H02", "split": "heldout", "category": "candidate_gap", "status": "APPROVED",
     "question": "must a resident be told an automated tool assessed them", "expected": [], "expected_outcome": "GAP"},
]


def write_labels(tmp_path):
    p = tmp_path / "labels.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in LABELS) + "\n", encoding="utf-8")
    return p


def scenario_file(tmp_path):
    d = {"scenario_id": "S-T", "title": "synthetic", "synthetic": True, "steps": [
        {"step_id": "T-1", "addressees": ["adviser"], "text": "Step 1. A service asks whether it may use records.",
         "hidden": {"contradicts_prior": False, "expected_candidate": "ESCALATE"}}]}
    p = tmp_path / "scenarios" / "S-T.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d), encoding="utf-8")
    return p


def case_run(kb, lp, split, out, freeze=None, code_tree=None, code_version="test"):
    from gapcourt.cases import run_cases
    from gapcourt.eval import load_labels

    kw = {} if code_tree is None else {"code_tree": code_tree}
    return run_cases(kb, load_labels(lp), split, ScriptedModel([reply()] * 4), out, real=False,
                     labels_sha=labels_sha256(lp), freeze=freeze, code_version=code_version, **kw)


def scenario_run(kb, sp, out, code_tree=None):
    from gapcourt.journey import JOURNEY_ROLES, load_scenario, run_and_save

    roles = [r for r in JOURNEY_ROLES if r.name == "adviser"]
    kw = {} if code_tree is None else {"code_tree": code_tree}
    return run_and_save(kb, load_scenario(sp), {"adviser": ScriptedModel([reply()] * 3)}, out, real=False,
                        code_version="test", roles=roles, **kw)


def freeze_for(kb, lp, sp, **kw):
    from gapcourt.score import make_freeze

    return make_freeze(lp, kb, scenario_paths=[sp], model_config={"model_id": "scripted-test-model"}, reviewer="R",
                       exposed_case_ids=[], attest_independent=True, code_version="test", **kw)


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


# ---- 1. the whole summary is in the digest ----------------------------------

def test_a_saved_scenario_run_binds_its_whole_summary_into_the_digest(kb, tmp_path):
    out = tmp_path / "runs" / "s1"
    scenario_run(kb, scenario_file(tmp_path), out)
    recs = AuditLog.load_records(out / "log.jsonl")
    digest, summary = load(out / "digest.json"), load(out / "summary.json")
    assert verify_records(recs, expected=digest).ok
    assert recs[-1]["kind"] == "event" and recs[-1]["event"] == "summary_bound"
    assert recs[-1]["payload"]["summary_sha256"] == summary_sha256(summary)
    assert check_summary_binding(recs, summary, digest, out) is None


@pytest.mark.parametrize("field,value", [("cost", {"cost_usd": 0.0, "calls": 0}), ("code_version", "abc1234"),
                                         ("stopped", None), ("kb_version", "kb-forged")])
def test_any_edit_to_summary_json_is_detected(kb, tmp_path, field, value):
    out = tmp_path / "runs" / "s1"
    scenario_run(kb, scenario_file(tmp_path), out)
    s = load(out / "summary.json")
    s[field] = value if value is not None else {"reason": "x"}
    (out / "summary.json").write_text(json.dumps(s), encoding="utf-8")
    recs = AuditLog.load_records(out / "log.jsonl")
    assert "summary" in check_summary_binding(recs, s, load(out / "digest.json"), out)


def test_an_added_summary_field_is_detected_too(kb, tmp_path):
    out = tmp_path / "runs" / "s1"
    scenario_run(kb, scenario_file(tmp_path), out)
    s = load(out / "summary.json")
    s["note_added_later"] = "x"
    assert check_summary_binding(AuditLog.load_records(out / "log.jsonl"), s, load(out / "digest.json"), out)


def test_a_digest_field_in_the_summary_that_differs_from_digest_json_is_detected(kb, tmp_path):
    out = tmp_path / "runs" / "s1"
    scenario_run(kb, scenario_file(tmp_path), out)
    s = load(out / "summary.json")
    s["digest"] = {"count": 1, "last_hash": "0" * 64}
    assert "digest" in check_summary_binding(AuditLog.load_records(out / "log.jsonl"), s, load(out / "digest.json"), out)


def test_a_log_with_no_binding_event_is_not_bound(kb, tmp_path):
    log = AuditLog(code_version="x")
    log.append_event("scenario_start", {})
    assert "not bound" in check_summary_binding(log.records, {"kind": "scenario"}, log.terminal_digest(), None)


def test_cases_sidecar_is_bound_by_hash(kb, tmp_path):
    lp = write_labels(tmp_path)
    out = tmp_path / "runs" / "d1"
    case_run(kb, lp, "dev", out)
    recs = AuditLog.load_records(out / "log.jsonl")
    s, digest = load(out / "summary.json"), load(out / "digest.json")
    assert check_summary_binding(recs, s, digest, out) is None
    with open(out / "cases.jsonl", "a", encoding="utf-8") as f:
        f.write("\n")
    assert "cases.jsonl" in check_summary_binding(recs, s, digest, out)


def test_score_excludes_a_run_whose_summary_was_edited(kb, tmp_path):
    from gapcourt.score import score_runs

    lp, sp = write_labels(tmp_path), scenario_file(tmp_path)
    runs = tmp_path / "runs"
    case_run(kb, lp, "dev", runs / "d1")
    s = load(runs / "d1" / "summary.json")
    s["cost"]["calls"] = 99
    (runs / "d1" / "summary.json").write_text(json.dumps(s), encoding="utf-8")
    res = score_runs(kb, lp, runs, "dev", scenarios_dir=sp.parent, allow_test_double=True)
    assert res["rows"]["outcome_agreement"]["system"]["n"] == 0
    assert any("summary" in x["reason"] for x in res["excluded_runs"])


def test_score_excludes_a_legacy_run_with_no_binding(kb, tmp_path):
    from gapcourt.score import score_runs

    lp, sp = write_labels(tmp_path), scenario_file(tmp_path)
    runs = tmp_path / "runs"
    d = runs / "old"
    case_run(kb, lp, "dev", d)
    # rebuild the run as an older version would have written it: no binding event
    recs = AuditLog.load_records(d / "log.jsonl")[:-1]
    (d / "log.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in recs), encoding="utf-8")
    (d / "digest.json").write_text(json.dumps({"count": len(recs), "last_hash": recs[-1]["record_hash"]}), encoding="utf-8")
    res = score_runs(kb, lp, runs, "dev", scenarios_dir=sp.parent, allow_test_double=True)
    assert res["rows"]["outcome_agreement"]["system"]["n"] == 0
    assert any("not bound" in x["reason"] for x in res["excluded_runs"])


def test_the_verify_command_checks_the_summary_beside_the_log(kb, tmp_path, capsys):
    from gapcourt.cli import main

    out = tmp_path / "runs" / "s1"
    scenario_run(kb, scenario_file(tmp_path), out)
    assert main(["verify-log", "--log", str(out / "log.jsonl"), "--digest", str(out / "digest.json")]) == 0
    assert "summary.json is bound" in capsys.readouterr().out
    s = load(out / "summary.json")
    s["cost"] = {"cost_usd": 0.0}
    (out / "summary.json").write_text(json.dumps(s), encoding="utf-8")
    assert main(["verify-log", "--log", str(out / "log.jsonl"), "--digest", str(out / "digest.json")]) == 1


def test_report_does_not_call_a_run_with_an_edited_summary_measured(tmp_path):
    from gapcourt.report import _verify_run

    # the helper writes the summary and the log; the edit below must be seen
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from test_report_fill import make_run

    runs = tmp_path / "runs"
    make_run(runs, "smoke/S-03-a")
    assert _verify_run(runs / "smoke" / "S-03-a") is None
    s = load(runs / "smoke" / "S-03-a" / "summary.json")
    s["cost"]["calls"] = 99
    (runs / "smoke" / "S-03-a" / "summary.json").write_text(json.dumps(s), encoding="utf-8")
    assert "summary" in _verify_run(runs / "smoke" / "S-03-a")


# ---- 2. the code a run used versus the frozen code ---------------------------

def test_code_tree_hash_is_stable_and_changes_with_the_source(tmp_path):
    from gapcourt.eval import code_tree_sha256

    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "a.pyc").write_bytes(b"junk")
    h1 = code_tree_sha256(tmp_path)
    assert h1 == code_tree_sha256(tmp_path)
    (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8")
    assert code_tree_sha256(tmp_path) != h1


def test_freeze_and_runs_record_the_code_tree(kb, tmp_path):
    from gapcourt.eval import code_tree_sha256

    lp, sp = write_labels(tmp_path), scenario_file(tmp_path)
    fr = freeze_for(kb, lp, sp)
    assert fr["code_tree_sha256"] == code_tree_sha256()
    s = scenario_run(kb, sp, tmp_path / "runs" / "s1")
    assert s["code_tree_sha256"] == code_tree_sha256()


def test_heldout_run_is_refused_before_any_call_when_the_code_changed_since_the_freeze(kb, tmp_path):
    lp, sp = write_labels(tmp_path), scenario_file(tmp_path)
    fr = freeze_for(kb, lp, sp, code_tree_sha256="f" * 64)
    with pytest.raises(EvalError, match="code"):
        case_run(kb, lp, "heldout", tmp_path / "runs" / "h1", freeze=fr)
    assert not (tmp_path / "runs" / "h1" / "log.jsonl").exists()


def test_heldout_score_excludes_a_run_made_with_other_code_and_can_label_it_instead(kb, tmp_path, monkeypatch):
    from gapcourt.score import score_runs

    monkeypatch.setattr("gapcourt.cases.check_freeze_for_heldout", lambda *a, **k: None)  # a run that got past the guard

    lp, sp = write_labels(tmp_path), scenario_file(tmp_path)
    fr = freeze_for(kb, lp, sp)
    fp = tmp_path / "FREEZE.json"
    fp.write_text(json.dumps(fr), encoding="utf-8")
    runs = tmp_path / "runs"
    case_run(kb, lp, "heldout", runs / "h1", freeze=fr, code_tree="e" * 64)  # the run's code differs from the frozen code
    res = score_runs(kb, lp, runs, "heldout", freeze_path=fp, scenarios_dir=sp.parent, allow_test_double=True)
    assert res["rows"]["outcome_agreement"]["system"]["n"] == 0
    assert any("code" in x["reason"] for x in res["excluded_runs"])
    res2 = score_runs(kb, lp, runs, "heldout", freeze_path=fp, scenarios_dir=sp.parent, allow_test_double=True,
                      allow_code_drift=True)
    assert res2["rows"]["outcome_agreement"]["system"]["n"] == 2
    assert res2["stamp"]["code_differs_from_freeze"]


def test_scenario_run_with_other_code_is_labelled_in_the_stamp(kb, tmp_path):
    from gapcourt.score import score_runs, write_results

    lp, sp = write_labels(tmp_path), scenario_file(tmp_path)
    fr = freeze_for(kb, lp, sp)
    fp = tmp_path / "FREEZE.json"
    fp.write_text(json.dumps(fr), encoding="utf-8")
    runs = tmp_path / "runs"
    scenario_run(kb, sp, runs / "s1", code_tree="e" * 64)
    res = score_runs(kb, lp, runs, "dev", freeze_path=fp, scenarios_dir=sp.parent, allow_test_double=True)
    assert res["stamp"]["code_differs_from_freeze"]
    write_results(res, tmp_path / "res")
    assert "differs from the frozen code" in (tmp_path / "res" / "results.md").read_text(encoding="utf-8")


def test_freeze_without_a_code_tree_falls_back_to_the_code_version(kb, tmp_path):
    from gapcourt.eval import code_drift_problem

    assert code_drift_problem({"code_version": "abc"}, {"code_version": "abc"}) is None
    assert "version" in code_drift_problem({"code_version": "abc"}, {"code_version": "def"})
    assert code_drift_problem({"code_tree_sha256": "a", "code_version": "x"}, {"code_tree_sha256": "a", "code_version": "y"}) is None
    assert "tree" in code_drift_problem({"code_tree_sha256": "a"}, {"code_tree_sha256": "b"})
    assert code_drift_problem({"code_tree_sha256": "a"}, {})


def test_a_log_whose_records_carry_another_code_version_than_the_summary_is_flagged(kb, tmp_path):
    from gapcourt.audit import AuditLog as AL

    out = tmp_path / "runs" / "s1"
    scenario_run(kb, scenario_file(tmp_path), out)
    recs = AL.load_records(out / "log.jsonl")
    s = load(out / "summary.json")
    s["code_version"] = "elsewhere"
    s.pop("digest", None)
    # a summary that was bound honestly but names a different version than the records carry
    log = AL(path=None, code_version="test")
    log.records = recs[:-1]
    from gapcourt.audit import bind_summary

    bind_summary(log, s)
    msg = check_summary_binding(log.records, s, log.terminal_digest(), None)
    assert msg and "code version" in msg


def test_log_model_check_still_applies_without_any_summary_edit(kb, tmp_path):
    from gapcourt.score import make_freeze, score_runs

    lp, sp = write_labels(tmp_path), scenario_file(tmp_path)
    fr = make_freeze(lp, kb, scenario_paths=[sp], model_config={"model_id": "claude-haiku-5-5"}, reviewer="R",
                     exposed_case_ids=[], attest_independent=True, code_version="test")
    fp = tmp_path / "FREEZE.json"
    fp.write_text(json.dumps(fr), encoding="utf-8")
    runs = tmp_path / "runs"
    case_run(kb, lp, "heldout", runs / "h1", freeze=fr)  # logged model: scripted-test-model
    res = score_runs(kb, lp, runs, "heldout", freeze_path=fp, scenarios_dir=sp.parent, allow_test_double=True)
    assert res["rows"]["outcome_agreement"]["system"]["n"] == 0
    assert any("model id in the verified log" in x["reason"] for x in res["excluded_runs"])
