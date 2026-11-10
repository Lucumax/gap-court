#!/usr/bin/env python3
"""Repository text lint for the entry's writing rules (adapted idea of submission_lint.py).

Usage: python scripts/repo_lint.py [--root .] [--allow-markers]
Exit 0 clean, 1 findings. Standard library only. Verbatim source text (corpus, fixtures, licence) is skipped.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

SKIP_PARTS = ("corpus/", "corpus_local/", "fixtures_synthetic/", "LICENSE", ".git/", "scripts/repo_lint.py", "third_party/", "kb_release/",
              "src/gapcourt/itu_adapter.py", "report/TECHNICAL_REPORT_TEMPLATE.md",
              "tests/test_repo_lint.py", "docs/CORPUS_FORMAT.md")
TEXT_SUFFIXES = {".md", ".py", ".txt", ".json", ".jsonl", ".toml", ".yml", ".yaml"}

SPELLED = (r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
           r"sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|several|many|decades?")
RULES = [
    ("dash", re.compile("[–—]")),
    ("years", re.compile(r"\b\d+\+?\s+years?\b|\b(?:%s)\s+years\b|\byears?\s+of\s+experience\b" % SPELLED, re.I)),
    ("country-recommendation", re.compile(r"\bthe\s+UAE\s+(should|must|ought|needs?\s+to)\b", re.I)),
    ("location", re.compile(r"\b(China|Chinese|Beijing|Shanghai|VPN|airport|itinerary|travel(l?ing)?)\b", re.I)),
    ("internal-path", re.compile(r"[A-Za-z]:\\\\?(Walter|Users)\b")),
    ("marker", re.compile(r"\bTODO(-[WR])?\b|\bTBD\b|\bCONFIRM\b")),
    ("overclaim", re.compile(
        r"\b(compliant|certified|endorsed by|guarantees?|first[- ]of[- ]its[- ]kind|world['’]?s first|"
        r"first[- ]ever|accurate|accuracy of)\b", re.I)),
]
# phrases where an overclaim word appears only to disclaim it
NEGATING = re.compile(r"\b(not|no|never|nor|without|does not|do not|cannot|isn't|neither|unless)\b", re.I)


def lint_text(text: str, name: str) -> list[dict]:
    norm = name.replace("\\", "/")
    if any(p in norm for p in SKIP_PARTS):
        return []
    out = []
    live_run = norm.startswith("runs/") or "/runs/" in norm  # sealed live-run records hold model output verbatim
    for i, line in enumerate(text.splitlines(), start=1):
        for rule, rx in RULES:
            if rule == "dash" and live_run:
                continue  # the dash rule governs authored prose; a run record is hash-sealed and cannot be edited
            m = rx.search(line)
            if not m:
                continue
            if rule == "overclaim" and NEGATING.search(line):
                continue
            out.append({"file": name, "line": i, "rule": rule, "text": m.group(0)})
    return out


def tracked_files(root: Path) -> list[Path]:
    try:
        res = subprocess.run(["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard"],
                             capture_output=True, text=True, check=True)
        return [root / p for p in res.stdout.splitlines()]
    except Exception:
        return [p for p in root.rglob("*") if p.is_file()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--allow-markers", action="store_true", help="skip TODO markers (drafts only)")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    findings = []
    for f in tracked_files(root):
        if f.suffix.lower() not in TEXT_SUFFIXES or not f.exists():
            continue
        rel = str(f.relative_to(root)).replace("\\", "/")
        findings += lint_text(f.read_text(encoding="utf-8", errors="replace"), rel)
    if a.allow_markers:
        findings = [x for x in findings if x["rule"] != "marker"]
    for x in findings:
        print(f"{x['file']}:{x['line']}: [{x['rule']}] {x['text']!r}")
    print(f"{len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
