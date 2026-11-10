"""Labels, hashing, freeze records and retrieval scoring.

Results are reported as `hit` of `n` with denominators. Nothing here says "accuracy".
Labels carry a status; DRAFT_UNAPPROVED labels may be used to test the machinery but
not to report results.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .audit import canonical
from .index import KB
from .retrieve import HybridRetriever

SPLITS = ("dev", "heldout")
STATUSES = ("DRAFT_UNAPPROVED", "APPROVED", "CORRECTED")
CATEGORIES = ("answerable", "paraphrase_trap", "missing_document", "ambiguity", "candidate_gap", "applicability_trap")
OUTCOMES = ("ANSWER", "ABSTAIN", "ESCALATE", "GAP", "SCOPE_FINDING")


class EvalError(ValueError):
    pass


def load_labels(path: str | Path) -> list[dict]:
    rows = []
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        if r.get("split") not in SPLITS:
            raise EvalError(f"{r.get('case_id')}: split must be one of {SPLITS}")
        if r.get("status") not in STATUSES:
            raise EvalError(f"{r.get('case_id')}: status must be one of {STATUSES}")
        if r.get("category") not in CATEGORIES:
            raise EvalError(f"{r.get('case_id')}: category must be one of {CATEGORIES}")
        if r.get("expected_outcome") not in OUTCOMES:
            raise EvalError(f"{r.get('case_id')}: expected_outcome must be one of {OUTCOMES}")
        rows.append(r)
    return rows


def labels_sha256(path: str | Path) -> str:
    rows = [json.loads(ln) for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]
    return hashlib.sha256(canonical(rows)).hexdigest()


def freeze_record(labels_path, *, kb_version: str, top_k: int, fusion: dict, abstain_threshold: float,
                  model_config: dict, code_version: str) -> dict:
    body = {"labels_sha256": labels_sha256(labels_path), "kb_version": kb_version, "top_k": top_k,
            "fusion": fusion, "abstain_threshold": abstain_threshold, "model_config": model_config,
            "code_version": code_version}
    return {**body, "freeze_sha256": hashlib.sha256(canonical(body)).hexdigest()}


def freeze_is_intact(freeze: dict) -> bool:
    body = {k: v for k, v in freeze.items() if k != "freeze_sha256"}
    return hashlib.sha256(canonical(body)).hexdigest() == freeze.get("freeze_sha256")


def code_tree_sha256(root: str | Path | None = None) -> str:
    """Hash of the package source (every .py file under `root`, default the gapcourt package, line endings normalised).
    It shows whether the code that ran is the code that was frozen even when later commits only touched documents."""
    base = Path(root) if root else Path(__file__).resolve().parent
    h = hashlib.sha256()
    sep = bytes([0])
    for p in sorted(base.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        h.update(p.relative_to(base).as_posix().encode("utf-8") + sep)
        h.update(p.read_bytes().replace(bytes([13, 10]), bytes([10])) + sep)
    return h.hexdigest()


def code_drift_problem(freeze: dict, run: dict) -> str | None:
    """Why a run's code cannot be shown to be the frozen code, or None. `run` is a run summary (or anything with the same keys)."""
    ft, rt = freeze.get("code_tree_sha256"), run.get("code_tree_sha256")
    if ft and rt:
        return None if ft == rt else f"the code tree differs from the frozen one (freeze {ft[:12]}, run {rt[:12]})"
    if ft and not rt:
        return "the run records no code tree hash, so its code cannot be compared with the frozen code"
    fv, rv = freeze.get("code_version"), run.get("code_version")
    if fv and rv and fv == rv and "dirty" not in str(rv):
        return None
    return (f"the code version differs from the frozen one (freeze {fv}, run {rv}) and no code tree hash was recorded "
            "to show the code is unchanged")


def applicability_problem(freeze: dict, run: dict) -> str | None:
    """Why a run's applicability approvals are not the frozen ones, or None. Compared before held-out calls and when scoring;
    a missing hash on either side that the other side has counts as a mismatch."""
    fa, ra = freeze.get("applicability_sha256"), run.get("applicability_sha256")
    if fa in (None, "", "missing"):
        return "the freeze record carries no applicability approvals hash; make the freeze again after approving the records"
    if fa == ra:
        return None
    return (f"the applicability approvals differ from the frozen ones (freeze {str(fa)[:12]}, run {str(ra)[:12]}); "
            "held-out work uses the frozen corpus/APPLICABILITY.json only")


def freeze_problem(freeze: dict | None, labels_sha: str | None = None, kb_version: str | None = None) -> str | None:
    """Why a freeze record cannot admit or score held-out work, or None. One place for every entry point."""
    if not freeze:
        return "there is no freeze record (run gapcourt freeze first)"
    if not freeze_is_intact(freeze):
        return "the freeze record does not match its own freeze_sha256"
    if not (freeze.get("reviewer") or "").strip() or freeze.get("reviewed_without_model_answers") is not True:
        return ("the freeze record carries no reviewer attestation (labels reviewed from source passages without seeing "
                "model answers); make it with gapcourt freeze --attest-independent")
    if not freeze.get("heldout_case_ids"):
        return "the freeze record lists no held-out cases"
    if labels_sha is not None and freeze.get("labels_sha256") != labels_sha:
        return "the label file hash differs from the freeze record; held-out work uses the frozen labels only"
    if kb_version is not None and freeze.get("kb_version") != kb_version:
        return (f"the knowledge base version {kb_version} differs from the frozen one {freeze.get('kb_version')}; "
                "held-out work uses the frozen knowledge base only")
    return None


def score_retrieval(kb: KB, labels: list[dict], collections: list[str], mode: str = "hybrid", k: int = 5,
                    split: str | None = None, require_approved: bool = False, **retr_kw) -> dict:
    rows = [r for r in labels if split is None or r["split"] == split]
    if require_approved and any(r["status"] == "DRAFT_UNAPPROVED" for r in rows):
        raise EvalError("unapproved labels cannot be used to report results")
    retr = HybridRetriever(kb, collections, k=k, mode=mode, **retr_kw)
    out: dict = {"config": {**retr.config(), "split": split}}
    n_scored = 0
    for r in rows:
        if not r["expected"]:
            continue
        hits = retr.search(r["question"])
        got = {(h.chunk.doc_id, h.chunk.locator) for h in hits}
        ok = any((e["doc_id"], e["locator"]) in got for e in r["expected"])
        cat = out.setdefault(r["category"], {"hit": 0, "n": 0})
        cat["n"] += 1
        cat["hit"] += int(ok)
        n_scored += 1
    out["n_scored"] = n_scored
    return out
