import json

import pytest

from gapcourt.audit import AuditLog, AuditError, verify_records
from gapcourt.llm import (
    ScriptedModel,
    ScriptExhausted,
    assert_real_backend,
    parse_json_response,
)
from gapcourt.verify import check_quotation, normalize


# ---- quotation verification -------------------------------------------------

CHUNK = ('2. This Law does not apply to: a. government agencies that process personal data; '
         'or b. data held for security purposes.')


def test_exact_quote_verified():
    r = check_quotation("government agencies that process personal data", CHUNK)
    assert r.verified and r.reason == "ok"


def test_whitespace_and_curly_quotes_normalized():
    chunk = "The agency shall not\n   use “records” for another purpose."
    assert check_quotation('use "records" for another purpose', chunk).verified


def test_altered_word_fails():
    r = check_quotation("government agencies that sell personal data", CHUNK)
    assert not r.verified and r.reason == "not_found"


def test_too_short_quote_rejected():
    r = check_quotation("This Law", CHUNK)
    assert not r.verified and r.reason == "too_short"


def test_ellipsis_fragments_must_appear_in_order():
    ok = check_quotation("does not apply to [...] data held for security purposes", CHUNK)
    assert ok.verified
    bad = check_quotation("data held for security purposes [...] does not apply to", CHUNK)
    assert not bad.verified and bad.reason == "fragments_out_of_order"


def test_verified_means_only_presence_in_passage():
    # a quote can be present and still not support the conclusion: the check says nothing about that
    r = check_quotation("government agencies that process personal data", CHUNK)
    assert r.verified and "supports" not in r.reason


def test_quote_cannot_start_or_end_inside_a_word():
    # a fragment that begins or ends mid-word is not the passage's wording
    assert not check_quotation("ata that process personal data; or b", CHUNK).verified
    assert not check_quotation("government agencies that process personal dat", CHUNK).verified
    assert not check_quotation("overnment agencies that process personal data", CHUNK).verified


def test_word_boundary_still_allows_punctuation_edges():
    assert check_quotation("process personal data; or b. data held", CHUNK).verified
    assert check_quotation("2. This Law does not apply to", CHUNK).verified


def test_ellipsis_fragments_cannot_manufacture_quoted_words():
    # one-word fragments matched inside longer words; four fragments of one word each passed
    passage = "The mayor refused the database and acted unfreely."
    assert not check_quotation("may ... use ... data ... freely", passage).verified
    assert not check_quotation("may [...] use [...] data [...] freely", passage).verified


def test_each_ellipsis_fragment_needs_two_words():
    p = "Entities may use data only for the stated purpose and never freely."
    assert not check_quotation("may use data [...] freely", p).verified  # fragment 'freely' is one word
    assert check_quotation("may use data [...] never freely", p).verified


def test_normalize_is_idempotent():
    s = " A\u2013B  “x” "
    assert normalize(normalize(s)) == normalize(s)


# ---- audit log --------------------------------------------------------------

def make_log(tmp_path=None, n=4):
    ticks = iter(f"2026-10-27T00:00:{i:02d}Z" for i in range(100))
    log = AuditLog(path=tmp_path, clock=lambda: next(ticks), code_version="test-1")
    for i in range(n):
        log.append_step(
            role="adviser", input={"q": i}, query=f"query {i}", scope={"collections": ["kb_uae"]},
            kb_version="kb-test", passages=[{"chunk_id": f"c{i}", "text_sha256": "ab" * 32}],
            rule_applied="R1", rationale=f"because {i}", decision="ESCALATE",
            model_id="scripted", prompt_sha256="cd" * 32, config_sha256="ef" * 32)
    return log


def test_chain_verifies_against_terminal_digest():
    log = make_log()
    digest = log.terminal_digest()
    assert digest["count"] == 4
    res = verify_records(log.records, expected=digest)
    assert res.ok, res.errors


