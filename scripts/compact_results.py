#!/usr/bin/env python3
"""Compact the scored-results block of the filled report draft before the Word build.

`gapcourt report-fill` pastes the whole `results.md` (its `#` and `##` headings, three stamp bullets with long hashes, eight
sub-tables) into the report. The Word build numbers every heading, so that raw block alone renders about 7 pages. This helper
replaces it, in a temporary copy of the draft, with the compact block the template expects:

  1. the stamp line (the `[measured: gapcourt score at ...; runs ...]` line `report-fill` wrote, plus split, labels, freeze, code, KB, model);
  2. the section 2b paired comparison with its prose and table, copied line for line;
  3. the false-gap and false-answer rows of section 3 (the least flattering numbers), cells copied verbatim into one sentence; a missing row stops the tool;
  4. one summary sentence for the scenario-step and revision counts (sections 4 and 5), one entry per run, cells copied verbatim;
  5. where the draft carries a `{{headline: ...}}` token (the opening of the report), the held-out headline sentence: the "all" row
     of section 2, full system and baseline cells copied verbatim, with its limits stated; a missing row stops the tool.

Warnings and exposure notes that results.md carries (test-double banner, code differs from the freeze, cases left out, not run or
blocked, runs not counted, judge-added runs, no KB-release receipt, scenario run before the freeze, expected rulings not frozen) are
kept as plain text, never dropped. Sections 1, 2, 6, 7 and 8 of results.md (retrieval, per-category agreement,
quotations, rejections and cost, KB release) are NOT in the compact block; the stamp line points to results.md for them.
Nothing is recomputed or rounded: every number token of the output is a whole token of results.md (a weak check, see numbers_not_in).

    python scripts/compact_results.py --draft docs/REPORT_DRAFT_5PAGE.md --results results/heldout/results.md --out TEMP/draft_compact.md
    python scripts/compact_results.py --results results/heldout/results.md        # prints the compact block only

The tracked draft and the results file are never modified; --out must differ from both. Standard library only.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

STAMP_RE = re.compile(r"^\[measured: gapcourt score at [^\n]*\]$", re.M)
NUM_TOKEN = re.compile(r"\d+(?:[.,]\d+)*")
HEX_WORD = re.compile(r"\b[0-9a-f]{8,}\b")
PIPE = re.compile(r"(?<!\\)\|")
BACKSLASH_PIPE = chr(92) + "|"

H_2B = ["count of cases", "full system", "retrieval plus abstain"]
H_4 = ["run", "scenario", "matched", "expected rulings frozen", "earlier run before freeze"]
H_5 = ["run", "revised", "stale rulings left standing"]
H_3 = ["", "full system", "baseline"]
H_2 = ["case type", "full system", "baseline"]
HEADLINE_RE = re.compile(r"\{\{headline:[^}]*\}\}")
FALSE_ROWS = ["gap where the label has an answer", "answer where the label has a gap or abstention"]


class CompactError(Exception):
    pass


def _section(md: str, prefix: str) -> list[str]:
    """Lines of the `## <prefix>...` section, without its heading, up to the next `## ` heading ([] when the section is absent)."""
    lines = md.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("## " + prefix):
            out = []
            for nxt in lines[i + 1:]:
                if nxt.startswith("## "):
                    break
                out.append(nxt)
            return out
    return []


def _cells(line: str) -> list[str]:
    return [c.replace(BACKSLASH_PIPE, "|").strip() for c in PIPE.split(line.strip())[1:-1]]


def _table(section: list[str], header: list[str], what: str) -> tuple[list[str], list[list[str]]]:
    """(raw lines, body rows) of the FIRST contiguous table of a section; the header must be exactly `header`; every row has len(header) cells."""
    raw, started = [], False
    for ln in section:
        if ln.lstrip().startswith("|"):
            raw.append(ln.rstrip())
            started = True
        elif started:
            break
    if not raw:
        return [], []
    if len(raw) < 2 or [c.lower() for c in _cells(raw[0])] != header:  # header + separator only is a valid empty table
        raise CompactError(f"{what}: the table header is not {' | '.join(header)}; the results.md layout changed, compact by hand")
    rows = [_cells(ln) for ln in raw[2:]]
    bad = [r for r in rows if len(r) != len(header)]
    if bad:
        raise CompactError(f"{what}: a row has {len(bad[0])} cells, expected {len(header)} ({' | '.join(bad[0])}); compact by hand")
    return raw, rows


def _bullet(md: str, key: str) -> str:
    m = re.search(rf"^- {re.escape(key)}:\s*(.*)$", md, re.M)
    return m.group(1).strip() if m else ""


def _short(hexstr: str) -> str:
    return hexstr[:12]


def _stamp_detail(md: str, ref: str) -> str:
    """One clause from the stamp bullets; long hashes cut to 12 characters (said so); run ids stay in results.md (`ref`)."""
    parts = []
    split = _bullet(md, "split")
    if split:
        m = re.match(r"([^;]+); labels: (\S+) sha256 `([0-9a-f]{64})` \((.*)\)$", split)
        parts.append(f"split {m.group(1)}; {m.group(2)} {m.group(4)} labels, sha256 prefix {_short(m.group(3))}" if m else f"split {split}")
    freeze = _bullet(md, "freeze record")
    fm = re.search(r"`([0-9a-f]{64})`", freeze)
    parts.append(f"freeze record prefix {_short(fm.group(1))}" if fm else "freeze record none")
    code = _bullet(md, "code")
    if code:
        parts.append(f"code {code}")
    kb = _bullet(md, "KB version")
    km = re.match(r"`([^`]+)`; models: ([^;]*); effort: (.*)$", kb)
    if km:
        parts.append(f"KB {km.group(1)}; {km.group(2)}, effort {km.group(3)}")
    return "; ".join(parts) + f"; run ids, full hashes: {ref}"


def _warnings(md: str) -> str:
    """Banner, code-differs line, the not-counted lines of section 2, runs not counted, judge-added runs and a missing KB receipt, verbatim."""
    out = []
    lines = md.splitlines()
    if len(lines) > 2 and lines[2].startswith("**") and lines[2].endswith("**"):
        out.append(lines[2].strip("*").strip())
    m = re.search(r"^- \*\*(Code differs from the frozen code.*?)\*\*$", md, re.M)
    if m:
        out.append(m.group(1))
    for ln in _section(md, "2. "):
        if re.match(r"(Left out as exposed|Not run, so not|Blocked on a missing)", ln):
            out.append(ln.strip())
    left_out = [ln[2:].strip() for ln in _section(md, "Runs not counted") if ln.startswith("- ")]  # score.py: second held-out run, test double, ...
    if left_out:
        out.append("Runs not counted: " + "; ".join(left_out))
    for ln in lines:
        if ln.startswith("Judge-added step runs"):
            out.append(ln.strip())
    kb = [ln.strip() for ln in _section(md, "8. ") if ln.strip().lower().startswith("no kb-release receipt")]
    out += [k[0].upper() + k[1:] for k in kb]
    return " ".join(x if x.endswith((".", ":")) else x + "." for x in out)


def _false_findings(md: str) -> str:
    """Section 3 of results.md, the least flattering numbers (false gap findings, false answers): both rows, labels and cells copied verbatim into one
    sentence (a table would cost about one line more than the worst-warnings page budget has). A missing section, header or row is an error."""
    _, rows = _table(_section(md, "3. False gap findings and false answers"), H_3, "section 3")
    parts = []
    for key in FALSE_ROWS:
        hit = [r for r in rows if r[0].lower() == key]
        if len(hit) != 1:
            raise CompactError(f"section 3 has no single row '{key}'; the results.md layout changed, compact by hand")
        parts.append(f"{hit[0][0]} {hit[0][1]} vs {hit[0][2]}")
    return "False gap findings and false answers, full system vs baseline (results.md section 3): " + "; ".join(parts) + "."


def headline_sentence(md: str) -> str:
    """The headline for the opening of the report, from the "all" row of section 2 of results.md (cells verbatim)."""
    _, rows = _table(_section(md, "2. Outcome agreement"), H_2, "section 2")
    hit = [r for r in rows if r[0].lower() == "all"]
    if len(hit) != 1:
        raise CompactError("section 2 has no single row 'all'; the results.md layout changed, write the headline by hand")
    return (f"Held-out result (frozen labels, each case run once): the full system agreed with the approved label in {hit[0][1]} cases, "
            f"the retrieval-plus-abstain baseline in {hit[0][2]}. Small sample, author-made cases, one reviewer: this shows agreement "
            "on a pilot set, not legal correctness.")


def fill_headline(draft: str, results_md: str) -> str:
    """Replace each `{{headline: ...}}` token with the headline sentence; a draft without the token is returned unchanged."""
    if not HEADLINE_RE.search(draft):
        return draft
    sentence = headline_sentence(results_md)
    return HEADLINE_RE.sub(lambda _m: sentence, draft)


def _shown(run_id: str) -> str:
    """Run id as shown in the report: without the final @hash (the join key keeps it)."""
    return re.sub(r"@[0-9a-f]+$", "", run_id)


def _summary_sentence(md: str) -> str:
    _, steps = _table(_section(md, "4. Scenario steps"), H_4, "section 4")
    _, revs = _table(_section(md, "5. Revisions"), H_5, "section 5")
    rev_by_run = {}
    for r in revs:  # section 5 labels read "run (S-01)" or "run (S-04 (development demonstration, ...))": id up to the FIRST " (", label the rest
        m = re.match(r"^(.*?) \((.*)\)$", r[0])
        key, label = (m.group(1), m.group(2)) if m else (r[0], "")
        if key in rev_by_run:
            raise CompactError(f"section 5 lists the run {key} twice; compact by hand")
        rev_by_run[key] = (r, label)
    seen_steps = set()
    for r in steps:
        if r[0] in seen_steps:
            raise CompactError(f"section 4 lists the run {r[0]} twice; compact by hand")
        seen_steps.add(r[0])

    def annotation(label: str) -> str:
        m = re.search(r"\((.*)\)", label)
        return m.group(1).replace(", reported separately", "") if m else ""

    entries = []
    for r in steps:
        e = f"{_shown(r[0])} {r[2]}"
        if r[0] in rev_by_run:
            e += f", revised {rev_by_run[r[0]][0][1]}, stale {rev_by_run[r[0]][0][2]}"
        flags = [annotation(r[1])] if "(" in r[1] else []
        if r[3] != "yes":
            flags.append("expected rulings not frozen")
        if r[4] == "yes":
            flags.append("earlier run before the freeze")
        entries.append(e + (f" ({'; '.join(flags)})" if flags else ""))
    for key in sorted(set(rev_by_run) - seen_steps):  # a revision row with no section 4 row: never dropped
        r, label = rev_by_run[key]
        entries.append(f"{_shown(key)} revised {r[1]}, stale {r[2]}" + (f" ({annotation(label)})" if annotation(label) else ""))
    if not entries:
        return "No scenario step or revision was scored."
    return ("Scenario steps matching the expected ruling, per run, with revisions after contradicting steps and stale rulings left standing: "
            + "; ".join(entries) + ".")


REF = "results/heldout/results.md"


def compact_block(results_md: str, stamp_line: str | None = None, ref: str = REF) -> str:
    """The compact replacement for the raw results block."""
    sec = _section(results_md, "2b. Paired comparison on identical cases")
    if not sec:
        raise CompactError("results.md has no '## 2b. Paired comparison on identical cases' section; nothing to compact")
    raw, rows = _table(sec, H_2B, "section 2b")
    if not rows:
        raise CompactError("section 2b has no table; the results.md layout changed, compact by hand")
    detail = _stamp_detail(results_md, ref)
    if stamp_line is None:
        stamp_line = "[measured: gapcourt score]"
    stamp_line = re.sub(r"; runs [^\]]*\]$", "]", stamp_line)  # the run ids live in results.md and the evidence index
    warn = _warnings(results_md)
    out = [f"{stamp_line} {detail}."]
    if warn:
        out += ["", warn]
    # section 2b whole (leading sentence, table, definition, pilot limit), then the false-gap and false-answer rows of section 3, then the steps
    out += ["", "\n".join(sec).strip(), "", _false_findings(results_md), "", _summary_sentence(results_md)]
    return "\n".join(out).strip()


def locate_raw_block(draft: str, results_md: str) -> tuple[int, int, str]:
    """(start, end, stamp line) of the raw block `report-fill` inserted: the stamp line, a blank line, then results.md stripped."""
    body = results_md.strip()
    at = draft.find(body)
    if at < 0:
        raise CompactError("the draft does not contain this results.md text; report-fill was run on another results folder, or the "
                           "block is still '[pending-live ...]' (nothing to compact)")
    if draft.count(body) > 1:
        raise CompactError("the results text occurs twice in the draft; refusing to guess")
    head = draft[:at]
    m = None
    for m in STAMP_RE.finditer(head):
        pass
    if m is None or head[m.end():].strip():
        raise CompactError("no '[measured: gapcourt score at ...]' stamp line directly before the results text")
    return m.start(), at + len(body), m.group(0)


def compact_draft(draft: str, results_md: str, ref: str = REF) -> str:
    start, end, stamp = locate_raw_block(draft, results_md)
    return fill_headline(draft[:start] + compact_block(results_md, stamp, ref) + draft[end:], results_md)


EVID_RE = re.compile(r"^- ((?:[\w.-]+/)*[\w.-]+?)/r(\d+): (.*); (\d+ calls, USD [\d.]+) (\[measured\])$")
KB_RE = re.compile(r"; KB (kb-[0-9a-f]+); code ")
CASES_RE = re.compile(r"(?<=: cases )[A-Z0-9,]+(?=;)")
EVID_HEAD = "Evidence index ("


def _evidence_span(lines: list[str]) -> tuple[int, int]:
    """[start, end) of the evidence index: the heading line up to the next markdown heading (or the end); (0, 0) when absent."""
    for i, ln in enumerate(lines):
        if ln.startswith(EVID_HEAD):
            j = i + 1
            while j < len(lines) and not lines[j].startswith("#"):
                j += 1
            return i, j
    return 0, 0


def collapse_evidence(draft: str) -> str:
    """Opt-in page saver for the evidence index (only the block that starts at the "Evidence index (" line; other bullets are never touched).
    (1) When EVERY measured run line names a knowledge base and it is the same one, the repeated "; KB ..." is dropped from those lines
    and said once after the heading; when they name two to four, each full version is said once after the heading under a letter
    (KB A, KB B) and each line keeps its letter. (2) Lines of repeated runs (S-01/r1, S-01/r2, ...) that differ only in run number, calls and cost
    become one line that lists each run's calls and cost verbatim. (3) Case lists get a space after each comma so they can wrap."""
    lines = draft.split(chr(10))
    s, e = _evidence_span(lines)
    if s == e:
        return draft
    blk = lines[s:e]
    blk = [CASES_RE.sub(lambda m: m.group(0).replace(",", ", "), ln) if ln.startswith("- ") else ln for ln in blk]
    measured = [ln for ln in blk if ln.startswith("- ") and ln.endswith("[measured]")]
    kbs = {m.group(1) for ln in measured if (m := KB_RE.search(ln))}
    if measured and len(kbs) == 1 and all(KB_RE.search(ln) for ln in measured):
        kb = kbs.pop()
        blk = [ln.replace(f"; KB {kb}; code ", "; code ") if ln in measured else ln for ln in blk]
        blk = [ln + f" Every measured run below used KB {kb}." if ln.startswith(EVID_HEAD) else ln for ln in blk]
    elif measured and 1 < len(kbs) <= 4 and all(KB_RE.search(ln) for ln in measured):
        # several knowledge bases (for example runs made before and after a knowledge-base change): each full version is said once
        # after the heading under a letter, and each run line names its letter
        order = list(dict.fromkeys(KB_RE.search(ln).group(1) for ln in measured))
        alias = {kb: "KB " + "ABCD"[i] for i, kb in enumerate(order)}
        blk = [KB_RE.sub(lambda m: f"; {alias[m.group(1)]}; code ", ln) if ln in measured else ln for ln in blk]
        legend = "; ".join(f"{alias[kb]} is {kb}" for kb in order)
        blk = [ln + f" Knowledge bases: {legend}." if ln.startswith(EVID_HEAD) else ln for ln in blk]
    groups: dict[tuple[str, str], list[tuple[int, str, str]]] = {}
    for i, ln in enumerate(blk):
        m = EVID_RE.match(ln)
        if m:
            groups.setdefault((m.group(1), m.group(3)), []).append((i, m.group(2), m.group(4)))
    drop, put = set(), {}
    for (name, head), items in groups.items():
        if len(items) < 2:
            continue
        runs = ", ".join(f"r{n}" for _, n, _ in items)
        per = "; ".join(f"r{n} {c}" for _, n, c in items)
        put[items[0][0]] = f"- {name}/{runs}: {head}; per run {per} [measured]"
        drop.update(i for i, _, _ in items[1:])
    blk = [put.get(i, ln) for i, ln in enumerate(blk) if i not in drop]
    return chr(10).join(lines[:s] + blk + lines[e:])


