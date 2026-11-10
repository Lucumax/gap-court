"""Release export: the public export, with the saved-run files listed in config/export_withheld.json left out.

Usage:  python scripts/release_export.py --out <new folder> [--commit] [--src .]

`gapcourt export-public` copies every saved live run whole. A run log can carry a model's verified quotation that is longer
than the export allows for link-only text (40 words); the export then refuses, as designed. This script stages the inputs
the exporter reads (root files, whitelisted folders, build/kb_public, runs, and corpus and corpus_local for the
quotation check) in a temporary folder, removes each withheld file after checking its sha256, writes a WITHHELD.md note
next to it (file, hash, reason; no quoted text), and runs the unchanged exporter on that folder. Every other rule of the
exporter still applies. The withheld files stay in the working repository and verify there with `gapcourt verify-log`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "src"))

from gapcourt.export import EXCLUDE_NAMES, ROOT_FILES, WHITELIST_DIRS, build_export  # noqa: E402

WITHHELD_FILE = Path("config") / "export_withheld.json"
STAGE_DIRS = tuple(WHITELIST_DIRS) + ("runs", "corpus_local")


class WithheldMismatch(RuntimeError):
    pass


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_withheld(src: Path) -> list[dict]:
    p = src / WITHHELD_FILE
    if not p.is_file():
        return []
    return json.loads(p.read_text(encoding="utf-8")).get("withheld", [])


def check_withheld(src: Path, entries: list[dict]) -> list[dict]:
    """The entries whose file exists, after checking each hash; a changed file stops the release."""
    present = []
    for e in entries:
        f = src / e["path"]
        if not f.is_file():
            continue
        got = _sha256(f)
        if got != e["sha256"]:
            raise WithheldMismatch(f"{e['path']}: sha256 {got[:12]} differs from the withheld record {e['sha256'][:12]}; review it")
        present.append(e)
    return present


def stage(src: Path, dest: Path, entries: list[dict]) -> None:
    ignore = shutil.ignore_patterns(*EXCLUDE_NAMES, "*.pyc")
    for name in ROOT_FILES:
        if (src / name).is_file():
            shutil.copy2(src / name, dest / name)
    for d in STAGE_DIRS:
        if (src / d).is_dir():
            shutil.copytree(src / d, dest / d, ignore=ignore)
    if (src / "build" / "kb_public").is_dir():
        shutil.copytree(src / "build" / "kb_public", dest / "build" / "kb_public", ignore=ignore)
    notes: dict[Path, list[dict]] = {}
    for e in entries:
        (dest / e["path"]).unlink()
        notes.setdefault((dest / e["path"]).parent, []).append(e)
    for folder, es in notes.items():
        lines = ["# Files withheld from the public export", "",
                 "These files of this saved run are kept in the working repository and left out here because they quote more",
                 "of a link-only source than the export allows (40 words). The other files of the run are unchanged; the full log",
                 "verifies against `digest.json` with `gapcourt verify-log` and is available on request.", "",
                 "| file | sha256 | reason |", "|---|---|---|"]
        lines += [f"| {Path(e['path']).name} | `{e['sha256']}` | {e['reason']} |" for e in es]
        (folder / "WITHHELD.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def release_export(src: str | Path, out: str | Path, commit: bool = False) -> dict:
    src = Path(src).resolve()
    entries = check_withheld(src, load_withheld(src))
    tmp = Path(tempfile.mkdtemp(prefix="gapcourt_stage_"))
    try:
        stage(src, tmp, entries)
        rep = build_export(tmp, out, commit=commit)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    rep["withheld"] = [e["path"] for e in entries]
    return rep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(HERE))
    ap.add_argument("--out", required=True)
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()
    rep = release_export(a.src, a.out, commit=a.commit)
    print(json.dumps({k: rep[k] for k in ("files", "out", "withheld", "placeholders", "commit") if k in rep}, indent=1))
    print("quotations of link-only text (files, longest run in words):",
          len(rep["quotations"]), max((q["longest_words"] for q in rep["quotations"].values()), default=0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
