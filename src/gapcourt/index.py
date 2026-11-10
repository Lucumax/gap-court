"""Knowledge base build, versioning, export and load."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .chunking import Chunk, ChunkParams, chunk_document
from .corpus import CorpusError, Document, load_document
from .embed import Embedder, embedder_from_spec


def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def embed_text(c: Chunk) -> str:
    return f"{c.title}. {c.locator}. {c.text}"


@dataclass
class IndexedChunk:
    chunk: Chunk
    collection: str


class KB:
    def __init__(self, entries: list[IndexedChunk], vectors: np.ndarray, doc_rows: list[dict],
                 params: ChunkParams, embedder: Embedder):
        self.entries = entries
        self.vectors = vectors
        self._doc_rows = doc_rows
        self.params = params
        self.embedder = embedder
        self.applicability: dict | None = None  # set by the command line from corpus/APPLICABILITY.json (gate rule 5)
        self.collections: dict[str, list[int]] = {}
        for i, e in enumerate(entries):
            self.collections.setdefault(e.collection, []).append(i)
        self._manifest = self._build_manifest()
        self.version = self._manifest["kb_version"]

    def all_chunks(self) -> list[Chunk]:
        return [e.chunk for e in self.entries]

    def chunk_by_id(self, chunk_id: str) -> Chunk:
        for e in self.entries:
            if e.chunk.chunk_id == chunk_id:
                return e.chunk
        raise KeyError(chunk_id)

    def collection_of(self, chunk_id: str) -> str:
        for e in self.entries:
            if e.chunk.chunk_id == chunk_id:
                return e.collection
        raise KeyError(chunk_id)

    def _build_manifest(self) -> dict:
        body = {
            "chunk_params": self.params.as_dict(),
            "embedder": {"name": self.embedder.name, "dim": int(self.embedder.dim)},
            "documents": self._doc_rows,
            "chunk_count": len(self.entries),
            "chunk_ids_sha256": hashlib.sha256(_canon([e.chunk.chunk_id for e in self.entries])).hexdigest(),
            "vectors_sha256": hashlib.sha256(np.ascontiguousarray(self.vectors).tobytes()).hexdigest(),
        }
        version = "kb-" + hashlib.sha256(_canon(body)).hexdigest()[:12]
        return {"kb_version": version, **body}

    def manifest(self) -> dict:
        return self._manifest

    def coverage_table(self) -> list[dict]:
        return [dict(r) for r in self._doc_rows]

    def save(self, out: str | Path) -> None:
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "KB_MANIFEST.json").write_text(
            json.dumps(self._manifest, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        with open(out / "chunks.jsonl", "w", encoding="utf-8", newline="\n") as f:
            for e in self.entries:
                row = e.chunk.to_dict()
                row["collection"] = e.collection
                f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        np.save(out / "vectors.npy", self.vectors.astype(np.float32))
        lines = [f"# References ({self.version})", ""]
        for r in self._doc_rows:
            lines.append(f"- {r['doc_id']} ({r['collection']}): {r['title']}. {r['issuer']}. {r['source_url']} "
                         f"(retrieved {r['retrieved_at']}; republish: {r['republish']})")
        (out / "REFERENCES.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def save_public(self, out: str | Path) -> None:
        """Release export: text and vectors only for republish=yes documents; hashes for all."""
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        open_docs = {r["doc_id"] for r in self._doc_rows if r["republish"] == "yes"}
        link_only = sorted(r["doc_id"] for r in self._doc_rows if r["republish"] != "yes")
        manifest = dict(self._manifest)
        manifest.pop("vectors_sha256", None)
        manifest.update({
            "public_release": True, "link_only_documents": link_only,
            "rebuild": ("Link-only documents are not redistributed. Run `gapcourt kb-fetch` to download each raw file from its official "
                        "address and check its sha256 (entries marked manual need a browser step), then `gapcourt prepare --rebuild-all` "
                        "to write the corpus files from the raw files with the recorded transforms, then `gapcourt build-kb`. "
                        "kb_version matches the published one only when every document is rebuilt byte for byte; documents that "
                        "`gapcourt prepare` reports as AUDITED are curated excerpts that pass a verbatim audit but have no byte-exact "
                        "transform (see docs/CORPUS_PROVENANCE.md)."),
        })
        (out / "KB_MANIFEST.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        keep = []
        with open(out / "chunks.jsonl", "w", encoding="utf-8", newline="\n") as f:
            for i, e in enumerate(self.entries):
                row = e.chunk.to_dict()
                row["collection"] = e.collection
                if e.chunk.doc_id not in open_docs:
                    row["text"] = None
                else:
                    keep.append(i)
                f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        np.save(out / "vectors.npy", self.vectors[keep].astype(np.float32))
        (out / "vectors_index.json").write_text(
            json.dumps([self.entries[i].chunk.chunk_id for i in keep], indent=0), encoding="utf-8")
        lines = [f"# References ({self.version})", ""]
        for r in self._doc_rows:
            tag = "link only" if r["republish"] != "yes" else "text included"
            lines.append(f"- {r['doc_id']} ({r['collection']}): {r['title']}. {r['issuer']}. {r['source_url']} "
                         f"(retrieved {r['retrieved_at']}; {tag})")
        (out / "REFERENCES.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (out / "KB_README.md").write_text(
            f"# Knowledge base {self.version}\n\n"
            "This knowledge base was compiled for the AI for Good Lab Hackathon, United Arab Emirates, with acknowledgement to the UAE.\n"
            "Independent entry. Not affiliated with or endorsed by ITU. Documents are public UAE instruments, cited with source links. "
            "No endorsement by any UAE authority is implied.\n\n"
            "Files: KB_MANIFEST.json (documents, hashes, chunking and embedder settings), chunks.jsonl (stable chunk ids, locators, "
            "text hashes; text only where redistribution is permitted), vectors.npy and vectors_index.json (vectors for those chunks), "
            "REFERENCES.md (one line per document).\n"
            "Link-only documents are listed in the manifest with their source URLs and file hashes; rebuild instructions are in the manifest.\n",
            encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path, embedder: Embedder | None = None) -> "KB":
        path = Path(path)
        manifest = json.loads((path / "KB_MANIFEST.json").read_text(encoding="utf-8"))
        if manifest.get("public_release"):
            raise CorpusError("a public release export cannot be loaded; rebuild from source files (see manifest)")
        entries: list[IndexedChunk] = []
        for line in (path / "chunks.jsonl").read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            coll = row.pop("collection")
            entries.append(IndexedChunk(Chunk(**row), coll))
        vectors = np.load(path / "vectors.npy")
        emb = embedder or embedder_from_spec(manifest["embedder"])
        p = manifest["chunk_params"]
        kb = cls(entries, vectors, manifest["documents"], ChunkParams(max_chars=p["max_chars"]), emb)
        if kb.version != manifest["kb_version"]:
            raise CorpusError("loaded KB does not match its manifest hash")
        return kb


def _scan(root: Path):
    for sub in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("_")):
        for f in sorted(sub.glob("*.md")):
            yield f"kb_{sub.name}", f


def build_kb(roots: list[Path], embedder: Embedder, params: ChunkParams | None = None,
             public_roots: list[Path] | None = None) -> KB:
    params = params or ChunkParams()
    public = [Path(p).resolve() for p in (public_roots or [])]
    found: list[tuple[str, Path, Document]] = []
    seen: set[str] = set()
    for root in roots:
        root = Path(root)
        if not root.exists():
            continue
        for coll, f in _scan(root):
            doc = load_document(f)
            if doc.doc_id in seen:
                raise CorpusError(f"duplicate doc_id {doc.doc_id}")
            seen.add(doc.doc_id)
            if root.resolve() in public and doc.meta["republish"] != "yes":
                raise CorpusError(
                    f"{doc.doc_id} is republish={doc.meta['republish']} but sits in a public root")
            found.append((coll, f, doc))
    found.sort(key=lambda t: (t[0], t[2].doc_id))

    entries: list[IndexedChunk] = []
    rows: list[dict] = []
    for coll, f, doc in found:
        chunks = chunk_document(doc, params)
        pages = {p for s in doc.sections for _, p in s.page_spans}
        pages |= {p for s in doc.sections for p in (s.page_start, s.page_end) if p is not None}
        rows.append({
            "doc_id": doc.doc_id, "collection": coll, "instrument_id": doc.meta["instrument_id"],
            "title": doc.meta["title"], "issuer": doc.meta["issuer"],
            "instrument_type": doc.meta["instrument_type"], "binding": doc.meta["binding"],
            "language": doc.meta["language"], "version": str(doc.meta["version"]),
            "source_url": doc.meta["source_url"], "retrieved_at": doc.meta["retrieved_at"],
            "republish": doc.meta["republish"], "source_sha256": doc.meta["sha256"],
            "file_sha256": doc.raw_sha256, "sections": sum(1 for s in doc.sections if s.text.strip()),
            "pages_seen": len(pages), "chunks": len(chunks),
            "oversize_chunks": sum(1 for c in chunks if c.oversize),
        })
        entries.extend(IndexedChunk(c, coll) for c in chunks)

    ids = [e.chunk.chunk_id for e in entries]
    if len(ids) != len(set(ids)):
        raise CorpusError("duplicate chunk ids; locators must be unique within a document")
    texts = [embed_text(e.chunk) for e in entries]
    vectors = embedder.embed(texts) if texts else np.zeros((0, embedder.dim), dtype=np.float32)
    return KB(entries, vectors.astype(np.float32), rows, params, embedder)
