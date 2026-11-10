"""Session 3: the cost caps as hard as the code can make them.

Before each call the ledger reserves an upper bound for that call: input tokens cannot exceed the UTF-8 byte count of
the request (a token covers at least one byte) plus a fixed overhead, output tokens cannot exceed max_tokens (the API
enforces it, thinking included). The call is sent only if spent + reserved + in-flight + that bound fits under both
caps. A crash or Ctrl+C mid-call leaves the reservation standing, in memory and in a journal file that the next run's
total cap reads. All offline; nothing here is model output."""
import json

import pytest

from gapcourt.llm import (
    INPUT_OVERHEAD_TOKENS,
    AnthropicBackend,
    CostLedger,
    ModelSettings,
    RunCapReached,
    TestDoubleAnthropicClient,
    fake_message,
    prior_spend_usd,
)

PRICES = {"claude-haiku-5-5": {"input": 0.10, "output": 0.50, "long_input": 0.50, "long_output": 2.50,
                               "long_threshold_input_tokens": 100000}}
M = "claude-haiku-5-5"
VALID = json.dumps({"decision": "ESCALATE", "rule_applied": "none", "rationale": "x", "citations": [],
                    "applicability": [], "gap": None, "revision_reason": None})


def settings(**kw):
    base = dict(model_id=M, max_tokens=16000, effort="medium", thinking={"type": "adaptive", "display": "summarized"},
                structured_output=True, max_retries=2, run_cap_usd=2.0, total_cap_usd=18.0, prices=PRICES)
    base.update(kw)
    return ModelSettings(**base)


class StatusError(Exception):
    def __init__(self, status):
        super().__init__(f"status {status}")
        self.status_code = status


def test_input_bound_is_never_below_the_utf8_byte_count():
    led = CostLedger(PRICES, 100.0)
    samples = ["a" * 5000, "ع" * 5000, "日本語のテキスト" * 300, "0123456789" * 500, "{}[]\"',:" * 500, "\U0001F600" * 800,
               json.dumps({"k": ["x" * 7] * 900})]
    for t in samples:
        assert led.input_token_bound(t) >= len(t.encode("utf-8")) + INPUT_OVERHEAD_TOKENS


def test_one_worst_case_call_cannot_take_spending_past_the_cap():
    # the provider's worst case for this request: every byte a token, the whole output budget used
    s = settings()
    req_probe = AnthropicBackend(s, CostLedger(PRICES, 100.0), client=TestDoubleAnthropicClient([])).build_request("sys", "usr " * 50)
    bound = AnthropicBackend(s, CostLedger(PRICES, 100.0), client=TestDoubleAnthropicClient([])).estimate_request(req_probe)
    cap = bound * 1.0001  # room for exactly one call
    led = CostLedger(PRICES, cap, None)
    nbytes = len(json.dumps(req_probe, ensure_ascii=False).encode("utf-8"))
    worst = fake_message(VALID, usage={"input_tokens": nbytes, "output_tokens": s.max_tokens})
    b = AnthropicBackend(s, led, client=TestDoubleAnthropicClient([worst, worst]), sleep=lambda _s: None)
    b.complete("sys", "usr " * 50)
    assert led.spent_usd <= cap
    with pytest.raises(RunCapReached):
        b.complete("sys", "usr " * 50)  # the second call could not fit, so it is not sent
    assert led.spent_usd <= cap


def test_the_call_in_flight_is_reserved_while_the_request_is_out():
    s = settings()
    led = CostLedger(PRICES, 2.0, 18.0)
    seen = {}

    def during(req):
        seen["inflight"] = led.inflight_usd
        seen["committed"] = led.committed_usd
        return fake_message(VALID)

    b = AnthropicBackend(s, led, client=TestDoubleAnthropicClient([during]), sleep=lambda _s: None)
    b.complete("s", "u")
    assert seen["inflight"] > 0.008 and seen["committed"] >= seen["inflight"]
    assert led.inflight_usd == 0.0  # released once the real usage is known


