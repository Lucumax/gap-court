"""Fill the report template from run output. Every number is tagged:

[measured]       from a saved run that called the model (not a test double); the evidence index lists the run ids
[offline]        from the knowledge base build, the provenance check or the offline test run; no model was involved
[pending-live]   no such run exists yet; the text names the session that produces it (never a guessed number)

Template syntax: {{fact:key}} one value; {{block:evidence}} the run list; {{block:results}} the `gapcourt score` tables.
A key that does not exist is an error, not a blank.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

WORDS_PER_PAGE = 500  # text with tables at the official layout; an estimate, to be replaced by the rendered page count

PENDING_HINT = {
    "probe": "probe session",
    "smoke": "smoke session",
    "live": "dev, heldout and repeats sessions",
    "dev": "dev session",
    "heldout": "heldout session",
    "repeats": "repeats session",
    "total": "smoke session",
    "demo": "dev session",
}


class ReportError(ValueError):
    pass


@dataclass
class Fact:
    value: str
    tag: str  # measured | offline
    source: str = ""


def _load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def _is_test_double(summary: dict) -> bool:
    return bool(summary.get("test_double") or (summary.get("cost") or {}).get("test_double"))


def _groups(rel: str) -> tuple[str, ...]:
    """Every group a run counts in: smoke, or live (all sessions after the smoke one) and dev (the development session alone)."""
    top = rel.split("/")[0]
    if top == "demo":  # development demonstrations (S-04) are reported apart from the live and frozen scenario runs
        return ("demo",)
    return ("smoke",) if top == "smoke" else (("live", "dev") if top == "dev" else ("live",))


def _verify_run(d: Path) -> str | None:
    """None if the run's log verifies against its retained digest, else the reason it cannot be counted."""
    from .audit import AuditLog, verify_records

    if not (d / "log.jsonl").exists() or not (d / "digest.json").exists():
        return "no log or no retained digest"
    try:
        recs = AuditLog.load_records(d / "log.jsonl")
        digest = _load(d / "digest.json")
        v = verify_records(recs, expected=digest)
        if not v.ok:
            return v.errors[0]
        from .audit import check_summary_binding

        return check_summary_binding(recs, _load(d / "summary.json"), digest, d)
    except (OSError, ValueError, KeyError) as e:
        return f"unreadable log, digest or summary ({type(e).__name__})"


def _scan_runs(runs_root: Path):
    live, ignored, unverified = [], [], []
    if not runs_root.exists():
        return live, ignored, unverified
    for sj in sorted(runs_root.rglob("summary.json")):
        rel = sj.parent.relative_to(runs_root).as_posix()
        if rel.startswith("_"):
            continue
        s = _load(sj)
        if _is_test_double(s):
            ignored.append((rel, sj.parent, s))
            continue
        why = _verify_run(sj.parent)
        (unverified.append((rel, sj.parent, s, why)) if why else live.append((rel, sj.parent, s)))
    return live, ignored, unverified


def _step_records(d: Path):
    p = d / "log.jsonl"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            if r.get("kind") == "step":
                yield r


def _contradicting_steps_run(d: Path, summary: dict, scenarios_dir: Path) -> int:
    """Steps of this run's scenario that the scenario marks as contradicting an earlier step and that were actually run.
    Revision behaviour (R4) can only have been exercised where this is above zero."""
    sid = summary.get("scenario_id")
    sp = scenarios_dir / f"{sid}.json" if sid else None
    if not sp or not sp.is_file():
        return 0
    try:
        steps = _load(sp).get("steps") or []
    except (OSError, ValueError):
        return 0
    ran = {(r.get("input") or {}).get("step_id") for r in _step_records(d)}
    return sum(1 for st in steps if (st.get("hidden") or {}).get("contradicts_prior") and st.get("step_id") in ran)


