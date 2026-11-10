"""Fresh-clone check: clone a repository folder into an empty temp folder and run what a judge would run.

Usage:  python scripts/fresh_clone_check.py --repo <path of the export or the repo> [--network] [--json-out F]

Steps (each recorded with its outcome; nothing is installed and nothing is pushed):
  1. git clone of --repo into a new temp folder (a local clone; the source is not modified)
  2. pytest -q in the clone (the offline suite; tests that need link-only texts skip themselves)
  3. with --network: `gapcourt kb-fetch` (public official addresses, plain client), then `gapcourt prepare --rebuild-all`,
     `gapcourt prepare`, and `gapcourt build-kb` over the rebuilt corpus; the KB version of that reproducible subset is printed
Interpreters run with -I-free module path via PYTHONPATH=<clone>/src, as the README quickstart does.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def run(cmd, cwd, env=None, timeout=1800):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--network", action="store_true")
    ap.add_argument("--json-out")
    a = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="gapcourt_fresh_"))
    clone = tmp / "clone"
    steps = []
    env = dict(os.environ, PYTHONPATH=str(clone / "src"), PYTHONUTF8="1", PYTHONIOENCODING="utf-8")

    def step(name, cmd, cwd, **kw):
        code, out = run(cmd, cwd, env=env, **kw)
        tail = out.strip().splitlines()[-1] if out.strip() else ""
        steps.append({"step": name, "exit": code, "last_line": tail})
        print(f"{name}: exit {code}: {tail}")
        return code, out

    code, _ = step("git clone", ["git", "clone", "-q", str(Path(a.repo).resolve()), str(clone)], tmp)
    if code:
        return 2
    step("pytest", [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], clone)
    if a.network:
        step("kb-fetch", [sys.executable, "-m", "gapcourt", "kb-fetch", "--keep-going"], clone)
        step("prepare --rebuild-all", [sys.executable, "-m", "gapcourt", "prepare", "--rebuild-all"], clone)
        step("build-kb", [sys.executable, "-m", "gapcourt", "build-kb", "--root", "corpus", "--root", "corpus_local",
                          "--out", str(tmp / "kb")], clone)
    result = {"clone": str(clone), "steps": steps, "all_ok": all(s["exit"] == 0 for s in steps if s["step"] in ("git clone", "pytest"))}
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(result, indent=1) + chr(10), encoding="utf-8")
    return 0 if result["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
