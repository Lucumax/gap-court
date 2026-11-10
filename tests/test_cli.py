import json
import shutil

import pytest

from gapcourt.cli import main


@pytest.fixture
def root(fixtures_dir, tmp_path):
    r = tmp_path / "corpus"
    (r / "uae").mkdir(parents=True)
    shutil.copy(fixtures_dir / "S01.md", r / "uae" / "S01.md")
    return r


def test_build_search_coverage_round_trip(root, tmp_path, capsys):
    out = tmp_path / "kb"
    assert main(["build-kb", "--root", str(root), "--out", str(out), "--max-chars", "300", "--dim", "128"]) == 0
    printed = capsys.readouterr().out
    assert "kb-" in printed
    assert main(["search", "--kb", str(out), "--collections", "kb_uae", "--mode", "bm25",
                 "--query", "government agencies processing personal data"]) == 0
    assert "S01 Article 2" in capsys.readouterr().out
    assert main(["coverage", "--kb", str(out)]) == 0
    assert "S01" in capsys.readouterr().out


def test_build_refuses_link_only_in_public_root(root, tmp_path):
    p = root / "uae" / "S01.md"
    p.write_text(p.read_text(encoding="utf-8").replace("republish: yes", "republish: link_only"), encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["build-kb", "--root", str(root), "--public-root", str(root), "--out", str(tmp_path / "kb")])


def test_verify_log_command(tmp_path, capsys):
    from gapcourt.audit import AuditLog

    p = tmp_path / "log.jsonl"
    log = AuditLog(path=p, code_version="t")
    log.append_event("x", {"a": 1})
    d = tmp_path / "digest.json"
    d.write_text(json.dumps(log.terminal_digest()), encoding="utf-8")
    assert main(["verify-log", "--log", str(p), "--digest", str(d)]) == 0
    assert "internal consistency verified" in capsys.readouterr().out
    # tamper
    lines = p.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[0])
    rec["payload"]["a"] = 2
    p.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    assert main(["verify-log", "--log", str(p), "--digest", str(d)]) == 1


def test_run_scenario_refuses_without_api_key(monkeypatch, root, tmp_path, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = tmp_path / "kb"
    main(["build-kb", "--root", str(root), "--out", str(out)])
    sc = tmp_path / "sc.json"
    sc.write_text(json.dumps({"scenario_id": "S-T", "title": "t", "synthetic": True,
                              "steps": [{"step_id": "1", "text": "x", "addressees": ["adviser"]}]}), encoding="utf-8")
    assert main(["run-scenario", "--kb", str(out), "--scenario", str(sc), "--out", str(tmp_path / "run"),
                 "--runs-root", str(tmp_path)]) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err
    assert not (tmp_path / "run" / "log.jsonl").exists()


def test_run_scenario_offline_rehearsal_with_test_double_is_labelled(root, tmp_path, capsys):
    out = tmp_path / "kb"
    main(["build-kb", "--root", str(root), "--out", str(out)])
    sc = tmp_path / "sc.json"
    sc.write_text(json.dumps({"scenario_id": "S-T", "title": "t", "synthetic": True,
                              "steps": [{"step_id": "1", "text": "x", "addressees": ["adviser"]}]}), encoding="utf-8")
    run = tmp_path / "runs" / "rehearsal"
    assert main(["run-scenario", "--kb", str(out), "--scenario", str(sc), "--out", str(run), "--test-double",
                 "--runs-root", str(tmp_path / "runs")]) == 0
    printed = capsys.readouterr().out
    assert "TEST DOUBLE" in printed
    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    assert summary["test_double"] is True
    assert all("TEST-DOUBLE" in m["model_id"] for m in summary["models"].values())
    assert json.loads((run / "cost.json").read_text(encoding="utf-8"))["test_double"] is True


def test_probe_model_with_test_double_records_and_is_labelled(tmp_path, capsys):
    assert main(["probe-model", "--test-double", "--runs-root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "TEST DOUBLE" in out and "as configured: accepted" in out
    rec = json.loads(next((tmp_path / "_probe").rglob("probe.json")).read_text(encoding="utf-8"))
    assert rec["test_double"] is True and rec["accepted"] == "as configured"


def test_probe_uses_the_real_assessment_schema_so_a_schema_the_api_cannot_compile_shows_up_before_the_smoke_session(tmp_path):
    from gapcourt.audit import canonical
    from gapcourt.roles import ASSESSMENT_SCHEMA
    import hashlib

    assert main(["probe-model", "--test-double", "--runs-root", str(tmp_path)]) == 0
    rec = json.loads(next((tmp_path / "_probe").rglob("probe.json")).read_text(encoding="utf-8"))
    assert rec["schema_sha256"] == hashlib.sha256(canonical(ASSESSMENT_SCHEMA)).hexdigest()
    assert rec["schema"] == "ASSESSMENT_SCHEMA (the one every role call sends)"
