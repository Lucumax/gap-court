"""Model connector (spec V3 section 6): Anthropic Messages backend, stop-reason rejections, cost caps.

Every test here is offline. Responses come from TestDoubleAnthropicClient, a labelled test double of the
one SDK call the backend makes (client.messages.create); nothing here is model output.
"""
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from gapcourt.audit import verify_records
from gapcourt.chunking import ChunkParams
from gapcourt.embed import HashingEmbedder
from gapcourt.index import build_kb
from gapcourt.llm import (
    REASONING_LABEL,
    AnthropicBackend,
    BackendNotConfigured,
    CostLedger,
    ModelCallFailed,
    ModelSettings,
    RunCapReached,
    ScriptedModel,
    TestDoubleAnthropicClient,
    assert_real_backend,
    fake_message,
    prior_spend_usd,
)
from gapcourt.roles import ASSESSMENT_SCHEMA, Coordinator, RoleAgent, RoleSpec
from gapcourt.scenario import Scenario, ScenarioEngine, Step

ROOT = Path(__file__).resolve().parents[1]
PRICES = {"claude-haiku-5-5": {"input": 0.10, "output": 0.50, "long_input": 0.50, "long_output": 2.50,
                               "long_threshold_input_tokens": 100000}}


def settings(**kw):
    base = dict(model_id="claude-haiku-5-5", max_tokens=16000, effort="medium",
                thinking={"type": "adaptive", "display": "summarized"}, structured_output=True,
                max_retries=2, run_cap_usd=2.0, total_cap_usd=18.0, prices=PRICES)
    base.update(kw)
    return ModelSettings(**base)


def backend(script, **kw):
    s = settings(**kw)
    client = TestDoubleAnthropicClient(script)
    return AnthropicBackend(s, CostLedger(s.prices, s.run_cap_usd, s.total_cap_usd), client=client,
                            sleep=lambda _s: None), client


VALID = json.dumps({"decision": "ESCALATE", "rule_applied": "none", "rationale": "unclear for this actor",
                    "citations": [], "applicability": [], "gap": None, "revision_reason": None})


@pytest.fixture
def kb(fixtures_dir, tmp_path):
    root = tmp_path / "corpus"
    (root / "uae").mkdir(parents=True)
    shutil.copy(fixtures_dir / "S01.md", root / "uae" / "S01.md")
    return build_kb([root], HashingEmbedder(dim=128), ChunkParams(max_chars=400))


def adviser(kb, model):
    spec = RoleSpec(name="adviser", page_role="Policy and legal adviser", collections=["kb_uae"],
                    focus="what governs the data sharing", instructions="Rule on the step.")
    return RoleAgent(spec, kb, model)


# ---- request shape ----------------------------------------------------------

def test_no_sampling_parameters_sent():
    b, client = backend([fake_message(VALID)])
    b.complete("sys", "usr", schema=ASSESSMENT_SCHEMA)
    req = client.requests[0]
    for k in ("temperature", "top_p", "top_k"):
        assert k not in req
        assert k not in json.dumps(req.get("output_config", {}))


