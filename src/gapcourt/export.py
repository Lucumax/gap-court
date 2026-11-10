"""Clean public export: a whitelist copy of the repository with a fresh single-commit history and no remote.

Nothing is copied unless it is on the whitelist below. Link-only text (corpus_local, the full knowledge base build), keys, the
private key-window helper, rehearsal output and internal planning documents never leave. Before anything is written the
whitelisted text files are scanned for secrets, internal paths, references to other entries and e-mail addresses; a finding
stops the export and names file, line and rule, never the matched text of a secret.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT_FILES = ("README.md", "LICENSE", "NOTICE", "NOT_DONE.md", "pyproject.toml", ".gitignore", ".gitattributes")
WHITELIST_DIRS = ("src", "tests", "scripts", "config", "scenarios", "dossiers", "evals", "docs", "report", "corpus", "third_party", "results")
EXCLUDE_NAMES = {"__pycache__", ".pytest_cache", ".venv", ".git", ".env"}
EXCLUDE_FILES = {"scripts/walter_key_window.ps1", "report/TECHNICAL_REPORT_TEMPLATE.md"}
EXCLUDE_PREFIXES = ("docs/SESSION_REPORT", "docs/PANEL", "docs/ELITE", "docs/INTERNAL", "report/REPORT_v")  # the rendered report is a submission file, not repository content
PUBLIC_WALTER_SCRIPTS = {"scripts/walter_live_runs.ps1"}  # the documented live-run script ships; every other scripts/walter_*.ps1 is a private helper
ALLOWED_BINARY_SUFFIXES = {".png", ".npy"}  # an image and numeric vectors; neither can hold a key in readable form
CREDENTIAL_NAME = re.compile(r"(?i)^\.env|\.(?:key|pem|p12|pfx|kdbx|token|jks)$|^id_(?:rsa|dsa|ed25519|ecdsa)|credential|secret|passw(?:or)?d")
EXPORT_NAME = "Lucumax"  # the public account the exported history is committed under (set per command, never in a git config file)
EXPORT_EMAIL = "242802005+Lucumax@users.noreply.github.com"
MAX_QUOTE_WORDS = 40  # a quotation longer than this from link-only text is a passage, not a verification quote
SHINGLE = 8
TEXT_SUFFIXES = {".md", ".py", ".txt", ".json", ".jsonl", ".toml", ".yml", ".yaml", ".ps1", ".cfg", ".html", ".gitignore", ".gitattributes", ""}

SECRET_RULES = [
    ("secret", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("secret", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
    ("secret", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("secret", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("secret", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("secret", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("secret", re.compile(r"(?i)\b(?:api[_-]?key|secret|token|password)\b\s*[:=]\s*['\"][A-Za-z0-9_\-/+=]{20,}['\"]")),
]
OTHER_RULES = [
    ("internal-path", re.compile(r"(?i)[A-Za-z]:[\\/]+(?:Walter|Users)\b|/c/(?:Walter|Users)\b|/Users/[a-z]+/|/home/[a-z][\w.-]*/")),
    ("other-entry", re.compile(r"\b(?:Meta|PayPal|Airwallex|Kaggle|Kiroween|Amazon Nova)\b")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+\-]+@(?!(?:users\.noreply\.github\.com|example\.invalid|itu\.int|anthropic\.com)(?![A-Za-z0-9\-]|\.[A-Za-z0-9]))[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
]
# files that define or test the scanner contain the patterns on purpose; the exemption names the rules, not the file
EXEMPT = {
    "src/gapcourt/export.py": {"other-entry", "email", "internal-path"},
    "tests/test_export.py": {"other-entry", "email", "internal-path"},
    "tests/test_repo_lint.py": {"internal-path"},
}
# vendored third-party files keep their authors' wording; only secrets are checked there
THIRD_PARTY_ONLY_SECRETS = ("third_party/",)


class ExportBlocked(RuntimeError):
    pass


def scan_text(text: str, name: str, secrets_only: bool = False) -> list[dict]:
    rules = SECRET_RULES if secrets_only else SECRET_RULES + OTHER_RULES
    exempt = EXEMPT.get(name, set())
    out = []
    for i, line in enumerate(text.splitlines(), start=1):
        for rule, rx in rules:
            if rule not in exempt and rx.search(line):
                out.append({"file": name, "line": i, "rule": rule})
    return out


def _is_text(p: Path) -> bool:
    return p.suffix.lower() in TEXT_SUFFIXES


def _tokens(text: str) -> list[tuple[str, int]]:
    """(lower-case alphanumeric token, line number)"""
    out = []
    for n, line in enumerate(text.splitlines(), start=1):
        out += [(t, n) for t in re.findall(r"[a-z0-9]+", line.lower())]
    return out


def _restricted_shingles(src: Path) -> set[tuple[str, ...]]:
    """Eight-word windows of every corpus body that is not republishable. Empty in a clone without link-only text."""
    sh: set[tuple[str, ...]] = set()
    for tree in ("corpus", "corpus_local"):
        for p in sorted((src / tree).glob("*/*.md")) if (src / tree).is_dir() else []:
            text = p.read_text(encoding="utf-8", errors="replace")
            if not text.startswith("---"):
                continue
            m = re.search(r"^republish:\s*(\S+)", text, re.M)
            if m and m.group(1) == "yes":
                continue
            toks = [t for t, _ in _tokens(text.split("\n---", 1)[-1])]
            sh.update(tuple(toks[i:i + SHINGLE]) for i in range(len(toks) - SHINGLE + 1))
    return sh


def _overlap_runs(text: str, shingles: set) -> list[tuple[int, int]]:
    """(words, first line) of each maximal run of consecutive eight-word windows found in the restricted set."""
    toks = _tokens(text)
    runs, cur, start = [], 0, 0
    for i in range(len(toks) - SHINGLE + 1):
        if tuple(t for t, _ in toks[i:i + SHINGLE]) in shingles:
            if cur == 0:
                start = toks[i][1]
            cur += 1
        elif cur:
            runs.append((cur + SHINGLE - 1, start))
            cur = 0
    if cur:
        runs.append((cur + SHINGLE - 1, start))
    return runs


def _excluded(rel: str) -> bool:
    parts = rel.split("/")
    name = parts[-1].lower()
    private_script = rel.startswith("scripts/") and (
        (name.startswith("walter_") and name.endswith(".ps1") and rel not in PUBLIC_WALTER_SCRIPTS) or "key_window" in name or name.startswith("private"))
    return (any(x in EXCLUDE_NAMES or x.endswith(".pyc") for x in parts) or rel in EXCLUDE_FILES
            or any(rel.startswith(p) for p in EXCLUDE_PREFIXES)
            or any(x.upper().startswith("INTERNAL") for x in parts)  # internal notes, wherever they sit
            or private_script)


def _live_run_dirs(runs: Path) -> list[Path]:
    keep = []
    for sj in sorted(runs.rglob("summary.json")):
        try:
            s = json.loads(sj.read_text(encoding="utf-8"))
        except Exception:
            continue
        if s.get("test_double") or (s.get("cost") or {}).get("test_double"):
            continue
        keep.append(sj.parent)
    return keep


def _collect(src: Path) -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    for name in ROOT_FILES:
        if (src / name).is_file():
            files.append((src / name, name))
    for d in WHITELIST_DIRS:
        base = src / d
        if base.is_dir():
            for p in sorted(base.rglob("*")):
                rel = p.relative_to(src).as_posix()
                if p.is_file() and not _excluded(rel):
                    files.append((p, rel))
    pub = src / "build" / "kb_public"
    if pub.is_dir():
        for p in sorted(pub.rglob("*")):
            if p.is_file() and not _excluded(p.relative_to(src).as_posix()):
                files.append((p, "kb_release/" + p.relative_to(pub).as_posix()))
    runs = src / "runs"
    if runs.is_dir():
        for d in _live_run_dirs(runs):
            for p in sorted(d.rglob("*")):
                rel = p.relative_to(src).as_posix()
                if p.is_file() and not _excluded(rel):
                    files.append((p, rel))
        for p in sorted(runs.glob("_probe/*/*.json")):
            try:
                if json.loads(p.read_text(encoding="utf-8")).get("test_double"):
                    continue
            except Exception:
                continue
            files.append((p, p.relative_to(src).as_posix()))
    return files


def build_export(src: str | Path, out: str | Path, commit: bool = False) -> dict:
    src, out = Path(src), Path(out)
    files = _collect(src)
    findings: list[dict] = []
    shingles = _restricted_shingles(src)
    quotations: dict[str, dict] = {}
    placeholders: dict[str, int] = {}
    for p, rel in files:
        if CREDENTIAL_NAME.search(p.name):
            findings.append({"file": rel, "line": 0, "rule": "credential-file (the name looks like a credential or key file)"})
        if not _is_text(p) and p.suffix.lower() not in ALLOWED_BINARY_SUFFIXES:
            findings.append({"file": rel, "line": 0, "rule": "unscanned-file-type (add a scanner or leave it out)"})
        if rel.startswith("corpus/") and rel.endswith(".md"):
            m = re.search(r"^republish:\s*(\S+)", p.read_text(encoding="utf-8", errors="replace"), re.M)
            if rel.count("/") >= 2 and (not m or m.group(1) != "yes"):
                findings.append({"file": rel, "line": 0, "rule": "link_only text in a public folder"})
        if _is_text(p):
            text = p.read_text(encoding="utf-8", errors="replace")
            findings += scan_text(text, rel, secrets_only=rel.startswith(THIRD_PARTY_ONLY_SECRETS))
            if "[WALTER:" in text and not rel.startswith(("src/", "tests/")):
                placeholders[rel] = text.count("[WALTER:")
            if shingles and not rel.startswith(("corpus/", "third_party/", "kb_release/")):
                runs = _overlap_runs(text, shingles)
                for words, line in runs:
                    if words > MAX_QUOTE_WORDS:
                        findings.append({"file": rel, "line": line, "rule": f"restricted-passage ({words} words of link-only text)"})
                if runs:
                    quotations[rel] = {"runs": len(runs), "longest_words": max(w for w, _ in runs)}
    if findings:
        lines = [f"{f['file']}:{f['line']}: {f['rule']}" for f in findings[:40]]
        raise ExportBlocked(f"{len(findings)} finding(s); nothing was written:\n" + "\n".join(lines))
    if out.exists():
        raise ExportBlocked(f"{out} already exists; choose a new folder (the export never overwrites)")
    for p, rel in files:
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dest)
    report = {"files": len(files), "out": str(out), "quotations": quotations, "placeholders": placeholders}
    if commit:
        env = {**os.environ, "GIT_AUTHOR_NAME": EXPORT_NAME, "GIT_AUTHOR_EMAIL": EXPORT_EMAIL,
               "GIT_COMMITTER_NAME": EXPORT_NAME, "GIT_COMMITTER_EMAIL": EXPORT_EMAIL}
        run = lambda *a: subprocess.run(["git", "-C", str(out), *a], capture_output=True, text=True, check=True, env=env)  # noqa: E731
        run("init", "-q", "-b", "main")
        run("add", "-A")
        if (out / "runs").is_dir():
            run("add", "-f", "--", "runs")  # the copied .gitignore lists runs/ for later local work; the live runs exported here are evidence
        run("commit", "-q", "-m", "Gap Court: clean public export" + chr(10) + chr(10) + "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>")
        report["commit"] = run("rev-parse", "--short", "HEAD").stdout.strip()
    return report
