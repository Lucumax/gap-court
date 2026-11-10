import json
import shutil

import numpy as np
import pytest

from gapcourt.bm25 import BM25, tokenize
from gapcourt.chunking import ChunkParams
from gapcourt.corpus import CorpusError
from gapcourt.embed import HashingEmbedder
from gapcourt.index import KB, build_kb
from gapcourt.retrieve import AbstainBaseline, HybridRetriever


@pytest.fixture
def kb(fixtures_dir, tmp_path):
    root = tmp_path / "corpus"
    (root / "uae").mkdir(parents=True)
    (root / "global").mkdir()
    shutil.copy(fixtures_dir / "S01.md", root / "uae" / "S01.md")
    shutil.copy(fixtures_dir / "S02.md", root / "global" / "S02.md")
    return build_kb([root], HashingEmbedder(dim=256), ChunkParams(max_chars=300))


def test_tokenize_lowercases_and_keeps_arabic():
    assert tokenize("The Agency, Article 2(2)!") == ["the", "agency", "article", "2", "2"]
    assert tokenize("قانون حماية") == ["قانون", "حماية"]


def test_bm25_ranks_matching_doc_first():
    docs = [tokenize(t) for t in ["benefit records sharing purpose", "workforce research universities", "automated decision notice"]]
    bm = BM25(docs)
    scores = bm.scores(tokenize("purpose of sharing benefit records"))
    assert int(np.argmax(scores)) == 0
    assert scores[1] == 0.0


def test_hashing_embedder_deterministic_and_normalized():
    e = HashingEmbedder(dim=128)
    a = e.embed(["automated decision notice", "workforce"])
    b = e.embed(["automated decision notice", "workforce"])
    assert np.array_equal(a, b)
    assert np.allclose(np.linalg.norm(a, axis=1), 1.0)
    sim_related = float(a[0] @ e.embed(["notice before an automated decision"])[0])
    sim_unrelated = float(a[0] @ e.embed(["universities research partnerships"])[0])
    assert sim_related > sim_unrelated


def test_collections_are_isolated(kb):
    assert set(kb.collections) == {"kb_uae", "kb_global"}
    uae_only = HybridRetriever(kb, collections=["kb_uae"], k=5)
    hits = uae_only.search("trustworthy artificial intelligence human oversight")
    assert hits and all(h.chunk.doc_id == "S01" for h in hits)


def test_hybrid_finds_expected_article(kb):
    r = HybridRetriever(kb, collections=["kb_uae"], k=3)
    hits = r.search("must the recipient use shared records for a different purpose")
    assert hits[0].chunk.locator == "Article 3"


def test_bm25_baseline_arm_has_no_vector_score(kb):
    r = HybridRetriever(kb, collections=["kb_uae"], k=3, mode="bm25")
    hits = r.search("government agencies processing personal data")
    assert hits[0].chunk.locator == "Article 2"
    assert all(h.vec_score is None for h in hits)


def test_unknown_mode_rejected(kb):
    with pytest.raises(ValueError):
        HybridRetriever(kb, collections=["kb_uae"], mode="magic")


def test_unknown_collection_rejected(kb):
    with pytest.raises(KeyError):
        HybridRetriever(kb, collections=["kb_nope"])


def test_abstain_baseline_abstains_on_unrelated_query(kb):
    base = AbstainBaseline(HybridRetriever(kb, collections=["kb_uae"], k=3), threshold=0.35)
    out = base.answer("submarine cable landing station licensing fees")
    assert out.abstained is True and out.passages == []
    ok = base.answer("agency shares benefit records with another agency purpose")
    assert ok.abstained is False and ok.passages[0].chunk.locator == "Article 3"


def test_kb_build_is_idempotent_and_version_is_manifest_hash(kb, fixtures_dir, tmp_path):
    root = tmp_path / "corpus"
    kb2 = build_kb([root], HashingEmbedder(dim=256), ChunkParams(max_chars=300))
    assert kb.version == kb2.version and kb.version.startswith("kb-")
    assert [c.chunk_id for c in kb.all_chunks()] == [c.chunk_id for c in kb2.all_chunks()]
    assert np.array_equal(kb.vectors, kb2.vectors)


