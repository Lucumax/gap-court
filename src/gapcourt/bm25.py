"""Small, dependency-free BM25 (Okapi) used as the baseline retrieval arm."""
from __future__ import annotations

import math
import re
from collections import Counter

import numpy as np

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
STOPWORDS = frozenset(
    "a an and are as at be been by for from has have in is it its of on or that the to was were will with this these those not no".split()
)


def tokenize(text: str, stop: bool = False) -> list[str]:
    toks = _TOKEN_RE.findall(text.lower())
    if stop:
        toks = [t for t in toks if t not in STOPWORDS]
    return toks


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.n = len(docs)
        self.doc_len = np.array([len(d) for d in docs], dtype=np.float64)
        self.avgdl = float(self.doc_len.mean()) if self.n else 0.0
        self.tf = [Counter(d) for d in docs]
        df: Counter = Counter()
        for c in self.tf:
            df.update(c.keys())
        self.idf = {t: math.log(1.0 + (self.n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: list[str]) -> np.ndarray:
        out = np.zeros(self.n, dtype=np.float64)
        if not self.n:
            return out
        for t in set(query):
            idf = self.idf.get(t)
            if idf is None:
                continue
            for i, c in enumerate(self.tf):
                f = c.get(t, 0)
                if f:
                    denom = f + self.k1 * (1 - self.b + self.b * self.doc_len[i] / (self.avgdl or 1.0))
                    out[i] += idf * f * (self.k1 + 1) / denom
        return out

    def max_score(self, query: list[str]) -> float:
        """Upper bound of one document's score for this query (term saturation)."""
        return float(sum(self.idf.get(t, 0.0) * (self.k1 + 1) for t in set(query) if t in self.idf))
