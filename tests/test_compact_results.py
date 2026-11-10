"""scripts/compact_results.py: the scored-results block shrinks to stamp line, paired table, one sentence, with no number changed."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("compact_results", ROOT / "scripts" / "compact_results.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["compact_results"] = mod
spec.loader.exec_module(mod)

SHA = "ab12cd34ef56" + "0" * 52
RESULTS = f"""# Gap Court results

- split: heldout; labels: labels_v1.jsonl sha256 `{SHA}` (approved)
- freeze record: `{"9f8e7d6c5b4a" + "1" * 52}`
- KB version: `kb-b08e7cfac2df`; models: claude-haiku-5-5; effort: medium
- code: scoring eae3b03; runs eae3b03
- runs: heldout/r1@aaaa, scenario/S-01-r1@bbbb

## 1. Governing passage in top 5

| case type | BM25 | vector | fused |
|---|---|---|---|
| all | 3 of 7 | 3 of 7 | 3 of 7 |

## 2. Outcome agreement with labels

Run: heldout/r1@aaaa. Baseline: retrieval plus abstain threshold (no roles, no gate).

| case type | full system | baseline |
|---|---|---|
| all | 4 of 7 | 2 of 7 |

Not run, so not in the counts above: PC20.

## 2b. Paired comparison on identical cases

same cases, same knowledge base, same retrieval settings (top 5, kb_uae); the baseline calls no model, so it has no model failures and never escalates.

| count of cases | full system | retrieval plus abstain |
|---|---|---|
| cases | 7 | 7 |
| outcome agreement (same label as the approved one) | 4 | 2 |
| decisive recommendations | 3 | 5 |
| unsupported decisive recommendations | 0 | 2 |
| escalations | 2 | 0 |
| abstentions or gap findings | 2 | 2 |
| model failures | 0 | 0 |

Unsupported decisive: answered where the approved label expects another outcome. Counts show pilot agreement on a few cases; they do not establish legal correctness.

## 3. False gap findings and false answers

| | full system | baseline |
|---|---|---|
| gap where the label has an answer | 1 of 4 | 0 of 4 |
| answer where the label has a gap or abstention | 0 of 3 | 2 of 3 |

## 4. Scenario steps matching the expected ruling

| run | scenario | matched | expected rulings frozen | earlier run before freeze |
|---|---|---|---|---|
| scenario/S-01-r1@bbbb | S-01 | 2 of 3 | yes | no |
| scenario/S-01-r2@cccc | S-01 | 3 of 3 | yes | no |
| scenario/S-02-r1@dddd | S-02 | 1 of 2 | yes | no |
| demo/S-04@eeee | S-04 (development demonstration, reported separately) | 2 of 2 | no | no |

## 5. Revisions after contradicting steps

| run | revised | stale rulings left standing |
|---|---|---|
| scenario/S-01-r1@bbbb (S-01) | 1 of 1 | 0 of 1 |
| scenario/S-01-r2@cccc (S-01) | 1 of 2 | 1 of 2 |

## 6. Quotations

Kept 41, dropped 3 (by reason: {{"not_found": 3}}). kept means the quotation was found in the cited passage.

## 7. Model rejections, tokens and cost

Rejections by reason: {{}}; calls 40; input tokens 120000; output tokens 30000; USD 0.0431.

## 8. KB release

no KB-release receipt saved under the runs folder
"""

STAMP = "[measured: gapcourt score at 2026-11-09T16:01:02Z; runs heldout/r1@aaaa, scenario/S-01-r1@bbbb]"
DRAFT = f"""## 5. Error scenarios

Scored rows (frozen labels):

{STAMP}

{RESULTS.strip()}

