"""Bilingual retrieval path for the Arabic-only instruments (U01, U08).

Model-free: a letter-set key that survives the ligature-order defects of the Arabic text layers, and a documented,
author-made map from English legal concepts to Arabic search terms. Nothing here translates legal text.
"""
import json
import shutil
from pathlib import Path

import pytest

from gapcourt import bilingual
from gapcourt.bilingual import (arabic_key, expand_query, expansion_tokens, map_sha256, retrieval_tokens,
                                variant_keys)
from gapcourt.bilingual_map import CONCEPTS
from gapcourt.chunking import ChunkParams
from gapcourt.embed import HashingEmbedder
from gapcourt.index import KB, build_kb
from gapcourt.retrieve import HybridRetriever

REPO = Path(__file__).resolve().parent.parent


def test_arabic_key_is_the_same_for_ligature_order_defects():
    # correct spelling and the order the real text layers produce for the same word
    assert arabic_key("المعالجة") == arabic_key("املعالجة")
    assert arabic_key("تسري") == arabic_key("ترسي")
    assert arabic_key("التشريع") == arabic_key("الترشيع")
    assert arabic_key("المعالجة") != arabic_key("المعالج")


def test_arabic_key_ignores_marks_tatweel_and_folds_letter_variants():
    assert arabic_key("علـى") == arabic_key("على") == arabic_key("عىل")
    assert arabic_key("الأمنية") == arabic_key("الامنية") == arabic_key("الإمنية")
    assert arabic_key("مُعالَجة") == arabic_key("معالجة")
    assert arabic_key("البيانات").startswith("ar~")


def test_retrieval_tokens_key_arabic_and_keep_english_unchanged():
    toks = retrieval_tokens("Article 18 المعالجة املؤمتتة of the law")
    assert "article" in toks and "18" in toks and "law" in toks
    assert arabic_key("المعالجة") in toks
    assert not any(t == "المعالجة" for t in toks)
    # same number of tokens as the plain tokenizer on text without Arabic marks, so document lengths do not move
    from gapcourt.bm25 import tokenize
    assert retrieval_tokens("The Agency, Article 2(2)!", stop=True) == tokenize("The Agency, Article 2(2)!", stop=True)


def test_expand_query_fires_only_when_every_group_matches():
    e = expand_query("Can a data subject object to an automated decision about them?")
    assert "automated_decisions" in e.concepts
    assert expand_query("Which Dubai body manages the Unified Population Register?").concepts == []
    # 'government' alone is not enough for the scope concept: it also needs the personal data law
    assert "scope_government" not in expand_query("Which government body keeps the register?").concepts
    assert "scope_government" in expand_query("Does the federal personal data law apply to a government entity?").concepts


def test_expand_query_uses_light_stemming_both_ways():
    assert "automated_decisions" in expand_query("An automated decisions regime").concepts
    assert "breach_notification" in expand_query("Must a controller notify the office of data breaches?").concepts


def test_expansion_reports_the_arabic_terms_it_added():
    e = expand_query("When must a data protection officer be appointed?")
    assert "dpo" in e.concepts
    assert e.terms and all(any("؀" <= ch <= "ۿ" for ch in t) for t in e.terms)


def test_expansion_tokens_add_clitic_variants():
    toks = expansion_tokens("How does a data subject lodge a complaint with the Data Office?")
    assert arabic_key("شكوى") in toks
    assert arabic_key("بشكوى") in toks  # the word is written with a prefixed preposition in the text
    assert expansion_tokens("Which Dubai body manages the register?") == []


def test_map_shape_is_documented_per_concept():
    ids = [c["id"] for c in CONCEPTS]
    assert len(ids) == len(set(ids)) >= 15
    for c in CONCEPTS:
        assert c["groups"] and all(g for g in c["groups"])
        assert c["arabic"] and c["targets"]
        assert all(t.split()[0] in ("U01", "U08") for t in c["targets"])
    assert len(map_sha256()) == 64