def test_altered_field_detected():
    log = make_log()
    digest = log.terminal_digest()
    recs = json.loads(json.dumps(log.records))
    recs[1]["decision"] = "ALLOW"
    assert not verify_records(recs, expected=digest).ok


def test_deleted_middle_record_detected():
    log = make_log()
    digest = log.terminal_digest()
    recs = [r for i, r in enumerate(log.records) if i != 1]
    assert not verify_records(recs, expected=digest).ok


def test_reordered_records_detected():
    log = make_log()
    recs = list(log.records)
    recs[1], recs[2] = recs[2], recs[1]
    assert not verify_records(recs, expected=log.terminal_digest()).ok


def test_truncation_needs_the_separate_digest():
    log = make_log()
    digest = log.terminal_digest()
    truncated = log.records[:3]
    assert verify_records(truncated).ok  # internally consistent: the stated limit
    res = verify_records(truncated, expected=digest)
    assert not res.ok and any("count" in e for e in res.errors)


def test_wholesale_rewrite_detected_only_with_digest():
    log = make_log()
    digest = log.terminal_digest()
    forged = make_log(n=4)  # same shape, rebuilt
    forged.records[2]["rationale_summary"] = "different"  # then re-chain honestly:
    rebuilt = AuditLog(clock=iter(f"2026-10-27T00:00:{i:02d}Z" for i in range(100)).__next__, code_version="test-1")
    for r in forged.records:
        rebuilt.append_step(
            role=r["role"], input=r["input"], query=r["query"], scope=r["scope"], kb_version=r["kb_version"],
            passages=r["passages"], rule_applied=r["rule_applied"], rationale=r["rationale_summary"],
            decision=r["decision"], model_id=r["model_id"], prompt_sha256=r["prompt_sha256"],
            config_sha256=r["config_sha256"])
    assert verify_records(rebuilt.records).ok
    assert not verify_records(rebuilt.records, expected=digest).ok


def test_required_fields_present():
    log = make_log(n=1)
    r = log.records[0]
    for k in ("seq", "role", "input", "query", "scope", "kb_version", "passages", "rule_applied",
              "rationale_summary", "decision", "timestamp", "code_version", "model_id",
              "prompt_sha256", "config_sha256", "prev_hash", "record_hash"):
        assert k in r


def test_decision_must_be_known():
    log = make_log(n=0)
    with pytest.raises(AuditError):
        log.append_step(role="x", input={}, query="q", scope={}, kb_version="v", passages=[],
                        rule_applied="r", rationale="t", decision="MAYBE", model_id="m",
                        prompt_sha256="0" * 64, config_sha256="0" * 64)


def test_persist_and_reload(tmp_path):
    p = tmp_path / "log.jsonl"
    log = make_log(tmp_path=p)
    digest = log.terminal_digest()
    loaded = AuditLog.load_records(p)
    assert verify_records(loaded, expected=digest).ok


def test_free_text_event_records_chain_too():
    log = make_log(n=1)
    log.append_event("scenario_step_released", {"step": 2})
    assert verify_records(log.records, expected=log.terminal_digest()).ok


# ---- model backend ----------------------------------------------------------

def test_scripted_model_returns_in_order_and_records_calls():
    m = ScriptedModel(['{"a": 1}', "second"])
    assert m.complete("sys", "user1").text == '{"a": 1}'
    assert m.complete("sys", "user2").text == "second"
    assert [c["user"] for c in m.calls] == ["user1", "user2"]
    with pytest.raises(ScriptExhausted):
        m.complete("s", "u")


def test_scripted_model_is_refused_for_real_runs():
    with pytest.raises(ValueError, match="scripted"):
        assert_real_backend(ScriptedModel([]))


def test_parse_json_response_handles_fences_and_prose():
    assert parse_json_response('```json\n{"x": 2}\n```') == {"x": 2}
    assert parse_json_response('Here you go: {"x": 3} thanks') == {"x": 3}
    with pytest.raises(ValueError):
        parse_json_response("no json here")
