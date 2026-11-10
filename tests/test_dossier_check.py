"""Source-reviewed dossier: every finding cites a verbatim, hash-checked passage, or it is flagged and left out.

Model-free. The checker re-reads the knowledge base text, so a quotation that is not in the cited passage, a passage
whose stored hash does not match its text, a duty resting on a non-binding source, or a finding with no passage at all
cannot appear as a finding.
"""
import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from gapcourt.chunking import ChunkParams
from gapcourt.dossier import evaluate_spec, render_markdown
from gapcourt.embed import HashingEmbedder
from gapcourt.index import KB, IndexedChunk, build_kb

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def kb(fixtures_dir, tmp_path):
    root = tmp_path / "corpus"
    (root / "uae").mkdir(parents=True)
    (root / "global").mkdir()
    shutil.copy(fixtures_dir / "S01.md", root / "uae" / "S01.md")
    shutil.copy(fixtures_dir / "S02.md", root / "global" / "S02.md")
    shutil.copy(fixtures_dir / "S03_ar.md", root / "uae" / "S03_ar.md")
    return build_kb([root], HashingEmbedder(dim=256), ChunkParams(max_chars=300))


def _spec(claims, findings, searches=None):
    return {"id": "T", "title": "Test dossier", "requirement": "A recipient may not reuse records for another purpose.",
            "claims": claims, "findings": findings, "searches": searches or []}


GOOD = {"id": "C1", "doc_id": "S01", "locator": "Article 3",
        "quote": "The recipient must not use the records for a different purpose without a written basis"}


def test_verbatim_quote_is_verified_with_chunk_id_and_hash(kb):
    res = evaluate_spec(kb, _spec([GOOD], [{"id": "F1", "kind": "requirement", "duty": True,
                                           "statement": "Reuse needs a written basis.", "claims": ["C1"]}]))
    c = res["claims"]["C1"]
    assert c["status"] == "verified" and c["chunk_id"].startswith("S01-")
    assert c["text_sha256_ok"] is True and len(c["text_sha256"]) == 64
    assert res["findings"]["F1"]["status"] == "supported"


def test_altered_quote_is_not_found_and_the_finding_is_flagged_not_supported(kb):
    bad = {**GOOD, "quote": "The recipient must not use the records for a different purpose without any basis"}
    res = evaluate_spec(kb, _spec([bad], [{"id": "F1", "kind": "requirement", "duty": True,
                                           "statement": "Reuse needs a basis.", "claims": ["C1"]}]))
    assert res["claims"]["C1"]["status"] == "quote_not_found"
    assert res["findings"]["F1"]["status"] == "flagged"
    md = render_markdown(_spec([bad], []), res)
    assert "Reuse needs a basis." not in md.split("## Flagged")[0]


def test_wrong_locator_and_missing_document_are_reported(kb):
    res = evaluate_spec(kb, _spec([{**GOOD, "id": "C1", "locator": "Article 9"},
                                   {**GOOD, "id": "C2", "doc_id": "ZZ"}],
                                  [{"id": "F1", "kind": "requirement", "statement": "x", "claims": ["C1", "C2"]}]))
    assert res["claims"]["C1"]["status"] == "no_such_passage"
    assert res["claims"]["C2"]["status"] == "no_such_document"
    assert res["findings"]["F1"]["status"] == "flagged"


def test_finding_without_a_cited_passage_is_flagged(kb):
    res = evaluate_spec(kb, _spec([], [{"id": "F1", "kind": "requirement", "statement": "There is a duty.", "claims": []}]))
    assert res["findings"]["F1"]["status"] == "flagged"
    assert "no passage" in res["findings"]["F1"]["reason"]


def test_a_stored_hash_that_does_not_match_the_text_is_flagged(kb):
    i = next(n for n, e in enumerate(kb.entries) if e.chunk.doc_id == "S01" and e.chunk.locator == "Article 3")
    kb.entries[i] = IndexedChunk(replace(kb.entries[i].chunk, text_sha256="0" * 64), kb.entries[i].collection)
    res = evaluate_spec(kb, _spec([GOOD], [{"id": "F1", "kind": "requirement", "statement": "x", "claims": ["C1"]}]))
    assert res["claims"]["C1"]["text_sha256_ok"] is False
    assert res["claims"]["C1"]["status"] == "hash_mismatch"
    assert res["findings"]["F1"]["status"] == "flagged"


