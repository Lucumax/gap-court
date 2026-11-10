"""Label review for the freeze.

`render_cards` writes one printable page: for each case the question, the draft expected outcome, the passage text the label points
to, and whether the label's basis quotation is found in that passage. It takes no run output, so a reviewer cannot see a model
answer while reviewing. `approve_labels` writes the approved label file in one step: named cases (or all) become APPROVED, cases
with corrections become CORRECTED (the original values are kept), and nothing is overwritten.
"""
from __future__ import annotations

import html as _html
import json
from pathlib import Path

from .eval import CATEGORIES, OUTCOMES, SPLITS, EvalError, load_labels
from .verify import check_quotation

CSS = ("body{font:17px/1.5 system-ui,sans-serif;max-width:56rem;margin:1rem auto;padding:0 1rem;color:#111;background:#fff}"
       ".card{border:2px solid #666;border-radius:8px;padding:.7rem 1rem;margin:1rem 0;break-inside:avoid}"
       "blockquote{margin:.4rem 0;padding:.3rem .8rem;border-left:5px solid #777;background:#f4f4f4;white-space:pre-wrap}"
       ".prop{border:2px dashed #8a6d00;border-radius:6px;padding:.3rem .8rem;margin:.6rem 0}"
       ".ok{color:#1b5e20;font-weight:700}.bad{color:#b71c1c;font-weight:700}.box{border:1px solid #444;display:inline-block;width:1.1rem;height:1.1rem;vertical-align:middle}"
       "@media (prefers-color-scheme:dark){body{background:#121212;color:#eee}blockquote{background:#222}}"
       "@media print{body{max-width:none;font-size:11pt}}")


def _e(x) -> str:
    return _html.escape(str(x), quote=True)


def _passages(kb, doc_id: str, locator: str) -> list:
    return [e.chunk for e in kb.entries if e.chunk.doc_id == doc_id and (e.chunk.locator == locator or e.chunk.locator.startswith(locator + " "))]


LEGEND = ("<h2>What the outcome words mean (the wording the adviser is given)</h2><ul>"
          "<li><b>ANSWER</b>: a quoted passage from a binding instrument answers the question (ALLOW or DENY).</li>"
          "<li><b>ABSTAIN</b>: the question is about a subject, instrument or authority that none of the listed instruments concerns "
          "(the document that would govern it is not among them).</li>"
          "<li><b>GAP</b>: the reviewed instruments cover the subject but no passage states a rule on the point asked.</li>"
          "<li><b>ESCALATE</b>: the passages disagree or are unclear for this actor; a person decides.</li>"
          "<li><b>SCOPE_FINDING</b>: a scope or exclusion clause of an instrument excludes the actor asked about.</li></ul>")


def _card(r: dict, kb, correction: dict | None = None, corrections_name: str | None = None) -> str:
    exp = r.get("expected") or []
    body = [f"<div class='card'><h2>{_e(r['case_id'])} <small>({_e(r['split'])}, {_e(r['category'])})</small></h2>",
            f"<p><strong>Question:</strong> {_e(r['question'])}</p>",
            f"<p><strong>Draft expected outcome:</strong> {_e(r['expected_outcome'])}. <strong>Status:</strong> {_e(r.get('status'))}.</p>"]
    if r.get("blocked_on"):
        body.append(f"<p class='bad'>BLOCKED: {_e(r['blocked_on'])}. This case is not run, approved or frozen until the blocker is cleared.</p>")
    if not exp:
        body.append("<p>Expected passages: none; no governing passage is expected in the knowledge base for this case.</p>")
    found_any = False
    for ex in exp:
        chunks = _passages(kb, ex["doc_id"], ex["locator"])
        body.append(f"<p><strong>Expected passage:</strong> {_e(ex['doc_id'])} {_e(ex['locator'])}</p>")
        if not chunks:
            body.append("<p class='bad'>PASSAGE NOT FOUND in this knowledge base: fix the locator or the label.</p>")
        for c in chunks:
            body.append(f"<blockquote>{_e(c.text)}</blockquote>")
            if r.get("basis_quote") and check_quotation(r["basis_quote"], c.text).verified:
                found_any = True
    q = r.get("basis_quote") or ""
    if q:
        body.append(f"<p><strong>Basis quotation in the label:</strong> &ldquo;{_e(q)}&rdquo; "
                    + ("<span class='ok'>quote found in the expected passage</span>" if found_any
                       else "<span class='bad'>QUOTE NOT FOUND in the expected passage(s)</span>") + "</p>")
    if correction is not None:
        body.append(_proposal(correction, kb, r, corrections_name))
    body.append("<p>Is the expected outcome right, given only this text? &nbsp; <span class='box'></span> yes &nbsp; "
                "<span class='box'></span> no, correct to: ______________</p></div>")
    return "".join(body)


