"""Model backend behind one interface.

AnthropicBackend calls the Anthropic Messages API through the official `anthropic` Python SDK.
Settings come from config/run_config.json: model id, max_tokens, effort,
thinking display, structured output. No sampling parameter (temperature, top_p, top_k) is ever sent.

Stop reasons are checked before any text is read. A truncated, refused, empty or otherwise
incomplete answer comes back as a ModelResponse with `rejection` set; the role turns it into a
visible MODEL_REJECTED record. It never becomes an abstention.

Costs: every call is priced from its usage with the configured price table and added to a
CostLedger. Before each call the ledger reserves an upper bound for that call (input tokens cannot
exceed the UTF-8 byte count of the request, output tokens cannot exceed max_tokens, which the API
enforces) and checks that spent + reserved + in-flight + that bound stays under the per-run cap and
the cumulative cap; otherwise RunCapReached is raised and nothing is sent. A crash while a request
is out leaves its bound reserved, in memory and in a journal file the next run's total cap reads.
cap_statement() says exactly what holds and what does not.

The API key is read by the SDK from ANTHROPIC_API_KEY in the operator's own shell. It is never
stored, logged or included in config().

Test doubles (never used for a reported run):
- ScriptedModel: returns scripted texts at the role interface.
- TestDoubleAnthropicClient: stands in for the SDK client (messages.create only), offline.
Both are refused by assert_real_backend().
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Protocol

REASONING_LABEL = "provider summary of model reasoning, not the raw reasoning"
SUMMARY_LOG_CHARS = 4000
# Tokens the provider may add around the request (framing, structured-output and thinking instructions) on top of the
# request text. An assumption, stated in cap_statement(); the request text itself is bounded by its byte count.
INPUT_OVERHEAD_TOKENS = 2000
JOURNAL_DIR = "_ledger"
RETRYABLE_STATUS = (408, 409, 429)
RETRYABLE_NAMES = ("APIConnectionError", "APITimeoutError")


class BackendNotConfigured(RuntimeError):
    pass


class ScriptExhausted(RuntimeError):
    pass


class RunCapReached(RuntimeError):
    """Raised before a call that could take spending past a cap. Nothing was sent."""


class ModelCallFailed(RuntimeError):
    """A request the API refused for a reason retrying cannot fix (bad request, auth, unknown model)."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


@dataclass
class ModelResponse:
    text: str
    model_id: str
    usage: dict = field(default_factory=dict)
    stop_reason: str | None = "end_turn"
    rejection: dict | None = None
    cost_usd: float = 0.0
    reasoning_summary: str | None = None
    effort: str | None = None
    request_id: str | None = None
    retries: list = field(default_factory=list)

    @property
    def raw_text_sha256(self) -> str:
        return sha256_text(self.text or "")

    def call_record(self) -> dict:
        if self.reasoning_summary:
            rs = {"label": REASONING_LABEL, "text": self.reasoning_summary[:SUMMARY_LOG_CHARS],
                  "sha256": sha256_text(self.reasoning_summary),
                  "clipped_for_log": len(self.reasoning_summary) > SUMMARY_LOG_CHARS}
        else:
            rs = {"label": "no summary"}
        return {"model_id": self.model_id, "stop_reason": self.stop_reason, "usage": dict(self.usage),
                "cost_usd": round(self.cost_usd, 8), "effort": self.effort, "reasoning_summary": rs,
                "rejection": self.rejection, "retries": list(self.retries), "request_id": self.request_id,
                "raw_text_sha256": self.raw_text_sha256}


class ModelBackend(Protocol):
    model_id: str
    is_scripted: bool

    def complete(self, system: str, user: str, schema: dict | None = None) -> ModelResponse: ...

    def config(self) -> dict: ...


# ---- settings ---------------------------------------------------------------