def test_a_duty_cannot_rest_on_a_non_binding_source(kb):
    q = {"id": "C2", "doc_id": "S02", "locator": "Principle 1",
         "quote": "will seek human oversight of consequential automated decisions"}
    res = evaluate_spec(kb, _spec([q], [{"id": "F1", "kind": "requirement", "duty": True, "statement": "Oversight is required.",
                                         "claims": ["C2"]},
                                        {"id": "F2", "kind": "context", "statement": "The strategy states an aim.",
                                         "claims": ["C2"]}]))
    assert res["claims"]["C2"]["status"] == "verified" and res["claims"]["C2"]["binding"] is False
    assert res["findings"]["F1"]["status"] == "flagged" and "non-binding" in res["findings"]["F1"]["reason"]
    assert res["findings"]["F2"]["status"] == "supported"


def test_arabic_passage_can_only_support_a_reading_and_the_quote_is_checked_as_text(kb):
    q = {"id": "C3", "doc_id": "S03", "locator": "Article 3", "quote": "عىل املتحكم إخطار املكتب بأي اخرتاق أو انتهاك للبيانات"}
    res = evaluate_spec(kb, _spec([q], [{"id": "F1", "kind": "requirement", "duty": True, "statement": "x", "claims": ["C3"]},
                                        {"id": "F2", "kind": "reading", "statement": "The Arabic text addresses breach notice.",
                                         "claims": ["C3"]}]))
    assert res["claims"]["C3"]["status"] == "verified" and res["claims"]["C3"]["language"] == "ar"
    assert res["findings"]["F1"]["status"] == "flagged" and "Arabic" in res["findings"]["F1"]["reason"]
    assert res["findings"]["F2"]["status"] == "supported"


def test_unresolved_finding_needs_a_recorded_search_and_is_labelled_corpus_bounded(kb):
    spec = _spec([GOOD], [{"id": "F1", "kind": "unresolved", "statement": "No reviewed text says how the regimes combine.",
                           "claims": ["C1"], "searches": ["S1"]},
                          {"id": "F2", "kind": "unresolved", "statement": "Nothing covers X.", "claims": ["C1"], "searches": []}],
                 searches=[{"id": "S1", "collections": ["kb_uae"], "query": "records reused for a different purpose"}])
    res = evaluate_spec(kb, spec)
    assert res["findings"]["F1"]["status"] == "supported"
    assert res["searches"]["S1"]["kb_version"] == kb.version and res["searches"]["S1"]["top5"]
    assert res["findings"]["F2"]["status"] == "flagged" and "recorded search" in res["findings"]["F2"]["reason"]


def test_raw_file_hash_is_checked_when_the_raw_file_is_present(kb, tmp_path):
    raw = tmp_path / "raw.bin"
    raw.write_bytes(b"raw bytes")
    import hashlib
    sha = hashlib.sha256(b"raw bytes").hexdigest()
    audit = {"files": [{"doc_id": "S01", "status": "REPRODUCED", "raw": "raw.bin"}]}
    spec = _spec([GOOD], [{"id": "F1", "kind": "requirement", "statement": "x", "claims": ["C1"]}])
    ok = evaluate_spec(kb, spec, audit=audit, repo_root=tmp_path, expected_raw_sha={"S01": sha})
    assert ok["claims"]["C1"]["raw"] == {"path": "raw.bin", "present": True, "sha256_ok": True}
    assert ok["claims"]["C1"]["provenance"] == "REPRODUCED"
    bad = evaluate_spec(kb, spec, audit=audit, repo_root=tmp_path, expected_raw_sha={"S01": "f" * 64})
    assert bad["claims"]["C1"]["raw"]["sha256_ok"] is False
    assert bad["findings"]["F1"]["status"] == "flagged"
    none = evaluate_spec(kb, spec, audit=audit, repo_root=tmp_path / "elsewhere", expected_raw_sha={"S01": sha})
    assert none["claims"]["C1"]["raw"]["present"] is False
    assert none["findings"]["F1"]["status"] == "supported"  # raw file absent (a fresh clone): the check is reported, not required


