from gapcourt.audit import AuditLog
from gapcourt.traceview import render_trace_html


def build():
    log = AuditLog(clock=lambda: "2026-10-27T00:00:00Z", code_version="t")
    log.append_event("scenario_start", {"scenario_id": "S-TEST", "synthetic": True})
    log.append_step(role="adviser", input={"q": "<script>alert(1)</script>"}, query="q", scope={}, kb_version="kb-x",
                    passages=[{"chunk_id": "c1", "text_sha256": "ab" * 32, "cite": "S01 Article 3"}],
                    rule_applied="r", rationale="because <b>x</b>", decision="ESCALATE", model_id="m",
                    prompt_sha256="0" * 64, config_sha256="1" * 64)
    return log


def test_html_escapes_content_and_labels_synthetic():
    log = build()
    html = render_trace_html(log.records, digest=log.terminal_digest())
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "SYNTHETIC" in html


def test_html_states_verification_limits_and_result():
    log = build()
    html = render_trace_html(log.records, digest=log.terminal_digest())
    assert "internal consistency verified" in html
    assert "does not establish authorship" in html
    bad = [dict(r) for r in log.records]
    bad[1]["decision"] = "ALLOW"
    assert "verification FAILED" in render_trace_html(bad, digest=log.terminal_digest())


def test_html_has_no_external_resources_and_no_logos():
    html = render_trace_html(build().records)
    assert "http://" not in html and "https://" not in html and "<img" not in html
    assert "<script" not in html and "<link" not in html and "src=" not in html
    assert "Independent entry. Not affiliated with or endorsed by ITU." in html


# ---- policy-reader cards ------------------------------------------------------------

SOURCES = {"U06": "https://official.example/u06.pdf", "U12": "https://official.example/u12.html"}


def journey_log(model_id="claude-haiku-5-5"):
    log = AuditLog(clock=lambda: "2026-11-06T10:00:00Z", code_version="abc1234")
    log.append_event("scenario_start", {"scenario_id": "S-01", "title": "Core: deployment, second-purpose request (SYNTHETIC)",
                                        "synthetic": True, "kb_version": "kb-test", "steps": 2})

    def step(role, step_id, n, text, decision, rationale, gate=None, rejection=None, calls=None):
        log.append_event("scenario_step_released", {"step_id": step_id, "step_no": n, "addressees": [role], "text_sha256": "0" * 64})
        return log.append_step(role=role, input={"step_id": step_id, "step_no": n, "messages": [f"[step {n}] {text}"]}, query="q",
                               scope={"collections": ["kb_uae"]}, kb_version="kb-test", passages=[], rule_applied="U06 Article 15",
                               rationale=rationale, decision=decision, model_id=model_id, prompt_sha256="0" * 64,
                               config_sha256="1" * 64, model_calls=calls or [], gate=gate, rejection=rejection)

    kept = {"chunk_id": "U06-aaa", "cite": "U06 Article 15", "doc_id": "U06", "locator": "Article 15",
            "quote": "Dubai Data may be used only for its intended purposes", "binding": True}
    step("adviser", "S01-1", 1, "Step 1. The service asks whether the pre-check may use the records.", "ESCALATE",
         "No verified binding quotation; a person decides.", gate={"kept": [], "dropped": [{"chunk_id": "x", "reason": "quotation_not_found"}],
                                                                  "scope_findings": [], "notes": []})
    step("adviser", "S01-2", 2, "Step 2. The service asks the records office to release the fields for a second purpose.", "DENY",
         "The records were collected for housing registration only.", gate={"kept": [kept], "dropped": [], "scope_findings": [], "notes": []})
    log.append_event("revision", {"role": "adviser", "step_id": "S01-2", "from": "ESCALATE", "to": "DENY",
                                  "reason": "the records office stated the original purpose", "prior_record_hash": "ab" * 32})
    log.append_event("gap_opened", {"gap_type": "corpus_coverage_gap", "requirement": "income threshold", "queries": ["income threshold benefit"],
                                    "kb_version": "kb-test", "scope": "kb_uae", "step_id": "S01-2", "passages": [], "reviewer": None,
                                    "note": None, "bounded_finding": "No applicable governing passage was found in KB version kb-test, within scope kb_uae, using the recorded searches (1).",
                                    "caveat": "Not reviewed by an analyst."})
    step("custodian", "S01-2", 2, "Step 2. ...", "MODEL_REJECTED", "the model's answer was cut off at the output limit; this step went to a person",
         rejection={"reason": "truncated"})
    log.append_event("model_rejection", {"role": "custodian", "step_id": "S01-2", "reason": "truncated"})
    log.append_event("ruling", {"step_id": "S01-2", "decision": "DENY", "reason": "adviser assessment stands", "final_authority": "human officer",
                                "executed": False, "cited": ["U06-aaa"], "model_rejections": []})
    return log


