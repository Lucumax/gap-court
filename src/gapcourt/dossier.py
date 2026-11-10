"""Source-reviewed dossier checker.

A dossier spec lists claims (a quotation, the instrument and the locator it was read at) and findings (statements, each
resting on claims). The checker re-reads the knowledge base text and decides, for every claim, whether the quotation is a
verbatim substring of the cited passage and whether the passage's stored hash matches its text, and for every finding
whether it may be shown as supported. A finding that cannot be shown as supported is flagged and rendered apart, never
as a finding. Nothing here calls a model. It checks that the words are there and the hashes agree; it does not check
that the passage supports the statement, which is the reviewer's reading (said in the rendered text).
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .export import MAX_QUOTE_WORDS
from .bilingual import check_quotation_letter_set
from .index import KB
from .retrieve import HybridRetriever
from .verify import check_quotation

KINDS = ("requirement", "context", "reading", "comparator", "unresolved")
ARABIC_OK = ("reading", "unresolved")  # an Arabic passage supports a reading or an open question, never a stated requirement
_LANGUAGE_CODE = re.compile(r"^[a-z]{2}$")  # a plain two-letter code; anything else (for example a mixed value) is unknown


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _locator_matches(found: str, wanted: str) -> bool:
    return found == wanted or found.startswith(wanted + " [")


def _check_claim(kb: KB, claim: dict, rows: dict, audit_rows: dict, repo_root: Path | None, expected_raw: dict) -> dict:
    out: dict = {"id": claim["id"], "doc_id": claim["doc_id"], "locator": claim["locator"], "quote": claim["quote"],
                 "words": len(claim["quote"].split()), "note": claim.get("note", "")}
    row = rows.get(claim["doc_id"])
    if row is None:
        return {**out, "status": "no_such_document"}
    out.update({"title": row["title"], "binding": bool(row["binding"]), "language": row["language"],
                "republish": row["republish"], "source_url": row["source_url"], "version": row["version"],
                "source_sha256": row["source_sha256"]})
    if out["republish"] != "yes" and out["words"] > MAX_QUOTE_WORDS:
        return {**out, "status": "quote_too_long_for_link_only"}  # the export limit: a longer run of link-only text is a passage
    passages = [e.chunk for e in kb.entries if e.chunk.doc_id == claim["doc_id"] and _locator_matches(e.chunk.locator, claim["locator"])]
    if not passages:
        return {**out, "status": "no_such_passage"}
    reason = "not_found"
    hit = None
    check = "exact"
    for ch in passages:
        res = check_quotation(claim["quote"], ch.text)
        if res.verified:
            hit = ch
            break
        if res.reason != "not_found":
            reason = res.reason
    if hit is None and row["language"] == "ar":  # a correctly spelled Arabic quotation against a defective text layer: a reading
        for ch in passages:
            if check_quotation_letter_set(claim["quote"], ch.text).verified:
                hit, check = ch, "letter_set"
                break
    if hit is None:
        return {**out, "status": f"quote_{reason}"}
    stored = hit.text_sha256
    recomputed = hashlib.sha256(hit.text.encode("utf-8")).hexdigest()
    out.update({"check": check, "chunk_id": hit.chunk_id, "locator_found": hit.locator, "text_sha256": stored,
                "text_sha256_ok": stored == recomputed})
    prov = audit_rows.get(claim["doc_id"])
    out["provenance"] = prov["status"] if prov else None
    out["raw"] = None
    if prov and prov.get("raw") and repo_root is not None:
        p = Path(repo_root) / prov["raw"]
        want = expected_raw.get(claim["doc_id"], row["source_sha256"])
        if p.is_file():
            out["raw"] = {"path": prov["raw"], "present": True, "sha256_ok": _sha256_file(p) == want}
        else:
            out["raw"] = {"path": prov["raw"], "present": False, "sha256_ok": None}
    if not out["text_sha256_ok"]:
        return {**out, "status": "hash_mismatch"}
    if out["raw"] and out["raw"]["present"] and out["raw"]["sha256_ok"] is False:
        return {**out, "status": "raw_hash_mismatch"}
    return {**out, "status": "verified"}


def _judge_finding(f: dict, claims: dict, searches: dict) -> tuple[str, str]:
    cited = f.get("claims") or []
    if not cited:
        return "flagged", "no passage is cited for this finding"
    bad = [f"{c}: {claims[c]['status']}" if c in claims else f"{c}: not defined" for c in cited
           if c not in claims or claims[c]["status"] != "verified"]
    if bad:
        return "flagged", "a cited passage does not check out (" + "; ".join(bad) + ")"
    kind = f.get("kind", "context")
    if kind not in KINDS:
        return "flagged", f"unknown kind {kind!r}"
    unknown = [c for c in cited if not _LANGUAGE_CODE.match(str(claims[c]["language"]))]
    if unknown:
        return "flagged", "unknown language value for " + ", ".join(f"{c}: {claims[c]['language']!r}" for c in unknown)
    if f.get("duty") or kind == "requirement":  # the bar belongs to the kind, not to a flag the spec may leave off
        nb = [c for c in cited if not claims[c]["binding"]]
        if nb:
            return "flagged", "a duty cannot rest on a non-binding source (" + ", ".join(f"{c}: {claims[c]['doc_id']}" for c in nb) + ")"
    if kind not in ARABIC_OK or f.get("duty"):
        ar = [c for c in cited if claims[c]["language"] == "ar"]
        if ar:
            return "flagged", ("Arabic-only passage (" + ", ".join(f"{c}: {claims[c]['doc_id']}" for c in ar) +
                               ") supports a reading, not a stated requirement or comparator; its English wording is not checked")
    if kind == "unresolved":
        ids = f.get("searches") or []
        if not ids or any(i not in searches for i in ids):
            return "flagged", "an unresolved finding needs at least one recorded search in this spec"
    return "supported", ""


def evaluate_spec(kb: KB, spec: dict, audit: dict | None = None, repo_root: str | Path | None = None,
                  expected_raw_sha: dict | None = None) -> dict:
    for what, items in (("claim", spec.get("claims", [])), ("finding", spec.get("findings", [])), ("search", spec.get("searches", []))):
        ids = [i["id"] for i in items]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"duplicate {what} id in the dossier spec: {', '.join(dup)}")
    rows = {r["doc_id"]: r for r in kb.coverage_table()}
    audit_rows = {r["doc_id"]: r for r in (audit or {}).get("files", [])}
    root = Path(repo_root) if repo_root is not None else None
    claims = {c["id"]: _check_claim(kb, c, rows, audit_rows, root, expected_raw_sha or {}) for c in spec.get("claims", [])}
    searches = {}
    for s in spec.get("searches", []):
        r = HybridRetriever(kb, s["collections"], k=5)
        hits = r.search(s["query"])
        searches[s["id"]] = {"id": s["id"], "query": s["query"], "collections": s["collections"], "kb_version": kb.version,
                             "retriever": r.config(), "top5": [f"{h.chunk.doc_id} {h.chunk.locator}" for h in hits]}
    findings = {}
    for f in spec.get("findings", []):
        status, reason = _judge_finding(f, claims, searches)
        findings[f["id"]] = {"status": status, "reason": reason}
    return {"id": spec.get("id"), "kb_version": kb.version, "claims": claims, "findings": findings, "searches": searches,
            "counts": {"claims": len(claims), "claims_verified": sum(1 for c in claims.values() if c["status"] == "verified"),
                       "findings": len(findings), "findings_supported": sum(1 for v in findings.values() if v["status"] == "supported")}}


def _claim_block(c: dict) -> list[str]:
    if c["status"] != "verified":
        return [f"- {c['id']} {c['doc_id']} {c['locator']}: {c['status']}"]
    status = {"yes": "open text", "link_only": "link only"}.get(c["republish"], "republication status " + str(c["republish"]))
    flags = [f"{'binding' if c['binding'] else 'non-binding'}", c["language"], status]
    if c.get("check") == "letter_set":
        flags.append("letter-set match, reading only")
    if c.get("provenance"):
        flags.append(c["provenance"].lower() + " from the raw file")
    if c.get("raw") and c["raw"]["present"]:
        flags.append("raw file hash matches" if c["raw"]["sha256_ok"] else "RAW FILE HASH DIFFERS")
    lines = [f"- {c['id']}, {c['doc_id']} {c['locator_found']} ({'; '.join(flags)}); chunk {c['chunk_id']}, "
             f"text sha256 {c['text_sha256'][:16]}, source file sha256 {c['source_sha256'][:16]}, {c['words']} words:",
             f"  > {c['quote']}"]
    if c.get("note"):
        lines.append(f"  Note: {c['note']}")
    return lines


def render_markdown(spec: dict, res: dict) -> str:
    claims, findings = res["claims"], res["findings"]
    out = [f"# {spec['title']}", ""]
    out += [spec.get("status_line", "Status: generated by `gapcourt dossier-check` from the specification and the knowledge base; "
                     "every quotation below was checked as a verbatim substring of the cited passage and every passage hash was recomputed."), ""]
    out += [f"KB version: {res['kb_version']}. Passages: {res['counts']['claims_verified']} of {res['counts']['claims']} quotations verified. "
            f"Findings shown as supported: {res['counts']['findings_supported']} of {res['counts']['findings']}.", ""]
    out += ["What the checks mean: the quotation is in the cited passage, the passage text matches its stored hash and, where the raw file "
            "is present, the raw file matches the recorded hash. They do not show that the passage supports the statement; that is a reading "
            "and carries the reviewer's name below once reviewed. This dossier states a domain-level requirement and compares global "
            "approaches. It makes no recommendation to any country.", ""]
    if spec.get("scenario_excerpt"):
        out += ["## Scenario step", spec["scenario_excerpt"], ""]
    failed = [f for f in spec.get("findings", []) if findings[f["id"]]["status"] != "supported"]
    if failed:
        out += [f"WARNING: {len(failed)} finding{'s' if len(failed) != 1 else ''} failed the checks ({', '.join(f['id'] for f in failed)}). "
                "The Requirement and Conclusion sections below were written before the check and are not re-derived from the "
                "supported findings: read them as unsupported wherever they lean on a failed finding.", ""]
    out += ["## Requirement at issue", spec["requirement"], ""]
    out += ["## Findings", ""]
    shown = [f for f in spec.get("findings", []) if findings[f["id"]]["status"] == "supported"]
    if not shown:
        out += ["None of the findings passed the checks.", ""]
    for f in shown:
        out += [f"### {f['id']} ({f.get('kind', 'context')}): {f['statement']}"]
        if f.get("limits"):
            out += [f"Limit: {f['limits']}"]
        out += [""]
        for cid in f["claims"]:
            out += _claim_block(claims[cid])
        for sid in f.get("searches", []):
            s = res["searches"][sid]
            out += [f"- Recorded search {sid} (corpus-bounded, KB {s['kb_version']}, collections {', '.join(s['collections'])}): "
                    f"'{s['query']}' returned: {'; '.join(s['top5'])}."]
        out += [""]
    for title, key in (("Alternative explanations and why they are not excluded", "alternatives"),
                       ("Global comparators: limits of comparison", "comparability"),
                       ("What this dossier does not claim", "not_claimed"),
                       ("Changes from the earlier skeleton", "supersedes_notes")):
        items = spec.get(key)
        if items:
            out += [f"## {title}"] + [f"- {i}" for i in (items if isinstance(items, list) else [items])] + [""]
    if spec.get("mapping"):
        out += ["## Mapping tags (by analogy, not conformity)", spec["mapping"], ""]
    if spec.get("conclusion"):
        out += ["## Conclusion (agent-drafted, preliminary)", spec["conclusion"], ""]
    out += ["## Flagged: not supported, kept out of the findings", ""]
    flagged = [f for f in spec.get("findings", []) if findings[f["id"]]["status"] != "supported"]
    if not flagged:
        out += ["None."]
    for f in flagged:
        out += [f"- {f['id']} ({f.get('kind', 'context')}): {f['statement']} Reason: {findings[f['id']]['reason']}."]
        for cid in f.get("claims") or []:
            if cid in claims and claims[cid]["status"] != "verified":
                out += [f"  - {cid} {claims[cid]['doc_id']} {claims[cid]['locator']}: {claims[cid]['status']}"]
    if spec.get("verification_notes"):
        out += ["", "## Primary-source verification notes"] + [f"- {n}" for n in spec["verification_notes"]]
    out += ["", "## Disposition", f"Reviewer: {spec.get('reviewer', '(pending)')}. Disposition: {spec.get('disposition', '(pending)')}. "
            f"Date: {spec.get('reviewed_on', '(pending)')}.", ""]
    return "\n".join(out).replace("## Flagged: not supported, kept out of the findings", "## Flagged: not supported, kept out of the findings")