Retrieval check, offline: 12 probes.
"""


def test_block_keeps_paired_table_and_sentences_verbatim():
    block = mod.compact_block(RESULTS, STAMP)
    for ln in RESULTS.splitlines():
        if ln.startswith("| ") and ("retrieval plus abstain" in ln or ln.startswith(("| cases", "| outcome agreement", "| decisive", "| unsupported", "| escalations", "| abstentions", "| model failures"))):
            assert ln in block
    assert "|---|---|---|" in block
    assert "Unsupported decisive: answered where the approved label expects another outcome." in block
    assert "never escalates." in block and "do not establish legal correctness." in block  # both 2b prose paragraphs, whole


def test_block_has_no_heading_and_is_short():
    block = mod.compact_block(RESULTS, STAMP)
    assert not any(ln.startswith("#") for ln in block.splitlines())
    assert len(block.split()) < len(RESULTS.split()) * 0.6
    assert block.startswith(STAMP.replace("; runs heldout/r1@aaaa, scenario/S-01-r1@bbbb", ""))
    assert "sha256 prefix ab12cd34ef56" in block and "labels_v1.jsonl" in block and "code scoring eae3b03; runs eae3b03" in block and "freeze record prefix 9f8e7d6c5b4a" in block and "kb-b08e7cfac2df" in block


def test_summary_sentence_groups_runs_and_copies_cells():
    block = mod.compact_block(RESULTS, STAMP)
    # one entry per run, section 5 joined to section 4 by run id (never by position), shortened ids without the @hash
    assert "scenario/S-01-r1 2 of 3, revised 1 of 1, stale 0 of 1" in block
    assert "scenario/S-01-r2 3 of 3, revised 1 of 2, stale 1 of 2" in block
    assert "scenario/S-02-r1 1 of 2" in block and "demo/S-04 2 of 2" in block
    assert "development demonstration" in block and "not frozen" in block  # the demonstration is flagged unfrozen too
    assert "S-02-r1 1 of 2 (expected rulings not frozen" in mod.compact_block(RESULTS.replace("| S-02 | 1 of 2 | yes |", "| S-02 | 1 of 2 | no |"), STAMP)
    r1 = "| scenario/S-01-r1@bbbb (S-01) | 1 of 1 | 0 of 1 |"
    r2 = "| scenario/S-01-r2@cccc (S-01) | 1 of 2 | 1 of 2 |"
    swapped = RESULTS.replace(r1 + chr(10) + r2, r2 + chr(10) + r1)
    assert "scenario/S-01-r1 2 of 3, revised 1 of 1, stale 0 of 1" in mod.compact_block(swapped, STAMP)
    odd = RESULTS.replace("scenario/S-01-r1@bbbb (S-01) | 1 of 1", "trial(old)/S-01-r1@bbbb (S-01) | 1 of 1").replace("| scenario/S-01-r1@bbbb | S-01 |", "| trial(old)/S-01-r1@bbbb | S-01 |")
    assert "trial(old)/S-01-r1 2 of 3, revised 1 of 1" in mod.compact_block(odd, STAMP)


def test_no_number_is_new_or_rounded():
    block = mod.compact_block(RESULTS, STAMP)
    assert mod.numbers_not_in(RESULTS + STAMP, block) == []
    assert mod.numbers_not_in("1 of 3", "0.33 of 3") == ["0.33"]  # the checker itself catches rounding


def test_warnings_are_kept():
    warned = RESULTS.replace("# Gap Court results\n", "# Gap Court results\n\n**TEST DOUBLE: not a result**\n", 1)
    assert "TEST DOUBLE: not a result. Not run" in mod.compact_block(warned, STAMP)
    assert "Not run, so not in the counts above: PC20." in mod.compact_block(RESULTS, STAMP)
    differs = RESULTS.replace("- runs: ", "- **Code differs from the frozen code for: x (y). These runs are counted.**\n- runs: ", 1)
    assert "Code differs from the frozen code for: x (y)" in mod.compact_block(differs, STAMP)


def test_draft_block_replaced_surroundings_untouched():
    out = mod.compact_draft(DRAFT, RESULTS)
    assert out.startswith("## 5. Error scenarios\n\nScored rows (frozen labels):\n\n[measured: gapcourt score at 2026-11-09T16:01:02Z] split heldout")
    assert out.endswith("Retrieval check, offline: 12 probes.\n")
    assert "## 1. Governing passage" not in out and "# Gap Court results" not in out
    assert out.count("## ") == 1  # only the report's own heading is left for the Word build to number


def test_refuses_when_block_is_not_there():
    pending = DRAFT.replace(STAMP + "\n\n" + RESULTS.strip(), "[pending-live: needs the heldout and repeats sessions, then `gapcourt score`]")
    with pytest.raises(mod.CompactError):
        mod.compact_draft(pending, RESULTS)
    with pytest.raises(mod.CompactError):
        mod.compact_block("# Gap Court results\n\n## 1. x\n", STAMP)


def test_cli_writes_copy_and_never_the_draft(tmp_path, capsys):
    d, r, o = tmp_path / "draft.md", tmp_path / "results.md", tmp_path / "out.md"
    d.write_text(DRAFT, encoding="utf-8")
    r.write_text(RESULTS, encoding="utf-8")
    assert mod.main(["--draft", str(d), "--results", str(r), "--out", str(o)]) == 0
    assert d.read_text(encoding="utf-8") == DRAFT
    assert "| cases | 7 | 7 |" in o.read_text(encoding="utf-8")
    with pytest.raises(SystemExit):
        mod.main(["--draft", str(d), "--results", str(r), "--out", str(d)])
    assert mod.main(["--results", str(r)]) == 0
    assert "| cases | 7 | 7 |" in capsys.readouterr().out


EVIDENCE = """Evidence index (saved runs; the hash chain of each log is verified with `gapcourt verify-log`).