def test_a_crash_while_the_request_is_out_leaves_its_bound_reserved():
    s = settings()
    led = CostLedger(PRICES, 2.0, 18.0)

    def boom(req):
        raise KeyboardInterrupt()

    b = AnthropicBackend(s, led, client=TestDoubleAnthropicClient([boom]), sleep=lambda _s: None)
    with pytest.raises(KeyboardInterrupt):
        b.complete("s", "u")
    assert led.reserved_usd > 0.008 and led.inflight_usd == 0.0  # the request may have been billed; counted at its bound


def test_an_api_refusal_of_the_request_is_not_reserved():
    from gapcourt.llm import ModelCallFailed

    s = settings()
    led = CostLedger(PRICES, 2.0, 18.0)
    b = AnthropicBackend(s, led, client=TestDoubleAnthropicClient([StatusError(400)]), sleep=lambda _s: None)
    with pytest.raises(ModelCallFailed):
        b.complete("s", "u")
    assert led.reserved_usd == 0.0 and led.inflight_usd == 0.0


def test_journal_lets_the_next_run_count_a_call_that_never_finished(tmp_path):
    s = settings()
    led = CostLedger(PRICES, 2.0, 18.0, journal_dir=tmp_path / "_ledger")

    def boom(req):
        raise KeyboardInterrupt()

    b = AnthropicBackend(s, led, client=TestDoubleAnthropicClient([boom]), sleep=lambda _s: None)
    with pytest.raises(KeyboardInterrupt):
        b.complete("s", "u")
    # no cost.json was ever written for this run; the journal alone carries the spend into the total cap
    assert prior_spend_usd(tmp_path) >= 0.008


def test_a_finished_run_is_not_counted_twice_when_its_cost_json_names_the_ledger(tmp_path):
    s = settings()
    led = CostLedger(PRICES, 2.0, 18.0, journal_dir=tmp_path / "_ledger")
    b = AnthropicBackend(s, led, client=TestDoubleAnthropicClient([fake_message(VALID)]), sleep=lambda _s: None)
    b.complete("s", "u")
    assert prior_spend_usd(tmp_path) == pytest.approx(led.spent_usd, rel=1e-6)  # journal only, run unfinished
    run = tmp_path / "run1"
    run.mkdir()
    (run / "cost.json").write_text(json.dumps({"test_double": False, **led.summary()}), encoding="utf-8")
    assert prior_spend_usd(tmp_path) == pytest.approx(led.spent_usd, rel=1e-6)  # cost.json now carries it; journal ignored


def test_a_test_double_run_leaves_no_journal(tmp_path):
    from gapcourt.cli import _backend

    class A:
        config = "config/run_config.json"
        test_double = True
        runs_root = str(tmp_path)

    _backend(A())
    assert not (tmp_path / "_ledger").exists()


def test_the_stated_worst_case_matches_the_numbers_the_code_enforces():
    s = settings()
    led = CostLedger(PRICES, 2.0, 18.0)
    b = AnthropicBackend(s, led, client=TestDoubleAnthropicClient([]))
    req = b.build_request("sys", "usr")
    est = b.estimate_request(req)
    stmt = b.cap_statement()
    assert "16000" in stmt and "2.00" in stmt and "18.00" in stmt and "price" in stmt.lower()
    assert f"{INPUT_OVERHEAD_TOKENS}" in stmt
    assert est == pytest.approx(b.ledger.cost_of(M, {"input_tokens": len(json.dumps(req, ensure_ascii=False).encode()) + INPUT_OVERHEAD_TOKENS,
                                                     "output_tokens": 16000}))


def test_the_cost_command_prints_the_cap_statement(tmp_path, capsys):
    from gapcourt.cli import main

    assert main(["cost", "--runs-root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "worst case" in out.lower() and "max_tokens" in out