def test_request_uses_config_model_generous_max_tokens_effort_and_adaptive_thinking():
    b, client = backend([fake_message(VALID)])
    b.complete("sys", "usr", schema=ASSESSMENT_SCHEMA)
    req = client.requests[0]
    assert req["model"] == "claude-haiku-5-5"
    assert req["max_tokens"] == 16000
    assert req["output_config"]["effort"] == "medium"
    assert req["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert req["system"] == "sys" and req["messages"] == [{"role": "user", "content": "usr"}]
    assert req["output_config"]["format"]["type"] == "json_schema"
    assert req["output_config"]["format"]["schema"] == ASSESSMENT_SCHEMA


def test_structured_output_and_effort_can_be_switched_off_by_config():
    b, client = backend([fake_message(VALID)], structured_output=False, effort=None, thinking=None)
    b.complete("sys", "usr", schema=ASSESSMENT_SCHEMA)
    req = client.requests[0]
    assert "output_config" not in req and "thinking" not in req


def test_assessment_schema_is_closed_and_lists_every_contract_key():
    assert ASSESSMENT_SCHEMA["additionalProperties"] is False
    assert set(ASSESSMENT_SCHEMA["required"]) == {"decision", "rule_applied", "rationale", "citations",
                                                  "applicability", "gap", "revision_reason"}
    assert set(ASSESSMENT_SCHEMA["properties"]["decision"]["enum"]) == {"ALLOW", "DENY", "ESCALATE", "GAP", "ABSTAIN"}


def test_repo_config_names_the_model_and_has_no_sampling_fields():
    s = ModelSettings.load(ROOT / "config" / "run_config.json")
    assert s.model_id == "claude-haiku-5-5"
    assert s.max_tokens >= 16000 and s.effort == "medium"
    assert s.run_cap_usd <= 2.0 and s.total_cap_usd < 20.0
    assert s.model_id in s.prices
    raw = (ROOT / "config" / "run_config.json").read_text(encoding="utf-8")
    assert "temperature" not in raw and "top_p" not in raw and "top_k" not in raw


def test_only_text_blocks_are_parsed_and_reasoning_summary_is_labelled():
    msg = fake_message(VALID, thinking="I compared the two articles.")
    b, _ = backend([msg])
    r = b.complete("sys", "usr")
    assert r.text == VALID and r.rejection is None
    rec = r.call_record()
    assert rec["reasoning_summary"]["label"] == REASONING_LABEL
    assert rec["reasoning_summary"]["text"] == "I compared the two articles."
    assert rec["stop_reason"] == "end_turn" and rec["effort"] == "medium"
    assert rec["usage"]["input_tokens"] > 0 and rec["cost_usd"] > 0


def test_no_thinking_block_logs_no_summary():
    b, _ = backend([fake_message(VALID)])
    assert b.complete("s", "u").call_record()["reasoning_summary"] == {"label": "no summary"}


# ---- stop reasons become visible rejections ---------------------------------

def test_max_tokens_stop_is_visible_rejection_not_abstain(kb):
    b, _ = backend([fake_message('{"decision": "ESCA', stop_reason="max_tokens")])
    out = adviser(kb, b).assess(step_no=1, messages=["x"], prior=[])
    assert out.decision == "MODEL_REJECTED" and out.decision != "ABSTAIN"
    assert out.rejection["reason"] == "truncated"
    assert "cut off" in out.rationale and "person" in out.rationale
    assert out.model_calls[0]["stop_reason"] == "max_tokens"


def test_refusal_stop_is_visible_rejection_with_category(kb):
    sd = SimpleNamespace(type="refusal", category="general_harms", explanation="declined")
    b, _ = backend([fake_message("", stop_reason="refusal", stop_details=sd)])
    out = adviser(kb, b).assess(step_no=1, messages=["x"], prior=[])
    assert out.decision == "MODEL_REJECTED"
    assert out.rejection["reason"] == "refused" and out.rejection["category"] == "general_harms"


def test_empty_content_is_rejection_not_crash(kb):
    msg = fake_message(None)  # no text block at all
    b, _ = backend([msg])
    out = adviser(kb, b).assess(step_no=1, messages=["x"], prior=[])
    assert out.decision == "MODEL_REJECTED" and out.rejection["reason"] == "empty"
    b2, _ = backend([fake_message("   ")])
    assert adviser(kb, b2).assess(step_no=1, messages=["x"], prior=[]).rejection["reason"] == "empty"


def test_unexpected_stop_reason_is_rejection(kb):
    b, _ = backend([fake_message(VALID, stop_reason="pause_turn")])
    out = adviser(kb, b).assess(step_no=1, messages=["x"], prior=[])
    assert out.decision == "MODEL_REJECTED" and out.rejection["reason"] == "unexpected_stop"


def test_unparseable_after_one_retry_is_rejection(kb):
    b, client = backend([fake_message("not json at all"), fake_message("still not json")])
    out = adviser(kb, b).assess(step_no=1, messages=["x"], prior=[])
    assert len(client.requests) == 2  # exactly one retry
    assert out.decision == "MODEL_REJECTED" and out.rejection["reason"] == "unparseable"
    assert len(out.rejection["raw_text_sha256"]) == 2 and all(len(h) == 64 for h in out.rejection["raw_text_sha256"])
    assert [c["attempt"] for c in out.model_calls] == [1, 2]


def test_unparseable_then_valid_retry_is_used_and_both_calls_logged(kb):
    b, client = backend([fake_message("oops"), fake_message(VALID)])
    out = adviser(kb, b).assess(step_no=1, messages=["x"], prior=[])
    assert out.decision == "ESCALATE" and out.rejection is None
    assert len(out.model_calls) == 2
    assert "could not be parsed" in client.requests[1]["messages"][0]["content"]


def test_scripted_model_rejection_path_matches_backend(kb):
    from gapcourt.llm import ModelResponse

    m = ScriptedModel([ModelResponse(text="", model_id="x", stop_reason="max_tokens",
                                     rejection={"reason": "truncated"})])
    out = adviser(kb, m).assess(step_no=1, messages=["x"], prior=[])
    assert out.decision == "MODEL_REJECTED"


# ---- retries, errors and keys -----------------------------------------------

class FakeStatusError(Exception):
    def __init__(self, status_code):
        super().__init__(f"status {status_code}")
        self.status_code = status_code


def test_retryable_errors_are_retried_and_logged():
    b, client = backend([FakeStatusError(429), FakeStatusError(529), fake_message(VALID)])
    r = b.complete("s", "u")
    assert r.rejection is None and len(client.requests) == 3
    assert [x["status"] for x in r.call_record()["retries"]] == [429, 529]


def test_retries_exhausted_is_visible_rejection():
    b, _ = backend([FakeStatusError(500)] * 3)
    r = b.complete("s", "u")
    assert r.rejection["reason"] == "api_unavailable"


def test_non_retryable_error_stops_the_run():
    b, client = backend([FakeStatusError(400)])
    with pytest.raises(ModelCallFailed) as e:
        b.complete("s", "u")
    assert e.value.status_code == 400 and len(client.requests) == 1


def test_missing_api_key_is_a_plain_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    s = settings()
    b = AnthropicBackend(s, CostLedger(s.prices, s.run_cap_usd, s.total_cap_usd))
    with pytest.raises(BackendNotConfigured, match="ANTHROPIC_API_KEY"):
        b.complete("s", "u")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-sentinel-not-real")
    assert "sk-sentinel" not in json.dumps(b.config())  # the key value never reaches config or its hash


def test_test_double_is_labelled_and_refused_for_real_runs():
    b, _ = backend([fake_message(VALID)])
    assert b.is_test_double and "TEST-DOUBLE" in b.model_id
    assert b.config()["client"].startswith("TEST DOUBLE")
    with pytest.raises(ValueError, match="test double"):
        assert_real_backend(b)


# ---- cost ledger and caps ---------------------------------------------------

def test_cost_ledger_and_run_cap():
    led = CostLedger(PRICES, run_cap_usd=0.01, total_cap_usd=18.0)
    c = led.record("claude-haiku-5-5", {"input_tokens": 100_000, "output_tokens": 0})
    assert c == pytest.approx(0.01)
    assert led.spent_usd == pytest.approx(0.01)
    with pytest.raises(RunCapReached):
        led.check("claude-haiku-5-5", 0.001)
    long = CostLedger(PRICES, run_cap_usd=100.0).cost_of("claude-haiku-5-5", {"input_tokens": 200_000, "output_tokens": 1000})
    assert long == pytest.approx(200_000 * 0.50 / 1e6 + 1000 * 2.50 / 1e6)


def test_backend_stops_before_a_call_that_could_pass_the_run_cap():
    s = settings(run_cap_usd=0.0001)
    client = TestDoubleAnthropicClient([fake_message(VALID)])
    b = AnthropicBackend(s, CostLedger(s.prices, s.run_cap_usd, s.total_cap_usd), client=client)
    with pytest.raises(RunCapReached):
        b.complete("s", "u")
    assert client.requests == []  # the call was never sent


def test_total_cap_counts_prior_runs(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "cost.json").write_text(json.dumps({"cost_usd": 17.99, "test_double": False}), encoding="utf-8")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "cost.json").write_text(json.dumps({"cost_usd": 5.0, "test_double": True}), encoding="utf-8")
    assert prior_spend_usd(tmp_path) == pytest.approx(17.99)
    led = CostLedger(PRICES, run_cap_usd=2.0, total_cap_usd=18.0, prior_spent_usd=prior_spend_usd(tmp_path))
    with pytest.raises(RunCapReached, match="total"):
        led.check("claude-haiku-5-5", 0.02)


