"""Deterministic gate between a role's raw output and a logged assessment.

Rules (all mechanical):
1. A citation is kept only if its chunk was retrieved for this call, its collection is allowed
   for this role, its quote appears in the chunk ("quotation verified"), and the quote is not
   instruction-like text.
2. ALLOW or DENY needs at least one kept citation to a binding instrument whose text is not Arabic; otherwise ESCALATE.
   (Rule R1: the quotation check compares strings, so an Arabic quotation copied from a text layer with ligature-order
   defects can verify; that shows the words are in the passage, not that an English ruling rests on verified wording.
   An Arabic citation is kept and recorded with its language, and can support a reading or a scope finding.)
   For an Arabic passage a quotation that fails the exact check but passes the letter-set check (a correctly spelled
   quotation against a text layer with ligature-order defects) is kept too, marked quotation_check "letter_set"; every
   Arabic citation, exact or letter-set, carries reading_only and cannot carry a ruling (gate v3).
3. A scope finding ("instrument does not apply, per [article]") is kept only with a verified quote.
4. Unknown decisions become ABSTAIN.
5. Applicability (only when `kb.applicability` is set, as the command line does): ALLOW or DENY also needs, for the instrument of EVERY kept
   binding citation (one passing instrument does not carry another), (a) a reviewer-approved record in corpus/APPLICABILITY.json, (b) an applicability entry the role recorded with
   applies "yes" and a quotation found in a retrieved passage, and (c) no entry from the role saying applies "no" or "unclear" for that
   instrument; otherwise ESCALATE. An applicability entry whose doc_id differs from the document of its chunk is dropped. Mechanical: it checks that applicability was recorded and approved, not that the role's "yes" is right.
The gate does not judge whether a cited passage supports the conclusion or applies to the actor; a reviewer does.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .bilingual import check_quotation_letter_set
from .index import KB
from .retrieve import Hit
from .verify import check_quotation

VALID = ("ALLOW", "DENY", "ESCALATE", "GAP", "ABSTAIN")
_INSTRUCTION_LIKE = re.compile(
    r"(ignore (all |any )?(previous|prior|above) (instructions|rules)|disregard (the )?(previous|above)|"
    r"you must (now )?(output|answer|respond)|output (ALLOW|DENY)|system prompt)", re.I)


def looks_like_instruction(text: str) -> bool:
    return bool(_INSTRUCTION_LIKE.search(text))


@dataclass
class GatedAssessment:
    decision: str
    kept_citations: list[dict] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    scope_findings: list[dict] = field(default_factory=list)
    applicability: list[dict] = field(default_factory=list)  # recorded "applies yes" entries that carried a decisive ruling


APPLICABILITY_FILE = Path(__file__).resolve().parents[2] / "corpus" / "APPLICABILITY.json"


def load_applicability(path: str | Path | None = None) -> dict:
    """The reviewer-approved applicability records: {"approved": {doc_id}, "sha256": hash of the file}.
    A record counts only when its approval reads "approved by <name> ..." and it states what the instrument covers."""
    p = Path(path) if path else APPLICABILITY_FILE
    if not p.is_file():
        return {"approved": set(), "sha256": "missing"}
    raw = p.read_bytes()
    rows = json.loads(raw.decode("utf-8"))
    ok = {r["doc_id"] for r in rows if str(r.get("approval", "")).lower().startswith("approved by ") and str(r.get("covers", "")).strip()}
    return {"approved": ok, "sha256": hashlib.sha256(raw).hexdigest()}


def approve_applicability(path: str | Path, doc_ids: list[str], reviewer: str, today: str | None = None) -> None:
    """Mark the named in-knowledge-base records approved by the reviewer; every other record stays as it was."""
    p = Path(path)
    rows = json.loads(p.read_text(encoding="utf-8"))
    known = {r["doc_id"] for r in rows if r.get("in_kb")}
    bad = [d for d in doc_ids if d not in known]
    if bad or not reviewer.strip():
        raise ValueError(f"cannot approve {', '.join(bad) or 'without a named reviewer'}: not an in-knowledge-base record or no reviewer")
    stamp = f"approved by {reviewer.strip()} on {today or date.today().isoformat()}"
    for r in rows:
        if r["doc_id"] in doc_ids:
            r["approval"] = stamp
    p.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def _binding_by_doc(kb: KB) -> dict[str, bool]:
    return {r["doc_id"]: bool(r["binding"]) for r in kb.coverage_table()}


def _language_by_doc(kb: KB) -> dict[str, str]:
    return {r["doc_id"]: str(r.get("language") or "") for r in kb.coverage_table()}


def _check(c, by_id: dict[str, Hit], allowed: list[str], binding: dict[str, bool], language: dict[str, str] | None = None):
    if not isinstance(c, dict):
        return None, {"chunk_id": None, "reason": "malformed_citation"}
    cid = c.get("chunk_id")
    if not isinstance(cid, str):
        return None, {"chunk_id": None, "reason": "malformed_citation"}
    hit = by_id.get(cid)
    if hit is None:
        return None, {"chunk_id": cid, "reason": "chunk_not_retrieved"}
    if hit.collection not in allowed:
        return None, {"chunk_id": cid, "reason": "collection_not_allowed"}
    if c.get("doc_id") is not None and c["doc_id"] != hit.chunk.doc_id:  # an entry names the document of the chunk it quotes
        return None, {"chunk_id": cid, "reason": "doc_id_mismatch"}
    quote = c.get("quote", "")
    if not isinstance(quote, str):
        return None, {"chunk_id": cid, "reason": "malformed_citation"}
    if looks_like_instruction(quote):
        return None, {"chunk_id": cid, "reason": "instruction_like_quote"}
    lang = (language or {}).get(hit.chunk.doc_id, "")
    q = check_quotation(quote, hit.chunk.text)
    mode = "exact"
    if not q.verified and lang == "ar" and check_quotation_letter_set(quote, hit.chunk.text).verified:
        q, mode = check_quotation_letter_set(quote, hit.chunk.text), "letter_set"
    if not q.verified:
        return None, {"chunk_id": cid, "reason": f"quotation_{q.reason}"}
    return {
        "chunk_id": cid, "quote": quote, "cite": hit.chunk.cite, "doc_id": hit.chunk.doc_id,
        "locator": hit.chunk.locator, "collection": hit.collection, "text_sha256": hit.chunk.text_sha256,
        "binding": binding.get(hit.chunk.doc_id, False), "quotation_verified": True,
        "language": lang, "quotation_check": mode, "reading_only": lang == "ar",
    }, None


def apply_gate(raw: dict, hits: list[Hit], allowed_collections: list[str], kb: KB) -> GatedAssessment:
    by_id = {h.chunk.chunk_id: h for h in hits}
    binding = _binding_by_doc(kb)
    language = _language_by_doc(kb)
    decision = str(raw.get("decision", "")).upper()
    out = GatedAssessment(decision=decision)
    if decision not in VALID:
        out.decision = "ABSTAIN"
        out.notes.append(f"gate: unknown decision {decision!r}")
    citations = raw.get("citations") or []
    if not isinstance(citations, list):
        out.notes.append("gate: citations was not a list; ignored")
        citations = []
    for c in citations:
        kept, dropped = _check(c, by_id, allowed_collections, binding, language)
        if kept:
            out.kept_citations.append(kept)
        else:
            out.dropped.append(dropped)
    applicability = raw.get("applicability") or []
    if not isinstance(applicability, list):
        out.notes.append("gate: applicability was not a list; ignored")
        applicability = []
    for a in applicability:
        if not isinstance(a, dict):
            out.dropped.append({"chunk_id": None, "reason": "malformed_citation"})
            continue
        if str(a.get("applies", "")).lower() != "no":
            continue
        kept, dropped = _check(a, by_id, allowed_collections, binding, language)
        if kept:
            out.scope_findings.append({
                "doc_id": kept["doc_id"], "per": kept["locator"], "chunk_id": kept["chunk_id"],
                "quote": kept["quote"], "language": kept["language"],
                "quotation_check": kept["quotation_check"], "reading_only": kept["reading_only"],
                "statement": f"instrument does not apply, per {kept['locator']}"})
        else:
            out.dropped.append(dropped)
    if out.decision in ("ALLOW", "DENY") and not any(c["binding"] and c["language"] != "ar" for c in out.kept_citations):
        if any(c["binding"] for c in out.kept_citations):
            out.notes.append(f"gate: the only binding citations are Arabic text (a reading, not a verified English ruling, rule R1), "
                             f"{out.decision} downgraded to ESCALATE")
        else:
            out.notes.append(f"gate: no verified binding citation, {out.decision} downgraded to ESCALATE")
        out.decision = "ESCALATE"
    appl = getattr(kb, "applicability", None)
    if appl is not None and out.decision in ("ALLOW", "DENY"):
        _require_applicability(out, applicability, appl, by_id, allowed_collections, binding, language)
    return out


def _require_applicability(out: GatedAssessment, entries: list, appl: dict, by_id: dict, allowed: list[str],
                           binding: dict[str, bool], language: dict[str, str]) -> None:
    yes: dict[str, dict] = {}
    contrary: dict[str, set] = {}
    for a in entries:
        if not isinstance(a, dict):
            continue
        applies = str(a.get("applies", "")).lower()
        if applies == "yes":
            kept, dropped = _check(a, by_id, allowed, binding, language)
            if kept:
                yes.setdefault(kept["doc_id"], kept)
            else:
                out.dropped.append(dropped)
        elif applies in ("no", "unclear"):
            hit = by_id.get(a.get("chunk_id"))
            if hit is not None and a.get("doc_id") is not None and a["doc_id"] != hit.chunk.doc_id:
                out.dropped.append({"chunk_id": a.get("chunk_id"), "reason": "doc_id_mismatch"})
                continue
            contrary.setdefault(hit.chunk.doc_id if hit else str(a.get("doc_id")), set()).add(applies)
    docs = list(dict.fromkeys(c["doc_id"] for c in out.kept_citations if c["binding"] and c["language"] != "ar"))
    why, passed = [], []
    for d in docs:  # every cited binding instrument must pass; one passing instrument does not carry the others
        if d not in appl.get("approved", set()):
            why.append(f"{d}: no approved applicability record")
        elif d in contrary:
            why.append(f"{d}: the role recorded applies {' and '.join(sorted(contrary[d]))}")
        elif d not in yes:
            why.append(f"{d}: no recorded applicability (applies yes with a verified quotation)")
        else:
            passed.append(d)
    if not why:
        out.applicability = [{k: yes[d][k] for k in ("doc_id", "locator", "chunk_id", "quote")} for d in passed]
        return
    out.notes.append("gate: applicability not established (" + "; ".join(why) + f"), {out.decision} downgraded to ESCALATE")
    out.decision = "ESCALATE"