@dataclass
class ModelSettings:
    model_id: str
    max_tokens: int = 16000
    effort: str | None = "medium"
    thinking: dict | None = field(default_factory=lambda: {"type": "adaptive", "display": "summarized"})
    structured_output: bool = True
    max_retries: int = 2
    timeout_s: float = 600.0
    run_cap_usd: float = 2.0
    total_cap_usd: float | None = 18.0
    prices: dict = field(default_factory=dict)
    price_source: str = ""

    @classmethod
    def load(cls, path: str | Path) -> "ModelSettings":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        if d.get("backend", "anthropic") != "anthropic":
            raise BackendNotConfigured(f"unsupported backend {d.get('backend')!r}")
        for banned in ("temperature", "top_p", "top_k"):
            if banned in d:
                raise BackendNotConfigured(f"{banned} is not sent to this API; remove it from {path}")
        return cls(model_id=d["model_id"], max_tokens=int(d.get("max_tokens", 16000)), effort=d.get("effort"),
                   thinking=d.get("thinking"), structured_output=bool(d.get("structured_output", True)),
                   max_retries=int(d.get("max_retries", 2)), timeout_s=float(d.get("timeout_s", 600.0)),
                   run_cap_usd=float(d["run_cap_usd"]),
                   total_cap_usd=float(d["total_cap_usd"]) if d.get("total_cap_usd") is not None else None,
                   prices=d.get("prices_usd_per_mtok", {}), price_source=d.get("price_source", ""))

    def to_config(self) -> dict:
        d = asdict(self)
        d.pop("prices")
        d["price"] = self.prices.get(self.model_id)
        return d


# ---- cost ledger ------------------------------------------------------------

