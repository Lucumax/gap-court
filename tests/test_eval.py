import json
import shutil

import pytest

from gapcourt.chunking import ChunkParams
from gapcourt.embed import HashingEmbedder
from gapcourt.eval import (
    EvalError,
    freeze_record,
    labels_sha256,
    load_labels,
    score_retrieval,
)
from gapcourt.index import build_kb

LABELS = [
    {"case_id": "C01", "split": "dev", "category": "answerable",
     "question": "must the recipient use shared records for a different purpose",
     "expected": [{"doc_id": "S01", "locator": "Article 3"}], "expected_outcome": "ANSWER", "status": "DRAFT_UNAPPROVED"},
    {"case_id": "C02", "split": "dev", "category": "answerable",
     "question": "which agencies are excluded from the law",
     "expected": [{"doc_id": "S01", "locator": "Article 2"}], "expected_outcome": "ANSWER", "status": "DRAFT_UNAPPROVED"},
    {"case_id": "C03", "split": "heldout", "category": "missing_document",
     "question": "submarine cable landing station licensing fees",
     "expected": [], "expected_outcome": "ABSTAIN", "status": "DRAFT_UNAPPROVED"},
]


@pytest.fixture
def kb(fixtures_dir, tmp_path):
    root = tmp_path / "corpus"
    (root / "uae").mkdir(parents=True)
    shutil.copy(fixtures_dir / "S01.md", root / "uae" / "S01.md")
    return build_kb([root], HashingEmbedder(dim=256), ChunkParams(max_chars=300))


def write(tmp_path, rows):
    p = tmp_path / "labels.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return p


def test_labels_hash_is_stable_and_order_independent_of_whitespace(tmp_path):
    a = write(tmp_path, LABELS)
    h1 = labels_sha256(a)
    b = tmp_path / "labels2.jsonl"
    b.write_text("\n".join(json.dumps(r, indent=None, sort_keys=True) for r in LABELS) + "\n\n", encoding="utf-8")
    assert h1 == labels_sha256(b)
    changed = [dict(LABELS[0], question="different")] + LABELS[1:]
    assert labels_sha256(write(tmp_path, changed)) != h1


def test_load_labels_requires_status_and_known_split(tmp_path):
    bad = [dict(LABELS[0], status="APPROVED_BY_NOBODY")]
    with pytest.raises(EvalError):
        load_labels(write(tmp_path, bad))
    with pytest.raises(EvalError):
        load_labels(write(tmp_path, [dict(LABELS[0], split="test")]))


def test_unapproved_labels_cannot_be_scored_as_results(tmp_path, kb):
    labels = load_labels(write(tmp_path, LABELS))
    with pytest.raises(EvalError, match="unapproved"):
        score_retrieval(kb, labels, ["kb_uae"], mode="hybrid", k=3, require_approved=True)


def test_retrieval_score_reports_k_of_n_per_category(tmp_path, kb):
    labels = load_labels(write(tmp_path, LABELS))
    res = score_retrieval(kb, labels, ["kb_uae"], mode="hybrid", k=3)
    assert res["answerable"] == {"hit": 2, "n": 2}
    assert "missing_document" not in res  # no governing passage is expected, so retrieval success is undefined
    assert res["n_scored"] == 2
    assert res["config"]["mode"] == "hybrid" and res["config"]["kb_version"] == kb.version


def test_bm25_and_hybrid_are_scored_under_identical_conditions(tmp_path, kb):
    labels = load_labels(write(tmp_path, LABELS))
    a = score_retrieval(kb, labels, ["kb_uae"], mode="bm25", k=3)
    b = score_retrieval(kb, labels, ["kb_uae"], mode="hybrid", k=3)
    assert a["config"]["k"] == b["config"]["k"] and a["config"]["collections"] == b["config"]["collections"]


def test_freeze_record_binds_everything_together(tmp_path, kb):
    p = write(tmp_path, LABELS)
    rec = freeze_record(p, kb_version=kb.version, top_k=3, fusion={"rrf_k": 60, "w_bm25": 1, "w_vec": 1},
                        abstain_threshold=0.35, model_config={"backend": "x", "model_id": "y"}, code_version="abc123")
    again = freeze_record(p, kb_version=kb.version, top_k=3, fusion={"rrf_k": 60, "w_bm25": 1, "w_vec": 1},
                          abstain_threshold=0.35, model_config={"backend": "x", "model_id": "y"}, code_version="abc123")
    assert rec["freeze_sha256"] == again["freeze_sha256"]
    other = freeze_record(p, kb_version=kb.version, top_k=4, fusion={"rrf_k": 60, "w_bm25": 1, "w_vec": 1},
                          abstain_threshold=0.35, model_config={"backend": "x", "model_id": "y"}, code_version="abc123")
    assert other["freeze_sha256"] != rec["freeze_sha256"]
    assert rec["labels_sha256"] == labels_sha256(p)