def test_map_terms_occur_in_their_target_chunks_of_the_real_texts():
    chunks_path = REPO / "build" / "kb" / "chunks.jsonl"
    if not chunks_path.exists():
        pytest.skip("needs the locally built knowledge base (U01 and U08 are link-only and not in the public tree)")
    chunks = [json.loads(ln) for ln in chunks_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    keys = {}
    for c in chunks:
        if c["doc_id"] in ("U01", "U08"):
            keys.setdefault((c["doc_id"], c["locator"].split(" [")[0]), set()).update(retrieval_tokens(c["text"]))
    problems = []
    for c in CONCEPTS:
        for term in c["arabic"]:
            words = [w for w in term.split()]
            ok = False
            for target in c["targets"]:
                doc, loc = target.split(" ", 1)
                # a word counts when the text has it, or it with an attached prefix (and, with, to, the)
                if all(variant_keys(w) & keys.get((doc, loc), set()) for w in words):
                    ok = True
            if not ok:
                problems.append(f"{c['id']}: {term!r} not found in {c['targets']}")
    assert not problems, "\n".join(problems)


@pytest.fixture
def mixed_kb(fixtures_dir, tmp_path):
    root = tmp_path / "corpus"
    (root / "uae").mkdir(parents=True)
    shutil.copy(fixtures_dir / "S01.md", root / "uae" / "S01.md")
    shutil.copy(fixtures_dir / "S03_ar.md", root / "uae" / "S03_ar.md")
    return build_kb([root], HashingEmbedder(dim=256), ChunkParams(max_chars=300))


def test_english_question_reaches_the_arabic_only_passage_only_with_the_bilingual_path(mixed_kb):
    q = "Can a person object to an automated decision made about them, including profiling?"
    off = HybridRetriever(mixed_kb, ["kb_uae"], k=3, bilingual=False).search(q)
    on = HybridRetriever(mixed_kb, ["kb_uae"], k=3, bilingual=True).search(q)
    assert ("S03", "Article 1") not in [(h.chunk.doc_id, h.chunk.locator) for h in off]
    assert ("S03", "Article 1") in [(h.chunk.doc_id, h.chunk.locator) for h in on]


def test_breach_question_reaches_article_3_of_the_arabic_fixture(mixed_kb):
    on = HybridRetriever(mixed_kb, ["kb_uae"], k=3, bilingual=True).search(
        "Must a controller notify the office of a personal data breach?")
    assert ("S03", "Article 3") in [(h.chunk.doc_id, h.chunk.locator) for h in on]


def test_arabic_question_matches_text_with_defective_ligature_order(mixed_kb):
    hits = HybridRetriever(mixed_kb, ["kb_uae"], k=3, bilingual=True, mode="bm25").search(
        "هل يحق لصاحب البيانات الاعتراض على المعالجة المؤتمتة؟")
    assert (hits[0].chunk.doc_id, hits[0].chunk.locator) == ("S03", "Article 1")


def test_no_trigger_means_identical_ranking_and_scores(mixed_kb):
    q = "must the recipient use shared records for a different purpose"
    off = HybridRetriever(mixed_kb, ["kb_uae"], k=5, bilingual=False).search(q)
    on = HybridRetriever(mixed_kb, ["kb_uae"], k=5, bilingual=True).search(q)
    assert [(h.chunk.chunk_id, h.score, h.bm25_norm) for h in off] == [(h.chunk.chunk_id, h.score, h.bm25_norm) for h in on]


def test_bm25_norm_stays_within_zero_and_one_when_terms_are_added(mixed_kb):
    hits = HybridRetriever(mixed_kb, ["kb_uae"], k=5, bilingual=True).search(
        "Can a person object to an automated decision made about them, including profiling?")
    assert hits and all(0.0 <= h.bm25_norm <= 1.0 for h in hits)


def test_config_records_the_map_hash_and_the_switch(mixed_kb):
    on = HybridRetriever(mixed_kb, ["kb_uae"], bilingual=True).config()
    off = HybridRetriever(mixed_kb, ["kb_uae"], bilingual=False).config()
    assert on["bilingual"] == {"map_sha256": map_sha256(), "arabic_key": bilingual.KEY_NAME, "arabic_slots": 2}
    assert off["bilingual"] is None
    assert on != off


def test_bilingual_is_on_by_default_for_roles_and_cases(mixed_kb):
    assert HybridRetriever(mixed_kb, ["kb_uae"]).config()["bilingual"] is not None


def test_cli_expand_query_prints_concepts_and_terms(capsys):
    from gapcourt.cli import main
    assert main(["expand-query", "--query", "When must a data protection officer be appointed?"]) == 0
    out = capsys.readouterr().out
    assert "dpo" in out and "map sha256" in out
    assert main(["expand-query", "--query", "Which Dubai body manages the register?"]) == 0
    assert "no expansion" in capsys.readouterr().out


def test_arabic_only_passages_take_at_most_two_slots_and_only_when_the_question_matches_the_map(mixed_kb):
    # The vector arm cannot rank an Arabic passage against an English question, so fusing the two languages in one list
    # either buries the Arabic passage or (if it borrowed a rank) floods the list. A fixed quota is the explicit rule.
    assert bilingual.ARABIC_SLOTS == 2
    q = ("Can a person object to an automated decision made about them, including profiling, "
         "and must a controller notify the office of a personal data breach?")
    r = HybridRetriever(mixed_kb, ["kb_uae"], k=5, bilingual=True)
    hits = r.search(q)
    ar = [h for h in hits if h.chunk.doc_id == "S03"]
    assert 1 <= len(ar) <= 2 and all(h.arabic_slot for h in ar)
    assert not any(h.arabic_slot for h in hits if h.chunk.doc_id == "S01")
    plain = r.search("must the recipient use shared records for a different purpose")
    assert not any(h.arabic_slot for h in plain)
    off = HybridRetriever(mixed_kb, ["kb_uae"], k=5, bilingual=False).search(q)
    assert not any(h.arabic_slot for h in off)


def test_arabic_slots_are_ranked_by_the_added_terms_alone(mixed_kb):
    on = HybridRetriever(mixed_kb, ["kb_uae"], k=5, bilingual=True).search(
        "Must a controller notify the office of a personal data breach?")
    ar = [h for h in on if h.arabic_slot]
    assert (ar[0].chunk.doc_id, ar[0].chunk.locator) == ("S03", "Article 3")


def test_english_passages_keep_their_order_when_arabic_slots_are_added(mixed_kb):
    q = "Can a person object to an automated decision made about them, including profiling?"
    off = [h.chunk.chunk_id for h in HybridRetriever(mixed_kb, ["kb_uae"], k=8, bilingual=False).search(q)
           if h.chunk.doc_id != "S03"]
    on = [h.chunk.chunk_id for h in HybridRetriever(mixed_kb, ["kb_uae"], k=8, bilingual=True).search(q)
          if not (h.chunk.doc_id == "S03")]
    assert on == off[:len(on)]


def test_interleaving_puts_the_best_english_passage_first(mixed_kb):
    hits = HybridRetriever(mixed_kb, ["kb_uae"], k=5, bilingual=True).search(
        "Can a person object to an automated decision made about them, including profiling?")
    assert not hits[0].arabic_slot  # ties go to the English list, so a baseline reading hits[0] sees the usual top passage


def test_real_knowledge_base_hybrid_probe_hits():
    kb_dir = REPO / "build" / "kb"
    probes_path = REPO / "evals" / "bilingual_probe_DRAFT.jsonl"
    if not (kb_dir / "chunks.jsonl").exists() or not probes_path.exists():
        pytest.skip("needs the locally built knowledge base with U01 and U08")
    kb = KB.load(kb_dir)
    probes = [json.loads(ln) for ln in probes_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    r = HybridRetriever(kb, ["kb_uae"], k=5, bilingual=True)
    hit = sum(1 for p in probes
              if any((e["doc_id"], e["locator"]) in {(h.chunk.doc_id, h.chunk.locator) for h in r.search(p["question"])}
                     for e in p["expected"]))
    assert hit >= 12, f"{hit} of {len(probes)} probes found their article in the top five"
