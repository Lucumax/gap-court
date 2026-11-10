"""Hybrid retrieval (BM25 + vectors, reciprocal-rank fusion), scoped by collection,
and a retrieval-plus-abstain baseline."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import bilingual as bl
from .bm25 import BM25
from .chunking import Chunk
from .index import KB

MODES = ("hybrid", "bm25", "vector")


@dataclass
class Hit:
    chunk: Chunk
    collection: str
    score: float
    bm25_score: float | None
    vec_score: float | None
    bm25_norm: float | None = None
    bm25_rank: int | None = None
    vec_rank: int | None = None
    arabic_slot: bool = False  # one of the reserved slots for Arabic-only passages (bilingual path), ranked by the added terms


@dataclass
class Answer:
    abstained: bool
    support: float
    passages: list[Hit] = field(default_factory=list)


def _mostly_arabic(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    return bool(letters) and sum(1 for ch in letters if "؀" <= ch <= "ۿ") / len(letters) >= 0.5


class HybridRetriever:
    def __init__(self, kb: KB, collections: list[str], k: int = 5, mode: str = "hybrid",
                 rrf_k: int = 60, w_bm25: float = 1.0, w_vec: float = 1.0, embedder=None,
                 doc_ids: list[str] | None = None, bilingual: bool = True):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        for c in collections:
            if c not in kb.collections:
                raise KeyError(c)
        self.kb, self.collections, self.k, self.mode = kb, list(collections), k, mode
        self.rrf_k, self.w_bm25, self.w_vec = rrf_k, w_bm25, w_vec
        self.embedder = embedder or kb.embedder
        self.doc_ids = list(doc_ids) if doc_ids is not None else None
        self.bilingual = bool(bilingual)
        self.idx = [i for c in collections for i in kb.collections[c]
                    if self.doc_ids is None or kb.entries[i].chunk.doc_id in self.doc_ids]
        self._arabic = np.array([_mostly_arabic(kb.entries[i].chunk.text) for i in self.idx], dtype=bool)
        self._bm25 = BM25([
            bl.retrieval_tokens(f"{kb.entries[i].chunk.title} {kb.entries[i].chunk.locator} {kb.entries[i].chunk.text}", stop=True)
            for i in self.idx
        ])

    def config(self) -> dict:
        return {"collections": self.collections, "doc_ids": self.doc_ids, "k": self.k, "mode": self.mode,
                "rrf_k": self.rrf_k, "w_bm25": self.w_bm25, "w_vec": self.w_vec, "kb_version": self.kb.version,
                "bilingual": ({"map_sha256": bl.map_sha256(), "arabic_key": bl.KEY_NAME, "arabic_slots": bl.ARABIC_SLOTS}
                              if self.bilingual else None)}

    def search(self, query: str, k: int | None = None) -> list[Hit]:
        k = k or self.k
        n = len(self.idx)
        if n == 0:
            return []
        qt = bl.retrieval_tokens(query, stop=True)
        extra = bl.expansion_tokens(query) if self.bilingual else []
        bm = self._bm25.scores(qt + extra)
        bm_max = self._bm25.max_score(qt) or 1.0  # the question's own terms; added Arabic terms do not lower the other passages' share
        qv = self.embedder.embed([query])[0]
        vs = self.kb.vectors[self.idx] @ qv
        bm_rank = {int(i): r for r, i in enumerate(np.argsort(-bm, kind="stable"), start=1)}
        vs_rank = {int(i): r for r, i in enumerate(np.argsort(-vs, kind="stable"), start=1)}
        fused = np.zeros(n)
        for i in range(n):
            if self.mode in ("hybrid", "bm25") and bm[i] > 0:
                fused[i] += (self.w_bm25 if self.mode == "hybrid" else 1.0) / (self.rrf_k + bm_rank[i])
            if self.mode in ("hybrid", "vector"):
                fused[i] += (self.w_vec if self.mode == "hybrid" else 1.0) / (self.rrf_k + vs_rank[i])
        use_bm = self.mode != "vector"
        use_vec = self.mode != "bm25"
        # Arabic-only passages cannot be ranked against an English question by the vector arm, so when the concept map
        # fired they get a fixed quota: the best ARABIC_SLOTS passages by the added Arabic terms alone, interleaved with the
        # usual list of the other passages by reciprocal rank (ties go to the usual list). Questions the map does not
        # match, and the "vector" ablation, are ranked exactly as before.
        arabic_slots: list[int] = []
        if extra and self.mode in ("hybrid", "bm25") and self._arabic.any():
            ar_scores = self._bm25.scores(extra)
            cand = [int(i) for i in np.argsort(-ar_scores, kind="stable") if self._arabic[i] and ar_scores[i] > 0]
            arabic_slots = cand[:bl.ARABIC_SLOTS]
        order = [int(i) for i in np.argsort(-fused, kind="stable") if not (arabic_slots and self._arabic[i])]
        merged = [(1.0 / (self.rrf_k + r), 0, i, False) for r, i in enumerate(order, start=1) if fused[i] > 0]
        merged += [(1.0 / (self.rrf_k + r), 1, i, True) for r, i in enumerate(arabic_slots, start=1)]
        merged.sort(key=lambda t: (-t[0], t[1]))
        hits: list[Hit] = []
        for rr, _, i, in_slot in merged[:k]:
            e = self.kb.entries[self.idx[i]]
            hits.append(Hit(
                chunk=e.chunk, collection=e.collection, score=float(rr * self.w_bm25) if in_slot else float(fused[i]),
                bm25_score=float(bm[i]) if use_bm else None,
                vec_score=float(vs[i]) if use_vec else None,
                bm25_norm=float(min(1.0, bm[i] / bm_max)) if use_bm else None,
                bm25_rank=bm_rank[i] if use_bm else None,
                vec_rank=vs_rank[i] if use_vec else None,
                arabic_slot=in_slot,
            ))
        return hits


class AbstainBaseline:
    """Retrieval plus an abstain threshold. No roles, no verifier."""

    def __init__(self, retriever: HybridRetriever, threshold: float = 0.35):
        self.retriever, self.threshold = retriever, threshold

    def support(self, hits: list[Hit]) -> float:
        if not hits:
            return 0.0
        top = hits[0]
        parts = []
        if top.bm25_norm is not None:
            parts.append(top.bm25_norm)
        if top.vec_score is not None:
            parts.append(max(0.0, top.vec_score))
        return float(np.mean(parts)) if parts else 0.0

    def answer(self, query: str) -> Answer:
        hits = self.retriever.search(query)
        s = self.support(hits)
        if s < self.threshold:
            return Answer(True, s, [])
        return Answer(False, s, hits)