def collect_facts(runs_root, kb_dir, offline_facts=None, results_dir=None, provenance=None, cases_file=None, scenarios_dir=None,
                  dossier_evidence=None, retrieval_results=None) -> dict:
    runs_root, kb_dir = Path(runs_root), Path(kb_dir)
    scenarios_dir = Path(scenarios_dir) if scenarios_dir else runs_root.parent / "scenarios"
    facts: dict[str, Fact] = {}
    blocks: dict[str, str] = {}

    def put(key, value, tag, source=""):
        facts[key] = Fact(str(value), tag, source)

    # ---- offline: the knowledge base ----
    man = _load(kb_dir / "KB_MANIFEST.json")
    put("kb.version", man["kb_version"], "offline", "KB_MANIFEST.json")
    put("kb.documents", len(man["documents"]), "offline", "KB_MANIFEST.json")
    put("kb.chunks", man["chunk_count"], "offline", "KB_MANIFEST.json")
    per = Counter(d["collection"] for d in man["documents"])
    for coll in ("kb_uae", "kb_global", "kb_framework"):
        put(f"kb.{coll}", per.get(coll, 0), "offline", "KB_MANIFEST.json")
    put("kb.embedder", man["embedder"]["name"], "offline", "KB_MANIFEST.json")

    if provenance and Path(provenance).exists():
        pv = _load(Path(provenance))
        put("corpus.reproduced", pv["reproduced"], "offline", "corpus/PROVENANCE_AUDIT.json")
        put("corpus.audited", pv["audited"], "offline", "corpus/PROVENANCE_AUDIT.json")
    if cases_file and Path(cases_file).exists():
        rows = [json.loads(l) for l in Path(cases_file).read_text(encoding="utf-8").splitlines() if l.strip()]
        put("cases.draft_total", len(rows), "offline", "evals pilot case file")
        put("cases.draft_dev", sum(r.get("split") == "dev" for r in rows), "offline", "evals pilot case file")
        put("cases.draft_heldout", sum(r.get("split") == "heldout" for r in rows), "offline", "evals pilot case file")
        put("cases.status", "draft, unapproved" if any(r.get("status") == "DRAFT_UNAPPROVED" for r in rows) else "approved", "offline", "evals pilot case file")
    if dossier_evidence and Path(dossier_evidence).exists():
        ev = _load(Path(dossier_evidence))
        dc = ev["counts"]
        if isinstance(ev.get("claims"), dict) and isinstance(ev.get("findings"), dict):  # re-derive, do not trust the aggregate
            again = {"claims": len(ev["claims"]), "claims_verified": sum(c.get("status") == "verified" for c in ev["claims"].values()),
                     "findings": len(ev["findings"]), "findings_supported": sum(f.get("status") == "supported" for f in ev["findings"].values())}
            if any(dc[k] != v for k, v in again.items()):
                raise ValueError(f"the dossier evidence counts disagree with the claims and findings beneath them: {dc} against {again}")
        for k in ("claims", "claims_verified", "findings", "findings_supported"):
            put(f"dossier.{k}", dc[k], "offline", "dossiers/DOSSIER_S01.evidence.json")
    if retrieval_results and Path(retrieval_results).exists():
        rr = _load(Path(retrieval_results))["runs"]
        off, on = rr["off"], rr["on"]
        for run in (off, on):
            pr, dv = run["probes"]["hybrid"], run["dev"]["hybrid"]
            bad = []
            if "by_probe" in pr and (pr["hit"] != sum(bool(v["hit"]) for v in pr["by_probe"].values()) or pr["n"] != len(pr["by_probe"])):
                bad.append("probes")
            if "by_case" in dv and dv["expected_found"] != sum(bool(v["expected_found"]) for v in dv["by_case"].values()):
                bad.append("development cases")
            if bad:
                raise ValueError("the retrieval result totals disagree with the per-question rows beneath them: " + ", ".join(bad))
        src = "results/bilingual_retrieval_after.json"
        put("retrieval.probes_n", on["probes"]["hybrid"]["n"], "offline", src)
        put("retrieval.probes_before", off["probes"]["hybrid"]["hit"], "offline", src)
        put("retrieval.probes_after", on["probes"]["hybrid"]["hit"], "offline", src)
        put("retrieval.dev_scored", on["dev"]["hybrid"]["scored"], "offline", src)
        put("retrieval.dev_before", off["dev"]["hybrid"]["expected_found"], "offline", src)
        put("retrieval.dev_after", on["dev"]["hybrid"]["expected_found"], "offline", src)
    if offline_facts and Path(offline_facts).exists():
        of = _load(Path(offline_facts))
        put("tests.passed", of["tests_passed"], "offline", "report/OFFLINE_FACTS.json")
        put("tests.skipped", of.get("tests_skipped", 0), "offline", "report/OFFLINE_FACTS.json")
        put("code.version", of.get("code_version", ""), "offline", "report/OFFLINE_FACTS.json")

    # ---- measured: probe ----
    probe_spend: Counter = Counter()  # every real probe call is spent money: it belongs in the total, as in `gapcourt cost`
    for pj in sorted(runs_root.glob("_probe/*/probe.json")) if runs_root.exists() else []:
        pr = _load(pj)
        if pr.get("test_double"):
            continue
        for att in pr.get("attempts") or []:
            c0 = att.get("call") or {}
            if c0:
                u0 = c0.get("usage") or {}
                probe_spend["calls"] += 1
                probe_spend["in"] += int(u0.get("input_tokens", 0))
                probe_spend["out"] += int(u0.get("output_tokens", 0))
                probe_spend["usd_micro"] += round(float(c0.get("cost_usd", 0)) * 1_000_000)
        call = (pr["attempts"][-1].get("call") or {}) if pr.get("attempts") else {}
        src = f"{pj.parent.parent.name}/{pj.parent.name}"
        put("probe.model", pr["model_id"], "measured", src)
        put("probe.accepted", pr.get("accepted") or "no variant accepted", "measured", src)
        usage = call.get("usage") or {}
        put("probe.tokens", f"{usage.get('input_tokens', 0)} in / {usage.get('output_tokens', 0)} out", "measured", src)
        put("probe.cost_usd", f"USD {call.get('cost_usd', 0):.6f}", "measured", src)

    # ---- measured: scenario and case runs ----
    live, ignored, unverified = _scan_runs(runs_root)
    agg: dict[str, Counter] = {g: Counter() for g in ("smoke", "live", "dev", "demo", "total")}
    rej: dict[str, Counter] = {g: Counter() for g in agg}
    stops: dict[str, Counter] = {g: Counter() for g in agg}
    for rel, d, s in live:
        c = s.get("cost") or {}
        contradicting = _contradicting_steps_run(d, s, scenarios_dir)
        for grp in (*_groups(rel), "total"):
            a = agg[grp]
            a["runs"] += 1
            a["calls"] += int(c.get("calls", 0))
            a["in"] += int(c.get("input_tokens", 0))
            a["out"] += int(c.get("output_tokens", 0))
            a["usd_micro"] += round(float(c.get("cost_usd", 0)) * 1_000_000)
            a["rejections"] += len(s.get("model_rejections") or [])
            a["revisions"] += len(s.get("revisions") or [])
            a["gaps"] += len(s.get("gaps") or [])
            a["contradicting_steps"] += contradicting
            for m in s.get("model_rejections") or []:
                rej[grp][m.get("reason", "unknown")] += 1
            for r in _step_records(d):
                g = r.get("gate") or {}
                a["kept"] += len(g.get("kept") or [])
                a["dropped"] += len(g.get("dropped") or [])
                for mc in r.get("model_calls") or []:
                    stops[grp][mc.get("stop_reason") or "none"] += 1
    for key, n in (("calls", "calls"), ("in", "in"), ("out", "out"), ("usd_micro", "usd_micro")):
        agg["total"][n] += probe_spend[key]
    for grp, a in agg.items():
        if not a["runs"] and not (grp == "total" and probe_spend["calls"]):
            continue
        src = f"{a['runs']} saved run(s)" + (f" and {probe_spend['calls']} probe call(s)" if grp == "total" and probe_spend["calls"] else "")
        put(f"{grp}.runs", a["runs"], "measured", src)
        put(f"{grp}.calls", a["calls"], "measured", src)
        put(f"{grp}.tokens_in", a["in"], "measured", src)
        put(f"{grp}.tokens_out", a["out"], "measured", src)
        put(f"{grp}.cost_usd", f"USD {a['usd_micro'] / 1_000_000:.4f}", "measured", src)
        put(f"{grp}.rejections", a["rejections"], "measured", src)
        put(f"{grp}.rejection_reasons", ", ".join(f"{k} {v}" for k, v in sorted(rej[grp].items())) or "none", "measured", src)
        put(f"{grp}.stop_reasons", ", ".join(f"{k} {v}" for k, v in sorted(stops[grp].items())) or "none", "measured", src)
        put(f"{grp}.statements_kept", a["kept"], "measured", src)
        put(f"{grp}.statements_dropped", a["dropped"], "measured", src)
        put(f"{grp}.revisions", a["revisions"], "measured", src)
        put(f"{grp}.gaps", a["gaps"], "measured", src)
        put(f"{grp}.contradicting_steps", a["contradicting_steps"], "measured", src)

    # ---- evidence index ----
    lines = ["Evidence index (saved runs under the runs folder; the hash chain of each log is verified with `gapcourt verify-log`).", ""]
    for rel, d, s in live:
        c = s.get("cost") or {}
        lines.append(f"- {rel}: {s.get('kind')} {s.get('scenario_id') or ','.join(s.get('case_ids', []))}; KB {s.get('kb_version')}; "
                     f"code {s.get('code_version')}; {c.get('calls', 0)} calls, USD {float(c.get('cost_usd', 0)):.4f} [measured]")
    for rel, d, s in ignored:
        lines.append(f"- {rel}: TEST DOUBLE run, no model was called; ignored")
    for rel, d, s, why in unverified:
        lines.append(f"- {rel}: log failed verification ({why}); not counted")
    if not live:
        lines.append("- [pending-live: no run that called the model exists yet]")
    blocks["evidence"] = "\n".join(lines)

    # ---- scored results ----
    rd = Path(results_dir) if results_dir else None
    if rd and (rd / "results.md").exists() and (rd / "results.json").exists():
        res_json = _load(rd / "results.json")
        stamp = res_json.get("stamp", {})
        counted = bool(stamp.get("run_ids"))  # a score with no counted run is not a measurement
        if not stamp.get("test_double") and counted:
            blocks["results"] = (f"[measured: gapcourt score at {stamp.get('scored_at', 'unknown time')}; runs {', '.join(stamp.get('run_ids', [])) or 'none'}]\n\n"
                                 + (rd / "results.md").read_text(encoding="utf-8").strip())
    facts["__blocks__"] = Fact(json.dumps(blocks), "offline")
    return facts


