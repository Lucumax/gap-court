"""Embedders behind one interface.

HashingEmbedder: local, deterministic, no download. It hashes light-stemmed word
unigrams, word bigrams and character 4-grams into a fixed-size signed vector. It
is a lexical stand-in, not a neural model. Name it that way in any report.

OllamaEmbedder: adapter for a local Ollama server (default model nomic-embed-text).
It is not exercised in unit tests against a live server.
"""
from __future__ import annotations

import zlib
from typing import Protocol

import numpy as np

from .bm25 import STOPWORDS, tokenize


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray: ...


def _stem(tok: str) -> str:
    for suf in ("ations", "ation", "ings", "ing", "ies", "ied", "ed", "es", "s"):
        if tok.endswith(suf) and len(tok) - len(suf) >= 4:
            return tok[: -len(suf)]
    return tok


class HashingEmbedder:
    name = "hashing-lexical-v1"

    def __init__(self, dim: int = 1024):
        self.dim = dim

    def _features(self, text: str):
        toks = [_stem(t) for t in tokenize(text) if t not in STOPWORDS]
        yield from (("w", t) for t in toks)
        yield from (("b", f"{a} {b}") for a, b in zip(toks, toks[1:]))
        for t in toks:
            padded = f"^{t}$"
            for i in range(len(padded) - 3):
                yield ("c", padded[i:i + 4])

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        weights = {"w": 1.0, "b": 0.7, "c": 0.35}
        for r, text in enumerate(texts):
            counts: dict[tuple[str, str], int] = {}
            for f in self._features(text):
                counts[f] = counts.get(f, 0) + 1
            for (kind, s), n in counts.items():
                h = zlib.crc32(f"{kind}:{s}".encode("utf-8"))
                idx = h % self.dim
                sign = 1.0 if (h >> 31) & 1 else -1.0
                out[r, idx] += sign * weights[kind] * (1.0 + np.log(n))
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return (out / norms).astype(np.float32)


class OllamaEmbedder:
    """Calls a local Ollama server. No key. Requires httpx."""

    def __init__(self, model: str = "nomic-embed-text", base_url: str = "http://localhost:11434",
                 dim: int = 768, client=None):
        self.name = f"ollama:{model}"
        self.model, self.base_url, self.dim = model, base_url.rstrip("/"), dim
        self._client = client

    def embed(self, texts: list[str]) -> np.ndarray:
        import httpx

        client = self._client or httpx.Client(timeout=120)
        resp = client.post(f"{self.base_url}/api/embed", json={"model": self.model, "input": texts})
        resp.raise_for_status()
        arr = np.asarray(resp.json()["embeddings"], dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return arr / norms


def embedder_from_spec(spec: dict) -> Embedder:
    if spec["name"] == HashingEmbedder.name:
        return HashingEmbedder(dim=int(spec["dim"]))
    if spec["name"].startswith("ollama:"):
        from .itu_adapter import ReferenceOllamaEmbedder

        return ReferenceOllamaEmbedder(model=spec["name"].split(":", 1)[1], dim=int(spec["dim"]))
    raise ValueError(f"unknown embedder {spec['name']}")