def test_markdown_puts_supported_findings_first_and_flagged_ones_apart(kb):
    bad = {**GOOD, "id": "C2", "quote": "this sentence is not in the passage at all"}
    spec = _spec([GOOD, bad], [{"id": "F1", "kind": "requirement", "duty": True, "statement": "Supported statement.", "claims": ["C1"]},
                               {"id": "F2", "kind": "requirement", "statement": "Unsupported statement.", "claims": ["C2"]}])
    md = render_markdown(spec, evaluate_spec(kb, spec))
    head, _, flagged = md.partition("## Flagged")
    assert "Supported statement." in head and "Unsupported statement." not in head
    assert "Unsupported statement." in flagged and "quote_not_found" in flagged
    assert kb.version in md
    assert "The recipient must not use the records for a different purpose without a written basis" in md


def test_cli_dossier_check_writes_both_files_and_strict_fails_on_a_flag(kb, tmp_path, capsys):
    from gapcourt.cli import main
    kb_dir = tmp_path / "kb"
    kb.save(kb_dir)
    good = _spec([GOOD], [{"id": "F1", "kind": "requirement", "statement": "x", "claims": ["C1"]}])
    p = tmp_path / "spec.json"
    p.write_text(json.dumps(good), encoding="utf-8")
    out_md, out_js = tmp_path / "d.md", tmp_path / "d.json"
    assert main(["dossier-check", "--spec", str(p), "--kb", str(kb_dir), "--out-md", str(out_md), "--out-json", str(out_js),
                 "--strict"]) == 0
    assert out_md.exists() and json.loads(out_js.read_text(encoding="utf-8"))["claims"]["C1"]["status"] == "verified"
    bad = _spec([{**GOOD, "quote": "an invented sentence that is not there"}],
                [{"id": "F1", "kind": "requirement", "statement": "x", "claims": ["C1"]}])
    p.write_text(json.dumps(bad), encoding="utf-8")
    assert main(["dossier-check", "--spec", str(p), "--kb", str(kb_dir), "--out-md", str(out_md), "--out-json", str(out_js),
                 "--strict"]) == 1
    assert main(["dossier-check", "--spec", str(p), "--kb", str(kb_dir), "--out-md", str(out_md), "--out-json", str(out_js)]) == 0


def test_committed_dossier_is_fully_supported_against_the_local_knowledge_base():
    spec_path = REPO / "dossiers" / "DOSSIER_S01.spec.json"
    kb_dir = REPO / "build" / "kb"
    if not spec_path.exists() or not (kb_dir / "chunks.jsonl").exists():
        pytest.skip("needs the dossier spec and the locally built knowledge base")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    audit = json.loads((REPO / "corpus" / "PROVENANCE_AUDIT.json").read_text(encoding="utf-8"))
    res = evaluate_spec(KB.load(kb_dir), spec, audit=audit, repo_root=REPO)
    bad = {k: v["status"] for k, v in res["claims"].items() if v["status"] != "verified"}
    assert not bad, bad
    flagged = {k: v["reason"] for k, v in res["findings"].items() if v["status"] != "supported"}
    declared = set(spec.get("expected_flagged", []))
    assert set(flagged) == declared, (flagged, declared)
    raws = [c["raw"] for c in res["claims"].values()]
    if not all(r and r["present"] for r in raws):
        pytest.skip("raw files not present: the rendered dossier records raw hash checks, so it is compared only where they ran")
    assert all(r["sha256_ok"] for r in raws)
    md = (REPO / "docs" / "DOSSIER_S01.md").read_text(encoding="utf-8")
    assert md == render_markdown(spec, res), "docs/DOSSIER_S01.md is stale: run gapcourt dossier-check"


def test_claim_flags_say_open_text_or_link_only_not_the_raw_republish_value(kb):
    res = evaluate_spec(kb, _spec([GOOD], [{"id": "F1", "kind": "requirement", "statement": "x", "claims": ["C1"]}]))
    md = render_markdown(_spec([GOOD], [{"id": "F1", "kind": "requirement", "statement": "x", "claims": ["C1"]}]), res)
    assert "; yes;" not in md and "open text" in md
