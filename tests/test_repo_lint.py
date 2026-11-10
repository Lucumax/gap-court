from pathlib import Path

import importlib.util

_spec = importlib.util.spec_from_file_location("repo_lint", Path(__file__).resolve().parents[1] / "scripts" / "repo_lint.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
lint_text = _mod.lint_text


def rules(text, name="doc.md"):
    return sorted({f["rule"] for f in lint_text(text, name)})


def test_clean_text_passes():
    assert rules("Plain sentence about retrieval and logs.") == []


def test_em_and_en_dash_flagged_outside_verbatim():
    assert rules("a \u2014 b") == ["dash"] and rules("a \u2013 b") == ["dash"]


def test_years_of_experience_flagged():
    assert "years" in rules("a seasoned analyst with 15 years of experience")
    assert "years" in rules("over ten years in policy")


def test_country_recommendation_flagged():
    assert "country-recommendation" in rules("The UAE should adopt a notice rule.")
    assert "country-recommendation" in rules("the UAE must require a review")
    assert rules("The report states what the reviewed instruments establish.") == []


def test_location_and_travel_text_flagged():
    assert "location" in rules("participants travel to Dubai for the finals")
    assert "location" in rules("use a VPN")


def test_internal_paths_and_markers_flagged():
    assert "internal-path" in rules("see C:\\Walter\\100 day plan\\x")
    assert "marker" in rules("TODO-W decide")


def test_accuracy_and_overclaim_words_flagged():
    assert "overclaim" in rules("The system is compliant and certified.")
    assert "overclaim" in rules("This is the first of its kind.")
    assert rules("The check does not show compliance.") == []


def test_verbatim_dirs_are_skipped():
    assert lint_text("a \u2014 b", "corpus/uae/U02.md") == []


def test_dash_in_a_sealed_live_run_record_is_model_output_not_authored_text():
    # A live run's log and trace hold what the model emitted (hash-sealed, so they cannot be edited); the dash rule governs
    # authored prose only. Every other rule still applies to run files.
    assert rules("rule_applied: U13 Article 4 – register", "runs/dev/20261102T102235Z/log.jsonl") == []
    assert rules("rule_applied: U13 Article 4 — register", "runs/dev/x/trace.html") == []
    assert rules("a – b", "docs/note.md") == ["dash"]
    assert "years" in rules("with 15 years of experience", "runs/dev/x/log.jsonl")
