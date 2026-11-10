"""Static, self-contained trace page for policy readers. No scripts, no external resources, no logos.

One card per released step: the step text, one block per role with a plain decision word, the reason, each quotation with a
"quotation verified" badge and a link to the official source, any change of position ("changed from X to Y because ..."), gap
cards that say in words what kind of gap it is, grey cards for model answers that were rejected, and the ruling. Hashes and
model-call details sit in a collapsed section. The page makes no claim beyond what the log shows.
"""
from __future__ import annotations

import html as _html
import json
import re

from .audit import verify_records

CSS = (
    "body{font-size:18px;line-height:1.55;font-family:system-ui,sans-serif;max-width:62rem;margin:1rem auto;padding:0 1rem;color:#1a1a1a;background:#fff}"
    "h1{font-size:1.5rem;margin:.2rem 0}h2{font-size:1.25rem;margin:.2rem 0}h3{font-size:1.05rem;margin:.6rem 0 .2rem}"
    ".card{border:2px solid #888;border-radius:8px;padding:.7rem 1rem;margin:1rem 0;background:#fff}"
    ".role{border-top:1px solid #ccc;margin-top:.6rem;padding-top:.4rem}"
    ".chips span{display:inline-block;border:1px solid #444;border-radius:999px;padding:0 .7rem;margin:.1rem .3rem .1rem 0;font-size:.85rem}"
    ".chip{display:inline-block;border-radius:6px;padding:0 .5rem;font-weight:700}"
    ".ESCALATE{background:#fff1cc;color:#5c3b00}.ALLOW{background:#d9f2dc;color:#0d4a15}.DENY{background:#fbdada;color:#7a0b0b}"
    ".GAP{background:#eadcf8;color:#38105f}.ABSTAIN{background:#e6e6e6;color:#333}.MODEL_REJECTED{background:#e6e6e6;color:#333}"
    ".rejected{background:#eee;border-color:#999;color:#333}.gap{border-color:#7b3fb0}"
    "blockquote{margin:.4rem 0;padding:.3rem .8rem;border-left:5px solid #2e7d32;background:#f3f8f3}"
    "blockquote footer{font-size:.85rem;color:#333}.badge{background:#2e7d32;color:#fff;border-radius:4px;padding:0 .4rem}"
    ".tag{border:1px solid #555;border-radius:4px;padding:0 .4rem}.revision{border-left:5px solid #b26a00;padding-left:.6rem;font-weight:600}"
    ".ok{color:#1b5e20;font-weight:700}.bad{color:#b71c1c;font-weight:700}.dropped{color:#555}"
    "code{font:13px ui-monospace,monospace;word-break:break-all}details{margin-top:.4rem;font-size:.9rem}a{color:#0b3d91}"
    "@media (prefers-color-scheme:dark){body{background:#121212;color:#eee}.card{background:#1b1b1b;border-color:#777}"
    "blockquote{background:#16261a}.rejected{background:#2a2a2a}a{color:#9cc2ff}.ESCALATE,.ALLOW,.DENY,.GAP,.ABSTAIN,.MODEL_REJECTED{filter:brightness(.95)}}"
    "@media print{body{max-width:none;font-size:11pt;color:#000;background:#fff}.card{break-inside:avoid;border-color:#000}details{display:block}}"
    "@page{size:A4;margin:14mm}")

DECISION_WORDS = {
    "ALLOW": "Allow (a proposal)", "DENY": "Deny (a proposal)", "ESCALATE": "Escalated to a person",
    "GAP": "No applicable passage found", "ABSTAIN": "Abstained", "MODEL_REJECTED": "Model answer rejected",
}
ROLE_WORDS = {
    "service": "Service agent (simulated Federal Household Benefit Service)",
    "adviser": "Policy and legal adviser",
    "custodian": "Records office (data custodian, simulated)",
    "oversight": "Complaints and oversight office (simulated)",
}
GAP_WORDS = {
    "retrieval_miss": "retrieval miss (a passage may exist but the searches did not find it)",
    "corpus_coverage_gap": "corpus coverage gap (no document in the knowledge base covers this)",
    "ambiguity_or_conflict": "ambiguity or conflict between sources",
    "implementation_gap": "implementation gap (a rule with no procedure)",
    "unresolved": "unresolved (not yet typed or reviewed by an analyst)",
    "candidate_policy_gap": "candidate policy gap (reviewed by a named analyst)",
}
DROP_WORDS = {
    "chunk_not_retrieved": "the cited passage was not among the passages retrieved for this step",
    "collection_not_allowed": "the cited passage is outside what this role may see",
    "instruction_like_quote": "the quoted text looked like an instruction, not a rule",
}
_SAFE_URL = re.compile(r"^https://[^\s\"'<>]+$")


