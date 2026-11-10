"""Bilingual retrieval path for the Arabic-only instruments (model-free).

Two parts, both lexical and deterministic:

1. A letter-set key for Arabic tokens. The Arabic text layers of U01 and U08 extract with ligature-order defects (the
   word for "the processing" comes out as "املعالجة" instead of "المعالجة"), so exact word matching fails even between
   two spellings of the same word. The key drops diacritics and tatweel, folds hamza-carrying alef, final yeh and teh
   marbuta, and sorts the letters of the token. Two spellings that differ only in letter order share a key. The cost is
   that anagrams also share a key; this is a retrieval aid and the role reads the passage itself.
2. A documented concept map (bilingual_map.py): English phrases in a question trigger Arabic search terms. It is a
   retrieval aid, not a translation, and nothing it produces is shown to a role as the law or used as a quotation.

Used only by the BM25 arm of HybridRetriever. The vector arm, the knowledge base and the stored vectors are untouched, so
the knowledge base version does not change. English-only text tokenizes exactly as before (see tests).
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field

from .bilingual_map import CONCEPTS
from .bm25 import STOPWORDS
from .embed import _stem
from .verify import MIN_FRAGMENT_WORDS, MIN_WORDS, QuoteCheck, _ELLIPSIS

KEY_NAME = "letter-set-v1"
KEY_PREFIX = "ar~"
ARABIC_SLOTS = 2  # slots reserved for Arabic-only passages when the concept map fires (see retrieve.py)
# diacritics (U+064B to U+065F, U+0670), tatweel (U+0640) and Quranic marks (U+06D6 to U+06ED)
_MARKS = re.compile("[ـً-ٰٟۖ-ۭ]")
_FOLD = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي"})
_ARABIC = re.compile("[؀-ۿ]")
_WORD = re.compile(r"\w+", re.UNICODE)
# one-letter and two-letter clitics that attach to the next word in Arabic script: and, with/in, to, so, like, the,
# and their common combinations
CLITICS = ("", "ال", "و", "ب", "ل", "ف", "ك", "وال", "بال", "لل", "فال", "كال")


def _clean(text: str) -> str:
    return _MARKS.sub("", unicodedata.normalize("NFKC", text))


def is_arabic(token: str) -> bool:
    return bool(_ARABIC.search(token))


def arabic_key(token: str) -> str:
    """Letter-set key of one token (see the module docstring)."""
    t = _clean(token).translate(_FOLD)
    return KEY_PREFIX + "".join(sorted(t))


def retrieval_tokens(text: str, stop: bool = False) -> list[str]:
    """Like bm25.tokenize, but Arabic-script tokens are replaced one for one by their letter-set key.
    Text without Arabic gives exactly the tokens bm25.tokenize gives, so English document lengths do not move."""
    if _ARABIC.search(text):
        text = _clean(text)  # Arabic presentation forms and diacritics; text without Arabic is left exactly as it is
    out = []
    for t in _WORD.findall(text.lower()):
        if is_arabic(t):
            out.append(arabic_key(t))
        elif not (stop and t in STOPWORDS):
            out.append(t)
    return out


def _stems(text: str) -> list[str]:
    return [_stem(t) for t in _WORD.findall(text.lower())]


def _contains(seq: list[str], phrase: list[str]) -> bool:
    n = len(phrase)
    return n > 0 and any(seq[i:i + n] == phrase for i in range(len(seq) - n + 1))


@dataclass
class Expansion:
    concepts: list[str] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)


def expand_query(query: str) -> Expansion:
    """Concepts whose every group has a matching phrase in the question, and the Arabic terms they add."""
    stems = _stems(query)
    exp = Expansion()
    for c in CONCEPTS:
        if all(any(_contains(stems, _stems(p)) for p in group) for group in c["groups"]):
            exp.concepts.append(c["id"])
            for term in c["arabic"]:
                if term not in exp.terms:
                    exp.terms.append(term)
    return exp


def variant_keys(raw_token: str) -> set[str]:
    base = _clean(raw_token)
    bare = base[2:] if base.startswith("ال") and len(base) > 4 else base
    forms = {base, bare}
    forms.update(c + bare for c in CLITICS if c)
    return {arabic_key(f) for f in forms}


def expansion_tokens(query: str) -> list[str]:
    """Query tokens to add to the BM25 query: the keys of every Arabic term word, with clitic variants."""
    out: list[str] = []
    for term in expand_query(query).terms:
        for raw in _WORD.findall(_clean(term)):
            if is_arabic(raw):
                out.extend(sorted(variant_keys(raw)))
            else:
                out.append(raw)
    return out


def _find_run(seq: list[str], run: list[str], start: int) -> int:
    n = len(run)
    for i in range(start, len(seq) - n + 1):
        if seq[i:i + n] == run:
            return i
    return -1


def check_quotation_letter_set(quote: str, passage_text: str) -> QuoteCheck:
    """Quotation check for an Arabic passage by letter-set key (NOT_DONE 29).

    The exact check (verify.check_quotation) fails when a model quotes correctly spelled Arabic against a text layer with
    ligature-order defects. This check compares the quotation with the passage word by word through the letter-set key,
    so the two spellings of a word agree. It is weaker than the exact check (it ignores letter order inside a word, so an
    anagram of the right letters would pass) and so a quotation that passes only this check is a READING: callers keep it
    as reading_only and never let it support a ruling. Rules: at least MIN_WORDS Arabic-script words in total; with an
    ellipsis every fragment has at least MIN_FRAGMENT_WORDS; fragments are contiguous runs of whole words in order.
    Only Arabic-script words count; callers apply it to Arabic passages only.
    """
    if not quote or not quote.strip():
        return QuoteCheck(False, "empty")
    frags = [[t for t in retrieval_tokens(f) if t.startswith(KEY_PREFIX)] for f in _ELLIPSIS.split(quote) if f.strip()]
    frags = [f for f in frags if f]
    if not frags:
        return QuoteCheck(False, "too_short")
    if sum(len(f) for f in frags) < MIN_WORDS:
        return QuoteCheck(False, "too_short")
    if len(frags) > 1 and any(len(f) < MIN_FRAGMENT_WORDS for f in frags):
        return QuoteCheck(False, "fragment_too_short")
    hay = retrieval_tokens(passage_text)
    pos = 0
    for f in frags:
        i = _find_run(hay, f, pos)
        if i < 0:
            return QuoteCheck(False, "fragments_out_of_order" if _find_run(hay, f, 0) >= 0 else "not_found")
        pos = i + len(f)
    return QuoteCheck(True, "ok")


def map_sha256() -> str:
    body = {"key": KEY_NAME, "clitics": list(CLITICS), "concepts": CONCEPTS}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
