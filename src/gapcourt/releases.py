"""KB releases: diff two versions, rerun every saved question, write a change receipt.

No citation-dependency index: after a release, every saved question and open gap is
rerun. Outcomes: unchanged, citation_changed (text hash differs, needs review),
reopened (cited passage gone), closed (an open gap now has supporting passages, for
analyst confirmation) and still_open.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .audit import canonical
from .index import KB
from .retrieve import AbstainBaseline, HybridRetriever


@dataclass
class SavedQuestion:
    qid: str
    query: str
    collections: list[str]
    status: str  # "answered" | "gap_open"
    cited: list[dict] = field(default_factory=list)  # {chunk_id, doc_id, locator, text_sha256}


def diff_kbs(old: KB, new: KB) -> dict:
    o = {r["doc_id"]: r["file_sha256"] for r in old.coverage_table()}
    n = {r["doc_id"]: r["file_sha256"] for r in new.coverage_table()}
    return {
        "old_version": old.version, "new_version": new.version,
        "added": sorted(set(n) - set(o)), "removed": sorted(set(o) - set(n)),
        "changed": sorted(d for d in set(o) & set(n) if o[d] != n[d]),
    }


def _find(kb: KB, doc_id: str, locator: str):
    return [c for c in kb.all_chunks() if c.doc_id == doc_id and c.locator == locator]


def rerun_all(old: KB, new: KB, questions: list[SavedQuestion], threshold: float = 0.35,
              synthetic: bool = False, k: int = 5) -> dict:
    counts = {"unchanged": 0, "citation_changed": 0, "reopened": 0, "closed": 0, "still_open": 0}
    results = []
    for q in questions:
        if q.status == "answered":
            outcome = "unchanged"
            for c in q.cited:
                found = _find(new, c["doc_id"], c["locator"])
                if not found:
                    outcome = "reopened"
                    break
                if all(f.text_sha256 != c["text_sha256"] for f in found):
                    outcome = "citation_changed"
            detail = {"cited": q.cited}
        else:
            base = AbstainBaseline(HybridRetriever(new, q.collections, k=k), threshold=threshold)
            ans = base.answer(q.query)
            known = {h.chunk.text_sha256 for h in HybridRetriever(old, q.collections, k=k).search(q.query)}
            fresh = [h for h in ans.passages if h.chunk.text_sha256 not in known]
            outcome = "closed" if (not ans.abstained and fresh) else "still_open"
            detail = {"support": round(ans.support, 4), "new_passages": [h.chunk.chunk_id for h in fresh]}
        counts[outcome] += 1
        results.append({"qid": q.qid, "outcome": outcome, **detail})
    body = {"diff": diff_kbs(old, new), "counts": counts, "results": results, "synthetic": synthetic,
            "threshold": threshold}
    return {**body, "receipt_sha256": hashlib.sha256(canonical(body)).hexdigest()}
