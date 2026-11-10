"""Offline retrieval check for the Arabic-only instruments (U01, U08).

Runs the author-made probe questions (evals/bilingual_probe_DRAFT.jsonl) and the dev-split labelled questions against a
built knowledge base with and without the bilingual retrieval path, and prints hit-at-5 and intrusion counts.
No model is called. The probe questions are the author's reading of the Arabic texts: they are not approved labels,
not part of any freeze, and a result here is a retrieval-machinery check, not an accuracy figure.
Held-out labelled lines are filtered out as text before any parsing, so no held-out question or label is loaded by this script.

    PYTHONPATH=src python scripts/bilingual_probe.py --kb build/kb --out results/bilingual_retrieval.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from gapcourt.index import KB
from gapcourt.retrieve import HybridRetriever

ARABIC_ONLY = ("U01", "U08")


def _retriever(kb, mode, bilingual):
    try:
        return HybridRetriever(kb, ["kb_uae"], k=5, mode=mode, bilingual=bilingual)
    except TypeError:  # code from before the bilingual path existed
        if bilingual:
            raise
        return HybridRetriever(kb, ["kb_uae"], k=5, mode=mode)


def run(kb, probes, dev_rows, bilingual):
    out = {"bilingual": bilingual, "probes": {}, "dev": {}}
    for mode in ("bm25", "hybrid"):
        r = _retriever(kb, mode, bilingual)
        hits = 0
        per = {}
        for p in probes:
            got = [(h.chunk.doc_id, h.chunk.locator) for h in r.search(p["question"])]
            ok = any((e["doc_id"], e["locator"]) in got for e in p["expected"])
            per[p["probe_id"]] = {"hit": ok, "top5": [f"{d} {loc}" for d, loc in got]}
            hits += int(ok)
        out["probes"][mode] = {"hit": hits, "n": len(probes), "by_probe": per}
        intr = 0
        dper = {}
        for row in dev_rows:
            got = [(h.chunk.doc_id, h.chunk.locator) for h in r.search(row["question"])]
            exp = {(e["doc_id"], e["locator"]) for e in row["expected"]}
            ok = bool(exp & set(got)) if exp else None
            n_ar = sum(1 for d, _ in got if d in ARABIC_ONLY)
            dper[row["case_id"]] = {"expected_found": ok, "arabic_only_in_top5": n_ar,
                                    "top5": [f"{d} {loc}" for d, loc in got]}
            intr += int(n_ar > 0)
        out["dev"][mode] = {"expected_found": sum(1 for v in dper.values() if v["expected_found"]),
                            "scored": sum(1 for v in dper.values() if v["expected_found"] is not None),
                            "questions_with_arabic_only_passage_in_top5": intr, "n_questions": len(dev_rows),
                            "by_case": dper}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", default="build/kb")
    ap.add_argument("--probes", default="evals/bilingual_probe_DRAFT.jsonl")
    ap.add_argument("--labels", default="evals/pilot_cases_DRAFT.jsonl")
    ap.add_argument("--out")
    ap.add_argument("--bilingual", choices=("on", "off", "both"), default="both")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    kb = KB.load(a.kb)
    probes = [json.loads(ln) for ln in Path(a.probes).read_text(encoding="utf-8").splitlines() if ln.strip()]
    # held-out lines are dropped before they are parsed: this script never loads a held-out question or label
    dev_rows = [json.loads(ln) for ln in Path(a.labels).read_text(encoding="utf-8").splitlines() if '"split": "dev"' in ln]
    dev_rows = [r for r in dev_rows if r["split"] == "dev"]
    res = {"kb_version": kb.version, "runs": {}}
    for flag in (("off", "on") if a.bilingual == "both" else (a.bilingual,)):
        res["runs"][flag] = run(kb, probes, dev_rows, flag == "on")
    for flag, r in res["runs"].items():
        for mode in ("bm25", "hybrid"):
            p, d = r["probes"][mode], r["dev"][mode]
            print(f"bilingual {flag:>3} {mode:>6}: probes {p['hit']} of {p['n']}; dev expected found {d['expected_found']} of "
                  f"{d['scored']}; dev questions with a U01/U08 passage in the top 5: "
                  f"{d['questions_with_arabic_only_passage_in_top5']} of {d['n_questions']}")
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