def test_unknown_model_price_refuses_to_run():
    led = CostLedger(PRICES, run_cap_usd=2.0)
    with pytest.raises(BackendNotConfigured, match="price"):
        led.check("claude-unknown-model", 0.0)


# ---- the real SDK request path, offline -------------------------------------

def test_real_sdk_request_shape_with_mock_transport(monkeypatch):
    anthropic = pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["path"] = request.url.path
        return httpx2.Response(200, json={
            "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
            "content": [{"type": "thinking", "thinking": "short summary", "signature": "sig"},
                        {"type": "text", "text": VALID}],
            "stop_reason": "end_turn", "stop_sequence": None, "stop_details": None,
            "usage": {"input_tokens": 120, "output_tokens": 40}})

    client = anthropic.Anthropic(api_key="test-not-a-real-key", max_retries=0,
                                 http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    s = settings()
    b = AnthropicBackend(s, CostLedger(s.prices, s.run_cap_usd, s.total_cap_usd), client=client)
    r = b.complete("sys", "usr", schema=ASSESSMENT_SCHEMA)
    body = seen["body"]
    assert seen["path"].endswith("/v1/messages")
    assert body["model"] == "claude-haiku-5-5" and body["max_tokens"] == 16000
    assert body["output_config"]["effort"] == "medium"
    assert body["thinking"]["type"] == "adaptive"
    assert not {"temperature", "top_p", "top_k"} & set(body)
    assert r.text == VALID and r.call_record()["reasoning_summary"]["text"] == "short summary"
    assert r.usage["input_tokens"] == 120


# ---- scenario level ---------------------------------------------------------

def _engine(kb, adviser_model, custodian_model=None):
    adv = adviser(kb, adviser_model)
    roles = [adv]
    if custodian_model is not None:
        roles.append(RoleAgent(RoleSpec(name="custodian", page_role="Institutional participant A",
                                        collections=["kb_uae"], focus="release", instructions="x"),
                               kb, custodian_model))
    steps = [Step("1", "Step one.", ["adviser"]),
             Step("2", "Step two: may the recipient agency use the records for a different purpose?",
                  ["adviser"] + (["custodian"] if custodian_model else []))]
    return ScenarioEngine(Scenario("S-T", "synthetic", steps), roles, Coordinator(), kb)


def test_rejection_records_are_chained_and_verifiable(kb):
    b, _ = backend([fake_message(VALID), fake_message('{"deci', stop_reason="max_tokens")])
    eng = _engine(kb, b)
    res = eng.run_all()
    assert verify_records(eng.log.records, expected=eng.log.terminal_digest()).ok
    steps = [r for r in eng.log.records if r["kind"] == "step"]
    assert steps[1]["decision"] == "MODEL_REJECTED"
    assert steps[1]["rejection"]["reason"] == "truncated"
    assert steps[1]["model_calls"][0]["stop_reason"] == "max_tokens"
    assert steps[0]["model_calls"][0]["usage"]["output_tokens"] > 0
    ev = [r for r in eng.log.records if r.get("event") == "model_rejection"]
    assert ev and ev[0]["payload"]["reason"] == "truncated"
    # a rejection is not a revision, and the step goes to a person
    assert res.revisions == []
    assert res.step_results[1].ruling.decision == "ESCALATE"
    assert res.step_results[1].ruling.model_rejections == [{"role": "adviser", "reason": "truncated"}]
    assert "person" in res.step_results[1].ruling.reason
    assert res.model_rejections and res.model_rejections[0]["reason"] == "truncated"


def test_coordinator_treats_rejection_like_escalate_for_other_roles(kb):
    deny_quote = "must not use the records for a different purpose"

    def deny(kw):
        user = kw["messages"][0]["content"]
        import re
        cid = None
        for m in re.finditer(r'<passage id="([A-Z0-9]+-[0-9a-f]{10})"[^>]*>\n(.*?)\n</passage>', user, re.S):
            if deny_quote in m.group(2):
                cid = m.group(1)
        return fake_message(json.dumps({"decision": "DENY", "rule_applied": "Art 3", "rationale": "r",
                                        "citations": [{"chunk_id": cid, "quote": deny_quote}],
                                        "applicability": [], "gap": None, "revision_reason": None}))
    adv_b, _ = backend([fake_message(VALID), deny])
    cust_b, _ = backend([fake_message("", stop_reason="refusal",
                                      stop_details=SimpleNamespace(type="refusal", category=None, explanation=None))])
    eng = _engine(kb, adv_b, cust_b)
    res = eng.run_all()
    r = res.step_results[1]
    assert r.ruling.decision == "DENY"  # the custodian's rejection counts as ESCALATE, the adviser stands
    assert r.ruling.model_rejections == [{"role": "custodian", "reason": "refused"}]


def test_run_cap_stops_run_and_saves_partial_log(kb, tmp_path):
    from gapcourt.journey import run_and_save

    s = settings(run_cap_usd=0.0085)  # room for one worst-case call estimate, not two
    client = TestDoubleAnthropicClient([fake_message(VALID)] * 5)
    b = AnthropicBackend(s, CostLedger(s.prices, s.run_cap_usd, s.total_cap_usd), client=client)
    sc = Scenario("S-T", "synthetic", [Step("1", "a", ["adviser"]), Step("2", "b", ["adviser"]), Step("3", "c", ["adviser"])])
    out = run_and_save(kb, sc, {"adviser": b}, tmp_path / "run", real=False, roles=[adviser(kb, b).spec])
    assert out["stopped"]["reason"] == "run cap reached"
    assert len(out["rulings"]) < 3
    recs = json.loads((tmp_path / "run" / "digest.json").read_text(encoding="utf-8"))
    from gapcourt.audit import AuditLog
    assert verify_records(AuditLog.load_records(tmp_path / "run" / "log.jsonl"), expected=recs).ok
    cost = json.loads((tmp_path / "run" / "cost.json").read_text(encoding="utf-8"))
    assert cost["test_double"] is True and cost["cost_usd"] <= 0.0085
    assert any(r.get("event") == "run_stopped" for r in AuditLog.load_records(tmp_path / "run" / "log.jsonl"))


# ---- Admission estimate and failed attempts -------------

def test_estimate_counts_the_whole_request_including_the_schema():
    s = settings()
    led = CostLedger(s.prices, s.run_cap_usd, s.total_cap_usd)
    b = AnthropicBackend(s, led, client=TestDoubleAnthropicClient([]))
    with_schema = b.estimate_request(b.build_request("sys", "usr", schema=ASSESSMENT_SCHEMA))
    without = b.estimate_request(b.build_request("sys", "usr", schema=None))
    assert with_schema > without


def test_non_ascii_text_is_estimated_at_up_to_one_token_per_character():
    led = CostLedger(PRICES, run_cap_usd=100.0)
    from gapcourt.llm import INPUT_OVERHEAD_TOKENS

    arabic = led.input_token_bound("ع" * 10_000) - INPUT_OVERHEAD_TOKENS
    english = led.input_token_bound("a" * 10_000) - INPUT_OVERHEAD_TOKENS
    assert arabic == 2 * english  # the bound is the UTF-8 byte count: Arabic letters take two bytes each


def test_failed_attempts_reserve_budget_that_counts_against_the_caps():
    s = settings(run_cap_usd=0.02)
    led = CostLedger(s.prices, s.run_cap_usd, s.total_cap_usd)
    client = TestDoubleAnthropicClient([FakeStatusError(529), FakeStatusError(529), fake_message(VALID)])
    b = AnthropicBackend(s, led, client=client, sleep=lambda _s: None)
    with pytest.raises(RunCapReached):
        b.complete("s", "u" * 2000)  # each failed attempt may still have been billed; the third attempt would pass the cap
    assert led.reserved_usd > 0 and len(client.requests) < 3
    assert led.summary()["reserved_usd"] == pytest.approx(led.reserved_usd, abs=1e-6)


def test_prior_spend_counts_reserved_budget_of_failed_attempts(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "cost.json").write_text(json.dumps({"cost_usd": 1.0, "reserved_usd": 0.25, "test_double": False}), encoding="utf-8")
    assert prior_spend_usd(tmp_path) == pytest.approx(1.25)