def test_kb_version_changes_when_a_document_changes(kb, tmp_path):
    root = tmp_path / "corpus"
    p = root / "uae" / "S01.md"
    p.write_text(p.read_text(encoding="utf-8").replace("Agency", "Authority"), encoding="utf-8")
    changed = build_kb([root], HashingEmbedder(dim=256), ChunkParams(max_chars=300))
    assert changed.version != kb.version


def test_save_and_load_round_trip(kb, tmp_path):
    out = tmp_path / "kbout"
    kb.save(out)
    for name in ("KB_MANIFEST.json", "chunks.jsonl", "vectors.npy", "REFERENCES.md"):
        assert (out / name).exists()
    manifest = json.loads((out / "KB_MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["kb_version"] == kb.version
    assert manifest["embedder"]["name"] == "hashing-lexical-v1"
    loaded = KB.load(out)
    assert loaded.version == kb.version
    assert np.array_equal(loaded.vectors, kb.vectors)
    assert [c.chunk_id for c in loaded.all_chunks()] == [c.chunk_id for c in kb.all_chunks()]


def test_every_chunk_traces_to_manifest_document(kb):
    docs = {d["doc_id"]: d for d in kb.manifest()["documents"]}
    for c in kb.all_chunks():
        assert c.doc_id in docs
        assert len(c.text_sha256) == 64


def test_public_tree_rejects_link_only_documents(fixtures_dir, tmp_path):
    root = tmp_path / "corpus"
    (root / "uae").mkdir(parents=True)
    text = (fixtures_dir / "S01.md").read_text(encoding="utf-8").replace("republish: yes", "republish: link_only")
    (root / "uae" / "S01.md").write_text(text, encoding="utf-8")
    with pytest.raises(CorpusError, match="link_only"):
        build_kb([root], HashingEmbedder(dim=64), ChunkParams(), public_roots=[root])


def test_coverage_table_counts_sections_and_pages(kb):
    rows = {r["doc_id"]: r for r in kb.coverage_table()}
    assert rows["S01"]["sections"] == 4
    assert rows["S01"]["pages_seen"] == 2
    assert rows["S01"]["language"] == "en"


def test_public_export_redacts_link_only_text_and_vectors(fixtures_dir, tmp_path):
    root = tmp_path / "corpus"
    (root / "uae").mkdir(parents=True)
    (root / "global").mkdir()
    t = (fixtures_dir / "S01.md").read_text(encoding="utf-8").replace("republish: yes", "republish: link_only")
    (root / "uae" / "S01.md").write_text(t, encoding="utf-8")
    shutil.copy(fixtures_dir / "S02.md", root / "global" / "S02.md")
    kb = build_kb([root], HashingEmbedder(dim=64), ChunkParams(max_chars=300))
    out = tmp_path / "pub"
    kb.save_public(out)
    rows = [json.loads(x) for x in (out / "chunks.jsonl").read_text(encoding="utf-8").splitlines()]
    s01 = [r for r in rows if r["doc_id"] == "S01"]
    s02 = [r for r in rows if r["doc_id"] == "S02"]
    assert s01 and all(r["text"] is None and len(r["text_sha256"]) == 64 for r in s01)
    assert s02 and all(r["text"] for r in s02)
    manifest = json.loads((out / "KB_MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["public_release"] is True and manifest["kb_version"] == kb.version
    assert manifest["link_only_documents"] == ["S01"]
    assert "S01" not in (out / "vectors_index.json").read_text(encoding="utf-8")
    assert "must record the purpose" not in (out / "chunks.jsonl").read_text(encoding="utf-8")
    with pytest.raises(CorpusError):
        KB.load(out)


def test_public_export_has_readme_with_acknowledgement_and_no_endorsement_claim(kb, tmp_path):
    out = tmp_path / "pub2"
    kb.save_public(out)
    text = (out / "KB_README.md").read_text(encoding="utf-8")
    assert "compiled for the AI for Good Lab Hackathon, United Arab Emirates, with acknowledgement to the UAE" in text
    assert "Independent entry. Not affiliated with or endorsed by ITU." in text
    assert "No endorsement by any UAE authority is implied." in text
    assert kb.version in text