def sources_from_kb(kb) -> dict:
    """doc_id -> first https address in the document's source_url field (some fields add a note after the address)."""
    out = {}
    for row in kb.coverage_table():
        m = re.search(r"https://[^\s\"'<>]+", str(row.get("source_url", "")))
        if m:
            url = m.group(0)
            while url.endswith((")", ".", ",", ";")) and (not url.endswith(")") or url.count(")") > url.count("(")):
                url = url[:-1]
            out[row["doc_id"]] = url
    return out


def _e(x) -> str:
    return _html.escape(str(x), quote=True)


def _drop_reason(code: str) -> str:
    if str(code).startswith("quotation_"):
        return "the quoted words were not found in the cited passage"
    return DROP_WORDS.get(code, "the statement could not be checked against a retrieved passage")


def _link(cite: str, doc_id: str, sources: dict) -> str:
    url = (sources or {}).get(doc_id, "")
    if url and _SAFE_URL.match(url):
        return f'<a href="{_e(url)}">{_e(cite)}</a>'
    return _e(cite)


def _clip(text: str, n: int = 900) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "..."


def _step_text(rec: dict) -> str:
    inp = rec.get("input") or {}
    n = inp.get("step_no")
    for m in inp.get("messages") or []:
        prefix = f"[step {n}] "
        if n is not None and str(m).startswith(prefix):
            return str(m)[len(prefix):]
    msgs = inp.get("messages") or []
    return str(msgs[-1]) if msgs else ""


def _group(records: list[dict]):
    """Cards by step id, in release order; records without a step id each get their own card."""
    cards: dict[str, dict] = {}
    order: list[str] = []
    title, synthetic, kb_version = "", False, ""
    models: list[str] = []
    for r in records:
        if r.get("kind") == "event":
            p = r.get("payload") or {}
            ev = r.get("event")
            if ev == "scenario_start":
                title, synthetic, kb_version = p.get("title", ""), bool(p.get("synthetic")), p.get("kb_version", "")
                continue
            sid = p.get("step_id")
            if sid is None:
                continue
            c = cards.setdefault(sid, {"id": sid, "no": None, "text": "", "roles": [], "revisions": [], "gaps": [], "ruling": None, "other": []})
            if ev == "scenario_step_released":
                c["no"] = p.get("step_no")
                if sid not in order:
                    order.append(sid)
            elif ev == "revision":
                c["revisions"].append(p)
            elif ev == "gap_opened":
                c["gaps"].append(p)
            elif ev == "ruling":
                c["ruling"] = p
            if sid not in order:
                order.append(sid)
        elif r.get("kind") == "step":
            inp = r.get("input") or {}
            sid = inp.get("step_id") or inp.get("case_id") or f"record-{r.get('seq')}"
            c = cards.setdefault(sid, {"id": sid, "no": inp.get("step_no"), "text": "", "roles": [], "revisions": [], "gaps": [], "ruling": None, "other": []})
            if sid not in order:
                order.append(sid)
            c["roles"].append(r)
            if not c["text"]:
                c["text"] = _step_text(r)
            if c["no"] is None:
                c["no"] = inp.get("step_no")
            if r.get("model_id") and r["model_id"] not in models:
                models.append(r["model_id"])
    return [cards[s] for s in order], title, synthetic, kb_version, models


def _role_block(r: dict, sources: dict) -> str:
    role = r.get("role", "")
    dec = r.get("decision", "")
    word = DECISION_WORDS.get(dec, dec)
    gate = r.get("gate") or {}
    head = f"<h3>{_e(ROLE_WORDS.get(role, role))} <span class='chip {_e(dec)}'>{_e(word)}</span></h3>"
    reason = f"<p>{_e(_clip(str(r.get('rationale_summary', ''))))}</p>"
    quotes = []
    for k in gate.get("kept") or []:
        tag = "binding instrument" if k.get("binding") else "non-binding source"
        quotes.append(
            f"<blockquote>&ldquo;{_e(k.get('quote', ''))}&rdquo;<footer><span class='badge'>quotation verified</span> "
            f"<span class='tag'>{tag}</span> {_link(str(k.get('cite', '')), str(k.get('doc_id', '')), sources)}</footer></blockquote>")
    for s in gate.get("scope_findings") or []:
        quotes.append(
            f"<p><strong>Scope finding:</strong> {_e(s.get('statement', ''))}</p><blockquote>&ldquo;{_e(s.get('quote', ''))}&rdquo;"
            f"<footer><span class='badge'>quotation verified</span> {_link(str(s.get('doc_id', '')) + ' ' + str(s.get('per', '')), str(s.get('doc_id', '')), sources)}</footer></blockquote>")
    dropped = "".join(f"<li>A statement was dropped: {_e(_drop_reason(str(d.get('reason', ''))))}.</li>" for d in gate.get("dropped") or [])
    dropped = f"<ul class='dropped'>{dropped}</ul>" if dropped else ""
    calls = r.get("model_calls") or []
    call_txt = "; ".join(
        f"attempt {_e(c.get('attempt', ''))}: stop {_e(c.get('stop_reason'))}, {_e((c.get('usage') or {}).get('input_tokens', ''))} in / "
        f"{_e((c.get('usage') or {}).get('output_tokens', ''))} out, USD {_e(c.get('cost_usd', ''))}, "
        f"reasoning: {_e((c.get('reasoning_summary') or {}).get('label', ''))}" for c in calls)
    details = (
        f"<details><summary>Record details</summary><div>record #{_e(r.get('seq'))} {_e(r.get('timestamp'))}; rule applied: {_e(r.get('rule_applied'))}; "
        f"query: {_e(_clip(str(r.get('query', '')), 300))}</div><div>model {_e(r.get('model_id'))}; kb {_e(r.get('kb_version'))}; code {_e(r.get('code_version'))}</div>"
        f"<div>{call_txt}</div><div>input: <code>{_e(json.dumps(r.get('input'), ensure_ascii=False))}</code></div><div>prompt <code>{_e(str(r.get('prompt_sha256', ''))[:16])}</code> config <code>{_e(str(r.get('config_sha256', ''))[:16])}</code> "
        f"prev <code>{_e(str(r.get('prev_hash', ''))[:16])}</code> hash <code>{_e(str(r.get('record_hash', ''))[:16])}</code></div></details>")
    cls = "card rejected" if dec == "MODEL_REJECTED" else "role"
    return f"<div class='{cls}'>{head}{reason}{''.join(quotes)}{dropped}{details}</div>"


