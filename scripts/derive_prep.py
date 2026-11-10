"""Derive corpus/PREP.json: the recorded transform for each corpus file that a generic extractor can reproduce.

Provenance. The Oct 27 corpus files were produced by throwaway extraction scripts that were not kept. This script
records what they did, in a form that can be re-run: for each candidate document it picks the extractor options
that leave the fewest differences from the existing file, writes the remaining differences as classified, guarded
patch hunks, rebuilds the file from its raw file and requires byte equality before it records anything.

A document whose differences would need source text stored in the patch (joined or rewritten lines of a link-only
or unknown-terms text) is not recorded here; it stays under the verbatim audit (`gapcourt prepare`).

Run from the repository root with the raw files in place:  python scripts/derive_prep.py
"""
from __future__ import annotations

import difflib
import itertools
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gapcourt.prepare import (  # noqa: E402
    PREP_FILE, _STRUCTURAL_LINE, _lsha, _front_matter, apply_patch, build_from_prep, corpus_files, extract_lines, find_raw,
)

HEADER_PATTERNS = {
    "U06": [r"^(Law No\. \(26\) of 2015 Regulating Data Dissemination and Exchange in the Emirate of Dubai|Page \d+ of 9)$"],
    "U11": [r"^(Law No\. \(24\) of 2023 Establishing the Dubai Data and Statistics Establishment|Page \d+ of 12)$"],
}
CANDIDATES = {
    "U06": "pymupdf_lines", "U11": "pymupdf_lines", "U08": "pymupdf_lines", "U15": "pymupdf_lines", "U16": "pymupdf_lines",
    "U12": "html_blocks", "U13": "html_blocks", "G6": "pymupdf_lines", "G7": "pymupdf_lines",
    "G5": "html_blocks", "G1": "html_blocks", "G4": "pymupdf_lines", "F1": "pymupdf_lines", "F2": "pymupdf_lines",
}


def split_file(text: str):
    assert text.startswith("---\n")
    end = text.index("\n---\n", 3)
    meta = [list(l.partition(": ")[::2]) if ": " in l else [l.rstrip(":"), ""] for l in text[4:end].split("\n")]
    body = text[end + 5:]
    assert body.endswith("\n")
    return meta, body[:-1].split("\n")


def classify(hunk_insert: list[str], deleted: int, deleted_text: str = "") -> str:
    if deleted:
        # a selection keeps some of the deleted lines (boundary lines) and adds structure; a rewrite adds new words
        kept = all(_STRUCTURAL_LINE.match(l) or len(l) <= 12 or not l.strip() or l.strip() in deleted_text for l in hunk_insert)
        return "excerpt_selection" if kept else "rewrite"
    if hunk_insert and all(l.startswith("### ") for l in hunk_insert):
        return "structural_heading"
    if hunk_insert and all(l.startswith("<!--page:") for l in hunk_insert):
        return "page_marker"
    if hunk_insert and all(l == "[...]" for l in hunk_insert):
        return "omission_mark"
    return "fragment"


def make_patch(a: list[str], b: list[str]) -> list[dict]:
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    patch = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        ins = b[j1:j2]
        h = {"at": i1, "delete": i2 - i1, "insert": ins, "class": classify(ins, i2 - i1, chr(10).join(a[i1:i2]))}
        if i2 > i1:
            h["delete_sha256"] = _lsha("\n".join(a[i1:i2]))
            h["deleted_lines"] = i2 - i1
        elif i1 < len(a):
            h["expect_before"] = _lsha(a[i1])
        patch.append(h)
    return patch


def main() -> int:
    prep = {"version": 1, "docs": {}, "accepted_edits": {}}
    existing = ROOT / PREP_FILE
    if existing.is_file():
        prep["accepted_edits"] = json.loads(existing.read_text(encoding="utf-8")).get("accepted_edits", {})
    for path in corpus_files(ROOT):
        text = path.read_text(encoding="utf-8")
        fm = _front_matter(text)
        doc = fm["doc_id"]
        if doc not in CANDIDATES:
            continue
        raw = find_raw(ROOT, doc, fm["sha256"].strip())
        if raw is None:
            print(f"{doc}: raw file missing; skipped")
            continue
        meta, body = split_file(text)
        extractor = CANDIDATES[doc]
        best = None
        for rs, db in itertools.product([True, False], [True, False]):
            opts = {"rstrip": rs, "drop_blank": db}
            if doc in HEADER_PATTERNS:
                opts["drop_regex"] = HEADER_PATTERNS[doc]
            if extractor == "html_blocks":
                opts = {}
            a = extract_lines(raw, extractor, opts)
            patch = make_patch(a, body)
            cost = (sum(1 for h in patch for l in h["insert"] if not _STRUCTURAL_LINE.match(l) and len(l) > 12), len(patch))
            if best is None or cost < best[0]:
                best = (cost, opts, patch)
            if extractor == "html_blocks":
                break
        _, opts, patch = best
        entry = {"path": path.relative_to(ROOT).as_posix(), "raw_name": raw.name, "extractor": extractor, "options": opts,
                 "meta": meta, "patch": patch}
        try:
            rebuilt = build_from_prep(entry, raw)
        except ValueError as exc:
            print(f"{doc}: not recorded ({exc})")
            continue
        if rebuilt != text:
            print(f"{doc}: rebuild differs; not recorded")
            continue
        counts = {}
        for h in patch:
            counts[h["class"]] = counts.get(h["class"], 0) + 1
        prep["docs"][doc] = entry
        print(f"{doc}: recorded {extractor} {opts} hunks {counts}")
    existing.parent.mkdir(exist_ok=True)
    existing.write_text(json.dumps(prep, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
