"""Corpus documents: front matter, sections, locators and page numbers.

Format is documented in docs/CORPUS_FORMAT.md. Text is kept verbatim.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

REQUIRED_KEYS = (
    "doc_id", "instrument_id", "title", "issuer", "instrument_type", "binding",
    "jurisdiction", "language", "official_translation", "source_url",
    "retrieved_at", "version", "effective_date", "supersedes", "status",
    "terms_note", "republish", "sha256",
)
REPUBLISH_VALUES = ("yes", "link_only", "unknown")

_PAGE_RE = re.compile(r"^<!--page:(\d+)-->\s*$")
_HEAD_RE = re.compile(r"^###\s+(.+?)\s*$")


class CorpusError(ValueError):
    pass


@dataclass
class Section:
    locator: str
    text: str
    page_start: int | None
    page_end: int | None
    # (character offset into text, page number in force from that offset)
    page_spans: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class Document:
    meta: dict
    sections: list[Section]
    raw_sha256: str

    @property
    def doc_id(self) -> str:
        return self.meta["doc_id"]

    @property
    def collection_hint(self) -> str:
        return self.meta.get("collection", "")


def _literal(value: str):
    v = value.strip()
    if v == "null":
        return None
    if v == "true":
        return True
    if v == "false":
        return False
    return v


def _split_front_matter(raw: str) -> tuple[dict, str]:
    lines = raw.lstrip("\ufeff").splitlines()
    if not lines or lines[0].strip() != "---":
        raise CorpusError("missing front matter")
    meta: dict = {}
    end = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = i
            break
        if not line.strip():
            continue
        if ":" not in line:
            raise CorpusError(f"bad front matter line: {line!r}")
        key, _, value = line.partition(":")
        meta[key.strip()] = _literal(value)
    if end is None:
        raise CorpusError("unterminated front matter")
    return meta, "\n".join(lines[end + 1:])


def parse_document(raw: str) -> Document:
    meta, body = _split_front_matter(raw)
    missing = [k for k in REQUIRED_KEYS if k not in meta]
    if missing:
        raise CorpusError(f"missing required keys: {', '.join(missing)}")
    if meta["republish"] not in REPUBLISH_VALUES:
        raise CorpusError(f"republish must be one of {REPUBLISH_VALUES}")

    sections: list[Section] = []
    seen_locators: dict[str, int] = {}
    locator = "Preamble"
    buf: list[str] = []
    spans: list[tuple[int, int]] = []
    length = 0
    page: int | None = None
    section_first_page: int | None = None

    def flush() -> None:
        nonlocal buf, spans, length, section_first_page
        text = "\n".join(buf).strip("\n")
        stripped = text.strip()
        if stripped or locator != "Preamble":
            lead = len(text) - len(text.lstrip("\n"))
            sp = [(max(0, off - lead), p) for off, p in spans]
            pages = [p for _, p in sp] or ([page] if page is not None else [])
            first = section_first_page if section_first_page is not None else (pages[0] if pages else None)
            last = pages[-1] if pages else first
            seen_locators[locator] = seen_locators.get(locator, 0) + 1
            uniq = locator if seen_locators[locator] == 1 else f"{locator} [{seen_locators[locator]}]"
            sections.append(Section(uniq, stripped, first, last, sp))
        buf, spans, length = [], [], 0

    for line in body.splitlines():
        pm = _PAGE_RE.match(line)
        if pm:
            page = int(pm.group(1))
            if not buf and section_first_page is None:
                section_first_page = page
            spans.append((length, page))
            continue
        hm = _HEAD_RE.match(line)
        if hm:
            flush()
            locator = hm.group(1)
            section_first_page = page
            spans = [(0, page)] if page is not None else []
            continue
        buf.append(line)
        length += len(line) + 1
    flush()
    return Document(meta=meta, sections=sections,
                    raw_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest())


def load_document(path: str | Path) -> Document:
    return parse_document(Path(path).read_text(encoding="utf-8"))
