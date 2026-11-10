"""Quotation verification: a mechanical check that a quoted string appears in a cited passage.

It does not show that the passage supports a conclusion or applies to the actor.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_QUOTES = {"“": '"', "”": '"', "„": '"', "‘": "'", "’": "'", "‚": "'"}
_DASHES = {"\u2013": "-", "\u2014": "-", "\u2212": "-"}
_ELLIPSIS = re.compile(r"\s*(?:\[\.\.\.\]|\[…\]|…|\.\.\.)\s*")
MIN_WORDS = 4
MIN_FRAGMENT_WORDS = 2  # a fragment of one word can be found almost anywhere and cannot carry a quotation


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    for k, v in {**_QUOTES, **_DASHES}.items():
        s = s.replace(k, v)
    return " ".join(s.split())


@dataclass(frozen=True)
class QuoteCheck:
    verified: bool
    reason: str  # ok | not_found | too_short | fragment_too_short | fragments_out_of_order | empty


def check_quotation(quote: str, passage_text: str) -> QuoteCheck:
    if not quote or not quote.strip():
        return QuoteCheck(False, "empty")
    frags = [normalize(f) for f in _ELLIPSIS.split(quote) if f.strip()]
    if not frags:
        return QuoteCheck(False, "empty")
    if sum(len(f.split()) for f in frags) < MIN_WORDS:
        return QuoteCheck(False, "too_short")
    if len(frags) > 1 and any(len(f.split()) < MIN_FRAGMENT_WORDS for f in frags):
        return QuoteCheck(False, "fragment_too_short")
    hay = normalize(passage_text)
    pos = 0
    for f in frags:
        i = _find_on_word_boundaries(hay, f, pos)
        if i < 0:
            return QuoteCheck(False, "fragments_out_of_order" if _find_on_word_boundaries(hay, f, 0) >= 0 else "not_found")
        pos = i + len(f)
    return QuoteCheck(True, "ok")


def _find_on_word_boundaries(hay: str, frag: str, start: int) -> int:
    """First index at or after start where frag occurs and neither end cuts through a word."""
    i = hay.find(frag, start)
    while i >= 0:
        before_ok = not (frag[0].isalnum() and i > 0 and hay[i - 1].isalnum())
        j = i + len(frag)
        after_ok = not (frag[-1].isalnum() and j < len(hay) and hay[j].isalnum())
        if before_ok and after_ok:
            return i
        i = hay.find(frag, i + 1)
    return -1
