"""Build the checked Arabic transcription of U08 (two-method merge), and its disagreement log.

U08 (Digital Customer and Digital Government Service Policy) exists only as an Arabic PDF whose text layer has
lam-alef ligature-order defects, so a correctly spelled Arabic quotation fails the exact check against it. This script
records how the checked transcription that replaces the text layer in the local knowledge base was made. It is a record
of the method, not a fresh-clone step: its inputs are private (link-only text) and stay outside the repository.

Two independent methods, merged; never either one alone:

1. Deterministic repair of the PDF text layer (no model): NFKC, tatweel and tashkeel stripped, the detached ligature
   merged, and every alef+lam pair in a token tried in both orders, the variant found in an Arabic frequency lexicon
   winning (method summary in docs/CORPUS_PROVENANCE.md).
2. Image transcription of each page (Codex, image only, no text layer given). It gives
   the line breaks and the reading order, which the text layer scrambles in tables and headings.

The Codex lines are the skeleton. Every word on which the two methods disagree (bag of words per page, tashkeel
ignored) was resolved against the page image by eye (crops at 4x of the PDF page), and the resolution is recorded in
WORD_FIXES (Codex wrong, the printed word restored) or LAYER_REPAIR_WRONG (Codex right, the repair produced another
word). The script stops unless, after the fixes, the only residual disagreements are exactly the recorded
LAYER_REPAIR_WRONG words, so an unreviewed disagreement cannot reach the output.

Output conventions (stated in the corpus front matter): tashkeel (short vowels, tanwin, shadda) and tatweel are not
kept, because the two methods were compared without them; a definition-table row is "term<TAB>definition" as Codex
wrote it; printed page numbers are dropped (page markers carry them); lines that exist only in the image and not in the
text layer (cover ministry header, the back-page social handle and web address) are left out and logged.

Usage (inputs under the private job folder; nothing here is written to the public tree except the text-free log):
  python scripts/build_u08_checked.py --pdf corpus_local/_raw/U08/digital_customer_policy_ar_u.ae.pdf \
      --codex-dir <dir with ctl_out_p1.md .. ctl_out_p10.md> --lexicon <ar_50k.txt> \
      --out corpus_local/_raw/U08/U08_checked_transcription.txt --log docs/U08_CHECKED_TRANSCRIPTION_LOG.json
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

ALEF = "اأإآ"
DIAC = re.compile("[ً-ٰٟ]")
WORD = re.compile(r"[ء-ي]+|[0-9A-Za-z]+")
TOK = re.compile(r"[ء-يﻻ]+|[0-9A-Za-z]+|[^\s]")
PREFIXES = ["", "و", "ف", "ب", "ك", "ل", "ول", "فل", "وب", "فب", "وك", "س", "وس"]

# Codex wrong, the printed word restored. (page, line number in the Codex output file, Codex word, printed word, evidence)
WORD_FIXES = [
    (3, 27, "لتمكينه", "وتمكنيه", "image p3 row 'المتعامل الرقمي' line 2: printed 'وتمكنيه' (a typo in the official PDF, kept)"),
    (3, 28, "معاملاته", "معاملته", "image p3 row 'المتعامل الرقمي' line 3: printed 'معاملته'"),
    (4, 1, "خلالها", "خلاها", "image p4 row 'المصادقة الرقمية': printed 'من خلاها' (a lam missing in the official PDF, kept); "
                             "the Nov 9 test had judged this by context as a text-layer drop; the image shows the print has it"),
    (6, 23, "الجهة", "الجهات", "image p6 item 5.2.1: printed 'على الجهات الحكومية'"),
    (6, 32, "وموحد", "ووحيد", "image p6 item 5.2.4: printed 'رئيسي ووحيد'"),
    (7, 24, "الربط", "الرابط", "image p7 heading 6.2: printed 'الرابط الحكومي للخدمات'"),
    (9, 26, "وإتباع", "واتباع", "image p9 item 9.3 line 2: printed 'واتباع' (no hamza)"),
    (9, 27, "الحكومية تقديم", "الحكومية بتقديم", "image p9 item 9.3 line 3: printed 'الحكومية بتقديم بياناتهم'"),
]
# Codex right, the lexicon repair produced another word: (page, repaired-layer word, printed word, evidence)
LAYER_REPAIR_WRONG = [
    (4, "إلمارات", "لإمارات", "image p4 row 'البيانات الحكومية' line 2: printed 'المحلية لإمارات الدولة'; a pair at token start is left alone by design"),
    (4, "ثلاث", "ثالث", "image p4 row 'البيانات الخصوصية': printed 'طرف ثالث'; the lexicon chose the homograph"),
    (5, "ولاحد", "والحد", "image p5 item 2.2 line 2: printed 'والحد من تعبئة'; the lexicon chose the wrong order"),
    (9, "تعلايمها", "تعاليمها", "image p9 item 9.3 line 2: printed 'تعاليمها'; unresolved-token heuristic flipped the wrong pair"),
]
# image-only lines (not in the text layer, so only one method saw them): left out
IMAGE_ONLY = {
    1: ["الإمارات العربية المتحدة", "وزارة شؤون مجلس الوزراء", "مكتب رئاسة مجلس الوزراء", "UNITED ARAB EMIRATES",
        "MINISTRY OF CABINET AFFAIRS", "PRIME MINISTER’S OFFICE"],
    10: ["@egsep", "www.egsep.ae"],
}


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def norm_base(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = re.sub("ـ+\\s+اا", "ﻻ", s)  # detached lam-alef ligature: tatweel + space + 'AA' -> 'LA'
    s = s.replace("ـ", "")
    return DIAC.sub("", s)


class Lexicon:
    def __init__(self, path: Path):
        self.c = collections.Counter()
        for line in path.read_text(encoding="utf-8").splitlines():
            w, _, n = line.rpartition(" ") if " " in line else (line, "", "1")
            w = DIAC.sub("", w.strip())
            try:
                self.c[w] += int(n)
            except ValueError:
                pass

    def known(self, w: str) -> int:
        w = w.replace("ﻻ", "لا")
        best = 0
        for p in PREFIXES:
            if p and not w.startswith(p):
                continue
            r = w[len(p):]
            for a in ("", "ال", "لل"):
                if a and not r.startswith(a):
                    continue
                c = r[len(a):]
                if a == "لل":
                    c = "ل" + c
                if len(c) >= 2 and self.c.get(c, 0) > best:
                    best = self.c[c]
            if self.c.get(r, 0) > best:
                best = self.c[r]
        return best


def _pairs(w: str) -> list[int]:
    return [i for i in range(len(w) - 1) if w[i] in ALEF and w[i + 1] == "ل"]


def fix_token(w: str, lex: Lexicon) -> tuple[str, str]:
    if not re.fullmatch("[ء-يﻻ]+", w):
        return w, "skip"
    idx = _pairs(w)
    cands = []
    for mask in range(1 << min(len(idx), 5)):
        s = list(w)
        for j, i in enumerate(idx[:5]):
            if mask >> j & 1:
                s[i], s[i + 1] = s[i + 1], s[i]
        v = "".join(s)
        k = lex.known(v)
        if k > 0:
            cands.append((k, -bin(mask).count("1"), v))
    if cands:
        cands.sort(reverse=True)
        amb = cands[0][2] != w and any(c[2] == w for c in cands)
        return cands[0][2], ("ambiguous" if amb else ("lexicon" if cands[0][2] != w else "lexicon_same"))
    idx = [i for i in idx if i > 0 and not (i <= 2 and w[:i] and all(c in "وفبك" for c in w[:i]) and w[i] == "ا" and len(w) - i >= 4)]
    s = list(w)
    for i in idx:
        s[i], s[i + 1] = s[i + 1], s[i]
    r = "".join(s)
    return r, ("heuristic" if r != w else "unresolved")


def repaired_layer_words(page_text: str, lex: Lexicon) -> tuple[list[str], collections.Counter]:
    out, stats = [], collections.Counter()
    for m in TOK.finditer(norm_base(page_text)):
        r, st = fix_token(m.group(), lex)
        r = r.replace("ﻻ", "لا")
        stats[st] += 1
        if WORD.fullmatch(r):
            out.append(r)
    return out, stats


def codex_lines(path: Path, page: int) -> list[str]:
    """Codex output lines, numbered as in the file (1-based), fences and the page-number note blanked, not removed."""
    lines = path.read_text(encoding="utf-8").splitlines()
    out = []
    for l in lines:
        s = l.replace("```text", "").replace("```", "")
        s = re.sub(r"رقم الصفحة[^\n]*", "", s).replace("[?]", "")
        if s.strip() == str(page):
            s = ""
        out.append(s)
    return out


def clean_line(s: str) -> str:
    s = unicodedata.normalize("NFC", s).replace("ـ", "")
    s = DIAC.sub("", s)
    return re.sub(r"[ ]{2,}", " ", s).rstrip()


def words(s: str) -> list[str]:
    return WORD.findall(norm_base(s))


def build(pdf: Path, codex_dir: Path, lexicon: Path) -> tuple[str, dict]:
    import fitz

    lex = Lexicon(lexicon)
    doc = fitz.open(str(pdf))
    pages_out, log_pages, residual_all, order_checks = [], [], [], []
    fixes_by_page = collections.defaultdict(list)
    for f in WORD_FIXES:
        fixes_by_page[f[0]].append(f)
    try:
        for n, page in enumerate(doc, start=1):
            layer = page.get_text()
            lw, stats = repaired_layer_words(layer, lex)
            if str(n) in lw:
                lw.remove(str(n))  # the printed page number (one occurrence; item numbers such as 2.1 stay)
            lines = codex_lines(codex_dir / f"ctl_out_p{n}.md", n)
            for (_, ln, old, new, _) in fixes_by_page[n]:
                line = lines[ln - 1]
                hits = [m.start() for m in re.finditer(r"(?<![ء-ي])" + re.escape(old) + r"(?![ء-ي])", line)]
                if len(hits) != 1:
                    raise SystemExit(f"p{n} line {ln}: expected the Codex word {old!r} exactly once, found {len(hits)}")
                lines[ln - 1] = line[:hits[0]] + new + line[hits[0] + len(old):]
            drop = set(IMAGE_ONLY.get(n, []))
            kept, dropped = [], []
            for l in lines:
                c = clean_line(l)
                if not c.strip():
                    continue
                if c.strip() in drop:
                    dropped.append(c.strip())
                    continue
                kept.append(c)
            if set(drop) - set(dropped):
                raise SystemExit(f"p{n}: image-only lines not found: {sorted(set(drop) - set(dropped))}")
            cw = [w for l in kept for w in words(l)]
            bc, bl = collections.Counter(cw), collections.Counter(lw)
            miss, extra = bc - bl, bl - bc  # in transcription not in repaired layer; in repaired layer not in transcription
            expected_miss = collections.Counter(p[2] for p in LAYER_REPAIR_WRONG if p[0] == n)
            expected_extra = collections.Counter(p[1] for p in LAYER_REPAIR_WRONG if p[0] == n)
            if miss != expected_miss or extra != expected_extra:
                residual_all.append({"page": n, "unexplained_in_transcription": dict(miss - expected_miss),
                                     "unexplained_in_layer": dict(extra - expected_extra)})
            # order check: every Arabic word run of each line (or table cell) must occur contiguously in the repaired layer's
            # own word order; runs are split at digits and Latin, where the layer's right-to-left order around numbers differs
            overrule = {p[1]: p[2] for p in LAYER_REPAIR_WRONG if p[0] == n}
            hay = " " + " ".join(overrule.get(w, w) for w in lw if not w.isascii()) + " "
            cells = whole = runs_ok = 0
            for l in kept:
                for cell in l.split("\t"):
                    ar = [w for w in words(cell) if not w.isascii()]
                    if len(ar) < 2:
                        continue
                    cells += 1
                    if " " + " ".join(ar) + " " in hay:
                        whole += 1
                        continue
                    runs = [r for r in re.split(r"[0-9A-Za-z()]+", cell) if words(r)]
                    if all(" " + " ".join(words(r)) + " " in hay for r in runs):
                        runs_ok += 1
                    else:
                        residual_all.append({"page": n, "order_differs": cell})
            order_checks.append({"page": n, "cells": cells, "same_order_as_layer": whole,
                                 "same_order_between_numbers_or_latin": runs_ok})
            pages_out.append(f"<!--page:{n}-->")
            pages_out += kept
            log_pages.append({"page": n, "transcription_words": len(cw), "layer_words": len(lw),
                              "agreement_after_resolution": round(sum((bc & bl).values()) / max(1, len(cw)), 4),
                              "repair_token_classes": dict(stats), "image_only_lines_left_out": len(dropped)})
    finally:
        doc.close()
    if residual_all:
        raise SystemExit("unreviewed disagreements remain; resolve them against the page image first: "
                         + json.dumps(residual_all, ensure_ascii=False))
    text = "\n".join(pages_out) + "\n"
    log = {
        "doc_id": "U08",
        "what": "checked transcription, not the official text layer: Codex image transcription merged with a deterministic "
                "lexicon-guided repair of the PDF text layer; every disagreement resolved against the page image",
        "built_by": "scripts/build_u08_checked.py",
        "built_on": "2026-11-09",
        "inputs": {"official_pdf_sha256": sha(pdf.read_bytes()),
                   "codex_outputs_sha256": {f"ctl_out_p{n}.md": sha((codex_dir / f"ctl_out_p{n}.md").read_bytes()) for n in range(1, 11)},
                   "codex_model": "gpt-6.1-sol, reasoning effort high, image only (page PNG at 170 dpi), no text layer in the prompt",
                   "lexicon": "hermitdave FrequencyWords ar_50k.txt (not stored)", "lexicon_sha256": sha(lexicon.read_bytes())},
        "output_conventions": ["tashkeel (short vowels, tanwin, shadda, sukun) and tatweel not kept",
                               "definition-table rows as term TAB definition, in image reading order",
                               "printed page numbers dropped; page markers carry them",
                               "lines only in the image (not in the text layer) left out"],
        "comparison": "bag of words per page, Arabic and Latin word tokens, tashkeel ignored (the text layer scrambles line order "
                      "in tables and headings, so an order-sensitive score is not used)",
        "disagreements": [{"page": p, "codex_line": ln, "codex": old, "repaired_layer": new, "resolved_to": new,
                           "who_was_wrong": "image transcription (Codex)", "evidence": ev} for (p, ln, old, new, ev) in WORD_FIXES]
                         + [{"page": p, "codex": good, "repaired_layer": bad, "resolved_to": good,
                             "who_was_wrong": "text-layer repair (lexicon)", "evidence": ev} for (p, bad, good, ev) in LAYER_REPAIR_WRONG],
        "image_only_lines_left_out": {str(k): len(v) for k, v in IMAGE_ONLY.items()},
        "pages": log_pages,
        "order_check": {"what": "each line or table cell's Arabic words found contiguously in the repaired layer's word order; "
                                "where the layer reorders around digits or Latin (right-to-left numbers, brackets), each run between "
                                "them is checked instead and the order across the number is taken from the image",
                        "cells": sum(o["cells"] for o in order_checks),
                        "same_order_as_layer": sum(o["same_order_as_layer"] for o in order_checks),
                        "same_order_between_numbers_or_latin": sum(o["same_order_between_numbers_or_latin"] for o in order_checks),
                        "differs": 0},
        "transcription_sha256": sha(text.encode("utf-8")),
        "residual_unexplained_disagreements": 0,
        "limits": ["a word wrong in both methods in the same way would not show as a disagreement",
                   "tashkeel is not verified", "page 1 cover header and page 10 handle are image-only and left out"],
    }
    return text, log


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--codex-dir", required=True)
    ap.add_argument("--lexicon", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--log", required=True)
    a = ap.parse_args(argv)
    text, log = build(Path(a.pdf), Path(a.codex_dir), Path(a.lexicon))
    Path(a.out).write_text(text, encoding="utf-8", newline="\n")
    Path(a.log).write_text(json.dumps(log, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {a.out} (sha256 {log['transcription_sha256']}); {len(WORD_FIXES)} Codex words corrected, "
          f"{len(LAYER_REPAIR_WRONG)} repair words overruled, 0 unexplained disagreements")
    return 0


if __name__ == "__main__":
    sys.exit(main())
