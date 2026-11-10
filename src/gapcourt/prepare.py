"""Deterministic raw-to-corpus preparation and its reproducibility check (`gapcourt prepare`).

A corpus file passes in one of two ways, and the check says which:

- REPRODUCED   a recorded transform rebuilds the file, byte for byte, from the raw file whose sha256 its front
               matter records. The transform is an extractor, its options and a structural patch of guarded
               hunks (corpus/PREP.json), or the Python recipe for U01.
- AUDITED      no byte-exact transform exists (curated excerpts that join wrapped lines, flatten tables, or take
               text from several raw files), but the verbatim audit passes: every content line of the file is
               found in the text of the hash-verified raw files under a named, limited normalization (see
               AUDIT_TIERS), and every line that is not is a registered edit in PREP.json["accepted_edits"].
               This is a statement about the words, not about the bytes.

Everything else fails and is reported plainly:

- DIFFERS      a transform exists but its output differs (the file was edited by hand, or the transform changed)
- UNMATCHED    the audit found content lines that are neither in the raw text nor registered edits
- NO_RECIPE    only with audit=False: no transform is registered
- RAW_MISSING  no raw file with the recorded sha256 under corpus_local/_raw/
- RECIPE_ERROR the transform failed (for example a missing PDF library) or is not allowed

Text is never translated or repaired by a transform.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import warnings
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

TREES = (("corpus", ("uae", "global", "framework")), ("corpus_local", ("uae", "global", "framework")))
STATUSES = ("REPRODUCED", "AUDITED", "DIFFERS", "UNMATCHED", "NO_RECIPE", "RAW_MISSING", "RECIPE_ERROR")
PASSING = ("REPRODUCED", "AUDITED")
PREP_FILE = "corpus/PREP.json"


@dataclass
class Recipe:
    doc_id: str
    build: Callable[[Path], str]
    description: str


@dataclass
class PrepResult:
    doc_id: str
    path: str
    status: str
    detail: str = ""
    raw: str | None = None
    audit: dict | None = None


def _front_matter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    block = text[3:end] if end > 0 else ""
    return dict(re.findall(r"^([A-Za-z0-9_]+):\s?(.*)$", block, re.M))


def find_raw(root: Path, doc_id: str, sha256: str) -> Path | None:
    """The raw file with this sha256: first under _raw/<doc_id>/, then anywhere under _raw/ (an amending act
    can sit beside the act it amends)."""
    base = root / "corpus_local" / "_raw"
    if not sha256 or not base.is_dir():
        return None
    own = base / doc_id
    for d in ([own] if own.is_dir() else []) + [base]:
        for p in sorted(d.rglob("*")):
            if p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() == sha256:
                return p
    return None


def corpus_files(root: Path) -> list[Path]:
    out = []
    for tree, cols in TREES:
        for c in cols:
            out += sorted((root / tree / c).glob("*.md"))
    return out


def _first_difference(a: str, b: str) -> str:
    la, lb = a.splitlines(), b.splitlines()
    for i, (x, y) in enumerate(zip(la, lb), start=1):
        if x != y:
            return f"line {i}: corpus {x[:80]!r} vs rebuilt {y[:80]!r}"
    if len(la) != len(lb):
        return f"line count: corpus {len(la)} vs rebuilt {len(lb)}"
    return "same lines; bytes differ (line endings or trailing whitespace)"


# ---- recorded transforms (corpus/PREP.json) -----------------------------------------

PATCH_CLASSES = ("structural_heading", "page_marker", "omission_mark", "excerpt_selection", "fragment", "rewrite")
_STRUCTURAL_LINE = re.compile(r"^(?:### .+|<!--page:\d+-->|\[\.\.\.\])$")
_SHORT_FRAGMENT = 12


def _lsha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_prep(root: str | Path) -> dict:
    p = Path(root) / PREP_FILE
    if not p.is_file():
        return {"version": 1, "docs": {}, "accepted_edits": {}}
    return json.loads(p.read_text(encoding="utf-8"))


def extract_lines(raw: str | Path, extractor: str, options: dict | None = None) -> list[str]:
    """Deterministic line list from a raw file. Options: rstrip, drop_blank, drop_regex (list), drop_tags (html)."""
    opts = options or {}
    raw = Path(raw)
    drops = [re.compile(r) for r in opts.get("drop_regex", [])]

    def clean(lines: list[str]) -> list[str]:
        out = []
        for l in lines:
            if opts.get("rstrip"):
                l = l.rstrip()
            if opts.get("drop_blank") and not l.strip():
                continue
            if any(d.search(l) for d in drops):
                continue
            out.append(l)
        return out

    if extractor == "text_lines":
        return clean(raw.read_text(encoding="utf-8").splitlines())
    if extractor == "pymupdf_lines":
        import fitz  # PyMuPDF; it also opens .docx

        out: list[str] = []
        doc = fitz.open(str(raw))
        try:
            for n, page in enumerate(doc, start=1):
                out.append(f"<!--page:{n}-->")
                out += clean(page.get_text().splitlines())
        finally:
            doc.close()
        return out
    if extractor == "html_blocks":
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(raw.read_bytes(), "lxml")
        for t in soup(["script", "style", "head"] + list(opts.get("drop_tags", []))):
            t.decompose()
        root = soup.body or soup
        out = ["<!--page:1-->"]
        for el in root.find_all(["p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr"]):
            if el.name == "p" and any(par.name in ("tr", "li") for par in el.parents):
                continue
            s = re.sub(r"\s+", " ", el.get_text(" ").replace("\xa0", " ")).strip()
            if s:
                out.append(s)
        return out
    raise ValueError(f"unknown extractor {extractor!r}")


def apply_patch(lines: list[str], patch: list[dict]) -> list[str]:
    """Apply guarded hunks. `at` indexes the unpatched list. A hunk deletes `delete` lines from `at` (guarded by the
    sha256 of the deleted text joined by newlines) and inserts `insert` there; a pure insert is guarded by the
    sha256 of the line it goes before (`expect_before`). Every hunk carries a class."""
    out = list(lines)
    for h in sorted(patch, key=lambda h: h["at"], reverse=True):
        if h.get("class") not in PATCH_CLASSES:
            raise ValueError(f"hunk at {h.get('at')}: missing or unknown class {h.get('class')!r}")
        at, n = h["at"], h.get("delete", 0)
        if n:
            if _lsha("\n".join(lines[at:at + n])) != h.get("delete_sha256"):
                raise ValueError(f"hunk at {at}: guard failed, the {n} lines to delete are not the recorded ones")
        elif "expect_before" in h:
            if at >= len(lines) or _lsha(lines[at]) != h["expect_before"]:
                raise ValueError(f"hunk at {at}: guard failed, the line the insert goes before is not the recorded one")
        out[at:at + n] = list(h.get("insert", []))
    return out


def _render(meta: list, lines: list[str]) -> str:
    head = "---\n" + "\n".join(f"{k}: {v}" for k, v in meta) + "\n---\n"
    return head + "\n".join(lines) + "\n"


def _check_patch_allowed(entry: dict) -> None:
    """A link-only or unknown-terms transform may record structure and short fragments, not source text."""
    republish = dict(entry["meta"]).get("republish", "")
    if republish == "yes":
        return
    for h in entry.get("patch", []):
        for l in h.get("insert", []):
            if not _STRUCTURAL_LINE.match(l) and len(l) > _SHORT_FRAGMENT:
                raise ValueError(f"patch at {h['at']} inserts source text of a link-only document; "
                                 "a transform for such a document may record structure and short fragments only")


def build_from_prep(entry: dict, raw: Path) -> str:
    _check_patch_allowed(entry)
    lines = extract_lines(raw, entry["extractor"], entry.get("options"))
    return _render(entry["meta"], apply_patch(lines, entry.get("patch", [])))


def _entry_for(entry: dict | None, sha256: str) -> dict | None:
    """The recorded transform whose raw file has this sha256: the entry itself, or its `fallback` (a transform of another
    raw file that writes the same corpus file, used when the entry's own raw file is private and absent, as for the
    checked U08 transcription, whose fallback is the official PDF's text layer)."""
    if entry is None:
        return None
    fb = entry.get("fallback")
    if fb is not None and sha256 and dict(fb["meta"]).get("sha256") == sha256 and dict(entry["meta"]).get("sha256") != sha256:
        return {**fb, "path": entry["path"]}
    return entry


def rebuild_all(root: str | Path, recipes: dict | None = None, skipped: list | None = None) -> list[Path]:
    """Write every corpus file that has a recorded transform (and the U01 recipe) from its raw file. This is what a
    fresh clone runs after `gapcourt kb-fetch`."""
    root = Path(root)
    recipes = RECIPES if recipes is None else recipes
    prep = load_prep(root)
    written = []
    for doc_id, entry in sorted(prep["docs"].items()):
        if "extractor" not in entry:
            continue
        sha = dict(entry["meta"]).get("sha256", "")
        raw = find_raw(root, doc_id, sha)
        if raw is None and entry.get("fallback") is not None:
            fsha = dict(entry["fallback"]["meta"]).get("sha256", "")
            fraw = find_raw(root, doc_id, fsha)
            if fraw is not None:
                entry, raw = _entry_for(entry, fsha), fraw
                if skipped is not None:
                    skipped.append((doc_id, f"private raw file with sha256 {sha[:16]}... absent; rebuilt from the fallback "
                                            f"transform of {entry.get('raw_name', 'the official file')} instead"))
        if raw is None:
            reason = f"raw file with sha256 {sha[:16]}... not found under corpus_local/_raw/ (run `gapcourt kb-fetch`, or the browser step it prints)"
            if skipped is None:
                raise FileNotFoundError(f"{doc_id}: {reason}")
            skipped.append((doc_id, reason))
            continue
        out = root / entry["path"]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(build_from_prep(entry, raw), encoding="utf-8", newline="\n")
        written.append(out)
    for doc_id, rec in sorted(recipes.items()):
        if doc_id in prep["docs"] and "extractor" in prep["docs"][doc_id]:
            continue
        base = root / "corpus_local" / "_raw" / doc_id
        files = [p for p in sorted(base.glob("*")) if p.is_file() and p.name != "SHA256SUMS.txt"] if base.is_dir() else []
        if len(files) != 1:
            reason = f"expected exactly one raw file under {base}"
            if skipped is None:
                raise FileNotFoundError(f"{doc_id}: {reason}")
            skipped.append((doc_id, reason))
            continue
        out = root / "corpus_local" / "uae" / f"{doc_id}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rec.build(files[0]), encoding="utf-8", newline="\n")
        written.append(out)
    return written


# ---- the verbatim audit ---------------------------------------------------------------

AUDIT_TIERS = {
    "verbatim": "the line, with whitespace runs collapsed, is a substring of the raw text",
    "whitespace": "equal once all whitespace is removed (inline markup rendered with or without a space)",
    "typography": "equal once curly quotes, dash variants, non-breaking and zero-width characters are folded",
    "enumerator": "equal once a leading list number or bullet (rendered from list markup or a separate line) is removed",
    "labelled": "'Label: text' where the label and the text are each found in the raw text (flattened table rows, glossary entries, key-value renderings)",
}
_FOLD = str.maketrans({
    chr(0x2018): "'", chr(0x2019): "'", chr(0x201C): '"', chr(0x201D): '"', chr(0x2012): "-", chr(0x2013): "-",
    chr(0x2014): "-", chr(0x2212): "-", chr(0x2011): "-", chr(0xA0): " ", chr(0x200B): "", chr(0xAD): "", chr(0x2009): " "})
_WS = re.compile(r"\s+")
_BULLETS = "".join(chr(c) for c in (0x2012, 0x2013, 0x2014, 0x2022))
_ENUM = re.compile(r"^(?:\(?[0-9]+(?:\.[0-9]+)*[.)]?|\(?[a-z]{1,3}[.)]|\([ivx]+\)|[" + _BULLETS + r"\-])\s+")
_LABEL = re.compile(r"^([^:]{1,60}):\s+(.+)$")
_HEADING = re.compile(r"^### (.+)$")
_BLOCK_TAGS = ("p", "div", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "br", "ul", "ol", "table", "section",
               "dt", "dd", "dl", "caption", "article", "header", "footer", "summary", "details", "thead", "tbody", "body")


def _collapse(s: str) -> str:
    return _WS.sub(" ", s).strip()


def _fold(s: str) -> str:
    return _collapse(s.translate(_FOLD))


def _squeeze(s: str) -> str:
    return _WS.sub("", s)


def _html_text(data: bytes) -> str:
    from bs4 import BeautifulSoup

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        soup = BeautifulSoup(data, "lxml")
    for t in soup(["script", "style"]):
        t.decompose()
    for t in soup.find_all(_BLOCK_TAGS):
        t.insert_before("\n")
        t.insert_after("\n")
    return soup.get_text("")


def raw_text_of(path: Path) -> str:
    """Text of one raw file for the audit. Only the declared file types are read; others contribute nothing."""
    ext = path.suffix.lower()
    if ext in (".pdf", ".docx"):
        import fitz

        d = fitz.open(str(path))
        try:
            return "\n".join(p.get_text() for p in d)
        finally:
            d.close()
    if ext in (".html", ".htm", ".xhtml"):
        return _html_text(path.read_bytes())
    if ext == ".zip":
        z = zipfile.ZipFile(path)
        return "\n".join(_html_text(z.read(n)) for n in z.namelist() if n.lower().endswith(".xml"))
    if ext == ".xml":
        return _html_text(path.read_bytes())
    if ext in (".json", ".py", ".md", ".txt", ".csv", ""):
        return path.read_text(encoding="utf-8", errors="replace")
    return ""


def _listed_hashes(folder: Path) -> dict[str, str]:
    """name -> sha256 from the folder's SHA256SUMS.txt (lines like '<hash> *./name' or '<hash>  name')."""
    sums = folder / "SHA256SUMS.txt"
    out: dict[str, str] = {}
    if not sums.is_file():
        return out
    for line in sums.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^([0-9a-fA-F]{64})\s+\*?(?:\./)?(.+?)\s*$", line)
        if m:
            out[m.group(2)] = m.group(1).lower()
    return out


def _raw_files(raw: Path) -> tuple[list[Path], list[str]]:
    """The raw files whose text may account for corpus lines: the hash-verified file itself and the siblings whose
    sha256 is listed in the folder's SHA256SUMS.txt and matches their bytes. Other files are ignored and named."""
    listed = _listed_hashes(raw.parent)
    verified, ignored = [], []
    for p in sorted(raw.parent.iterdir()):
        if not p.is_file() or p.name == "SHA256SUMS.txt":
            continue
        if p == raw or listed.get(p.name) == hashlib.sha256(p.read_bytes()).hexdigest():
            verified.append(p)
        else:
            ignored.append(p.name)
    verified.sort(key=lambda p: p != raw)  # the file named by the front-matter hash comes first, so it wins ties
    return verified, ignored


def audit_text(body: str, raw_files: list[Path], registered: set[str], ignored_files: list[str] | None = None) -> dict:
    """Account for every content line of a corpus body against the text of its raw files."""
    # one entry per verified raw file, so each matched line is attributed to the file that supplied it
    sources = []
    for p in raw_files:
        rt_p = _collapse(raw_text_of(p))
        frt_p = _fold(rt_p)
        sources.append((p.name, rt_p, _squeeze(rt_p), _squeeze(frt_p)))
    sfrt_low = "\n".join(s[3] for s in sources).lower()
    by_source = {p.name: 0 for p in raw_files}
    tiers = {k: 0 for k in AUDIT_TIERS}
    structural = {"page_markers": 0, "headings": 0, "omission_marks": 0}
    unmatched, unverified, reg = [], [], 0

    def found_in(line: str, rt: str, srt: str, sfrt: str) -> str | None:
        if _collapse(line) in rt:
            return "verbatim"
        if _squeeze(line) in srt:
            return "whitespace"
        if _squeeze(_fold(line)) in sfrt:
            return "typography"
        stripped = _ENUM.sub("", line, count=1)
        if stripped != line and _squeeze(_fold(stripped)) in sfrt:
            return "enumerator"
        m = _LABEL.match(line)
        if m:
            lab, rest = m.group(1), _ENUM.sub("", m.group(2), count=1)
            if _squeeze(_fold(lab)) in sfrt and _squeeze(_fold(rest)) in sfrt:
                return "labelled"
        return None

    def found(line: str) -> tuple[str, str] | None:
        """(tier, raw file name) of the verified raw file that accounts for the whole line most exactly, or None."""
        best = None
        for name, rt, srt, sfrt in sources:
            tier = found_in(line, rt, srt, sfrt)
            if tier and (best is None or list(AUDIT_TIERS).index(tier) < list(AUDIT_TIERS).index(best[0])):
                best = (tier, name)
        return best

    for n, line in enumerate(body.splitlines(), start=1):
        s = line.strip()
        if not s:
            continue
        if re.fullmatch(r"<!--page:\d+-->", s):
            structural["page_markers"] += 1
            continue
        if s == "[...]":
            structural["omission_marks"] += 1
            continue
        hm = _HEADING.match(s)
        if hm:
            structural["headings"] += 1
            label = hm.group(1)
            m = re.match(r"^(Article|Section|Clause|Principle)\s+(.+)$", label)
            if m:
                kind, num = m.groups()
                variants = (f"{kind}({num})", f"{kind}{num}")
                if not any(v.lower() in sfrt_low for v in variants):
                    unverified.append(label)
            elif label.startswith("Part: "):
                if _squeeze(_fold(label[6:])).lower() not in sfrt_low:
                    unverified.append(label)
            elif _squeeze(_fold(label)).lower() not in sfrt_low:
                unverified.append(label)
            continue
        hit = found(s)
        if hit:
            tiers[hit[0]] += 1
            by_source[hit[1]] += 1
        elif _lsha(s) in registered:
            reg += 1
        else:
            unmatched.append({"line": n, "line_sha256": _lsha(s), "chars": len(s)})
    return {"tiers": tiers, "structural": structural, "registered_edits": reg, "unmatched": unmatched,
            "headings_unverified": unverified, "content_lines": sum(tiers.values()) + reg + len(unmatched),
            "raw_files": [p.name for p in raw_files], "matches_by_source": by_source,
            "unverified_raw_files_ignored": list(ignored_files or [])}


def audit_corpus_file(root: Path, path: Path, raw: Path, doc_id: str, prep: dict) -> dict:
    text = path.read_text(encoding="utf-8")
    end = text.find("\n---", 3)
    body = text[end + 5:] if end > 0 else text
    reg = {e["line_sha256"] for e in prep.get("accepted_edits", {}).get(doc_id, [])}
    files, ignored = _raw_files(raw)
    return audit_text(body, files, reg, ignored)


# ---- the check ---------------------------------------------------------------------------

def check_corpus(root: str | Path, recipes: dict | None = None, audit: bool = True) -> list[PrepResult]:
    root = Path(root)
    recipes = RECIPES if recipes is None else recipes
    prep = load_prep(root)
    results = []
    for path in corpus_files(root):
        rel = path.relative_to(root).as_posix()
        data = path.read_bytes()
        meta = _front_matter(data.decode("utf-8", errors="replace"))
        doc_id = meta.get("doc_id", path.stem)
        raw = find_raw(root, doc_id, meta.get("sha256", "").strip())
        if raw is None:
            results.append(PrepResult(doc_id, rel, "RAW_MISSING",
                                       f"no file with sha256 {meta.get('sha256', '')[:16]}... under corpus_local/_raw/{doc_id}/"))
            continue
        rraw = raw.relative_to(root).as_posix()
        entry = _entry_for(prep["docs"].get(doc_id), meta.get("sha256", "").strip())
        rec = recipes.get(doc_id)
        build, what = None, None
        if rec is not None:
            build, what = (lambda: rec.build(raw)), rec.description
        elif entry is not None and "extractor" in entry:
            build = lambda: build_from_prep(entry, raw)  # noqa: E731
            what = f"recorded transform: {entry['extractor']}, {len(entry.get('patch', []))} patch hunks"
        if build is None:
            if not audit:
                results.append(PrepResult(doc_id, rel, "NO_RECIPE", "raw file verified; no registered recipe rebuilds the corpus file", rraw))
                continue
            try:
                a = audit_corpus_file(root, path, raw, doc_id, prep)
            except Exception as exc:
                results.append(PrepResult(doc_id, rel, "RECIPE_ERROR", f"audit failed: {type(exc).__name__}: {exc}", rraw))
                continue
            if a["content_lines"] == 0:
                results.append(PrepResult(doc_id, rel, "EMPTY",
                                           "the corpus file has no content lines (only headings, page markers or omission marks); "
                                           "nothing was verified", rraw, a))
            elif a["unmatched"]:
                where = ", ".join(f"line {u['line']}" for u in a["unmatched"][:8])
                results.append(PrepResult(doc_id, rel, "UNMATCHED",
                                           f"{len(a['unmatched'])} of {a['content_lines']} content lines are not in the raw text and not registered ({where})", rraw, a))
            else:
                t = a["tiers"]
                results.append(PrepResult(doc_id, rel, "AUDITED",
                                           f"{a['content_lines']} content lines found in raw ({t['verbatim']} exact, {t['whitespace']} whitespace, "
                                           f"{t['typography']} typography, {t['enumerator']} enumerator, {t['labelled']} labelled), "
                                           f"{a['registered_edits']} registered edits", rraw, a))
            continue
        try:
            rebuilt = build()
        except Exception as exc:
            results.append(PrepResult(doc_id, rel, "RECIPE_ERROR", f"{type(exc).__name__}: {exc}", rraw))
            continue
        if rebuilt.encode("utf-8") == data:
            results.append(PrepResult(doc_id, rel, "REPRODUCED", what, rraw))
        else:
            results.append(PrepResult(doc_id, rel, "DIFFERS", _first_difference(data.decode("utf-8", "replace"), rebuilt), rraw))
    return results


def audit_report(results: list[PrepResult]) -> dict:
    """A text-free summary of the check, safe to commit: statuses, counts, hashes."""
    return {"files": [{"doc_id": r.doc_id, "path": r.path, "status": r.status, "raw": r.raw, "detail": r.detail,
                       "audit": r.audit} for r in results],
            "reproduced": sum(r.status == "REPRODUCED" for r in results),
            "audited": sum(r.status == "AUDITED" for r in results),
            "failing": [r.doc_id for r in results if r.status not in PASSING],
            "tiers": AUDIT_TIERS}


def rebuild(root: str | Path, doc_id: str, recipes: dict | None = None) -> Path:
    """Write one corpus file from its raw file with its recipe or recorded transform."""
    root = Path(root)
    recipes = RECIPES if recipes is None else recipes
    entry = load_prep(root)["docs"].get(doc_id)
    for path in corpus_files(root):
        meta = _front_matter(path.read_text(encoding="utf-8"))
        if meta.get("doc_id") == doc_id:
            raw = find_raw(root, doc_id, meta.get("sha256", "").strip())
            if raw is None:
                raise FileNotFoundError(f"raw file for {doc_id} not found")
            entry = _entry_for(entry, meta.get("sha256", "").strip())
            if doc_id in recipes:
                text = recipes[doc_id].build(raw)
            elif entry is not None and "extractor" in entry:
                text = build_from_prep(entry, raw)
            else:
                raise KeyError(f"no recipe or recorded transform for {doc_id}")
            path.write_text(text, encoding="utf-8", newline="\n")
            return path
    raise FileNotFoundError(f"no corpus file for {doc_id}")


# ---- recipes ----------------------------------------------------------------

def _u01(raw: Path) -> str:
    """Federal Decree-Law 45 of 2021, Arabic print edition (moved unchanged from scripts/build_u01.py)."""
    from .ingest import extract_pdf_pages, render_corpus_md, split_into_sections

    pages = extract_pdf_pages(raw)
    drops = [r"^قانون حماية البيانات الشخصية لدولة", r"^من إصدارات دار نشر معهد دبي القضائي$", r"^\d{1,2}\s+\d{1,2}$"]
    body = split_into_sections(pages, lang="ar", drop_lines=drops)
    meta = {
        "doc_id": "U01",
        "instrument_id": "AE-FDL-45-2021",
        "title": "Federal Decree-Law No. (45) of 2021 on the Protection of Personal Data (Arabic print edition)",
        "issuer": "President of the United Arab Emirates (print edition by Dubai Judicial Institute, 2023)",
        "instrument_type": "decree-law",
        "binding": True,
        "jurisdiction": "AE-federal",
        "language": "ar",
        "official_translation": False,
        "source_url": "https://assets.u.ae/api/public/content/954629a06cc64fd887771ce3273d6154?v=21d88839",
        "retrieved_at": "2026-10-27T14:09:00Z",
        "version": "issued 2021-09-20; Official Gazette 712 supplement 1, 2021-09-26; in force 2022-01-02 (per print edition p.3)",
        "effective_date": "2022-01-02",
        "supersedes": None,
        "status": "in force (as stated in the 2023 print edition; amendments not checked)",
        "terms_note": "Print edition p.4 reserves all publishing rights to Dubai Judicial Institute and bars reproduction without written permission; link only",
        "republish": "link_only",
        "sha256": hashlib.sha256(Path(raw).read_bytes()).hexdigest(),
        "extraction": "pypdfium2 text layer, page numbers are PDF page indexes; Arabic ligature order defects in the text layer (for example the word for 'in' extracts reversed); not repaired; unsuitable for quotation verification",
    }
    return render_corpus_md(meta, body)


RECIPES: dict[str, Recipe] = {
    "U01": Recipe("U01", _u01, "pypdfium2 text layer, Arabic article headings, running headers dropped"),
}