- dev/20261105T173759Z: cases PC01,PC03; KB kb-b08e7cfac2df; code 82c4576; 11 calls, USD 0.0080 [measured]
- S-01/r1: scenario S-01; KB kb-b08e7cfac2df; code eae3b03; 6 calls, USD 0.0078 [measured]
- S-01/r2: scenario S-01; KB kb-b08e7cfac2df; code eae3b03; 6 calls, USD 0.0079 [measured]
- S-02/r1: scenario S-02; KB kb-b08e7cfac2df; code eae3b03; 4 calls, USD 0.0052 [measured]
"""


def test_tighten_evidence_merges_repeats_and_states_kb_once():
    out = mod.collapse_evidence(EVIDENCE)
    assert out.count("kb-b08e7cfac2df") == 1 and "Every measured run below used KB kb-b08e7cfac2df." in out
    assert "- S-01/r1, r2: scenario S-01; code eae3b03; per run r1 6 calls, USD 0.0078; r2 6 calls, USD 0.0079 [measured]" in out
    assert "- S-02/r1: scenario S-02; code eae3b03; 4 calls, USD 0.0052 [measured]" in out
    assert "- dev/20261105T173759Z: cases PC01, PC03; code 82c4576; 11 calls, USD 0.0080 [measured]" in out  # list spaced so it can wrap
    assert mod.numbers_not_in(EVIDENCE, out) == []
    mixed = EVIDENCE.replace("S-02/r1: scenario S-02; KB kb-b08e7cfac2df", "S-02/r1: scenario S-02; KB kb-111111111111")
    m = mod.collapse_evidence(mixed)  # two different KBs: each full version once, under a letter, and each line keeps its letter
    assert m.count("kb-b08e7cfac2df") == 1 and m.count("kb-111111111111") == 1
    assert "Knowledge bases: KB A is kb-b08e7cfac2df; KB B is kb-111111111111." in m
    assert "- S-02/r1: scenario S-02; KB B; code eae3b03; 4 calls, USD 0.0052 [measured]" in m
    assert "- S-01/r1, r2: scenario S-01; KB A; code eae3b03; per run r1 6 calls, USD 0.0078; r2 6 calls, USD 0.0079 [measured]" in m
    assert mod.numbers_not_in(mixed, m) == []


def test_runs_not_counted_are_carried_not_dropped():
    # score.py ends results.md with "## Runs not counted" when a run was left out (second held-out run, test double, failed verification)
    left = RESULTS + "\n## Runs not counted\n\n- heldout/r2@ffff: later held-out run; the first result is kept\n- heldout/r3@9999: test double\n"
    block = mod.compact_block(left, STAMP)
    assert "heldout/r2@ffff: later held-out run; the first result is kept" in block and "heldout/r3@9999: test double" in block
    assert "Runs not counted" not in mod.compact_block(RESULTS, STAMP)
    assert mod.numbers_not_in(left + STAMP, block) == []


def test_scenario_run_before_freeze_is_flagged():
    # section 4's last column says whether an earlier run of the scenario was made before the freeze: an exposure note, never dropped
    exposed = RESULTS.replace("| S-02 | 1 of 2 | yes | no |", "| S-02 | 1 of 2 | yes | yes |")
    assert "S-02-r1 1 of 2 (earlier run before the freeze)" in mod.compact_block(exposed, STAMP)
    assert "earlier run before the freeze" not in mod.compact_block(RESULTS, STAMP)


def test_escaped_pipes_short_rows_and_second_tables(tmp_path):
    esc = RESULTS.replace("| scenario/S-02-r1@dddd | S-02 | 1 of 2 | yes | no |", "| scenario/S-02-r1@dddd | S-02 \\| variant | 1 of 2 | yes | no |")
    block = mod.compact_block(esc, STAMP)
    assert "1 of 2" in block and "not frozen for" not in block.split("development demonstration")[0]  # count stays a count, no false unfrozen warning
    short = RESULTS.replace("| scenario/S-02-r1@dddd | S-02 | 1 of 2 | yes | no |", "| scenario/S-02-r1@dddd | S-02 | 1 of 2 |")
    with pytest.raises(mod.CompactError):
        mod.compact_block(short, STAMP)
    second = RESULTS.replace("## 5. Revisions", "| run | scenario | matched |\n|---|---|---|\n| diagnostic/r9 | diagnostic | 99 of 100 |\n\n## 5. Revisions")
    assert "99 of 100" not in mod.compact_block(second, STAMP)


def test_more_disclosures_kept_and_stamp_reference_follows_the_results_path():
    more = RESULTS + "\nJudge-added step runs (protocol examples, not counted above): judge/r1\n"
    block = mod.compact_block(more, STAMP)
    assert "Judge-added step runs (protocol examples, not counted above): judge/r1" in block
    assert "o KB-release receipt saved under the runs folder" in block  # fixture section 8 says none was saved
    assert "full hashes: results/final/results.md" in mod.compact_block(RESULTS, STAMP, ref="results/final/results.md")
    assert "full hashes: results/heldout/results.md" in mod.compact_block(RESULTS, STAMP)


def test_exact_2b_heading_and_header_row():
    diag = RESULTS.replace("## 2b. Paired comparison", "## 2b.1 Diagnostics\n\n| measure | full system | baseline |\n|---|---|---|\n| calls | 99 | 0 |\n\n## 2b. Paired comparison", 1)
    block = mod.compact_block(diag, STAMP)
    assert "| calls | 99 | 0 |" not in block and "| cases | 7 | 7 |" in block
    with pytest.raises(mod.CompactError):
        mod.compact_block(RESULTS.replace("| count of cases | full system | retrieval plus abstain |", "| count of cases | system | baseline |"), STAMP)


def test_out_must_not_be_an_input(tmp_path):
    d, r = tmp_path / "draft.md", tmp_path / "results.md"
    d.write_text(DRAFT, encoding="utf-8")
    r.write_text(RESULTS, encoding="utf-8")
    with pytest.raises(SystemExit):
        mod.main(["--draft", str(d), "--results", str(r), "--out", str(r)])
    assert r.read_text(encoding="utf-8") == RESULTS


def test_kb_stated_once_only_when_every_measured_run_names_it():
    ev = EVIDENCE + "- S-03/r1: scenario S-03; KB None; code eae3b03; 1 calls, USD 0.0005 [measured]\n- demo/r1: TEST DOUBLE run, no model was called; ignored\n"
    out = mod.collapse_evidence(ev)
    assert "Every measured run below used KB" not in out and out.count("KB kb-b08e7cfac2df") == 3  # dev, merged S-01 r1/r2, S-02
    # only the evidence index is rewritten, not earlier bullets with the same shape
    ctx = "Pre-freeze diagnostic runs:\n- S-01/r1: scenario S-01; KB kb-b08e7cfac2df; code eae3b03; 6 calls, USD 0.0010 [measured]\n\n" + EVIDENCE
    o2 = mod.collapse_evidence(ctx)
    assert "- S-01/r1: scenario S-01; KB kb-b08e7cfac2df; code eae3b03; 6 calls, USD 0.0010 [measured]" in o2
    assert "- S-01/r1, r2:" in o2


def test_number_check_compares_whole_tokens():
    assert mod.numbers_not_in("calls 31; input tokens 186000; USD 0.0418", "model failures 6") == ["6"]  # 6 is inside 186000 but is not a token there
    assert mod.numbers_not_in("labels sha256 `" + "bd2b2a045ab0174b" + "0" * 48 + "`", "sha256 prefix bd2b2a045ab0") == []  # hash digits are not numbers
    assert mod.numbers_not_in("4 of 7", "4 of 7") == []


def test_run_ids_with_at_signs_stay_distinct_and_orphan_revisions_keep_their_label():
    s4 = ("| trial@a/r1@aaaaaaaaaaaa | S-01 | 2 of 3 | yes | no |\n| trial@b/r1@bbbbbbbbbbbb | S-01 | 1 of 3 | yes | no |\n")
    s5 = ("| trial@a/r1@aaaaaaaaaaaa (S-01) | 1 of 2 | 0 of 2 |\n| trial@b/r1@bbbbbbbbbbbb (S-01) | 0 of 2 | 2 of 2 |\n"
          "| demo/S-04/r1@eeee (S-04 (development demonstration, reported separately)) | 0 of 1 | 1 of 1 |\n")
    md = RESULTS.split("## 4. Scenario")[0] + "## 4. Scenario steps matching the expected ruling\n\n" + mod_header4() + s4 + "\n## 5. Revisions after contradicting steps\n\n" + mod_header5() + s5 + "\n## 6. Quotations\n\nx\n"
    block = mod.compact_block(md, STAMP)
    assert "trial@a/r1 2 of 3, revised 1 of 2, stale 0 of 2" in block and "trial@b/r1 1 of 3, revised 0 of 2, stale 2 of 2" in block
    assert "demo/S-04/r1 revised 0 of 1, stale 1 of 1 (S-04 (development demonstration" not in block  # label kept, parenthesis tidy
    assert "development demonstration" in block.split("demo/S-04/r1")[1]
    dup = md.replace("trial@b/r1@bbbbbbbbbbbb (S-01)", "trial@a/r1@aaaaaaaaaaaa (S-01)")
    with pytest.raises(mod.CompactError):
        mod.compact_block(dup, STAMP)


def mod_header4():
    return "| run | scenario | matched | expected rulings frozen | earlier run before freeze |\n|---|---|---|---|---|\n"


def mod_header5():
    return "| run | revised | stale rulings left standing |\n|---|---|---|\n"


def test_empty_scenario_tables_are_fine_and_a_numeric_reference_path_is_not_an_error(tmp_path):
    md = RESULTS.split("## 4. Scenario")[0] + "## 4. Scenario steps matching the expected ruling\n\n" + mod_header4() + "\n## 5. Revisions after contradicting steps\n\n" + mod_header5() + "\n## 6. Quotations\n\nx\n"
    assert "No scenario step or revision was scored." in mod.compact_block(md, STAMP)
    d = tmp_path / "draft.md"
    r = tmp_path / "res.md"
    d.write_text(DRAFT, encoding="utf-8")
    r.write_text(RESULTS, encoding="utf-8")
    assert mod.main(["--draft", str(d), "--results", str(r), "--out", str(tmp_path / "o.md"), "--ref", "results/review-424242/results.md"]) == 0


def test_file_errors_exit_2_not_a_traceback(tmp_path, capsys):
    d, r = tmp_path / "draft.md", tmp_path / "results.md"
    d.write_text(DRAFT, encoding="utf-8")
    r.write_text(RESULTS, encoding="utf-8")
    assert mod.main(["--draft", str(d), "--results", str(r), "--out", str(tmp_path / "nodir" / "o.md")]) == 2
    assert mod.main(["--draft", str(d), "--results", str(tmp_path / "missing.md"), "--out", str(tmp_path / "o.md")]) == 2
    assert capsys.readouterr().err.count("error:") == 2


GAP_ROW = "| gap where the label has an answer | 1 of 4 | 0 of 4 |"
ANS_ROW = "| answer where the label has a gap or abstention | 0 of 3 | 2 of 3 |"


def test_false_gap_and_false_answer_rows_are_in_the_block_exactly():
    # the least flattering numbers (section 3 of results.md) are in the report block with their exact numbers and denominators, not only behind a pointer
    block = mod.compact_block(RESULTS, STAMP)
    line = ("False gap findings and false answers, full system vs baseline (results.md section 3): "
            "gap where the label has an answer 1 of 4 vs 0 of 4; answer where the label has a gap or abstention 0 of 3 vs 2 of 3.")
    assert line in block.splitlines()
    assert block.index("Unsupported decisive:") < block.index(line) < block.index("Scenario steps matching")  # after the paired table, before the steps
    assert mod.numbers_not_in(RESULTS + STAMP, block) == []
    swapped = RESULTS.replace("1 of 4 | 0 of 4", "3 of 4 | 4 of 4").replace("0 of 3 | 2 of 3", "2 of 3 | 3 of 3")
    b2 = mod.compact_block(swapped, STAMP)
    assert "gap where the label has an answer 3 of 4 vs 4 of 4; answer where the label has a gap or abstention 2 of 3 vs 3 of 3." in b2


def test_missing_false_finding_row_or_section_exits_non_zero(tmp_path, capsys):
    for bad in (RESULTS.replace(ANS_ROW + chr(10), ""), RESULTS.replace(GAP_ROW + chr(10), ""),
                RESULTS.replace("## 3. False gap findings and false answers", "## 3. Something else")):
        with pytest.raises(mod.CompactError):
            mod.compact_block(bad, STAMP)
        d, r = tmp_path / "draft.md", tmp_path / "results.md"
        d.write_text(DRAFT.replace(RESULTS.strip(), bad.strip()), encoding="utf-8")
        r.write_text(bad, encoding="utf-8")
        assert mod.main(["--draft", str(d), "--results", str(r), "--out", str(tmp_path / "o.md")]) == 2
        assert mod.main(["--results", str(r)]) == 2
        assert not (tmp_path / "o.md").exists()
    assert capsys.readouterr().err.count("section 3") == 6  # two CLI calls for each of the three bad inputs
    with pytest.raises(mod.CompactError):  # changed header: the layout moved, compact by hand
        mod.compact_block(RESULTS.replace("| | full system | baseline |", "| | system | baseline |"), STAMP)


def test_false_finding_row_with_a_changed_number_trips_the_strict_check(tmp_path):
    # the same strict check as the rest of the block: a number not in results.md exits 3 and writes nothing
    assert mod.numbers_not_in(RESULTS + STAMP, mod.compact_block(RESULTS, STAMP).replace("2 of 3", "0.67 of 3")) == ["0.67"]


# ---- headline token (added 2026-11-10) -------------------------------------------------------------------------

def test_headline_token_is_filled_from_the_all_row_of_section_2_verbatim():
    draft = "# Title\n\nOpening.\n\n{{headline: from results.md section 2}}\n\n" + DRAFT
    out = mod.compact_draft(draft, RESULTS)
    assert "{{headline" not in out
    assert "agreed with the approved label in 4 of 7 cases, the retrieval-plus-abstain baseline in 2 of 7." in out
    assert "not legal correctness" in out and "one reviewer" in out


def test_draft_without_headline_token_is_unchanged_by_the_headline_step():
    assert mod.fill_headline(DRAFT, RESULTS) == DRAFT


def test_headline_token_without_an_all_row_stops_the_tool(tmp_path):
    broken = RESULTS.replace("| all | 4 of 7 | 2 of 7 |", "| answerable | 4 of 7 | 2 of 7 |")
    with pytest.raises(mod.CompactError):
        mod.fill_headline("{{headline: x}}", broken)
    draft = "{{headline: x}}\n\n" + DRAFT.replace(RESULTS.strip(), broken.strip())
    (tmp_path / "r.md").write_text(broken, encoding="utf-8")
    (tmp_path / "d.md").write_text(draft, encoding="utf-8")
    assert mod.main(["--results", str(tmp_path / "r.md"), "--draft", str(tmp_path / "d.md"), "--out", str(tmp_path / "o.md")]) == 2
    assert not (tmp_path / "o.md").exists()
