"""Boundary-safe chunking with stable ids and locators.

A chunk never starts or ends inside a word. Chunks break only at sentence or
list-item boundaries. A single sentence longer than max_chars is kept whole
(the cap is a target, and the exception is recorded as `oversize=True`).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .corpus import Document, Section

_ENUM_ONLY = re.compile(r"^\(?[0-9A-Za-z\u0600-\u06FF]{1,4}[.)]$")
_SENT_SPLIT = re.compile(r"(?<=[.;:?!\u061B\u061F\u06D4])\s+")
_LINE_START = re.compile(r"^\s*(\(?[0-9]{1,3}[.)]|\(?[a-zA-Z][.)])\s")


@dataclass(frozen=True)
class ChunkParams:
    max_chars: int = 1200

    def as_dict(self) -> dict:
        return {"max_chars": self.max_chars, "overlap": 0, "unit": "sentence_or_list_item"}


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_id: str
    version: str
    locator: str
    ordinal: int
    text: str
    text_sha256: str
    page_start: int | None
    page_end: int | None
    oversize: bool
    title: str

    @property
    def cite(self) -> str:
        base = f"{self.doc_id} {self.locator}"
        return base

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id, "doc_id": self.doc_id, "version": self.version,
            "locator": self.locator, "ordinal": self.ordinal, "text": self.text,
            "text_sha256": self.text_sha256, "page_start": self.page_start,
            "page_end": self.page_end, "oversize": self.oversize, "title": self.title,
        }


def make_chunk_id(doc_id: str, version: str, locator: str, ordinal: int) -> str:
    h = hashlib.sha256(f"{doc_id}\x1f{version}\x1f{locator}\x1f{ordinal}".encode("utf-8")).hexdigest()
    return f"{doc_id}-{h[:10]}"


def split_units(text: str) -> list[str]:
    """Split into sentence or list-item units, keeping every character of content."""
    units: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        # a new line that starts a list item begins a new unit
        lines = para.split("\n")
        blocks: list[str] = []
        cur: list[str] = []
        for ln in lines:
            if cur and _LINE_START.match(ln):
                blocks.append(" ".join(cur))
                cur = [ln.strip()]
            else:
                cur.append(ln.strip())
        if cur:
            blocks.append(" ".join(cur))
        for block in blocks:
            parts = [p for p in _SENT_SPLIT.split(block.strip()) if p]
            merged: list[str] = []
            carry = ""
            for p in parts:
                if carry:
                    p = f"{carry} {p}"
                    carry = ""
                if _ENUM_ONLY.match(p):
                    carry = p
                    continue
                merged.append(p)
            if carry:
                merged.append(carry)
            units.extend(merged)
    return [u for u in units if u.strip()]


def _page_at(section: Section, offset: int) -> int | None:
    page = section.page_start
    for off, p in section.page_spans:
        if off <= offset:
            page = p
        else:
            break
    return page


def _locate(section_text: str, unit: str, start: int) -> int:
    # offsets are approximate when whitespace was collapsed
    idx = section_text.find(unit.split(" ")[0], start)
    return idx if idx >= 0 else start


def chunk_section(doc: Document, section: Section, params: ChunkParams) -> list[Chunk]:
    units = split_units(section.text)
    groups: list[list[str]] = []
    cur: list[str] = []
    cur_len = 0
    for u in units:
        add = len(u) + (1 if cur else 0)
        if cur and cur_len + add > params.max_chars:
            groups.append(cur)
            cur, cur_len = [u], len(u)
        else:
            cur.append(u)
            cur_len += add
    if cur:
        groups.append(cur)

    version = str(doc.meta["version"])
    chunks: list[Chunk] = []
    search_from = 0
    for ordinal, g in enumerate(groups):
        text = " ".join(g)
        start = _locate(section.text, g[0], search_from)
        search_from = start + 1
        end_unit = g[-1]
        end = _locate(section.text, end_unit, start)
        oversize = len(text) > params.max_chars
        chunks.append(Chunk(
            chunk_id=make_chunk_id(doc.doc_id, version, section.locator, ordinal),
            doc_id=doc.doc_id, version=version, locator=section.locator, ordinal=ordinal,
            text=text, text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            page_start=_page_at(section, start), page_end=_page_at(section, end),
            oversize=oversize, title=str(doc.meta["title"]),
        ))
    return chunks


def chunk_document(doc: Document, params: ChunkParams | None = None) -> list[Chunk]:
    params = params or ChunkParams()
    out: list[Chunk] = []
    for s in doc.sections:
        if s.text.strip():
            out.extend(chunk_section(doc, s, params))
    return out