def _pending(key: str) -> str:
    hint = PENDING_HINT.get(key.split(".")[0], "live session")
    return f"[pending-live: needs the {hint}]"


KNOWN_PENDING_KEYS = {
    "probe.model", "probe.accepted", "probe.tokens", "probe.cost_usd",
    *{f"{g}.{k}" for g in ("smoke", "live", "dev", "demo", "total") for k in (
        "runs", "calls", "tokens_in", "tokens_out", "cost_usd", "rejections", "rejection_reasons", "stop_reasons",
        "statements_kept", "statements_dropped", "revisions", "gaps", "contradicting_steps")},
}
KNOWN_OFFLINE_KEYS = {"tests.passed", "tests.skipped", "code.version", "corpus.reproduced", "corpus.audited",
                      "cases.draft_total", "cases.draft_dev", "cases.draft_heldout", "cases.status",
                      "dossier.claims", "dossier.claims_verified", "dossier.findings", "dossier.findings_supported",
                      "retrieval.probes_n", "retrieval.probes_before", "retrieval.probes_after", "retrieval.dev_scored",
                      "retrieval.dev_before", "retrieval.dev_after"}


def fill_template(template: str, facts: dict) -> str:
    blocks = json.loads(facts["__blocks__"].value) if "__blocks__" in facts else {}

    def fact(m):
        key = m.group(1).strip()
        f = facts.get(key)
        if f is not None:
            return f"{f.value} [{f.tag}]"
        if key in KNOWN_PENDING_KEYS:
            return _pending(key)
        if key in KNOWN_OFFLINE_KEYS:
            return f"[pending-offline: run scripts/offline_facts.py for {key}]"
        raise ReportError(f"unknown fact key {key!r}")

    def block(m):
        name = m.group(1).strip()
        if name in blocks:
            return blocks[name]
        if name == "results":
            return "[pending-live: needs the heldout and repeats sessions, then `gapcourt score`]"
        raise ReportError(f"unknown block {name!r}")

    template = re.sub(r"<!--.*?-->\s*", "", template, flags=re.S)  # authoring comments never reach the output
    out = re.sub(r"\{\{fact:([^}]+)\}\}", fact, template)
    return re.sub(r"\{\{block:([^}]+)\}\}", block, out)


