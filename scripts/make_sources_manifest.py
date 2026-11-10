"""Write corpus/SOURCES.json, the acquisition manifest, from corpus_local front matter and raw files.

Usage: python scripts/make_sources_manifest.py [--checks checks.json]
`--checks` merges a `last_checked` record per file from a kb-fetch check made in a fresh folder.
Run on the machine that holds corpus_local/; the committed manifest is what a fresh clone uses.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gapcourt.fetch import KNOWN_BLOCKING_HOSTS, validate_manifest  # noqa: E402

MEDIA = {".pdf": "pdf", ".html": "html", ".htm": "html", ".docx": "docx", ".zip": "zip", ".json": "json"}
HTML_NOTE = ("HTML page: the host may serve different bytes on a later visit (dynamic markup), so a mismatch "
             "needs review against the recorded hash, never a silent update")
URL_OVERRIDES = {
    "G5": ("https://legalinstruments.oecd.org/public/doc/648/f401b8d2-9993-41ec-82c5-d53a69e4991b.htm",
           "instrument body file named in the G5 extraction note"),
    "G8": ("https://www.gesetze-im-internet.de/sgb_10/xml.zip", "SGB X XML download"),
}
EXTRA = [  # further raw files behind a document, not named by its front-matter sha256
    {"doc_id": "G8", "file": "corpus_local/_raw/G8/vwvfg_xml.zip", "url": "https://www.gesetze-im-internet.de/vwvfg/xml.zip",
     "note": "VwVfG XML download (second file behind G8)"},
]
PENDING = [  # official texts not yet obtained; hashes are recorded after the reviewer downloads and reviews them
    {"doc_id": "U04", "title": "UAE Charter for the Development and Use of Artificial Intelligence",
     "url": "https://uaelegislation.gov.ae/en/policy/details/the-uae-charter-for-the-development-and-use-of-artificial-intelligence",
     "file": "corpus_local/uae/_incoming/U04_charter_en.pdf", "media": "pdf"},
    {"doc_id": "U10", "title": "Federal Decree-Law No. (23) of 2024 on social support (title to be confirmed from the official text)",
     "url": "https://uaelegislation.gov.ae/en/legislations/2670", "file": "corpus_local/uae/_incoming/U10_en.pdf",
     "media": "pdf"},
]


def front_matter(p: Path) -> dict:
    t = p.read_text(encoding="utf-8")
    return dict(re.findall(r"^([A-Za-z0-9_]+): (.*)$", t.split("\n---", 1)[0], re.M))


def host(url: str) -> str:
    return re.sub(r"^https?://([^/]+).*$", r"\1", url)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--checks")
    a = ap.parse_args(argv)
    checks = json.loads(Path(a.checks).read_text(encoding="utf-8")) if a.checks else {}
    sources = []
    for p in sorted((ROOT / "corpus_local").glob("*/*.md")):
        if p.parent.name.startswith("_"):
            continue
        fm = front_matter(p)
        d = fm["doc_id"]
        raw_dir = ROOT / "corpus_local" / "_raw" / d
        raw = next((f for f in sorted(raw_dir.rglob("*")) if f.is_file() and sha(f) == fm["sha256"]), None)
        if raw is None:
            raise SystemExit(f"{d}: no raw file matches the front-matter sha256")
        url, why = URL_OVERRIDES.get(d, (fm["source_url"].split()[0], "source_url in the corpus front matter"))
        sources.append({"doc_id": d, "title": fm.get("title", ""), "file": raw.relative_to(ROOT).as_posix(),
                        "url": url, "url_basis": why, "sha256": fm["sha256"], "bytes": raw.stat().st_size,
                        "media": MEDIA.get(raw.suffix.lower(), "txt"), "republish": fm.get("republish")})
    for x in EXTRA:
        f = ROOT / x["file"]
        sources.append({"doc_id": x["doc_id"], "title": "", "file": x["file"], "url": x["url"], "url_basis": x["note"],
                        "sha256": sha(f), "bytes": f.stat().st_size, "media": MEDIA[f.suffix.lower()],
                        "republish": "unknown"})
    for s in sources:
        blocked = host(s["url"]) in KNOWN_BLOCKING_HOSTS
        s["access"] = "manual" if blocked else "automatic"
        if blocked:
            s["manual_reason"] = f"{host(s['url'])} refused an automated client during collection"
        if s["media"] == "html":
            s["note"] = HTML_NOTE
    for x in PENDING:
        sources.append({**x, "url_basis": "official portal address; refused a scripted client (HTTP 403) on 2026-10-27",
                        "sha256": None, "bytes": None, "republish": "link_only", "access": "manual",
                        "manual_reason": "not yet obtained: the host refused a scripted client; Walter downloads it in a "
                                         "browser (W5), then the hash is recorded here after review"})
    for s in sources:
        c = checks.get(s["file"])
        if not c:
            continue
        s["last_checked"] = c
        if s["access"] == "automatic" and c["result"] not in ("fetched_ok", "present_ok"):
            # a plain client did not get the recorded bytes in the dated check: browser step, not a script
            s["access"] = "manual"
            s["manual_reason"] = f"kb-fetch check on {c['date']} from a fresh folder: {c['result']} ({c['detail']})"
    m = {"manifest_version": 1,
         "statement": ("Acquisition manifest for the link-only sources. kb-fetch downloads automatic entries with a "
                       "plain client and checks each sha256; manual entries are hosts that refused a scripted client or "
                       "files not yet obtained, and kb-fetch prints the browser step for them. A host can refuse later "
                       "even where it worked before, so complete automatic retrieval is not promised. Nothing listed "
                       "here is republished in this repository."),
         "sources": sources}
    errs = validate_manifest(m)
    if errs:
        raise SystemExit("; ".join(errs))
    out = ROOT / "corpus" / "SOURCES.json"
    out.write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(out, len(sources), "entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
