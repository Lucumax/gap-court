"""Progressively released scenarios.

A step is plain text plus addressee roles. Roles receive only released steps addressed to
them. Hidden author labels (for scoring) stay in `Step.hidden` and are never put in a prompt.
All scenarios here are author-made and carry synthetic=True.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .audit import AuditLog
from .gaps import GapEntry, GapType, parse_gap_type
from .index import KB
from .llm import assert_real_backend
from .roles import REJECTED, Assessment, Coordinator, RoleAgent, Ruling


@dataclass
class Step:
    step_id: str
    text: str
    addressees: list[str]
    hidden: dict = field(default_factory=dict)


@dataclass
class Scenario:
    scenario_id: str
    title: str
    steps: list[Step]
    synthetic: bool = True


@dataclass
class StepResult:
    step: Step
    assessments: dict[str, Assessment]
    ruling: Ruling
    handoffs: list[dict]


@dataclass
class ScenarioResult:
    step_results: list[StepResult] = field(default_factory=list)
    revisions: list[dict] = field(default_factory=list)
    gaps: list[GapEntry] = field(default_factory=list)
    model_rejections: list[dict] = field(default_factory=list)
    digest: dict | None = None


class ScenarioEngine:
    def __init__(self, scenario: Scenario, roles: list[RoleAgent], coordinator: Coordinator, kb: KB,
                 log: AuditLog | None = None):
        self.scenario, self.kb, self.coordinator = scenario, kb, coordinator
        self.roles = {r.name: r for r in roles}
        self.log = log or AuditLog(code_version="dev")
        self._released = 0
        self._prior: dict[str, list[dict]] = {r: [] for r in self.roles}
        self._last_hash: dict[str, str] = {}
        self._last_decision: dict[str, str] = {}
        self.result = ScenarioResult()
        self.log.append_event("scenario_start", {
            "scenario_id": scenario.scenario_id, "title": scenario.title, "synthetic": scenario.synthetic,
            "kb_version": kb.version, "steps": len(scenario.steps)})

    def _messages_for(self, role: str, upto: int) -> list[str]:
        out = []
        for i, s in enumerate(self.scenario.steps[:upto], start=1):
            if role in s.addressees:
                out.append(f"[step {i}] {s.text}")
        return out

    def release_next(self) -> StepResult:
        if self._released >= len(self.scenario.steps):
            raise IndexError("no more steps")
        step = self.scenario.steps[self._released]
        self._released += 1
        step_no = self._released
        self.log.append_event("scenario_step_released", {
            "step_id": step.step_id, "step_no": step_no, "addressees": step.addressees,
            "text_sha256": hashlib.sha256(step.text.encode("utf-8")).hexdigest()})
        handoffs = [self.coordinator.route(step, step_no)]
        self.log.append_event("handoff", handoffs[0])

        assessments: dict[str, Assessment] = {}
        for name in step.addressees:
            role = self.roles[name]
            msgs = self._messages_for(name, step_no)
            a = role.assess(step_no, msgs, self._prior[name])
            rec = self.log.append_step(
                role=name, input={"step_id": step.step_id, "step_no": step_no, "messages": msgs},
                query=a.query, scope=a.scope, kb_version=self.kb.version, passages=a.passages,
                rule_applied=a.rule_applied,
                rationale=a.rationale + ((" Gate: " + "; ".join(a.notes)) if a.notes else ""),
                decision=a.decision, model_id=a.model_id, prompt_sha256=a.prompt_sha256,
                config_sha256=a.config_sha256, model_calls=a.model_calls, gate=a.gate_record(),
                rejection=a.rejection)
            assessments[name] = a
            if a.decision == REJECTED:
                rej = {"role": name, "step_id": step.step_id, **(a.rejection or {}),
                       "stop_reason": a.model_calls[-1].get("stop_reason") if a.model_calls else None,
                       "record_hash": rec["record_hash"]}
                self.result.model_rejections.append(rej)
                self.log.append_event("model_rejection", rej)
                continue  # a rejection is not a position: no prior entry, no revision, no gap
            self._prior[name].append({"step_no": step_no, "decision": a.decision,
                                      "rule_applied": a.rule_applied, "rationale": a.rationale})
            prev = self._last_decision.get(name)
            if prev is not None and prev != a.decision:
                rev = {"role": name, "step_id": step.step_id, "from": prev, "to": a.decision,
                       "new_fact": step.text,
                       "reason": a.revision_reason or "ruling changed after a newly released step",
                       "prior_record_hash": self._last_hash[name]}
                self.result.revisions.append(rev)
                self.log.append_event("revision", rev)
            self._last_decision[name] = a.decision
            self._last_hash[name] = rec["record_hash"]
            if a.decision == "GAP" and a.gap:
                entry = GapEntry(
                    gap_type=parse_gap_type(a.gap.get("type")), requirement=str(a.gap.get("requirement", "")),
                    queries=[a.query], kb_version=self.kb.version,
                    scope=", ".join(a.scope.get("collections", [])), step_id=step.step_id, passages=a.passages)
                self.result.gaps.append(entry)
                self.log.append_event("gap_opened", entry.to_dict())

        ruling, more = self.coordinator.reconcile(step, assessments)
        for h in more:
            self.log.append_event("handoff", h)
        handoffs += more
        self.log.append_event("ruling", {"step_id": step.step_id, "decision": ruling.decision,
                                         "reason": ruling.reason, "final_authority": ruling.final_authority,
                                         "executed": ruling.executed, "cited": ruling.cited,
                                         "model_rejections": ruling.model_rejections})
        sr = StepResult(step, assessments, ruling, handoffs)
        self.result.step_results.append(sr)
        return sr

    def run_all(self, real: bool = False) -> ScenarioResult:
        if real:
            for r in self.roles.values():
                assert_real_backend(r.model)
        while self._released < len(self.scenario.steps):
            self.release_next()
        self.result.digest = self.log.terminal_digest()
        return self.result
