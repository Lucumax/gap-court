"""The one-command live-run script for the operator's window: sessions, guards, and the report filling itself afterwards."""
from __future__ import annotations

import json
import re
import sys
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "walter_live_runs.ps1"
PWSH = shutil.which("pwsh") or shutil.which("powershell")


def test_script_has_a_full_session_a_report_session_and_still_holds_no_key():
    text = SCRIPT.read_text(encoding="utf-8")
    for session in ("probe", "smoke", "dev", "heldout", "repeats", "status", "full", "report"):
        assert f"'{session}'" in text, session
    assert "sk-ant" not in text and not re.search(r"\$env:ANTHROPIC_API_KEY\s*=", text)
    assert "report-fill" in text and "build-kb" in text


def test_full_session_refuses_without_a_freeze_and_names_the_freeze_command():
    text = SCRIPT.read_text(encoding="utf-8")
    full = text[text.index("'full' {"):]
    assert "FREEZE" in full.upper() and "gapcourt freeze" in text and "--attest-independent" in text
    # order inside the full session: dev, heldout, repeats, then score, then the report
    seg = full[:full.index("\n    }\n")] if "\n    }\n" in full else full
    positions = [seg.find(k) for k in ("Run-Dev", "Run-Heldout", "Run-Repeats", "Run-Score", "Update-Report")]
    assert all(p >= 0 for p in positions) and positions == sorted(positions)


@pytest.mark.skipif(PWSH is None or not (ROOT / "corpus_local" / "uae" / "U06.md").exists(), reason="PowerShell or the UAE corpus not available")
def test_smoke_rehearsal_runs_offline_and_writes_a_report_that_ignores_test_doubles():
    out = ROOT / "runs_rehearsal" / "REPORT_DRAFT_REHEARSAL.md"
    out.unlink(missing_ok=True)
    proc = subprocess.run([PWSH, "-NoProfile", "-File", str(SCRIPT), "-Session", "smoke", "-Rehearsal"],
                          cwd=ROOT, capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stdout[-1500:] + proc.stderr[-1500:]
    assert "REHEARSAL" in proc.stdout and out.exists()
    text = out.read_text(encoding="utf-8")
    assert "pending-live" in text and "TEST DOUBLE" in text
    # The rehearsal never measures anything from its own test-double runs. Once a real scored run exists (after the live
    # session), the draft it refreshes may carry that real score: every run a [measured ...] marker names must then be a
    # saved real run under runs/, never a rehearsal run.
    section = text.split("Evidence index")[0].split("Development session")[1]
    for marker in re.findall(r"\[measured:[^\]]*\]", section):
        names = re.findall(r"([\w./-]+)@[0-9a-f]{12}", marker)
        assert names, marker
        for name in names:
            summary = ROOT / "runs" / name / "summary.json"
            assert summary.is_file(), f"{name} is not a saved run under runs/"
            s = json.loads(summary.read_text(encoding="utf-8"))
            assert not s.get("test_double") and not (s.get("cost") or {}).get("test_double"), name


@pytest.mark.skipif(PWSH is None, reason="PowerShell not available")
def test_live_session_without_a_key_stops_with_a_plain_message():
    env = {k: v for k, v in __import__("os").environ.items() if k not in ("ANTHROPIC_API_KEY", "MODEL_API_KEY", "OPENAI_API_KEY")}
    proc = subprocess.run([PWSH, "-NoProfile", "-File", str(SCRIPT), "-Session", "smoke"], cwd=ROOT, capture_output=True,
                          text=True, env=env, timeout=120)
    assert proc.returncode == 2 and "ANTHROPIC_API_KEY is not set" in proc.stdout


# ---- Session 3: rehearsal of the live path found three plumbing hazards ---------------------------------------

def _write_probe(runs, name, accepted, test_double=False, current_schema=True):
    from gapcourt.audit import canonical
    from gapcourt.roles import ASSESSMENT_SCHEMA
    import hashlib

    d = runs / "_probe" / name
    d.mkdir(parents=True)
    rec = {"accepted": accepted, "test_double": test_double}
    if current_schema:  # probes made before Session 3 used a tiny stand-in schema and carry no schema hash
        rec["schema_sha256"] = hashlib.sha256(canonical(ASSESSMENT_SCHEMA)).hexdigest()
    (d / "probe.json").write_text(json.dumps(rec), encoding="utf-8")


def test_probe_status_is_true_only_for_an_accepted_real_probe(tmp_path, capsys):
    from gapcourt.cli import main

    runs = tmp_path / "runs"
    assert main(["probe-status", "--runs-root", str(runs)]) == 1  # nothing yet
    _write_probe(runs, "a", None)  # the first probe found no variant the API accepts
    assert main(["probe-status", "--runs-root", str(runs)]) == 1
    _write_probe(runs, "b", "as configured", test_double=True)
    assert main(["probe-status", "--runs-root", str(runs)]) == 1
    _write_probe(runs, "old", "as configured", current_schema=False)  # accepted, but with the old tiny schema
    assert main(["probe-status", "--runs-root", str(runs)]) == 1
    _write_probe(runs, "c", "as configured")
    assert main(["probe-status", "--runs-root", str(runs)]) == 0


def test_preflight_names_what_is_missing_and_passes_here(capsys):
    from gapcourt.cli import main

    assert main(["preflight"]) == 0
    out = capsys.readouterr().out
    assert "python" in out.lower() and "anthropic" in out.lower() and "numpy" in out.lower()


def test_the_script_asks_probe_status_and_preflight_instead_of_looking_for_a_file():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "probe-status" in text and "preflight" in text
    assert "-Filter probe.json" not in text  # a failed probe also leaves a probe.json


WINPS = shutil.which("powershell")


@pytest.mark.skipif(WINPS is None or not sys.platform.startswith("win"), reason="Windows PowerShell 5.1 not available")
def test_a_cap_stop_written_to_stderr_does_not_abort_the_script_under_windows_powershell(tmp_path):
    # In Windows PowerShell 5.1, `*>&1 | Tee-Object` turns a native command's stderr line into an error record that
    # stops a script run with $ErrorActionPreference = 'Stop'. The script must still print its own cap message.
    fake = tmp_path / "fakepython.cmd"
    # answers the preflight normally; the run-cases call stops on a cap, with the message on stderr as gapcourt does
    fake.write_text("@echo off\r\necho %* | findstr /c:\"run-cases\" >nul\r\nif errorlevel 1 exit /b 0\r\n"
                    "echo run stopped: run cap reached: simulated 1>&2\r\nexit /b 3\r\n", encoding="ascii")
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "KB_MANIFEST.json").write_text("{}", encoding="utf-8")
    cmd = (f"& '{SCRIPT}' -Session dev -Rehearsal -Kb '{kb}' -Python '{fake}' *>&1 | Tee-Object -FilePath '{tmp_path / 'tee.txt'}'; "
           "exit $LASTEXITCODE")
    proc = subprocess.run([WINPS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd], cwd=ROOT,
                          capture_output=True, text=True, timeout=120)
    out = proc.stdout + proc.stderr
    assert "A cost cap was reached" in out, out[-1500:]
    assert "NativeCommandError" not in out
    assert proc.returncode == 3
