"""ITU reference ingestion (CrashingGuru/ITUAIReadiness, MIT) adapted for Gap Court: parity and isolation tests.

The vendored files are byte-identical copies at a pinned commit. The adapter reimplements the functions it uses with
the changes listed in NOTICE; these tests run the vendored originals against the adapted versions on the same input
with import stubs (no Ollama, no ChromaDB needed), so a drift between them fails here.
"""
from __future__ import annotations

import hashlib
import importlib.util
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from gapcourt import itu_adapter as itu

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "third_party" / "itu_reference"


# ---- vendored files -----------------------------------------------------------------

def test_vendored_files_match_the_recorded_hashes_and_pinned_commit():
    sums = {}
    for line in (VENDOR / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        digest, _, name = line.partition(" *")
        sums[name.strip()] = digest
    assert "simulation/server/knowledge/ingest.py" in sums and "simulation/server/knowledge/kb.py" in sums and "LICENSE" in sums
    for name, digest in sums.items():
        assert hashlib.sha256((VENDOR / name).read_bytes()).hexdigest() == digest, name
    assert itu.REFERENCE["commit"] == "84010da9e9c443afcceeccb86d1700a662223395"
    assert itu.verify_vendored() == []


def test_notice_names_the_source_licence_commit_and_every_change():
    notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
    assert "CrashingGuru/ITUAIReadiness" in notice and "MIT" in notice and itu.REFERENCE["commit"] in notice
    for token in ("docling", "ollama", "chromadb", "chunk_text", "embed_texts", "KnowledgeBase"):
        assert token in notice, token
    assert "Copyright (c) 2026 Vishnu Ram OV" in (VENDOR / "LICENSE").read_text(encoding="utf-8")


# ---- parity with the vendored originals ------------------------------------------------

class FakeOllamaClient:
    calls: list = []
    fail_batches_over = None

    def __init__(self, host=None):
        self.host = host

    def embed(self, model, input):
        FakeOllamaClient.calls.append((model, list(input)))
        if FakeOllamaClient.fail_batches_over and len(input) > FakeOllamaClient.fail_batches_over:
            raise RuntimeError("batch too large")
        return {"embeddings": [[float(len(t)), 1.0, 0.5] for t in input]}


@pytest.fixture()
def vendored_ingest(monkeypatch):
    """The vendored ingest.py loaded with stub modules for its imports (ollama, server.config, server.knowledge.kb)."""
    stub_ollama = types.ModuleType("ollama")
    stub_ollama.Client = FakeOllamaClient
    stub_cfg = types.ModuleType("server.config")
    stub_cfg.settings = types.SimpleNamespace(chunk_size=256, chunk_overlap=30, embed_model="nomic-embed-text",
                                              ollama_base_url="http://localhost:11434")
    stub_kb = types.ModuleType("server.knowledge.kb")
    stub_kb.KnowledgeBase = object
    for name, mod in (("ollama", stub_ollama), ("server", types.ModuleType("server")),
                      ("server.knowledge", types.ModuleType("server.knowledge")),
                      ("server.config", stub_cfg), ("server.knowledge.kb", stub_kb)):
        monkeypatch.setitem(sys.modules, name, mod)
    spec = importlib.util.spec_from_file_location("vendored_itu_ingest", VENDOR / "simulation/server/knowledge/ingest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    FakeOllamaClient.calls = []
    FakeOllamaClient.fail_batches_over = None
    return mod


def _long_text(n_words):
    paras = []
    w = 0
    while w < n_words:
        paras.append(" ".join(f"word{i}" for i in range(w, min(w + 90, n_words))))
        w += 90
    return "\n\n".join(paras)


@pytest.mark.parametrize("n_words", [0, 5, 40, 300, 700, 2500])
def test_chunk_text_matches_the_vendored_original(vendored_ingest, n_words):
    text = _long_text(n_words)
    assert itu.chunk_text(text) == vendored_ingest.chunk_text(text)
    assert itu.chunk_text(text, chunk_size=50, overlap=7, max_chars=300) == vendored_ingest.chunk_text(text, 50, 7, 300)


def test_metadata_inference_and_html_strip_match_the_vendored_original(vendored_ingest):
    for name in ("UAE_National_AI_Strategy_2031.pdf", "Y.3172.pdf", "OECD_principles.pdf", "Ethiopia_policy_act.pdf",
                 "some_regulation.html", "random.txt", "EU_AI_Act.pdf", "sample_national_plan.md"):
        assert itu.infer_source_type(name) == vendored_ingest.infer_source_type(name), name
        assert itu.infer_country(name) == vendored_ingest.infer_country(name), name
    html = "<html><body><p>One <b>two</b></p>\n<p>three</p></body></html>"
    assert itu._strip_html(html) == vendored_ingest._strip_html(html)
    a = itu.build_metadata(Path("x/Y.3172.pdf"), 2, 9, {"document_category": "standards"})
    b = vendored_ingest.build_metadata(Path("x/Y.3172.pdf"), 2, 9, {"document_category": "standards"})
    a.pop("ingested_at"), b.pop("ingested_at")
    assert a == b


def test_embed_texts_batches_and_truncates_like_the_vendored_original(vendored_ingest):
    texts = ["short"] * 20 + ["x" * 2600, "y" * 50]
    ref = vendored_ingest.embed_texts(texts)
    ref_calls, FakeOllamaClient.calls = FakeOllamaClient.calls, []
    mine = itu.embed_texts(texts, client=FakeOllamaClient())
    assert mine == ref
    assert FakeOllamaClient.calls == ref_calls
    assert [len(c[1]) for c in ref_calls] == [16, 6]
    assert max(len(t) for _, batch in FakeOllamaClient.calls for t in batch) == 2000


def test_embed_texts_falls_back_to_one_at_a_time_like_the_original(vendored_ingest):
    FakeOllamaClient.fail_batches_over = 1
    texts = ["a", "bb", "ccc"]
    ref = vendored_ingest.embed_texts(texts)
    ref_calls, FakeOllamaClient.calls = FakeOllamaClient.calls, []
    mine = itu.embed_texts(texts, client=FakeOllamaClient())
    assert mine == ref and FakeOllamaClient.calls == ref_calls


# ---- ChromaDB store, collection isolation, stable ids ---------------------------------

class FakeCollection:
    def __init__(self, name, metadata):
        self.name, self.metadata, self.rows = name, metadata, {}

    def add(self, ids, documents, embeddings, metadatas):
        for i, d, e, m in zip(ids, documents, embeddings, metadatas):
            assert i not in self.rows, "duplicate id"
            self.rows[i] = (d, np.asarray(e, dtype=float), m)

    def count(self):
        return len(self.rows)

    def get(self, include):
        ids = list(self.rows)
        return {"ids": ids, "documents": [self.rows[i][0] for i in ids], "embeddings": [self.rows[i][1] for i in ids],
                "metadatas": [self.rows[i][2] for i in ids]}

    def query(self, query_embeddings, n_results, include, **kw):
        q = np.asarray(query_embeddings[0], dtype=float)
        scored = sorted(((1 - float(q @ v / ((np.linalg.norm(q) * np.linalg.norm(v)) or 1)), i, d, m)
                         for i, (d, v, m) in self.rows.items()))[:n_results]
        return {"ids": [[s[1] for s in scored]], "documents": [[s[2] for s in scored]],
                "metadatas": [[s[3] for s in scored]], "distances": [[s[0] for s in scored]]}


class FakeClient:
    def __init__(self, path, settings=None):
        self.path, self.cols = path, {}

    def get_or_create_collection(self, name, metadata=None):
        return self.cols.setdefault(name, FakeCollection(name, metadata))

    def delete_collection(self, name):
        self.cols.pop(name, None)


@pytest.fixture()
def fake_chromadb(monkeypatch):
    mod = types.ModuleType("chromadb")
    mod.PersistentClient = lambda path, settings=None: FakeClient(path, settings)
    cfg = types.ModuleType("chromadb.config")
    cfg.Settings = lambda **kw: types.SimpleNamespace(**kw)
    monkeypatch.setitem(sys.modules, "chromadb", mod)
    monkeypatch.setitem(sys.modules, "chromadb.config", cfg)
    return mod


def _tiny_kb(tmp_path):
    from gapcourt.chunking import ChunkParams
    from gapcourt.embed import HashingEmbedder
    from gapcourt.index import build_kb

    def doc(root, coll, doc_id, text):
        d = tmp_path / root / coll
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{doc_id}.md").write_text(
            f"---\ndoc_id: {doc_id}\ninstrument_id: I-{doc_id}\ntitle: Title {doc_id}\nissuer: Issuer\ninstrument_type: law\nbinding: true\n"
            f"jurisdiction: X\nlanguage: en\nofficial_translation: false\nsource_url: https://x/{doc_id}\nretrieved_at: 2026-11-01T00:00:00Z\n"
            f"version: v1\neffective_date: 2026-01-01\nsupersedes: null\nstatus: in force\nterms_note: t\nrepublish: yes\nsha256: {'0' * 64}\n---\n"
            f"<!--page:1-->\n### Article 1\n{text}\n### Article 2\nSecond article about {doc_id}.\n", encoding="utf-8")

    doc("corpus", "uae", "U90", "Personal data may be used only for the purpose collected.")
    doc("corpus", "global", "G90", "An automated decision requires notice and an explanation.")
    return build_kb([tmp_path / "corpus"], HashingEmbedder(dim=64), ChunkParams(max_chars=400))


def test_chroma_store_keeps_collections_separate_and_ids_stable(tmp_path, fake_chromadb):
    kb = _tiny_kb(tmp_path)
    store = itu.ChromaStore(tmp_path / "chroma")
    counts = itu.load_kb_into_store(kb, store, embed_fn=lambda texts: [[float(len(t) % 7), 1.0, 0.0] for t in texts])
    assert counts == {c: len(ix) for c, ix in kb.collections.items()} and sum(counts.values()) == len(kb.entries)
    uae_ids = {kb.entries[i].chunk.chunk_id for i in kb.collections["kb_uae"]}
    hits = store.search("kb_uae", [1.0, 1.0, 0.0], n_results=50)
    assert set(hits["ids"][0]) == uae_ids
    assert all(m["collection"] == "kb_uae" and "locator" in m and m["doc_id"] == "U90" for m in hits["metadatas"][0])
    assert store.stats() == counts
    assert store._client.cols["kb_uae"].metadata == {"hnsw:space": "cosine"}


def test_flat_vector_export_matches_kb_order(tmp_path, fake_chromadb):
    kb = _tiny_kb(tmp_path)
    store = itu.ChromaStore(tmp_path / "chroma")
    itu.load_kb_into_store(kb, store, embed_fn=lambda texts: [[float(i), 1.0, 0.0] for i, _ in enumerate(texts)])
    ids, vecs = itu.export_flat_vectors(store, kb)
    assert ids == [e.chunk.chunk_id for e in kb.entries] and vecs.shape == (len(kb.entries), 3)


def test_real_chromadb_roundtrip_if_installed(tmp_path):
    pytest.importorskip("chromadb")
    kb = _tiny_kb(tmp_path)
    store = itu.ChromaStore(tmp_path / "chroma")
    counts = itu.load_kb_into_store(kb, store, embed_fn=lambda texts: [[float(len(t) % 7) + 1, 1.0, 0.5] for t in texts])
    assert sum(counts.values()) == len(kb.entries)
    res = store.search("kb_global", [1.0, 1.0, 0.5], n_results=2)
    assert all(i.startswith("G90") for i in res["ids"][0])


# ---- the embedder behind our interface -------------------------------------------------

def test_reference_embedder_uses_the_adapted_embed_texts_and_normalizes():
    emb = itu.ReferenceOllamaEmbedder(client=FakeOllamaClient())
    assert emb.name == "ollama:nomic-embed-text"
    out = emb.embed(["abc", "defgh"])
    assert out.shape == (2, 3) and np.allclose(np.linalg.norm(out, axis=1), 1.0, atol=1e-5)
    assert emb.dim == 3


def test_reference_embedder_reports_a_missing_server_plainly():
    class Down:
        def embed(self, **kw):
            raise ConnectionError("refused")

    with pytest.raises(itu.EmbedderUnavailable, match="Ollama"):
        itu.ReferenceOllamaEmbedder(client=Down()).embed(["x"])


# ---- build-kb with the reference embedder and store -----------------------------------------

def test_build_kb_with_ollama_embedder_names_the_embedder_and_fills_chroma(tmp_path, fixtures_dir, monkeypatch, fake_chromadb, capsys):
    import shutil

    from gapcourt.cli import main
    from gapcourt.index import KB

    root = tmp_path / "corpus"
    (root / "uae").mkdir(parents=True)
    shutil.copy(fixtures_dir / "S01.md", root / "uae" / "S01.md")
    monkeypatch.setattr(itu, "embed_texts", lambda texts, model=itu.EMBED_MODEL, base_url=itu.OLLAMA_BASE_URL, client=None:
                        [[float(len(t) % 5) + 1.0, 2.0, 0.5] for t in texts])
    out = tmp_path / "kb"
    assert main(["build-kb", "--root", str(root), "--out", str(out), "--embedder", "ollama", "--chroma-dir", str(tmp_path / "chroma")]) == 0
    kb = KB.load(out)
    assert kb.embedder.name == "ollama:nomic-embed-text" and kb.vectors.shape[1] == 3
    printed = capsys.readouterr().out
    assert "chroma" in printed and "kb_uae" in printed


def test_build_kb_ollama_down_is_a_plain_error_not_a_traceback(tmp_path, fixtures_dir, monkeypatch):
    import shutil

    from gapcourt.cli import main

    root = tmp_path / "corpus"
    (root / "uae").mkdir(parents=True)
    shutil.copy(fixtures_dir / "S01.md", root / "uae" / "S01.md")

    def down(*a, **k):
        raise itu.EmbedderUnavailable("could not get embeddings from Ollama at http://localhost:11434")

    monkeypatch.setattr(itu, "embed_texts", down)
    with pytest.raises(SystemExit) as e:
        main(["build-kb", "--root", str(root), "--out", str(tmp_path / "kb"), "--embedder", "ollama"])
    assert "Ollama" in str(e.value)
