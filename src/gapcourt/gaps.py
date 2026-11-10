"""Typed gap outcomes. Only an analyst-reviewed entry is called a candidate policy gap."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum


class GapError(ValueError):
    pass


class GapType(str, Enum):
    RETRIEVAL_MISS = "retrieval_miss"
    CORPUS_COVERAGE_GAP = "corpus_coverage_gap"
    AMBIGUITY_OR_CONFLICT = "ambiguity_or_conflict"
    IMPLEMENTATION_GAP = "implementation_gap"
    UNRESOLVED = "unresolved"  # system output: not yet typed or reviewed
    CANDIDATE_POLICY_GAP = "candidate_policy_gap"  # analyst-reviewed only


CAVEAT = ("Analyst-reviewed candidate. This is not a finding that no governing rule exists; "
          "a small declared corpus cannot show exhaustive absence.")


def bounded_finding(kb_version: str, scope: str, queries: list[str]) -> str:
    return (f"No applicable governing passage was found in KB version {kb_version}, within scope {scope}, "
            f"using the recorded searches ({len(queries)}).")


def parse_gap_type(value) -> GapType:
    """Map a model-proposed type to a system type. A proposal can never be the candidate type."""
    try:
        t = GapType(str(value).lower())
    except ValueError:
        return GapType.UNRESOLVED
    return GapType.UNRESOLVED if t is GapType.CANDIDATE_POLICY_GAP else t


@dataclass
class GapEntry:
    gap_type: GapType
    requirement: str
    queries: list[str]
    kb_version: str
    scope: str
    step_id: str
    passages: list[dict] = field(default_factory=list)
    reviewer: str | None = None
    note: str | None = None

    @property
    def bounded(self) -> str:
        return bounded_finding(self.kb_version, self.scope, self.queries)

    @property
    def caveat(self) -> str:
        return CAVEAT if self.gap_type is GapType.CANDIDATE_POLICY_GAP else "Not reviewed by an analyst."

    def to_dict(self) -> dict:
        return {"gap_type": self.gap_type.value, "requirement": self.requirement, "queries": self.queries,
                "kb_version": self.kb_version, "scope": self.scope, "step_id": self.step_id,
                "passages": self.passages, "reviewer": self.reviewer, "note": self.note,
                "bounded_finding": self.bounded, "caveat": self.caveat}


def promote_to_candidate(entry: GapEntry, reviewer: str, note: str) -> GapEntry:
    if not reviewer or not reviewer.strip():
        raise GapError("a named reviewer is required to promote a gap to a candidate policy gap")
    if not note or not note.strip():
        raise GapError("a coverage-check note is required")
    return replace(entry, gap_type=GapType.CANDIDATE_POLICY_GAP, reviewer=reviewer, note=note)
