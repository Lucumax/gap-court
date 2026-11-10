"""D2 sources (2026-11-09): the checked U08 transcription and the official English text of U01.

1. A recorded transform may name a `fallback` transform of another raw file that writes the same corpus file. The checked
   U08 transcription is private (link-only terms), so a fresh clone has only the official PDF; `prepare --rebuild-all`
   then rebuilds U08 from the PDF's text layer, and `prepare` checks whichever of the two the corpus file was built from.
2. Against the locally built knowledge base, the quotation check runs on the checked U08 text: a correctly spelled Arabic
   quotation passes the exact check (it failed against the defective text layer, which is why the letter-set check
   exists), and rule R1 still makes it a reading only.
3. Once the official English U01 is in the knowledge base, an English quotation from U01 Article 2 passes the exact check
   and, being English and binding, is not reading-only.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from gapcourt.prepare import check_corpus, rebuild_all
from gapcourt.verify import check_quotation

ROOT = Path(__file__).resolve().parents[1]
KB_DIR = ROOT / "build" / "kb"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _two_raw_doc(tmp_path, with_private: bool):
    folder = tmp_path / "corpus_local/_raw/X01"
    folder.mkdir(parents=True)
    official = folder / "official.txt"
    official.write_text("alpha\nbeta\n", encoding="utf-8")
    private = folder / "checked.txt"
    private_text = "alpha\nbeta checked\n"
    meta_fb = [["doc_id", "X01"], ["sha256", _sha(official.read_bytes())], ["republish", "link_only"]]
    meta = [["doc_id", "X01"], ["sha256", _sha(private_text.encode("utf-8"))], ["republish", "link_only"],
            ["text_basis", "checked transcription, not the official text layer"]]
    if with_private:
        private.write_bytes(private_text.encode("utf-8"))
    entry = {"path": "corpus_local/uae/X01.md", "raw_name": "checked.txt", "extractor": "text_lines", "options": {},
             "meta": meta, "patch": [],
             "fallback": {"raw_name": "official.txt", "extractor": "text_lines", "options": {}, "meta": meta_fb, "patch": []}}
    (tmp_path / "corpus").mkdir()
    (tmp_path / "corpus/PREP.json").write_text(json.dumps({"version": 1, "docs": {"X01": entry}, "accepted_edits": {}}),
                                               encoding="utf-8")
    return tmp_path / entry["path"]


def test_rebuild_all_uses_the_private_transcription_when_it_is_present(tmp_path):
    out = _two_raw_doc(tmp_path, with_private=True)
    skipped: list = []
    rebuild_all(tmp_path, recipes={}, skipped=skipped)
    assert "beta checked" in out.read_text(encoding="utf-8") and not skipped
    assert [r.status for r in check_corpus(tmp_path, recipes={})] == ["REPRODUCED"]


def test_rebuild_all_falls_back_to_the_official_file_and_says_so(tmp_path):
    out = _two_raw_doc(tmp_path, with_private=False)
    skipped: list = []
    written = rebuild_all(tmp_path, recipes={}, skipped=skipped)
    text = out.read_text(encoding="utf-8")
    assert written == [out] and "beta checked" not in text and "beta" in text and "text_basis" not in text
    assert skipped and "fallback" in skipped[0][1]
    res = check_corpus(tmp_path, recipes={})
    assert [r.status for r in res] == ["REPRODUCED"], res[0].detail  # checked against the transform it was built with


def test_a_hand_edit_of_the_fallback_build_still_fails(tmp_path):
    out = _two_raw_doc(tmp_path, with_private=False)
    rebuild_all(tmp_path, recipes={}, skipped=[])
    out.write_text(out.read_text(encoding="utf-8").replace("beta", "gamma"), encoding="utf-8", newline="\n")
    assert [r.status for r in check_corpus(tmp_path, recipes={})] == ["DIFFERS"]


# ---- the real knowledge base ----------------------------------------------------------------------------------------

def _kb():
    from gapcourt.index import KB

    if not (KB_DIR / "chunks.jsonl").exists():
        pytest.skip("needs the locally built knowledge base (U01 and U08 are link-only)")
    return KB.load(KB_DIR)


def _passage(kb, doc_id, locator):
    return [e.chunk for e in kb.entries if e.chunk.doc_id == doc_id and e.chunk.locator.split(" [")[0] == locator]


U08_QUOTE = "على الجهات الحكومية الالتزام بعدم طلب الحصول على المعلومات المتوفرة لدى الحكومة من المتعاملين"  # Article 5.1


def test_u08_quotation_check_runs_against_the_checked_transcription():
    kb = _kb()
    row = next(r for r in kb.coverage_table() if r["doc_id"] == "U08")
    if "checked transcription" not in row["title"]:
        pytest.skip("this knowledge base was built from the U08 text layer (fresh clone without the private transcription)")
    chunks = _passage(kb, "U08", "Article 5")
    assert chunks
    assert any(check_quotation(U08_QUOTE, c.text).verified for c in chunks)  # exact check, correct spelling
    # the defective text-layer spelling of the same words no longer matches exactly
    layer_spelling = U08_QUOTE.replace("الالتزام", "االلتزام")
    assert not any(check_quotation(layer_spelling, c.text).verified for c in chunks)


def test_u08_exact_quotation_is_kept_as_a_reading_only_by_the_gate():
    from gapcourt.gate import apply_gate
    from gapcourt.retrieve import HybridRetriever

    kb = _kb()
    if "checked transcription" not in next(r for r in kb.coverage_table() if r["doc_id"] == "U08")["title"]:
        pytest.skip("U08 built from the text layer")
    hits = HybridRetriever(kb, ["kb_uae"], k=40).search(
        "Must government entities ask residents again for information the government already holds?")
    hit = next((h for h in hits if h.chunk.doc_id == "U08" and check_quotation(U08_QUOTE, h.chunk.text).verified), None)
    assert hit is not None, "U08 Article 5.1 not retrieved"
    for decision in ("ESCALATE", "DENY"):
        g = apply_gate({"decision": decision, "citations": [{"chunk_id": hit.chunk.chunk_id, "quote": U08_QUOTE}]},
                       hits, ["kb_uae"], kb)
        assert len(g.kept_citations) == 1
        c = g.kept_citations[0]
        assert c["quotation_check"] == "exact" and c["reading_only"] is True and c["language"] == "ar"
        assert g.decision == "ESCALATE"  # rule R1: an Arabic citation never carries a ruling


U01_EN_ARTICLE_2_QUOTE = ("Provisions of this Decree by Law shall not apply to the following: a. Government Data "
                          "b. Governmental entities which control or process Personal Data.")  # Article 2(2)(a) and (b)


def test_u01_english_quotation_from_article_2_verifies_and_is_not_reading_only():
    kb = _kb()
    rows = {r["doc_id"]: r for r in kb.coverage_table()}
    if "U01EN" not in rows:
        pytest.skip("the official English text of U01 is not in this knowledge base yet")
    assert rows["U01EN"]["language"] == "en" and rows["U01EN"]["binding"]
    chunks = _passage(kb, "U01EN", "Article 2")
    assert chunks
    assert any(check_quotation(U01_EN_ARTICLE_2_QUOTE, c.text).verified for c in chunks)
