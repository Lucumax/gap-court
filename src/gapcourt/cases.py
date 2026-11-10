"""Pilot-case runs: each labelled question through the full system (adviser role plus gate) and through the
retrieval-plus-abstain baseline (no roles, no gate). Saved like a scenario run so `gapcourt score` can
recompute every row from the folder alone.

Only the question text reaches a prompt; labels stay in the label file. A held-out run needs a freeze
record that matches the label file (see the freeze rules in `gapcourt.score`).
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .audit import AuditLog
from .eval import EvalError
from .index import KB
from .retrieve import AbstainBaseline, HybridRetriever
from .roles import REJECTED, RoleAgent, RoleSpec

CASE_COLLECTIONS = ["kb_uae"]
CASE_ADVISER = RoleSpec(
    name="adviser", page_role="Policy and legal adviser", collections=CASE_COLLECTIONS, focus="",
    instructions=(
        "An analyst asks one question about the reviewed instruments; there is no simulated journey step. "
        "Answer ALLOW or DENY only when a quoted passage from a binding instrument answers the question: ALLOW when "
        "the passages permit what is asked or state the answer, DENY when they prohibit it. If an instrument's scope "
        "excludes the actor asked about, record that in applicability with applies set to no. Answer ESCALATE if the "
        "passages disagree or are unclear for this actor. Answer GAP if the reviewed instruments cover the subject but no "
        "passage states a rule on the point asked. Answer ABSTAIN if the question is about a subject, instrument or "
        "authority that none of the listed instruments concerns (the document that would govern it is not among them)."))


def reviewed_instruments_line(kb: KB) -> str:
    """The instruments a case question is answered against, so the model can tell ABSTAIN (not among them) from GAP (among
    them, no rule). Bibliographic titles only; the first live run (PC11) answered GAP for a question no listed instrument covers."""
    docs = [d for d in kb.manifest()["documents"] if d.get("collection") in CASE_COLLECTIONS]
    items = "; ".join(f"{d['doc_id']}: {str(d.get('title', '')).strip()[:140]}" for d in docs)
    return f"Reviewed instruments (the only ones this question is answered against): {items}."


def outcome_of(decision: str, scope_findings: list) -> str:
    """Fixed mapping from the final decision to a label outcome (ANSWER, ABSTAIN, ESCALATE, GAP, SCOPE_FINDING).
    The decision decides: ALLOW or DENY is ANSWER and ESCALATE is ESCALATE whatever scope findings accompany it (they stay in the
    record); a verified scope finding turns only a GAP or ABSTAIN into SCOPE_FINDING."""
    if decision == REJECTED:
        return REJECTED
    if decision in ("ALLOW", "DENY"):
        return "ANSWER"
    if scope_findings and decision != "ESCALATE":
        return "SCOPE_FINDING"
    return decision


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def check_freeze_for_heldout(freeze: dict | None, labels_sha: str, kb_version: str | None = None,
                             run: dict | None = None) -> None:
    """`run` carries the code and approvals the run will use ({"code_tree_sha256", "code_version", "applicability_sha256"});
    they must be the frozen ones."""
    from .eval import applicability_problem, code_drift_problem, freeze_problem

    why = freeze_problem(freeze, labels_sha, kb_version)
    if not why and run is not None:
        why = code_drift_problem(freeze, run) or applicability_problem(freeze, run)
    if why:
        raise EvalError(f"a held-out run needs an attested freeze made before the run: {why}")


def cases_from_records(recs: list[dict], labels: dict[str, dict]) -> list[dict]:
    """Per-case outcomes rebuilt from the hash-chained log alone (the cases.jsonl sidecar is not covered by the digest)."""
    baseline = {r["payload"]["case_id"]: r["payload"] for r in recs
                if r.get("kind") == "event" and r.get("event") == "baseline"}
    out = []
    for r in recs:
        if r.get("kind") != "step":
            continue
        cid = (r.get("input") or {}).get("case_id")
        if cid is None or cid not in baseline:
            continue
        gate = r.get("gate") or {}
        out.append({"case_id": cid, "category": (labels.get(cid) or {}).get("category"),
                    "system_outcome": outcome_of(r.get("decision"), gate.get("scope_findings") or []),
                    "decision": r.get("decision"), "baseline_outcome": baseline[cid]["outcome"],
                    "baseline_support": baseline[cid].get("support"),
                    "kept_citations": [k.get("chunk_id") for k in gate.get("kept") or []]})
    return out


def run_cases(kb: KB, labels: list[dict], split: str, model, out_dir: str | Path, real: bool, labels_sha: str,
              freeze: dict | None = None, code_version: str = "unversioned", threshold: float = 0.35,
              case_ids: list[str] | None = None, code_tree: str | None = None) -> dict:
    from .audit import bind_summary
    from .eval import code_tree_sha256
    from .llm import ModelCallFailed, RunCapReached, assert_real_backend

    if real:
        assert_real_backend(model)
    code_tree = code_tree or code_tree_sha256()
    if split == "heldout":
        check_freeze_for_heldout(freeze, labels_sha, kb.version,
                                 run={"code_tree_sha256": code_tree, "code_version": code_version,
                                      "applicability_sha256": (getattr(kb, "applicability", None) or {}).get("sha256")})
    rows = [r for r in labels if r["split"] == split and not r.get("blocked_on")
            and (not case_ids or r["case_id"] in case_ids)]
    if split == "heldout" and freeze:
        rows = [r for r in rows if r["case_id"] not in set(freeze.get("exposed_case_ids") or [])]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    log_path = out / "log.jsonl"
    if log_path.exists():
        raise FileExistsError(f"{log_path} exists; a run directory is never overwritten")
    started = _now()
    log = AuditLog(path=log_path, code_version=code_version)
    log.append_event("cases_start", {"split": split, "labels_sha256": labels_sha, "kb_version": kb.version,
                                     "freeze_sha256": (freeze or {}).get("freeze_sha256"),
                                     "case_ids": [r["case_id"] for r in rows], "synthetic": False})
    spec = replace(CASE_ADVISER, instructions=CASE_ADVISER.instructions + " " + reviewed_instruments_line(kb))
    agent = RoleAgent(spec, kb, model)
    baseline = AbstainBaseline(HybridRetriever(kb, CASE_COLLECTIONS, k=5), threshold=threshold)
    results, rejections, stopped = [], [], None
    for r in rows:
        ans = baseline.answer(r["question"])
        try:
            a = agent.assess(1, [f"[case {r['case_id']}] {r['question']}"], [], retrieval_text=r["question"])
        except (RunCapReached, ModelCallFailed) as e:
            stopped = {"reason": "run cap reached" if isinstance(e, RunCapReached) else "model call failed",
                       "detail": str(e), "completed_cases": len(results)}
            log.append_event("run_stopped", stopped)
            break
        except Exception as e:  # noqa: BLE001 - whatever happens, the paid work so far is saved and visible
            stopped = {"reason": "unexpected error", "detail": f"{type(e).__name__}: {e}", "completed_cases": len(results)}
            log.append_event("run_stopped", stopped)
            break
        rec = log.append_step(
            role="adviser", input={"case_id": r["case_id"], "question": r["question"]}, query=a.query, scope=a.scope,
            kb_version=kb.version, passages=a.passages, rule_applied=a.rule_applied,
            rationale=a.rationale + ((" Gate: " + "; ".join(a.notes)) if a.notes else ""), decision=a.decision,
            model_id=a.model_id, prompt_sha256=a.prompt_sha256, config_sha256=a.config_sha256,
            model_calls=a.model_calls, gate=a.gate_record(), rejection=a.rejection)
        base_out = "ABSTAIN" if ans.abstained else "ANSWER"
        log.append_event("baseline", {"case_id": r["case_id"], "outcome": base_out, "support": round(ans.support, 6),
                                      "passages": [h.chunk.chunk_id for h in ans.passages]})
        if a.decision == REJECTED:
            rejections.append({"case_id": r["case_id"], **(a.rejection or {})})
            log.append_event("model_rejection", {"case_id": r["case_id"], **(a.rejection or {})})
        results.append({"case_id": r["case_id"], "split": split, "category": r["category"],
                        "system_outcome": outcome_of(a.decision, a.scope_findings), "decision": a.decision,
                        "baseline_outcome": base_out, "baseline_support": round(ans.support, 6),
                        "kept_citations": [c["chunk_id"] for c in a.kept_citations],
                        "scope_findings": a.scope_findings, "rejection": a.rejection, "record_hash": rec["record_hash"]})
    (out / "cases.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in results), encoding="utf-8")
    test_double = bool(getattr(model, "is_test_double", False) or getattr(model, "is_scripted", False))
    led = getattr(model, "ledger", None)
    cost = {"test_double": test_double, **(led.summary() if led else {"calls": 0, "cost_usd": 0.0})}
    (out / "cost.json").write_text(json.dumps(cost, indent=2), encoding="utf-8")
    from .traceview import render_trace_html, sources_from_kb

    summary = {"kind": "cases", "split": split, "labels_sha256": labels_sha, "kb_version": kb.version,
               "freeze_sha256": (freeze or {}).get("freeze_sha256"), "started_at": started,
               "case_ids": [x["case_id"] for x in results], "models": {"adviser": model.config()},
               "code_version": code_version, "test_double": test_double, "cost": cost, "stopped": stopped,
               "model_rejections": rejections, "baseline_threshold": threshold, "code_tree_sha256": code_tree}
    if getattr(kb, "applicability", None) is not None:
        summary["applicability_sha256"] = kb.applicability["sha256"]
    if test_double:
        summary["label"] = "TEST DOUBLE RUN: no model was called; not a result"
    # the whole summary and the cases sidecar are bound into the digest as the log's last record
    digest = bind_summary(log, summary, sidecars={"cases.jsonl": out / "cases.jsonl"})
    summary["digest"] = digest
    (out / "digest.json").write_text(json.dumps(digest, indent=2), encoding="utf-8")
    (out / "trace.html").write_text(render_trace_html(log.records, digest=digest, sources=sources_from_kb(kb)), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary
