"""Turn extracted page text into the corpus file format (docs/CORPUS_FORMAT.md).

Own implementation: stable page markers and article headings, front matter, no
vendored reference code. Text is never translated or repaired.
"""
from __future__ import annotations

import re
from pathlib import Path

REQUIRED_ORDER = (
    "doc_id", "instrument_id", "title", "issuer", "instrument_type", "binding", "jurisdiction", "language",
    "official_translation", "source_url", "retrieved_at", "version", "effective_date", "supersedes", "status",
    "terms_note", "republish", "sha256",
)

_HEADINGS = {
    "en": re.compile(r"^\s*(?:Article|ARTICLE|Section|SECTION)\s*\(?\s*(\d+)\s*\)?\s*[.:]?\s*$"),
    "ar": re.compile(r"^\s*(?:المادة\s*[()]*\s*(\d+)\s*[()]*|[()]*\s*(\d+)\s*[()]*\s*المادة)\s*$"),
}


def extract_pdf_pages(path: str | Path) -> list[str]:
    """Page texts via pypdfium2 (text layer only; no OCR)."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    try:
        return [doc[i].get_textpage().get_text_range() for i in range(len(doc))]
    finally:
        doc.close()


def split_into_sections(pages: list[str], lang: str = "en", drop_lines: list[str] | None = None,
                        label: str = "Article") -> str:
    heading = _HEADINGS[lang]
    drops = [re.compile(p) for p in (drop_lines or [])]
    out: list[str] = []
    for n, text in enumerate(pages, start=1):
        out.append(f"<!--page:{n}-->")
        for line in text.splitlines():
            if any(d.search(line.strip()) for d in drops):
                continue
            m = heading.match(line)
            if m:
                num = next(g for g in m.groups() if g)
                out.append(f"### {label} {num}")
            else:
                out.append(line.rstrip())
    return "\n".join(out).strip() + "\n"


def render_corpus_md(meta: dict, body: str) -> str:
    keys = [k for k in REQUIRED_ORDER if k in meta] + [k for k in meta if k not in REQUIRED_ORDER]
    lines = ["---"]
    for k in keys:
        v = meta[k]
        if v is None:
            s = "null"
        elif v is True:
            s = "true"
        elif v is False:
            s = "false"
        else:
            s = str(v)
        if "\n" in s or "\r" in s:
            raise ValueError(f"front matter value for {k} must be a single line")
        lines.append(f"{k}: {s}")
    lines.append("---")
    return "\n".join(lines) + "\n" + body
