"""Record the offline facts the report cites: test counts and code version. Run before `gapcourt report-fill`.

Usage (repository root):  python scripts/offline_facts.py
Writes report/OFFLINE_FACTS.json. The numbers come from running the offline test suite now; nothing is estimated.
"""
from __future__ import annotations

import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=ROOT, capture_output=True, text=True)
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    passed = int((re.search(r"(\d+) passed", tail) or [0, 0])[1])
    failed = int((re.search(r"(\d+) failed", tail) or [0, 0])[1])
    skipped = int((re.search(r"(\d+) skipped", tail) or [0, 0])[1])
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, capture_output=True, text=True).stdout.strip())
    out = {"tests_passed": passed, "tests_failed": failed, "tests_skipped": skipped, "pytest_summary": tail,
           "code_version": sha + ("+dirty" if dirty else ""), "date": datetime.date.today().isoformat()}
    (ROOT / "report").mkdir(exist_ok=True)
    (ROOT / "report" / "OFFLINE_FACTS.json").write_text(json.dumps(out, indent=1) + chr(10), encoding="utf-8")
    print(json.dumps(out))
    return 0 if failed == 0 and proc.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
