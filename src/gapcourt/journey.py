"""Journey J1 (simulated service, real instruments): role definitions and scenario loading.

Entities in scenarios are SIMULATED. Document scopes below are the information partition:
the custodian sees the emirate-level data instruments, the oversight office sees the grievance
and redress material, the adviser sees the whole UAE collection, the service agent sees none.
"""
from __future__ import annotations

import json
from pathlib import Path

from .audit import AuditLog
from .index import KB
from .roles import Coordinator, RoleAgent, RoleSpec
from .scenario import Scenario, ScenarioEngine, Step

JOURNEY_ROLES = [
    RoleSpec(
        name="service", page_role="Innovative solution (service agent)", collections=["kb_uae"], use_kb=False,
        focus="", instructions=(
            "You represent the simulated Federal Household Benefit Service. You propose how the pre-check should proceed "
            "and ask the policy adviser before acting. You do not issue, deny or release anything. "
            "Answer ESCALATE to mean 'I am asking the adviser'.")),
    RoleSpec(
        name="adviser", page_role="Policy and legal adviser", collections=["kb_uae"], doc_ids=None,
        focus="which instrument governs this use of personal records and who decides", instructions=(
            "You retrieve and cite. You rule ALLOW, DENY or ESCALATE only with a quotation from a binding instrument that "
            "applies to the actor. If an instrument excludes the actor, record that in applicability. If nothing governs, answer GAP. "
            "If you change your earlier position because of a new fact, say so in revision_reason.")),
    RoleSpec(
        name="custodian", page_role="Institutional participant A (data custodian)", collections=["kb_uae"],
        doc_ids=["U06", "U11", "U12", "U13"], focus="what this records office may release and for which purposes",
        instructions=("You speak for the simulated Dubai Population Records Office. State your mandate and what you may release, "
                      "using only your own instruments.")),
    RoleSpec(
        name="oversight", page_role="Institutional participant B (complaints and oversight office)", collections=["kb_uae"],
        doc_ids=["U11", "U12", "U15", "U16"], focus="complaint, grievance, redress and access to personal data",
        instructions=("You speak for the simulated Complaints and Oversight Office. You review escalations, record the decision "
                      "and say whether the case can be reopened, using only your own instruments.")),
]


def load_scenario(path: str | Path) -> Scenario:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    steps = [Step(s["step_id"], s["text"], s["addressees"], s.get("hidden", {})) for s in d["steps"]]
    return Scenario(d["scenario_id"], d["title"], steps, synthetic=bool(d.get("synthetic", True)))


def add_step(scenario: Scenario, text: str, to: list[str]) -> Scenario:
    """A copy of the scenario with one judge-written step appended (released after the existing steps)."""
    known = {r.name for r in JOURNEY_ROLES}
    bad = [t for t in to if t not in known]
    if not to or bad:
        raise ValueError(f"unknown addressee {bad or to}; use one or more of {sorted(known)}")
    if not text.strip():
        raise ValueError("the added step needs text")
    n = 1 + sum(1 for s in scenario.steps if s.hidden.get("judge_added"))
    step = Step(f"{scenario.scenario_id}-J{n}", text.strip(), list(to), {"judge_added": True})
    return Scenario(scenario.scenario_id, scenario.title, list(scenario.steps) + [step], synthetic=scenario.synthetic)


def scenario_to_json(scenario: Scenario) -> dict:
    return {"scenario_id": scenario.scenario_id, "title": scenario.title, "synthetic": scenario.synthetic,
            "steps": [{"step_id": s.step_id, "addressees": s.addressees, "text": s.text, "hidden": s.hidden}
                      for s in scenario.steps]}


def build_engine(kb: KB, scenario: Scenario, models: dict, log: AuditLog | None = None,
                 roles: list[RoleSpec] | None = None) -> ScenarioEngine:
    specs = roles or JOURNEY_ROLES
    agents = [RoleAgent(spec, kb, models[spec.name]) for spec in specs]
    return ScenarioEngine(scenario, agents, Coordinator(), kb, log=log)


