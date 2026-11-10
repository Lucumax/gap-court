"""ITU reference ingestion (CrashingGuru/ITUAIReadiness, MIT) adapted for Gap Court. See NOTICE for every change.

The reference pipeline parses a document, chunks it, embeds the chunks with `nomic-embed-text` through a local Ollama
server and stores them in ChromaDB. Here the parts that carry the reference behaviour are kept with their logic
unchanged (chunk_text, embed_texts, metadata inference, the PyMuPDF parse path, the ChromaDB store); the knowledge
base keeps this repository's own sections, stable chunk ids and article and page locators, so the reference chunker is
used for parity only and the reference store receives our chunks. Nothing here runs a model or a server by itself:
Ollama and ChromaDB are imported lazily and can be replaced by test doubles.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)

VENDOR_DIR = Path(__file__).resolve().parents[2] / "third_party" / "itu_reference"
REFERENCE = {
    "repo": "https://github.com/CrashingGuru/ITUAIReadiness",
    "commit": "84010da9e9c443afcceeccb86d1700a662223395",
    "licence": "MIT",
    "retrieved": "2026-11-01",
}

# reference defaults (server/config.py), inlined
CHUNK_SIZE = 256
CHUNK_OVERLAP = 30
EMBED_MODEL = "nomic-embed-text"
OLLAMA_BASE_URL = "http://localhost:11434"


class EmbedderUnavailable(RuntimeError):
    pass


def verify_vendored() -> list[str]:
    """Problems with the vendored copy (empty list = every file present and byte-identical to the recorded hash)."""
    problems = []
    sums = VENDOR_DIR / "SHA256SUMS.txt"
    if not sums.is_file():
        return ["SHA256SUMS.txt missing"]
    for line in sums.read_text(encoding="utf-8").splitlines():
        digest, _, name = line.partition(" *")
        p = VENDOR_DIR / name.strip()
        if not p.is_file():
            problems.append(f"{name}: missing")
        elif hashlib.sha256(p.read_bytes()).hexdigest() != digest:
            problems.append(f"{name}: sha256 differs from the recorded hash")
    return problems


# ---- parsing (reference ingest.py; PyMuPDF path only) ------------------------------------

_SOURCE_TYPE_PATTERNS: list[tuple[str, str]] = [
    (r"(?i)ITU|Y\.3172|AI4G|sandbox", "standards_document"),
    (r"(?i)UNESCO|OECD|Oxford|UNCTAD|CCIA|Turing", "international_org_report"),
    (r"(?i)national.*(strategy|plan)|NAIS|SDAIA|EBIA|PBIA", "national_strategy"),
    (r"(?i)policy|regulation|act|governance", "policy_document"),
    (r"(?i)sample_national", "national_strategy"),
    (r"(?i)AI_Ready_Framework", "international_org_report"),
]
_COUNTRY_PATTERNS: list[tuple[str, str]] = [
    (r"(?i)ethiopia", "Ethiopia"),
    (r"(?i)saudi|SDAIA|NSDAI", "Saudi Arabia"),
    (r"(?i)egypt", "Egypt"),
    (r"(?i)kenya", "Kenya"),
    (r"(?i)zambia", "Zambia"),
    (r"(?i)brazil|PBIA|EBIA", "Brazil"),
    (r"(?i)india|NITI", "India"),
    (r"(?i)singapore|NAIS", "Singapore"),
    (r"(?i)japan", "Japan"),
    (r"(?i)china", "China"),
    (r"(?i)\bUS\b|USA|united.states", "United States"),
    (r"(?i)\bUK\b|united.kingdom", "United Kingdom"),
    (r"(?i)\bEU\b|european", "European Union"),
    (r"(?i)african.union", "African Union"),
]


def parse_pdf(filepath: Path) -> str:
    """Reference `_parse_pdf`, PyMuPDF path: page texts joined by a blank line."""
    import fitz

    doc = fitz.open(str(filepath))
    try:
        pages = [page.get_text() for page in doc]
    finally:
        doc.close()
    return "\n\n".join(pages)


def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP, max_chars: int = 2000) -> list[str]:
    """Reference `chunk_text` (word-count chunks with overlap, hard-capped by characters)."""
    if not text or not text.strip():
        return []
    paragraphs = re.split(r"\n\s*\n", text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]
    chunks: list[str] = []
    current_words: list[str] = []
    for para in paragraphs:
        words = para.split()
        for word in words:
            current_words.append(word)
            if len(current_words) >= chunk_size:
                chunks.append(" ".join(current_words))
                current_words = current_words[-overlap:] if overlap > 0 else []
    if current_words:
        last_chunk = " ".join(current_words)
        if len(current_words) > 20 or not chunks:
            chunks.append(last_chunk)
        elif chunks:
            chunks[-1] = chunks[-1] + " " + last_chunk
    safe_chunks: list[str] = []
    for chunk in chunks:
        if len(chunk) <= max_chars:
            safe_chunks.append(chunk)
        else:
            for i in range(0, len(chunk), max_chars):
                piece = chunk[i: i + max_chars].strip()
                if piece:
                    safe_chunks.append(piece)
    return safe_chunks


def infer_source_type(filename: str) -> str:
    for pattern, source_type in _SOURCE_TYPE_PATTERNS:
        if re.search(pattern, filename):
            return source_type
    return "international_org_report"


def infer_country(filename: str) -> str:
    for pattern, country in _COUNTRY_PATTERNS:
        if re.search(pattern, filename):
            return country
    return "global"


def build_metadata(filepath: Path, chunk_index: int, total_chunks: int, extra: Optional[dict[str, Any]] = None) -> dict[str, str]:
    meta: dict[str, str] = {
        "source_file": filepath.name,
        "source_type": infer_source_type(filepath.name),
        "country": infer_country(filepath.name),
        "chunk_index": str(chunk_index),
        "total_chunks": str(total_chunks),
        "ingested_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    }
    if extra:
        for k, v in extra.items():
            meta[k] = str(v)
    return meta


# ---- embedding (reference embed_texts) -----------------------------------------------------

def embed_texts(texts: list[str], model: str = EMBED_MODEL, base_url: str = OLLAMA_BASE_URL, client=None) -> list[list[float]]:
    """Reference `embed_texts`: batches of 16, 2,000-character pre-truncation, one-at-a-time fallback, 4,000-character
    last resort. `client` is an `ollama.Client`-like object; by default the `ollama` package is used."""
    if client is None:
        try:
            import ollama
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise EmbedderUnavailable("the `ollama` Python package is not installed (pip install ollama)") from exc
        client = ollama.Client(host=base_url)
    max_text_chars = 2000
    safe_texts = [t[:max_text_chars] if len(t) > max_text_chars else t for t in texts]
    embeddings: list[list[float]] = []
    batch_size = 16
    try:
        for i in range(0, len(safe_texts), batch_size):
            batch = safe_texts[i: i + batch_size]
            try:
                response = client.embed(model=model, input=batch)
                embeddings.extend(response["embeddings"])
            except Exception:
                logger.warning("Batch embed failed at offset %d, falling back to one-at-a-time", i)
                for j, text in enumerate(batch):
                    try:
                        resp = client.embed(model=model, input=[text])
                        embeddings.extend(resp["embeddings"])
                    except Exception:
                        logger.warning("Single embed failed for chunk %d (len=%d), truncating to 4000 chars", i + j, len(text))
                        resp = client.embed(model=model, input=[text[:4000]])
                        embeddings.extend(resp["embeddings"])
    except Exception as exc:
        raise EmbedderUnavailable(
            f"could not get embeddings from Ollama at {base_url} for model {model}: {type(exc).__name__}: {exc}. "
            f"Is the Ollama server running and `ollama pull {model}` done?") from exc
    return embeddings


class ReferenceOllamaEmbedder:
    """Our Embedder interface over the reference `embed_texts` (nomic-embed-text through a local Ollama server)."""

    def __init__(self, model: str = EMBED_MODEL, base_url: str = OLLAMA_BASE_URL, client=None, dim: int = 768):
        self.name = f"ollama:{model}"
        self.model, self.base_url, self.dim, self._client = model, base_url, dim, client

    def embed(self, texts: list[str]) -> np.ndarray:
        arr = np.asarray(embed_texts(list(texts), self.model, self.base_url, client=self._client), dtype=np.float32)
        if arr.ndim == 2 and arr.shape[0]:
            self.dim = int(arr.shape[1])
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return arr / norms


# ---- ChromaDB store (reference KnowledgeBase, reduced) --------------------------------------

class ChromaStore:
    """Persistent store with one ChromaDB collection per Gap Court collection (cosine distance, telemetry off)."""

    def __init__(self, persist_dir: str | Path):
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        self._client = chromadb.PersistentClient(path=str(persist_dir), settings=ChromaSettings(anonymized_telemetry=False))
        self._collections: dict[str, Any] = {}

    def _col(self, name: str):
        if name not in self._collections:
            self._collections[name] = self._client.get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})
        return self._collections[name]

    def add_documents(self, collection: str, ids: list[str], texts: list[str], embeddings, metadatas: list[dict]) -> list[str]:
        col = self._col(collection)
        batch = 5000
        for i in range(0, len(texts), batch):
            end = min(i + batch, len(texts))
            col.add(ids=ids[i:end], documents=texts[i:end], embeddings=[list(map(float, e)) for e in embeddings[i:end]],
                    metadatas=metadatas[i:end])
        return ids

    def search(self, collection: str, query_embedding, n_results: int = 5) -> dict[str, Any]:
        col = self._col(collection)
        return col.query(query_embeddings=[list(map(float, query_embedding))], n_results=min(n_results, col.count() or 1),
                         include=["documents", "metadatas", "distances", "embeddings"])

    def get_all(self, collection: str) -> dict[str, Any]:
        return self._col(collection).get(include=["documents", "metadatas", "embeddings"])

    def count(self, collection: str) -> int:
        return self._col(collection).count()

    def stats(self) -> dict[str, int]:
        return {name: col.count() for name, col in self._collections.items()}

    def reset(self, names: list[str]) -> None:
        for name in names:
            try:
                self._client.delete_collection(name)
            except Exception:
                pass
            self._collections.pop(name, None)


def load_kb_into_store(kb, store: ChromaStore, embed_fn: Callable[[list[str]], list[list[float]]] | None = None,
                       use_kb_vectors: bool = False) -> dict[str, int]:
    """Store every chunk of a Gap Court KB in the collection it belongs to, under its stable chunk id, with locator
    metadata. `embed_fn` defaults to the reference `embed_texts` (Ollama); `use_kb_vectors` stores the vectors the KB was
    built with instead of embedding again."""
    from .index import embed_text

    embed_fn = embed_fn or embed_texts
    counts: dict[str, int] = {}
    for coll, idxs in sorted(kb.collections.items()):
        entries = [kb.entries[i] for i in idxs]
        texts = [embed_text(e.chunk) for e in entries]
        vectors = [list(map(float, kb.vectors[i])) for i in idxs] if use_kb_vectors else embed_fn(texts)
        ids = [e.chunk.chunk_id for e in entries]
        metas = [{"doc_id": e.chunk.doc_id, "collection": coll, "locator": e.chunk.locator, "kb_version": kb.version,
                  "page_start": int(e.chunk.page_start or 0)} for e in entries]
        store.add_documents(coll, ids, [e.chunk.text for e in entries], vectors, metas)
        counts[coll] = len(ids)
    return counts


def export_flat_vectors(store: ChromaStore, kb) -> tuple[list[str], np.ndarray]:
    """Vectors in KB entry order, read back from the store (the flat export the retriever and the public release use)."""
    by_id: dict[str, np.ndarray] = {}
    for coll in kb.collections:
        got = store.get_all(coll)
        for i, v in zip(got["ids"], got["embeddings"]):
            by_id[i] = np.asarray(v, dtype=np.float32)
    ids = [e.chunk.chunk_id for e in kb.entries]
    return ids, np.vstack([by_id[i] for i in ids]) if ids else np.zeros((0, 0), dtype=np.float32)
