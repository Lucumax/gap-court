"""Prompt findings from the first live smoke session (2026-11-02, Claude Haiku, USD 0.0024).

What the live run showed: asked a question that no reviewed instrument covers (PC11), the model returned GAP, not the
ABSTAIN the label expects, and marked all four retrieved passages 'applies: no', which the gate turned into four
"instrument does not apply" scope findings from passages that were merely off-topic (an aim clause, a definition, a
principle). These tests pin the prompt wording that answers both. They show what is sent to the model, not how the model
answers; the next live development session measures that.
All runs here use the scripted test double.
"""
import json
import shutil

import pytest

from gapcourt.chunking import ChunkParams
from gapcourt.embed import HashingEmbedder
from gapcourt.eval import labels_sha256, load_labels
from gapcourt.index import build_kb
from gapcourt.llm import ScriptedModel
from gapcourt.roles import CONTRACT


@pytest.fixture
def kb(fixtures_dir, tmp_path):
    root = tmp_path / "corpus"
    (root / "uae").mkdir(parents=True)
    shutil.copy(fixtures_dir / "S01.md", root / "uae" / "S01.md")
    return build_kb([root], HashingEmbedder(dim=128), ChunkParams(max_chars=300))


def _reply():
    return json.dumps({"decision": "GAP", "rule_applied": "none", "rationale": "x", "citations": [], "applicability": [],
                       "gap": None, "revision_reason": None})


def _case_system_prompt(kb, tmp_path):
    from gapcourt.cases import run_cases

    lab = tmp_path / "labels.jsonl"
    lab.write_text(json.dumps({"case_id": "C01", "split": "dev", "category": "missing_document", "status": "DRAFT_UNAPPROVED",
                               "question": "what is the household income threshold", "expected": [],
                               "expected_outcome": "ABSTAIN"}) + "\n", encoding="utf-8")
    m = ScriptedModel([_reply()])
    run_cases(kb, load_labels(lab), "dev", m, tmp_path / "run", real=False, labels_sha=labels_sha256(lab), code_version="test")
    return m.calls[0]["system"]


def test_contract_reserves_applies_no_for_a_scope_clause_that_excludes_the_actor():
    assert "applies" in CONTRACT
    assert "merely off-topic" in CONTRACT          # an off-topic passage is not a scope finding
    assert "leave it out" in CONTRACT              # ... and is left out of applicability, not marked no
    assert "scope, application or exclusion" in CONTRACT   # the quote for applies=no must be that kind of sentence


def test_contract_gap_rule_defers_to_the_role_instructions_on_abstain():
    assert "no passage governs the question, answer GAP" in CONTRACT
    assert "unless your role instructions tell you to ABSTAIN" in CONTRACT


def test_case_prompt_lists_the_reviewed_instruments_so_abstain_can_be_decided(kb, tmp_path):
    system = _case_system_prompt(kb, tmp_path)
    assert "Reviewed instruments" in system
    assert "S01: SYNTHETIC Test Law on Benefit Data" in system
    assert "ABSTAIN" in system and "none of the listed instruments" in system


def test_case_prompt_lists_only_the_case_collection(kb, tmp_path):
    from gapcourt.cases import reviewed_instruments_line

    line = reviewed_instruments_line(kb)
    assert line.startswith("Reviewed instruments") and "S01" in line
    assert "G1" not in line and "F1" not in line


def test_retrieval_query_for_a_case_is_the_bare_question_not_the_label_id(kb, tmp_path):
    # Session 5 finding 3: the logged query was "[case PC09] <question>", so the role retrieved with the label id in the query while
    # the scorer's retrieval row used the bare question. The case label stays in the message the model reads; retrieval uses the question.
    from gapcourt.cases import run_cases

    lab = tmp_path / "labels.jsonl"
    lab.write_text(json.dumps({"case_id": "C01", "split": "dev", "category": "missing_document", "status": "DRAFT_UNAPPROVED",
                               "question": "what is the household income threshold", "expected": [],
                               "expected_outcome": "ABSTAIN"}) + "\n", encoding="utf-8")
    m = ScriptedModel([_reply()])
    run_cases(kb, load_labels(lab), "dev", m, tmp_path / "run", real=False, labels_sha=labels_sha256(lab), code_version="test")
    recs = [json.loads(ln) for ln in (tmp_path / "run" / "log.jsonl").read_text(encoding="utf-8").splitlines()]
    steps = [r for r in recs if r.get("query")]
    assert steps and all(r["query"] == "what is the household income threshold" for r in steps)
    assert "[case C01] what is the household income threshold" in m.calls[0]["user"]