def _ledgers(models: dict) -> list:
    seen, out = set(), []
    for m in models.values():
        led = getattr(m, "ledger", None)
        if led is not None and id(led) not in seen:
            seen.add(id(led))
            out.append(led)
    return out


def run_and_save(kb: KB, scenario: Scenario, models: dict, out_dir: str | Path, real: bool,
                 code_version: str = "unversioned", roles: list[RoleSpec] | None = None,
                 extra: dict | None = None, code_tree: str | None = None) -> dict:
    """Run a scenario, write log, digest, static trace page, cost and summary.

    `real=True` refuses scripted models and test doubles. If a cost cap is reached the run stops before
    the call, a `run_stopped` event is logged, and everything up to that point is saved and verifiable.
    """
    from .audit import bind_summary
    from .eval import code_tree_sha256
    from .llm import ModelCallFailed, RunCapReached, assert_real_backend
    from .traceview import render_trace_html, sources_from_kb

    if real:
        for m in models.values():
            assert_real_backend(m)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    log_path = out / "log.jsonl"
    if log_path.exists():
        raise FileExistsError(f"{log_path} exists; a run directory is never overwritten")
    test_double = any(getattr(m, "is_test_double", False) or getattr(m, "is_scripted", False) for m in models.values())
    from datetime import datetime, timezone

    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    log = AuditLog(path=log_path, code_version=code_version)
    eng = build_engine(kb, scenario, models, log=log, roles=roles)
    stopped = None
    try:
        res = eng.run_all(real=real)
    except (RunCapReached, ModelCallFailed) as e:
        stopped = {"reason": "run cap reached" if isinstance(e, RunCapReached) else "model call failed",
                   "detail": str(e), "completed_steps": len(eng.result.step_results)}
        log.append_event("run_stopped", stopped)
        res = eng.result
    except Exception as e:  # noqa: BLE001 - whatever happens, the paid work so far is saved and visible
        stopped = {"reason": "unexpected error", "detail": f"{type(e).__name__}: {e}",
                   "completed_steps": len(eng.result.step_results)}
        log.append_event("run_stopped", stopped)
        res = eng.result
    ledgers = _ledgers(models)
    cost = {"test_double": test_double, "calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "reserved_usd": 0.0,
            "ledger_ids": []}
    for led in ledgers:
        sm = led.summary()
        cost["ledger_ids"].append(sm["ledger_id"])
        for k in ("calls", "input_tokens", "output_tokens", "cost_usd", "reserved_usd"):
            cost[k] += sm[k]
        cost.update({k: sm[k] for k in ("run_cap_usd", "total_cap_usd", "prior_spent_usd")})
    cost["cost_usd"] = round(cost["cost_usd"], 6)
    (out / "cost.json").write_text(json.dumps(cost, indent=2), encoding="utf-8")
    summary = {
        "kind": "scenario", "started_at": started,
        "scenario_id": scenario.scenario_id, "synthetic": scenario.synthetic, "kb_version": kb.version,
        "rulings": [{"step_id": r.step.step_id, "decision": r.ruling.decision, "reason": r.ruling.reason,
                     "final_authority": r.ruling.final_authority, "model_rejections": r.ruling.model_rejections}
                    for r in res.step_results],
        "revisions": res.revisions, "gaps": [g.to_dict() for g in res.gaps],
        "model_rejections": res.model_rejections,
        "models": {n: m.config() for n, m in models.items()}, "cost": cost, "test_double": test_double,
        "stopped": stopped, "code_version": code_version, "code_tree_sha256": code_tree or code_tree_sha256(),
        "note": "retain digest.json separately from log.jsonl; record count and last hash are the check",
    }
    if getattr(kb, "applicability", None) is not None:
        summary["applicability_sha256"] = kb.applicability["sha256"]
    if test_double:
        summary["label"] = "TEST DOUBLE RUN: no model was called; not a result"
    if extra:
        summary.update(extra)
    # the whole summary is bound into the digest as the log's last record
    digest = bind_summary(log, summary)
    summary["digest"] = digest
    (out / "digest.json").write_text(json.dumps(digest, indent=2), encoding="utf-8")
    (out / "trace.html").write_text(render_trace_html(log.records, digest=digest, sources=sources_from_kb(kb)), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary
