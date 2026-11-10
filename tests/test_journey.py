import json
from pathlib import Path

import pytest

from gapcourt.audit import verify_records
from gapcourt.embed import HashingEmbedder
from gapcourt.index import build_kb
from gapcourt.journey import JOURNEY_ROLES, build_engine, load_scenario
from gapcourt.llm import ScriptedModel

ROOT = Path(__file__).resolve().parents[1]
SCEN = ROOT / "scenarios"


def test_scenarios_are_synthetic_and_never_name_a_country_recommendation():
    for p in SCEN.glob("S-0*.json"):
        sc = load_scenario(p)
        assert sc.synthetic is True and sc.steps
        text = p.read_text(encoding="utf-8").lower()
        assert ("the uae " + "should") not in text


def test_roles_cover_the_page_roles_and_partition_documents():
    names = {r.name for r in JOURNEY_ROLES}
    assert names == {"service", "adviser", "custodian", "oversight"}
    by = {r.name: r for r in JOURNEY_ROLES}
    assert by["service"].use_kb is False
    assert by["adviser"].collections == ["kb_uae"] and by["adviser"].doc_ids is None
    assert by["custodian"].doc_ids and by["oversight"].doc_ids
    assert set(by["custodian"].doc_ids) != set(by["oversight"].doc_ids)
    assert all("kb_global" not in r.collections for r in JOURNEY_ROLES)


@pytest.mark.skipif(not (ROOT / "corpus_local" / "uae" / "U06.md").exists(), reason="link-only corpus not present")
def test_s01_runs_end_to_end_with_scripted_models_and_logs_verify():
    kb = build_kb([ROOT / "corpus", ROOT / "corpus_local"], HashingEmbedder(dim=256))
    reply = json.dumps({"decision": "ESCALATE", "rule_applied": "none", "rationale": "unclear for this actor",
                        "citations": [], "gap": None, "applicability": [], "revision_reason": None})
    models = {r.name: ScriptedModel([reply] * 5) for r in JOURNEY_ROLES}
    eng = build_engine(kb, load_scenario(SCEN / "S-01.json"), models)
    res = eng.run_all()
    assert [s.ruling.decision for s in res.step_results] == ["ESCALATE"] * 3
    assert verify_records(eng.log.records, expected=eng.log.terminal_digest()).ok
    # custodian only ever saw its own instruments
    cust = eng.roles["custodian"].model.calls[0]["user"]
    assert 'id="U12-' in cust or 'id="U06-' in cust or 'id="U13-' in cust or 'id="U11-' in cust
    assert 'id="U15-' not in cust and 'id="U16-' not in cust
    # hidden labels never reach a prompt
    allp = " ".join(c["user"] + c["system"] for m in models.values() for c in m.calls)
    assert "expected_candidate" not in allp and "decider_candidate" not in allp


def test_run_and_save_writes_log_digest_trace_and_refuses_scripted_for_real(tmp_path):
    from gapcourt.journey import run_and_save

    kb_root = tmp_path / "c"
    (kb_root / "uae").mkdir(parents=True)
    src = (ROOT / "tests" / "fixtures_synthetic" / "S01.md").read_text(encoding="utf-8")
    (kb_root / "uae" / "S01.md").write_text(src, encoding="utf-8")
    kb = build_kb([kb_root], HashingEmbedder(dim=64))
    reply = json.dumps({"decision": "ESCALATE", "rule_applied": "none", "rationale": "x", "citations": []})
    sc = load_scenario(SCEN / "S-03.json")
    models = {r.name: ScriptedModel([reply] * 3) for r in JOURNEY_ROLES}
    with pytest.raises(ValueError, match="scripted"):
        run_and_save(kb, sc, models, tmp_path / "bad", real=True)
    out = run_and_save(kb, sc, models, tmp_path / "run", real=False, code_version="t")
    for name in ("log.jsonl", "digest.json", "trace.html", "summary.json"):
        assert (tmp_path / "run" / name).exists()
    assert out["digest"]["count"] >= 3
    from gapcourt.audit import AuditLog
    assert verify_records(AuditLog.load_records(tmp_path / "run" / "log.jsonl"), expected=out["digest"]).ok