class CostLedger:
    """USD spent in this run, priced from usage. Caps: per run, and cumulative across saved runs."""

    def __init__(self, prices: dict, run_cap_usd: float, total_cap_usd: float | None = None,
                 prior_spent_usd: float = 0.0, journal_dir: str | Path | None = None):
        self.prices = prices
        self.run_cap_usd = float(run_cap_usd)
        self.total_cap_usd = total_cap_usd
        self.prior_spent_usd = float(prior_spent_usd)
        self.spent_usd = 0.0
        self.reserved_usd = 0.0  # attempts that failed without a usage report; they may still have been billed
        self.inflight_usd = 0.0  # the bound of the request that is out right now
        self.ledger_id = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
        self._journal = (Path(journal_dir) / f"{self.ledger_id}.jsonl") if journal_dir else None
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def _price(self, model_id: str) -> dict:
        p = self.prices.get(model_id)
        if not p:
            raise BackendNotConfigured(f"no price recorded for {model_id}; add it to the config price table "
                                       "so the run cap can be enforced")
        return p

    def cost_of(self, model_id: str, usage: dict) -> float:
        p = self._price(model_id)
        inp = int(usage.get("input_tokens") or 0)
        cw = int(usage.get("cache_creation_input_tokens") or 0)
        cr = int(usage.get("cache_read_input_tokens") or 0)
        out = int(usage.get("output_tokens") or 0)
        long = "long_threshold_input_tokens" in p and (inp + cw + cr) > p["long_threshold_input_tokens"]
        pin = p["long_input"] if long else p["input"]
        pout = p["long_output"] if long else p["output"]
        return ((inp + 1.25 * cw + 0.1 * cr) * pin + out * pout) / 1e6

    @staticmethod
    def input_token_bound(prompt) -> int:
        """An upper bound on the input tokens of a request: every token covers at least one byte of the text, so the UTF-8
        byte count cannot be exceeded; INPUT_OVERHEAD_TOKENS covers what the provider adds around it. `prompt` is the
        serialized request text, or a character count (taken at up to 4 bytes per character)."""
        n = prompt * 4 if isinstance(prompt, int) else len(prompt.encode("utf-8"))
        return n + INPUT_OVERHEAD_TOKENS

    def estimate(self, model_id: str, prompt, max_tokens: int) -> float:
        """Upper bound for one call: the byte-count bound on input and the whole output budget (thinking included)."""
        return self.cost_of(model_id, {"input_tokens": self.input_token_bound(prompt), "output_tokens": max_tokens})

    # -- the journal: spending that survives a crash or Ctrl+C (the run's cost.json is written only at the end) --
    def _journal_write(self, kind: str, usd: float) -> None:
        if self._journal is None:
            return
        self._journal.parent.mkdir(parents=True, exist_ok=True)
        with open(self._journal, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({"ledger_id": self.ledger_id, "kind": kind, "usd": round(usd, 8)}) + "\n")
            f.flush()
            os.fsync(f.fileno())

    @property
    def committed_usd(self) -> float:
        return self.spent_usd + self.reserved_usd + self.inflight_usd

    def begin_call(self, model_id: str, estimate_usd: float) -> None:
        """Check the caps against the bound of this call, then hold the bound as in flight until the usage is known."""
        self.check(model_id, estimate_usd)
        self._journal_write("begin", estimate_usd)  # if the journal cannot be written, nothing is sent
        self.inflight_usd = estimate_usd

    def abandon_call(self) -> None:
        """The attempt ended without a usage report (timeout, 5xx, crash): it may have been billed, so its bound stays reserved."""
        if self.inflight_usd:
            self.reserved_usd += self.inflight_usd
            self._journal_write("reserved", self.inflight_usd)
            self.inflight_usd = 0.0

    def release_call(self) -> None:
        """The API refused the request outright (a 4xx): nothing was processed, so nothing was billed."""
        if self.inflight_usd:
            self._journal_write("released", self.inflight_usd)
            self.inflight_usd = 0.0

    def reserve(self, estimate_usd: float) -> None:
        self.reserved_usd += estimate_usd

    def check(self, model_id: str, estimate_usd: float) -> None:
        self._price(model_id)
        committed = self.committed_usd
        if committed + estimate_usd > self.run_cap_usd:
            raise RunCapReached(f"run cap USD {self.run_cap_usd:.2f}: spent {self.spent_usd:.4f} (plus {self.reserved_usd:.4f} "
                                f"reserved for failed attempts), next call could cost up to {estimate_usd:.4f}")
        if self.total_cap_usd is not None and self.prior_spent_usd + committed + estimate_usd > self.total_cap_usd:
            raise RunCapReached(f"total cap USD {self.total_cap_usd:.2f}: earlier runs {self.prior_spent_usd:.4f}, "
                                f"this run {self.spent_usd:.4f} (plus {self.reserved_usd:.4f} reserved), next call up to {estimate_usd:.4f}")

    def record(self, model_id: str, usage: dict) -> float:
        c = self.cost_of(model_id, usage)
        self.spent_usd += c
        self.inflight_usd = 0.0
        self._journal_write("spent", c)
        self.calls += 1
        self.input_tokens += int(usage.get("input_tokens") or 0)
        self.output_tokens += int(usage.get("output_tokens") or 0)
        return c

    def summary(self) -> dict:
        return {"ledger_id": self.ledger_id, "calls": self.calls, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "cost_usd": round(self.spent_usd, 6), "reserved_usd": round(self.reserved_usd, 6),
                "run_cap_usd": self.run_cap_usd,
                "total_cap_usd": self.total_cap_usd, "prior_spent_usd": round(self.prior_spent_usd, 6)}