def _proposal(changes: dict, kb, r: dict, name: str | None) -> str:
    """A proposed correction shown on the card. It is not applied here: approve-labels applies it only when the reviewer
    passes the corrections file."""
    where = f" in <code>{_e(name)}</code>" if name else ""
    out = [f"<div class='prop'><p><strong>Proposed correction{where}, NOT applied.</strong> It takes effect only if you pass this "
           "file to approve-labels; the draft above is unchanged.</p><ul>"]
    for k, v in changes.items():
        if k in ("expected", "basis_quote"):
            continue
        out.append(f"<li>{_e(k)}: {_e(r.get(k))} &rarr; {_e(v)}</li>")
    out.append("</ul>")
    if "blocked_on" in changes and not changes["blocked_on"] and r.get("blocked_on"):
        out.append("<p class='ok'>This correction clears the blocker: the case would be approved, frozen and scored.</p>")
    elif changes.get("blocked_on"):
        out.append(f"<p class='bad'>This correction blocks the case: {_e(changes['blocked_on'])}</p>")
    q = changes.get("basis_quote", r.get("basis_quote")) or ""
    found_any = False
    for ex in changes.get("expected") or []:
        chunks = _passages(kb, ex["doc_id"], ex["locator"])
        out.append(f"<p><strong>Proposed expected passage:</strong> {_e(ex['doc_id'])} {_e(ex['locator'])}</p>")
        if not chunks:
            out.append("<p class='bad'>PASSAGE NOT FOUND in this knowledge base.</p>")
        for c in chunks:
            out.append(f"<blockquote>{_e(c.text)}</blockquote>")
            if q and check_quotation(q, c.text).verified:
                found_any = True
    if q and changes.get("expected"):
        out.append(f"<p><strong>Proposed basis quotation:</strong> &ldquo;{_e(q)}&rdquo; "
                   + ("<span class='ok'>quote found in the proposed passage</span>" if found_any
                      else "<span class='bad'>QUOTE NOT FOUND in the proposed passage(s)</span>") + "</p>")
    out.append("</div>")
    return "".join(out)


def render_cards(labels: list[dict], kb, corrections: dict | None = None, corrections_name: str | None = None) -> str:
    parts = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>Label review cards</title><style>{CSS}</style></head><body>",
             f"<h1>Label review cards</h1><p>Knowledge base {_e(kb.version)}. {len(labels)} cases. Review each card from its source passage, "
             "without seeing model answers; none are shown here. Local use only: this page contains link-only text and is never part of the public export.</p>",
             (f"<p>Dashed boxes show proposed corrections from <code>{_e(corrections_name or 'the corrections file')}</code>; none is applied "
              "until you pass that file to approve-labels.</p>" if corrections else ""),
             LEGEND]
    for title, split in (("Held-out cases (review from the passage alone)", "heldout"), ("Development cases", "dev")):
        rows = [r for r in labels if r["split"] == split]
        if rows:
            parts.append(f"<h2>{title}</h2>")
            parts += [_card(r, kb, (corrections or {}).get(r["case_id"]), corrections_name) for r in rows]
    parts.append("</body></html>")
    return chr(10).join(parts)


def approve_labels(src: str | Path, out: str | Path, reviewer: str, approve="all", corrections: dict | None = None) -> list[dict]:
    if not reviewer or not reviewer.strip():
        raise EvalError("a named reviewer is required")
    out = Path(out)
    if out.exists():
        raise EvalError(f"{out} exists; approved labels are written once (record a correction as a new file)")
    rows = load_labels(src)
    ids = {r["case_id"] for r in rows}
    corrections = corrections or {}
    blocked = {r["case_id"] for r in rows if r.get("blocked_on")}
    if approve != "all" and (set(approve) & blocked):
        raise EvalError("blocked case(s) cannot be approved until the blocker is cleared by a correction "
                        f"(blocked_on = null): {', '.join(sorted(set(approve) & blocked))}")
    wanted = (ids - blocked) if approve == "all" else set(approve)
    unknown = (set(wanted) | set(corrections)) - ids
    if unknown:
        raise EvalError(f"unknown case id(s): {', '.join(sorted(unknown))}")
    allowed = {"split": SPLITS, "category": CATEGORIES, "expected_outcome": OUTCOMES}
    new = []
    for r in rows:
        r = dict(r)
        cid = r["case_id"]
        if cid in corrections:
            changes = corrections[cid]
            for k, v in changes.items():
                if k in allowed and v not in allowed[k]:
                    raise EvalError(f"{cid}: {k} must be one of {allowed[k]}")
                if k in ("case_id", "status"):
                    raise EvalError(f"{cid}: {k} cannot be corrected")
            r["original"] = {k: r.get(k) for k in changes}
            r.update(changes)
            r["status"] = "CORRECTED"
            r["reviewed_by"] = reviewer.strip()
        elif cid in wanted:
            r["status"] = "APPROVED"
            r["reviewed_by"] = reviewer.strip()
        new.append(r)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in new) + "\n", encoding="utf-8", newline="\n")
    load_labels(out)  # the written file must pass the same validation the freeze uses
    return new