_ALLOWED_NUMERAL = re.compile(
    r"Y\.3172|ITU-T|\b(?:19|20)\d\d\b|\b[A-Z]{1,3}-?\d+[A-Za-z]?(?:-\d+)*\b|Article\s*\(?\d+\)?(?:\(\w\))?|Articles?\s+\d+|\(\d+\)(?:\(\w\))?|"
    r"clause\s+\d+(?:\.\d+)*|Clause\s+\d+(?:\.\d+)*|^\s*#+\s.*$|^\s*\d+\.\s|\bStep\s+\d\b|\bsteps?\s+\d(?:\s+to\s+\d)?\b|"
    r"\bD\d\b|\b\d+\s*(?:dimensions|factors)\b|Readiness\s+\d(?:\.\d)?|Report\s+\d\.\d|version\s+\d|\bv\d\b|\b0\.\d+\s+page|"
    r"\[\s*\d(?:\.\d)?\s*pages?\]|(?:Law|Resolution|Directive|Regulation|Decree-Law)\s+\d+(?:\s+of\s+\d{4})?|Haiku\s+\d\.\d|"
    r"\bsections?\s+\d+\b|\bR\d\b|(?:^|\|)\s*\d{1,2}(?:;\s*\d{1,2})*(?=\s+[A-Z&]|\s*\||\s*;)|;\s*\d{1,2}\b|\d{1,2}\s+of\s+\d{4}|"
    r"\b(?:Part|Principle)\s+\d\b|\b(?:Oct|Nov|Dec)\s+\d{1,2}\b|\bM-\d\d-\d\d\b|"
    r"\bpage\s+budget\b|\b5-page\b|\bfive\b|\b\d+\s*(?:USD|CHF)\b|\bUSD\s*\d+\b|[T]ODO-\w+|`[^`]*`", re.M)
_TOKEN = re.compile(r"\{\{[^}]+\}\}")


def lint_numerals(template: str) -> list[str]:
    """Numerals in the template prose that are not a tagged fact and not an identifier (article numbers, node ids, dates)."""
    text = _TOKEN.sub(" ", re.sub(r"<!--.*?-->", " ", template, flags=re.S))
    text = _ALLOWED_NUMERAL.sub(" ", text)
    return re.findall(r"(?<![\w.])\d+(?:[.,]\d+)?(?![\w.]*\w)", text)


def estimate_pages(text: str) -> float:
    return len(re.findall(r"\S+", text)) / WORDS_PER_PAGE