def prior_spend_usd(runs_root: str | Path) -> float:
    """Sum of cost.json files under a runs folder, excluding test-double rehearsals, plus every journal whose run never
    wrote its cost.json (a crash or Ctrl+C): its spent, reserved and still-in-flight amounts all count."""
    root = Path(runs_root)
    if not root.exists():
        return 0.0
    total = 0.0
    finished: set[str] = set()
    for p in root.rglob("cost.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        finished.update(([d["ledger_id"]] if d.get("ledger_id") else []) + list(d.get("ledger_ids") or []))
        if not d.get("test_double"):
            total += float(d.get("cost_usd") or 0.0) + float(d.get("reserved_usd") or 0.0)
    for j in (root / JOURNAL_DIR).glob("*.jsonl") if (root / JOURNAL_DIR).exists() else []:
        if j.stem in finished:
            continue
        pending = 0.0
        for line in j.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            usd = float(r.get("usd") or 0.0)
            if r.get("kind") == "begin":
                pending = usd
            elif r.get("kind") in ("spent", "reserved"):
                total += usd
                pending = 0.0
            elif r.get("kind") == "released":
                pending = 0.0
        total += pending
    return total


def cap_statement(s: "ModelSettings") -> str:
    """What the cost caps hold to, in numbers, and the ways they can still be wrong. Printed by `gapcourt cost`."""
    price = s.prices.get(s.model_id) or {}
    base = f"{s.max_tokens} output tokens at USD {price.get('output', '?')} per million"
    return (
        f"Cost caps for {s.model_id}: per run USD {s.run_cap_usd:.2f}, in total USD "
        f"{(s.total_cap_usd if s.total_cap_usd is not None else float('nan')):.2f}. "
        f"Worst case for any one call: (UTF-8 bytes of the whole request + {INPUT_OVERHEAD_TOKENS}) input tokens at USD "
        f"{price.get('input', '?')} per million (the long-context price above {price.get('long_threshold_input_tokens', 'n/a')} "
        f"input tokens) plus {base}; max_tokens, thinking included, is enforced by the API. A call is sent only if spent + "
        "reserved + in flight + that bound fits under both caps, so under the stated assumptions no call can take spending "
        "past a cap. A failed attempt, or a crash while a request is out, stays reserved at the bound (journal in "
        f"{JOURNAL_DIR}/). The assumptions, not enforced by code: the price table is right (it is marked unconfirmed until "
        f"checked on the live pricing page; if a price is understated by a factor f, spending can exceed a cap by up to "
        f"(f-1) times the cap), the provider adds no more than {INPUT_OVERHEAD_TOKENS} tokens of its own around the request, "
        "and nothing else spends on the same key (the Console limit is a separate backstop)."
    )


# ---- Anthropic backend ------------------------------------------------------

def _retryable(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in RETRYABLE_STATUS or status >= 500
    return any(c.__name__ in RETRYABLE_NAMES for c in type(exc).__mro__)


def _usage_dict(u) -> dict:
    if u is None:
        return {}
    keys = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    return {k: int(getattr(u, k, 0) or 0) for k in keys}


class AnthropicBackend:
    is_scripted = False

    def __init__(self, settings: ModelSettings, ledger: CostLedger, client=None,
                 sleep: Callable[[float], None] = time.sleep, api_key_env: str = "ANTHROPIC_API_KEY"):
        self.settings = settings
        self.ledger = ledger
        self._client = client
        self._sleep = sleep
        self.api_key_env = api_key_env
        self.is_test_double = bool(getattr(client, "is_test_double", False))
        self.model_id = f"TEST-DOUBLE({settings.model_id})" if self.is_test_double else settings.model_id

    def cap_statement(self) -> str:
        return cap_statement(self.settings)

    def config(self) -> dict:
        client = ("TEST DOUBLE (offline, no network, not model output)" if self.is_test_double
                  else "anthropic SDK, Messages API")
        return {"backend": "anthropic", "client": client, "model_id": self.model_id,
                "settings": self.settings.to_config(), "api_key_env": self.api_key_env}

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not os.environ.get(self.api_key_env):
            raise BackendNotConfigured(f"environment variable {self.api_key_env} is not set; set it in your own "
                                       "PowerShell window, never in a file")
        try:
            import anthropic
        except ImportError as e:
            raise BackendNotConfigured("the anthropic package is not installed: pip install -e .[model]") from e
        # SDK retries are off so that every retry is made, and logged, here.
        self._client = anthropic.Anthropic(max_retries=0, timeout=self.settings.timeout_s)
        return self._client

    def build_request(self, system: str, user: str, schema: dict | None = None) -> dict:
        s = self.settings
        req = {"model": s.model_id, "max_tokens": s.max_tokens, "system": system,
               "messages": [{"role": "user", "content": user}]}
        if s.thinking:
            req["thinking"] = dict(s.thinking)
        oc = {}
        if s.effort:
            oc["effort"] = s.effort
        if s.structured_output and schema is not None:
            oc["format"] = {"type": "json_schema", "schema": schema}
        if oc:
            req["output_config"] = oc
        return req

    def estimate_request(self, req: dict) -> float:
        """Upper bound in USD for one attempt, from the whole serialized request (system, messages, schema, settings)."""
        text = json.dumps(req, ensure_ascii=False)
        return self.ledger.estimate(self.settings.model_id, text, self.settings.max_tokens)

    def complete(self, system: str, user: str, schema: dict | None = None) -> ModelResponse:
        s = self.settings
        req = self.build_request(system, user, schema)
        est = self.estimate_request(req)
        client = self._get_client()
        retries: list[dict] = []
        attempt = 0
        while True:
            self.ledger.begin_call(s.model_id, est)  # checks both caps against this call's bound, then holds it in flight
            try:
                msg = client.messages.create(**req)
                break
            except Exception as exc:  # classified below; non-API errors re-raise
                status = getattr(exc, "status_code", None)
                if not _retryable(exc):
                    if isinstance(status, int) or type(exc).__module__.startswith("anthropic"):
                        self.ledger.release_call()  # the API refused the request outright; nothing was billed
                        raise ModelCallFailed(f"{type(exc).__name__}: {exc}", status if isinstance(status, int) else None) from exc
                    self.ledger.abandon_call()  # not an API answer: the request may have reached the provider
                    raise
                retries.append({"attempt": attempt + 1, "error": type(exc).__name__, "status": status})
                self.ledger.abandon_call()  # no usage came back, so this attempt may or may not have been billed
                if attempt >= s.max_retries:
                    return ModelResponse(text="", model_id=self.model_id, usage={}, stop_reason=None,
                                         rejection={"reason": "api_unavailable", "error": type(exc).__name__,
                                                    "status": status},
                                         effort=s.effort, retries=retries)
                self._sleep(min(2 ** attempt, 30))
                attempt += 1
            except BaseException:  # Ctrl+C or a crash while the request is out: it may have been billed
                self.ledger.abandon_call()
                raise
        return self._read(msg, retries)

    def _read(self, msg, retries: list) -> ModelResponse:
        s = self.settings
        blocks = list(getattr(msg, "content", None) or [])
        texts = [b.text for b in blocks if getattr(b, "type", None) == "text" and getattr(b, "text", None) is not None]
        thoughts = [b.thinking for b in blocks if getattr(b, "type", None) == "thinking" and getattr(b, "thinking", None)]
        text = "".join(texts)
        usage = _usage_dict(getattr(msg, "usage", None))
        cost = self.ledger.record(s.model_id, usage)
        stop = getattr(msg, "stop_reason", None)
        rejection = None
        if stop == "max_tokens":
            rejection = {"reason": "truncated"}
        elif stop == "refusal":
            sd = getattr(msg, "stop_details", None)
            rejection = {"reason": "refused", "category": getattr(sd, "category", None) if sd else None,
                         "explanation": getattr(sd, "explanation", None) if sd else None}
        elif stop == "model_context_window_exceeded":
            rejection = {"reason": "context_exceeded"}
        elif stop not in ("end_turn", "stop_sequence"):
            rejection = {"reason": "unexpected_stop", "stop_reason": stop}
        elif not text.strip():
            rejection = {"reason": "empty"}
        if rejection is not None:
            rejection["raw_text_sha256"] = sha256_text(text)
        return ModelResponse(text=text, model_id=self.model_id, usage=usage, stop_reason=stop, rejection=rejection,
                             cost_usd=cost, reasoning_summary="\n".join(thoughts) or None, effort=s.effort,
                             request_id=getattr(msg, "_request_id", None) or getattr(msg, "id", None),
                             retries=retries)


# ---- test doubles -----------------------------------------------------------

TEST_DOUBLE_REPLY = json.dumps({
    "decision": "ESCALATE", "rule_applied": "none",
    "rationale": "TEST DOUBLE reply: no model was called; this text is not model output.",
    "citations": [], "applicability": [], "gap": None, "revision_reason": None})


def fake_message(text: str | None, stop_reason: str = "end_turn", thinking: str | None = None,
                 usage: dict | None = None, stop_details=None, model: str = "test-double") -> SimpleNamespace:
    """A response shaped like the SDK's Message, for the test double only."""
    content = []
    if thinking is not None:
        content.append(SimpleNamespace(type="thinking", thinking=thinking, signature="test-double"))
    if text is not None:
        content.append(SimpleNamespace(type="text", text=text))
    u = {"input_tokens": 6000, "output_tokens": 1500, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    u.update(usage or {})
    return SimpleNamespace(id="msg_test_double", content=content, stop_reason=stop_reason, stop_details=stop_details,
                           usage=SimpleNamespace(**u), model=model, _request_id="req_test_double")


class TestDoubleAnthropicClient:
    """OFFLINE TEST DOUBLE of the one SDK call used here (client.messages.create). No network.

    Each scripted item is a fake_message, an exception to raise, or a callable(request) returning either.
    When the script runs out it answers with TEST_DOUBLE_REPLY. Output is never model output and is
    refused for reported runs.
    """

    __test__ = False  # not a pytest test class
    is_test_double = True

    def __init__(self, script: list | None = None, default=None):
        self.requests: list[dict] = []
        self._script = list(script or [])
        self._default = default
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **req):
        self.requests.append(req)
        item = self._script.pop(0) if self._script else (self._default or fake_message(TEST_DOUBLE_REPLY))
        if callable(item) and not isinstance(item, SimpleNamespace):
            item = item(req)
        if isinstance(item, BaseException):
            raise item
        return item


class ScriptedModel:
    """Deterministic test double at the role interface. Never use for a reported run.

    Script items are texts, callables(system, user) returning text, or ModelResponse objects.
    """

    model_id = "scripted-test-model"
    is_scripted = True
    is_test_double = True

    def __init__(self, responses: list):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, system: str, user: str, schema: dict | None = None) -> ModelResponse:
        self.calls.append({"system": system, "user": user})
        if not self._responses:
            raise ScriptExhausted("scripted model has no more responses")
        nxt = self._responses.pop(0)
        if isinstance(nxt, ModelResponse):
            return nxt
        text = nxt(system, user) if callable(nxt) else nxt
        return ModelResponse(text=text, model_id=self.model_id)

    def config(self) -> dict:
        return {"backend": "scripted", "model_id": self.model_id}


def assert_real_backend(model) -> None:
    if getattr(model, "is_scripted", False):
        raise ValueError("a scripted model cannot be used for a reported run")
    if getattr(model, "is_test_double", False):
        raise ValueError("a test double cannot be used for a reported run")


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_json_response(text: str) -> dict:
    m = _FENCE.search(text or "")
    candidate = m.group(1) if m else (text or "")
    for attempt in (candidate, candidate[candidate.find("{"):candidate.rfind("}") + 1] if "{" in candidate else ""):
        if not attempt:
            continue
        try:
            obj = json.loads(attempt)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    raise ValueError("no JSON object found in model response")