def render_trace_html(records: list[dict], digest: dict | None = None, sources: dict | None = None) -> str:
    sources = sources or {}
    res = verify_records(records, expected=digest)
    cards, title, synthetic, kb_version, models = _group(records)
    double = any(str(m).startswith("TEST-DOUBLE") for m in models)
    status = ("<p class='ok'>internal consistency verified"
              + (" against the retained digest" if digest else " (no retained digest supplied)") + "</p>") if res.ok else \
        "<p class='bad'>verification FAILED</p><ul>" + "".join(f"<li>{_e(x)}</li>" for x in res.errors) + "</ul>"
    chips = ["<span>SYNTHETIC world: simulated entities, real instruments</span>"] if synthetic else []
    chips += [f"<span>model: {_e(', '.join(models))}</span>"] if models else []
    chips += [f"<span>KB: {_e(kb_version)}</span>"] if kb_version else []
    chips.append("<span>log verified</span>" if res.ok else "<span>log NOT verified</span>")
    if double:
        chips.append("<span>TEST DOUBLE: no model was called</span>")
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>Gap Court trace</title><style>{CSS}</style></head><body>",
        f"<h1>Gap Court trace{(': ' + _e(title)) if title else ''}</h1>",
        "<p>Independent entry. Not affiliated with or endorsed by ITU.</p>",
        f"<div class='chips'>{''.join(chips)}</div>",
    ]
    if synthetic:
        parts.append("<p><strong>SYNTHETIC scenario.</strong> Entities, people and services are simulated; only the quoted instruments are real.</p>")
    if double:
        parts.append("<p class='bad'>TEST DOUBLE run: the answers below were not produced by a model and are not a result.</p>")
    parts.append(status)
    parts.append("<p>This check detects inconsistent alteration, truncation and reordering. It does not establish authorship or that any statement is true. "
                 "A quotation marked verified means the quoted words appear in the cited passage; it is not a legal interpretation.</p>")
    for c in cards:
        head = f"Step {_e(c['no'])}" if c.get("no") is not None else _e(c["id"])
        body = [f"<section class='card' id='{_e(c['id'])}'><h2>{head}</h2>"]
        if c["text"]:
            body.append(f"<p>{_e(c['text'])}</p>")
        for r in c["roles"]:
            body.append(_role_block(r, sources))
        for rv in c["revisions"]:
            body.append(f"<p class='revision'>&rarr; {_e(ROLE_WORDS.get(rv.get('role'), rv.get('role')))} changed from {_e(rv.get('from'))} to "
                        f"{_e(rv.get('to'))} because {_e(rv.get('reason'))}</p>")
        for g in c["gaps"]:
            gt = GAP_WORDS.get(g.get("gap_type"), g.get("gap_type"))
            qs = "".join(f"<li><code>{_e(q)}</code></li>" for q in g.get("queries") or [])
            body.append(f"<div class='card gap'><h3>Gap: {_e(gt)}</h3><p>{_e(g.get('bounded_finding', ''))}</p>"
                        f"<p>Requirement at issue: {_e(g.get('requirement', ''))}</p><p>Searches recorded:</p><ul>{qs}</ul>"
                        f"<p>{_e(g.get('caveat', ''))}</p></div>")
        if c["ruling"]:
            rl = c["ruling"]
            body.append(f"<p class='ruling'><strong>Ruling (a proposal):</strong> {_e(DECISION_WORDS.get(rl.get('decision'), rl.get('decision')))}. "
                        f"Decided by: {_e(rl.get('final_authority'))}. {_e(rl.get('reason'))}.</p>")
        body.append("</section>")
        parts.append("".join(body))
    parts.append("</body></html>")
    return "\n".join(parts)
