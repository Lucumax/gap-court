import shutil

import pytest

from gapcourt.chunking import ChunkParams
from gapcourt.embed import HashingEmbedder
from gapcourt.index import build_kb
from gapcourt.releases import SavedQuestion, diff_kbs, rerun_all


def make(fixtures_dir, tmp_path, name, mutate=None, drop_s02=False):
    root = tmp_path / name
    (root / "uae").mkdir(parents=True)
    shutil.copy(fixtures_dir / "S01.md", root / "uae" / "S01.md")
    if not drop_s02:
        shutil.copy(fixtures_dir / "S02.md", root / "uae" / "S02.md")
    if mutate:
        p = root / "uae" / "S01.md"
        p.write_text(mutate(p.read_text(encoding="utf-8")), encoding="utf-8")
    return build_kb([root], HashingEmbedder(dim=256), ChunkParams(max_chars=300))


@pytest.fixture
def v1(fixtures_dir, tmp_path):
    return make(fixtures_dir, tmp_path, "v1")


def saved_from(kb, qid, query, locator):
    c = next(c for c in kb.all_chunks() if c.locator == locator and c.doc_id == "S01")
    return SavedQuestion(qid=qid, query=query, collections=["kb_uae"], status="answered",
                         cited=[{"chunk_id": c.chunk_id, "doc_id": c.doc_id, "locator": c.locator, "text_sha256": c.text_sha256}])


def test_diff_lists_added_removed_changed_documents(v1, fixtures_dir, tmp_path):
    v2 = make(fixtures_dir, tmp_path, "v2", mutate=lambda t: t.replace("written basis", "documented legal basis"), drop_s02=True)
    d = diff_kbs(v1, v2)
    assert d["removed"] == ["S02"] and d["added"] == [] and d["changed"] == ["S01"]
    assert d["old_version"] == v1.version and d["new_version"] == v2.version


def test_unchanged_kb_leaves_every_question_unchanged(v1, fixtures_dir, tmp_path):
    same = make(fixtures_dir, tmp_path, "same")
    q = saved_from(v1, "Q1", "must the recipient use shared records for a different purpose", "Article 3")
    r = rerun_all(v1, same, [q])
    assert r["counts"] == {"unchanged": 1, "citation_changed": 0, "reopened": 0, "closed": 0, "still_open": 0}


def test_changed_clause_marks_citation_changed(v1, fixtures_dir, tmp_path):
    v2 = make(fixtures_dir, tmp_path, "v2c", mutate=lambda t: t.replace("without a written basis", "without prior written approval"))
    q = saved_from(v1, "Q1", "must the recipient use shared records for a different purpose", "Article 3")
    r = rerun_all(v1, v2, [q])
    assert r["results"][0]["outcome"] == "citation_changed"


def test_removed_source_reopens_question(v1, fixtures_dir, tmp_path):
    v2 = make(fixtures_dir, tmp_path, "v2r", mutate=lambda t: t.split("### Article 3")[0])
    q = saved_from(v1, "Q1", "must the recipient use shared records for a different purpose", "Article 3")
    r = rerun_all(v1, v2, [q])
    assert r["results"][0]["outcome"] == "reopened"
    assert r["counts"]["reopened"] == 1


def test_new_clause_closes_an_open_gap_candidate(v1, fixtures_dir, tmp_path):
    q = SavedQuestion(qid="G1", query="record of purpose retention period for shared benefit records",
                      collections=["kb_uae"], status="gap_open", cited=[])
    v0 = make(fixtures_dir, tmp_path, "v0", mutate=lambda t: t.split("### Article 3")[0])
    r = rerun_all(v0, v1, [q], threshold=0.3)
    assert r["results"][0]["outcome"] == "closed"


def test_gap_stays_open_when_nothing_new_applies(v1, fixtures_dir, tmp_path):
    q = SavedQuestion(qid="G2", query="submarine cable landing station licensing fees", collections=["kb_uae"],
                      status="gap_open", cited=[])
    r = rerun_all(v1, v1, [q], threshold=0.3)
    assert r["results"][0]["outcome"] == "still_open"


def test_receipt_hash_is_deterministic_and_synthetic_flag_is_carried(v1, fixtures_dir, tmp_path):
    q = saved_from(v1, "Q1", "must the recipient use shared records for a different purpose", "Article 3")
    a = rerun_all(v1, v1, [q], synthetic=True)
    b = rerun_all(v1, v1, [q], synthetic=True)
    assert a["receipt_sha256"] == b["receipt_sha256"] and a["synthetic"] is True