def evidence_section(text: str) -> str:
    lines = text.split(chr(10))
    s, e = _evidence_span(lines)
    return chr(10).join(lines[s:e])


def _tokens(text: str) -> set[str]:
    return set(NUM_TOKEN.findall(HEX_WORD.sub(" ", text)))


def numbers_not_in(source: str, block: str) -> list[str]:
    """Numeric tokens of `block` that are not a whole numeric token of `source` (must be empty: no recomputation, no rounding). Digits inside
    hex words (hashes, kb ids) are not numbers; 12-character hashes must occur in the source. A weak check by design: it does not say WHICH
    figure a token belongs to; the tables are copied line for line and tested for that."""
    stray = sorted(_tokens(block) - _tokens(source))
    stray += sorted(h for h in set(re.findall(r"\b[0-9a-f]{12}\b", block)) if h not in source)
    return stray


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", required=True, help="results.md of the scored run (e.g. results/heldout/results.md)")
    ap.add_argument("--draft", help="filled report draft that report-fill wrote; omit to print the compact block only")
    ap.add_argument("--tighten-evidence", action="store_true",
                    help="also shorten the evidence index (one KB statement, repeated runs such as S-01/r1..r3 in one line); use when the page check fails")
    ap.add_argument("--out", help="where to write the compacted copy of the draft (required with --draft; never the draft or the results file)")
    ap.add_argument("--ref", help=f"path printed after 'run ids, full hashes:' (default: the --results path when it is relative, else {REF})")
    a = ap.parse_args(argv)
    rp = Path(a.results)
    ref = a.ref or (rp.as_posix() if not rp.is_absolute() else REF)
    out_p = Path(a.out) if a.out else None
    if a.draft and not out_p:
        ap.error("--out is required with --draft")
    try:
        if out_p:
            for src in (Path(a.draft), rp):
                if out_p.resolve() == src.resolve() or (out_p.exists() and src.exists() and out_p.samefile(src)):
                    ap.error(f"--out must differ from {src.name}: neither the tracked draft nor the unabridged results is overwritten")
        results_md = rp.read_text(encoding="utf-8")
        if not a.draft:
            print(compact_block(results_md, ref=ref))
            return 0
        draft = Path(a.draft).read_text(encoding="utf-8")
        new = compact_draft(draft, results_md, ref)
        if a.tighten_evidence:
            new = collapse_evidence(new)
        stamp = locate_raw_block(draft, results_md)[2]
        block = compact_block(results_md, stamp, ref)
        if HEADLINE_RE.search(draft):
            block += chr(10) + headline_sentence(results_md)
        stray = numbers_not_in(results_md + chr(10) + stamp + chr(10) + ref, block)  # the reference path may carry digits
        stray += numbers_not_in(evidence_section(draft), evidence_section(new))
        if stray:
            print(f"error: numbers in the compact text that are not in results.md or the evidence index: {', '.join(stray)}", file=sys.stderr)
            return 3
        out_p.write_text(new, encoding="utf-8", newline=chr(10))
    except (CompactError, OSError, UnicodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"wrote {a.out}: {len(draft.split()) - len(new.split())} words removed; every number token of the compact block is a whole token of results.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
