"""Hash-chained audit log with a separately retained terminal digest.

What the check shows: records are internally consistent, in order, and match a
digest (count and last hash) that was retained somewhere else. What it does not
show: who wrote them, or that any statement in them is true. Without an
independently retained digest, a whole chain could be rewritten and still verify.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

GENESIS = "0" * 64
DECISIONS = ("ALLOW", "ESCALATE", "DENY", "GAP", "ABSTAIN", "INFO", "MODEL_REJECTED")


class AuditError(ValueError):
    pass


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def record_hash(record: dict) -> str:
    body = {k: v for k, v in record.items() if k != "record_hash"}
    return hashlib.sha256(canonical(body)).hexdigest()


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class VerifyResult:
    ok: bool
    errors: list[str] = field(default_factory=list)


class AuditLog:
    def __init__(self, path: str | Path | None = None, clock: Callable[[], str] = _utcnow,
                 code_version: str = "unversioned"):
        self.path = Path(path) if path else None
        self.clock = clock
        self.code_version = code_version
        self.records: list[dict] = []
        if self.path and self.path.exists():
            self.records = self.load_records(self.path)

    @staticmethod
    def load_records(path: str | Path) -> list[dict]:
        return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]

    def _append(self, record: dict) -> dict:
        record["seq"] = len(self.records)
        record["timestamp"] = self.clock()
        record["code_version"] = self.code_version
        record["prev_hash"] = self.records[-1]["record_hash"] if self.records else GENESIS
        record["record_hash"] = record_hash(record)
        self.records.append(record)
        if self.path:
            with open(self.path, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                f.flush()
        return record

    def append_step(self, *, role: str, input: dict, query: str, scope: dict, kb_version: str,
                    passages: list[dict], rule_applied: str, rationale: str, decision: str,
                    model_id: str, prompt_sha256: str, config_sha256: str, model_calls: list | None = None,
                    gate: dict | None = None, rejection: dict | None = None) -> dict:
        """`model_calls` carries stop reason, usage, cost, effort, the labelled reasoning summary and retries
        for each call; `gate` the kept and dropped citations; `rejection` why a model answer was rejected."""
        if decision not in DECISIONS:
            raise AuditError(f"unknown decision {decision!r}")
        rec = {
            "kind": "step", "role": role, "input": input, "query": query, "scope": scope,
            "kb_version": kb_version, "passages": passages, "rule_applied": rule_applied,
            "rationale_summary": rationale, "decision": decision, "model_id": model_id,
            "prompt_sha256": prompt_sha256, "config_sha256": config_sha256,
        }
        for k, v in (("model_calls", model_calls), ("gate", gate), ("rejection", rejection)):
            if v is not None:
                rec[k] = v
        return self._append(rec)

    def append_event(self, name: str, payload: dict) -> dict:
        return self._append({"kind": "event", "event": name, "payload": payload})

    def terminal_digest(self) -> dict:
        return {"count": len(self.records), "last_hash": self.records[-1]["record_hash"] if self.records else GENESIS}


def verify_records(records: list[dict], expected: dict | None = None) -> VerifyResult:
    errors: list[str] = []
    prev = GENESIS
    for i, r in enumerate(records):
        if r.get("seq") != i:
            errors.append(f"record {i}: seq is {r.get('seq')}, expected {i}")
        if r.get("prev_hash") != prev:
            errors.append(f"record {i}: prev_hash does not match the preceding record")
        if r.get("record_hash") != record_hash(r):
            errors.append(f"record {i}: record_hash does not match content")
        prev = r.get("record_hash", "")
    if expected is not None:
        if len(records) != expected["count"]:
            errors.append(f"count {len(records)} differs from retained digest count {expected['count']}")
        last = records[-1]["record_hash"] if records else GENESIS
        if last != expected["last_hash"]:
            errors.append("last hash differs from retained digest")
    return VerifyResult(not errors, errors)


# ---- binding the run summary and its sidecars into the retained digest -------------

SUMMARY_EVENT = "summary_bound"


def summary_sha256(summary: dict) -> str:
    """Hash of the whole summary except its own `digest` field (the digest cannot cover itself)."""
    return hashlib.sha256(canonical({k: v for k, v in summary.items() if k != "digest"})).hexdigest()


def _file_sha256(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def bind_summary(log: "AuditLog", summary: dict, sidecars: dict | None = None) -> dict:
    """Append the final `summary_bound` event (hash of every summary field, hashes of the named sidecar files) and
    return the terminal digest, which now covers them. Call it last, after the summary is complete."""
    payload = {"summary_sha256": summary_sha256(summary), "summary_keys": sorted(k for k in summary if k != "digest")}
    if sidecars:
        payload["sidecars"] = {name: _file_sha256(p) for name, p in sorted(sidecars.items())}
    log.append_event(SUMMARY_EVENT, payload)
    return log.terminal_digest()


def check_summary_binding(records: list[dict], summary: dict, digest: dict | None = None,
                          run_dir: str | Path | None = None) -> str | None:
    """None if summary.json (and the sidecars the log names) are exactly what the log's last record bound; otherwise why not.
    The log itself is checked separately against the retained digest (verify_records)."""
    last = records[-1] if records else {}
    if last.get("kind") != "event" or last.get("event") != SUMMARY_EVENT:
        return "summary not bound into the digest (the log does not end with a summary_bound record; an older or altered run)"
    payload = last.get("payload") or {}
    if payload.get("summary_sha256") != summary_sha256(summary):
        return "summary.json differs from the summary bound into the digest (edited after the run)"
    if digest is not None and summary.get("digest") not in (None, digest):
        return "the digest inside summary.json differs from digest.json"
    if run_dir is not None:
        for name, want in (payload.get("sidecars") or {}).items():
            p = Path(run_dir) / name
            # a missing sidecar is harmless (outcomes are read from the log); a changed one is not
            if p.exists() and _file_sha256(p) != want:
                return f"{name} sidecar differs from the file bound into the digest (edited after the run)"
    versions = {r.get("code_version") for r in records if r.get("code_version")}
    if summary.get("code_version") and versions and versions != {summary["code_version"]}:
        return (f"the summary names code version {summary['code_version']} but the log's records carry "
                f"{', '.join(sorted(map(str, versions)))}")
    return None