def test_cards_show_header_chips_step_text_and_plain_decision_words():
    log = journey_log()
    html = render_trace_html(log.records, digest=log.terminal_digest(), sources=SOURCES)
    for needle in ("SYNTHETIC", "claude-haiku-5-5", "kb-test", "log verified", "Core: deployment, second-purpose request",
                   "Step 1. The service asks whether the pre-check may use the records.", "Escalated to a person", "Deny (a proposal)"):
        assert needle in html, needle


def test_cards_show_each_quotation_with_badge_binding_tag_and_source_link():
    log = journey_log()
    html = render_trace_html(log.records, digest=log.terminal_digest(), sources=SOURCES)
    assert "<blockquote" in html and "Dubai Data may be used only for its intended purposes" in html
    assert "quotation verified" in html and "binding instrument" in html
    assert '<a href="https://official.example/u06.pdf"' in html and "U06 Article 15" in html
    assert "quotation_not_found" not in html and "not found in the cited passage" in html  # plain words for a dropped statement


def test_cards_show_the_revision_the_gap_and_the_rejection_in_words():
    log = journey_log()
    html = render_trace_html(log.records, digest=log.terminal_digest(), sources=SOURCES)
    assert "changed from ESCALATE to DENY because the records office stated the original purpose" in html
    assert "corpus coverage gap" in html and "No applicable governing passage was found in KB version kb-test" in html
    assert "income threshold benefit" in html and "Not reviewed by an analyst" in html
    assert "class='card rejected'" in html and "cut off" in html
    assert "Ruling (a proposal)" in html and "human officer" in html


def test_cards_keep_technical_records_in_details_and_print_cleanly_without_scripts():
    log = journey_log()
    html = render_trace_html(log.records, digest=log.terminal_digest(), sources=SOURCES)
    assert "<details" in html and "@media print" in html and "font-size:18px" in html.replace(" ", "")
    assert "<script" not in html and "<link" not in html and "src=" not in html and "<img" not in html
    assert html.count("https://") == html.count('href="https://')  # links only, no loaded resources


def test_test_double_runs_are_labelled_on_the_page():
    log = journey_log(model_id="TEST-DOUBLE(claude-haiku-5-5)")
    html = render_trace_html(log.records, digest=log.terminal_digest(), sources=SOURCES)
    assert "TEST DOUBLE" in html and "no model was called" in html


def test_page_escapes_hostile_text_in_cards():
    log = AuditLog(clock=lambda: "2026-11-06T10:00:00Z", code_version="t")
    log.append_event("scenario_start", {"scenario_id": "S", "title": "<img src=x onerror=alert(1)>", "synthetic": True, "kb_version": "k", "steps": 1})
    log.append_step(role="adviser", input={"step_id": "s1", "step_no": 1, "messages": ["[step 1] <script>bad()</script>"]}, query="q", scope={},
                    kb_version="k", passages=[], rule_applied="r", rationale="<b>x</b>", decision="DENY", model_id="m",
                    prompt_sha256="0" * 64, config_sha256="1" * 64,
                    gate={"kept": [{"chunk_id": "c", "cite": "U06 Article 1", "doc_id": "U06", "locator": "Article 1", "quote": "<i>q</i>", "binding": True}],
                          "dropped": [], "scope_findings": [], "notes": []})
    html = render_trace_html(log.records, sources={"U06": "javascript:alert(1)"})
    assert "<script>bad()" not in html and "<img src=x" not in html and "javascript:" not in html
