"""Replaceable daily proposals; API calls occur only in explicit Gemini mode."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
import json
import os
from pathlib import Path
from time import perf_counter
import math

from .data import canonical_hash, write_json

PROMPT = """SwingGuard-inspired price-only adaptation, not a paper reproduction.
Propose long-only BTCUSDT and ETHUSDT spot weights for the next UTC day, at most 1x
combined gross exposure. Use ONLY supplied completed historical observations.
No live retrieval, remembered outcomes, shorting, borrowing or tool calls.
Weights are fractions of portfolio equity; cash is permitted. Shared normalization
divides weights by max(1, their sum). Commission is 10 bp and slippage 5 bp per side.
The deterministic controller has final authority. If risk_context is present, use
its state, drawdown, daily loss and market shock when GENERATING the proposal.
The fixed stop and target in execution_assumptions are enforced by the engine.
Return JSON with exactly weights (BTCUSDT, ETHUSDT decimal strings) and a brief
rationale grounded in the supplied features. No web or external historical facts.
"""
SCHEMA = {"type": "object", "additionalProperties": False,
          "properties": {"weights": {"type": "object", "additionalProperties": False,
              "properties": {s: {"type": "string"} for s in ("BTCUSDT", "ETHUSDT")},
              "required": ["BTCUSDT", "ETHUSDT"]}, "rationale": {"type": "string"}},
          "required": ["weights", "rationale"]}

# Exact request alias, prices and accepted response names verified together.
# Add versioned names only after verifying their pricing; prefixes confer no trust.
VERIFIED_GEMINI_PRICING = {
    "gemini-3.6-flash": {
        "input": Decimal("0.75"), "output": Decimal("3.75"),
        "returned_models": frozenset({"gemini-3.6-flash"}),
    },
}


@contextmanager
def budget_lock(path):
    """An OS lock releases on process exit; competing paid writers fail closed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("another Gemini writer holds the budget lock; no request sent") from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@dataclass(frozen=True)
class Proposal:
    created_ms: int
    received_ms: int
    expires_ms: int
    weights: dict[str, str]
    status: str
    input_hash: str
    model: str
    rationale: str
    cost_usd: str = "0"


def validate_weights(weights, symbols):
    if not isinstance(weights, dict) or set(weights) != set(symbols):
        raise ValueError("exact symbol weights required")
    result = {}
    for symbol in symbols:
        if not isinstance(weights[symbol], str):
            raise ValueError("weights must be decimal strings")
        value = Decimal(weights[symbol])
        if not value.is_finite() or not 0 <= value <= 1:
            raise ValueError("weights must be finite long-only fractions")
        result[symbol] = value
    normalizer = max(Decimal(1), sum(result.values()))
    return {s: value / normalizer for s, value in result.items()}


def proposal_valid(proposal, as_of, now, ttl, max_latency=None):
    return (proposal.created_ms == as_of and proposal.created_ms <= proposal.received_ms <= now
            and now < proposal.expires_ms <= proposal.created_ms + ttl
            and (max_latency is None or proposal.received_ms - proposal.created_ms <= max_latency))


class RuleProvider:
    model = "offline-trend-rule-v2"

    def __init__(self, config, cache_root):
        self.config, self.cache_root = config, Path(cache_root)
        self.audit = []
        self.sent_count = 0

    def identity(self, as_of, features, risk):
        return {"model": self.model, "version": self.config["gemini"]["prompt_version"],
                "as_of_ms": as_of, "features": features, "risk_context": risk,
                "ttl_ms": self.config["proposal_ttl_ms"],
                "lifecycle": {k: self.config.get(k) for k in (
                    "lifecycle_version", "proposal_renewal_grace_ms", "max_proposal_latency_ms")},
                "execution_assumptions": {k: self.config[k] for k in (
                    "commission_bps", "slippage_bps", "stop_loss_pct", "take_profit_pct")}}

    def generate(self, request):
        risk = request["risk_context"]
        cautious = risk is not None and risk["state"] != "NORMAL"
        weights = {}
        for symbol, f in request["features"].items():
            trend = f["close"] > f["sma60" if cautious else "sma20"]
            momentum = f["return7d"] > (0.015 if cautious else 0)
            active = trend and momentum and not (risk is not None and risk["state"] == "SURVIVAL")
            weights[symbol] = "0.5" if active else "0"
        return {"weights": weights, "rationale": "risk-conditioned 60d trend/1.5% 7d momentum under non-normal risk" if cautious
                else "20d trend and positive 7d momentum"}, 0, "0", self.model

    def make_proposal(self, request, raw, latency, cost, returned_model):
        key, as_of = canonical_hash(request), request["as_of_ms"]
        try:
            if not isinstance(raw, dict) or set(raw) != {"weights", "rationale"}:
                raise ValueError("invalid JSON shape")
            weights = validate_weights(raw["weights"], self.config["symbols"])
            if not isinstance(raw["rationale"], str) or len(raw["rationale"]) > 2000:
                raise ValueError("invalid rationale")
            status, rationale = "ok", raw["rationale"]
        except (ValueError, TypeError, ArithmeticError):
            weights = {s: Decimal(0) for s in self.config["symbols"]}
            status, rationale = "invalid_response_flat_fallback", "invalid structured proposal"
        return Proposal(as_of, as_of + latency, as_of + self.config["proposal_ttl_ms"],
            {s: str(w) for s, w in weights.items()}, status, key, returned_model, rationale, cost)

    def propose(self, as_of, features, risk=None):
        request = self.identity(as_of, features, risk)
        key = canonical_hash(request)
        path = self.cache_root / f"{key}.json"
        hit = path.exists()
        if hit:
            saved = json.loads(path.read_text(encoding="utf-8"))
            if saved["request"] != request or saved["input_hash"] != key:
                raise ValueError("proposal cache mismatch")
            proposal = Proposal(**saved["proposal"])
        else:
            raw, latency, cost, returned_model = self.generate(request)
            proposal = self.make_proposal(request, raw, latency, cost, returned_model)
            write_json(path, {"request": request, "input_hash": key, "proposal": asdict(proposal)})
        self.audit.append({"as_of_ms": as_of, "input_hash": key, "cache_hit": hit,
                           "risk_supplied": risk is not None, "proposal": asdict(proposal)})
        return proposal


class GeminiProvider(RuleProvider):
    """Durable response journal is primary; per-request caches are derived files."""
    def __init__(self, config, cache_root, budget, client=None):
        super().__init__(config, cache_root)
        self.model = config["gemini"]["model"]
        self.budget = Decimal(str(budget))
        if not self.budget.is_finite() or not 0 < self.budget <= 10:
            raise ValueError("explicit Gemini budget must be positive and at most 10 USD")
        self.client = client
        self.ledger_path = self.cache_root / "budget.json"
        from .journal import read_ledger
        self.ledger = read_ledger(self.ledger_path)

    def identity(self, as_of, features, risk):
        return {**super().identity(as_of, features, risk), "prompt": PROMPT,
                "schema": SCHEMA, "generation": self.config["gemini"], "journal_contract": 2}

    def _pricing(self):
        c = self.config["gemini"]
        verified = VERIFIED_GEMINI_PRICING.get(self.model)
        # No unverified model/rate substitutions under a claimed USD cap.
        if (verified is None or Decimal(c["assumed_input_usd_per_million"]) != verified["input"]
                or Decimal(c["assumed_output_usd_per_million"]) != verified["output"]):
            raise ValueError("unverified model/pricing combination; no request sent")
        return {"model": self.model, "input_usd_per_million": c["assumed_input_usd_per_million"],
                "output_usd_per_million": c["assumed_output_usd_per_million"],
                "valid_through": "2026-12-31", "source": c["pricing_source"]}

    def _client_for_send(self):
        if self.client is not None:
            return self.client
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            try:
                from dotenv import dotenv_values
                key = dotenv_values(".env").get("GEMINI_API_KEY")
            except ImportError:
                pass
        if not key:
            raise ValueError("GEMINI_API_KEY unavailable; no request sent")
        from google import genai
        from google.genai import types
        self.client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=30_000,
            client_args={"trust_env": False}, retry_options=types.HttpRetryOptions(attempts=1)))
        return self.client

    def _halt(self, key, status):
        entry = self.ledger[key]
        entry["status"] = status
        write_json(self.ledger_path, self.ledger)
        raise RuntimeError("Gemini journal requires review; reservation/liability retained, no automatic resend")

    def _recover(self, key, request):
        from .journal import validate_usage, usage_cost
        entry = self.ledger[key]
        if (entry.get("format_version") != 2 or entry.get("request") != request
                or entry.get("request_hash") != key):
            raise RuntimeError("legacy/incomplete journal cannot recover this request; no resend")
        if entry["status"] not in ("response_received", "settled"):
            raise RuntimeError("request outcome or usage uncertain; automatic resubmission forbidden")
        response = entry.get("response")
        if not isinstance(response, dict) or entry.get("response_hash") != canonical_hash(response):
            self._halt(key, "record_corrupt")
        try:
            pricing = self._pricing()
            if entry.get("pricing") != pricing:
                raise ValueError("pricing snapshot mismatch")
            returned = response.get("returned_model")
            if not isinstance(returned, str) or returned not in VERIFIED_GEMINI_PRICING[self.model]["returned_models"]:
                raise ValueError("unpriced returned model")
            usage = validate_usage(response.get("usage"), self.config["gemini"])
            cost = usage_cost(usage, pricing)
            if cost > Decimal(entry["reservation_usd"]):
                raise ValueError("cost exceeds reserved bound")
            latency = response["latency_ms"]
            if not isinstance(latency, int) or isinstance(latency, bool) or latency < 1:
                raise ValueError("invalid response latency")
        except (ValueError, KeyError, ArithmeticError):
            self._halt(key, "usage_uncertain")
        try:
            raw = json.loads(response.get("text") or "")
        except (ValueError, TypeError):
            raw = None
        proposal = self.make_proposal(request, raw, latency, str(cost), returned)
        if entry["status"] == "settled":
            if (entry.get("proposal") != asdict(proposal)
                    or Decimal(entry["cost_or_reservation_usd"]) != cost
                    or entry.get("normalized_usage") != usage):
                self._halt(key, "record_corrupt")
        else:
            entry.update(status="settled", cost_or_reservation_usd=str(cost), normalized_usage=usage,
                         input_tokens=usage["input_tokens"], output_tokens_including_thinking=usage["output_tokens_including_thinking"],
                         proposal=asdict(proposal))
            # Response, usage, cost and proposal publish as one recoverable record.
            write_json(self.ledger_path, self.ledger)
        return proposal

    def _obtain(self, request):
        from .journal import capture_usage, ledger_total, observed_liability
        key, c = canonical_hash(request), self.config["gemini"]
        if key in self.ledger:
            return self._recover(key, request)
        # Even a different hash must not bypass an unresolved paid attempt.
        if any(entry.get("status") != "settled" for entry in self.ledger.values()):
            raise RuntimeError("unresolved journal entry blocks new paid calls")
        pricing = self._pricing()
        if date.today().isoformat() > pricing["valid_through"]:
            raise ValueError("pricing snapshot expired; no request sent")
        prompt = PROMPT + json.dumps({k: request[k] for k in (
            "as_of_ms", "features", "risk_context", "execution_assumptions")}, sort_keys=True)
        if len(prompt.encode()) + len(json.dumps(SCHEMA).encode()) > c["max_input_bytes"]:
            raise ValueError("bounded input exceeded; no request sent")
        reservation = Decimal(c["reservation_usd_per_request"])
        bounded_cost = (Decimal(c["max_input_bytes"]) * Decimal(pricing["input_usd_per_million"]) +
                        Decimal(c["max_output_tokens"]) * Decimal(pricing["output_usd_per_million"])) / 1_000_000
        if not reservation.is_finite() or reservation < bounded_cost:
            raise ValueError("reservation does not cover bounded request; no request sent")
        if ledger_total(self.ledger) + reservation > self.budget:
            raise RuntimeError("shared Gemini budget exhausted; no request sent")
        from google.genai import types
        generation = types.GenerateContentConfig(temperature=c["temperature"], max_output_tokens=c["max_output_tokens"],
            thinking_config=types.ThinkingConfig(thinking_level=c["thinking_level"]),
            response_mime_type="application/json", response_json_schema=SCHEMA,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
        client = self._client_for_send()
        self.ledger[key] = {"format_version": 2, "request": request, "request_hash": key,
            "status": "reserved_or_unknown", "reservation_usd": str(reservation),
            "cost_or_reservation_usd": str(reservation), "pricing": pricing}
        write_json(self.ledger_path, self.ledger)
        started = perf_counter()
        try:
            self.sent_count += 1
            response = client.models.generate_content(model=self.model, contents=prompt, config=generation)
            record = {"text": response.text if isinstance(response.text, str) else None,
                "returned_model": getattr(response, "model_version", None),
                "latency_ms": max(1, math.ceil((perf_counter() - started) * 1000)),
                "usage": capture_usage(response.usage_metadata)}
        except Exception:
            # No SDK exception or HTTP/credential details enter artifacts or logs.
            raise RuntimeError("Gemini outcome uncertain; reservation retained, automatic retry forbidden") from None
        self.ledger[key].update(status="response_received", response=record, response_hash=canonical_hash(record),
            cost_or_reservation_usd=str(max(reservation, observed_liability(record["usage"], pricing))))
        # If this save fails, the old reservation remains unresolved. Never resend.
        write_json(self.ledger_path, self.ledger)
        return self._recover(key, request)

    def propose(self, as_of, features, risk=None):
        from .journal import read_ledger
        request = self.identity(as_of, features, risk)
        key = canonical_hash(request)
        path = self.cache_root / f"{key}.json"
        with budget_lock(self.cache_root / "budget.lock"):
            self.ledger = read_ledger(self.ledger_path)
            existed = key in self.ledger
            if path.exists() and not existed:
                raise RuntimeError("orphan response cache has no billing journal; no automatic resend")
            proposal = self._obtain(request)
            expected = {"request": request, "input_hash": key, "proposal": asdict(proposal)}
            try:
                hit = path.exists() and json.loads(path.read_text(encoding="utf-8")) == expected
            except (OSError, ValueError):
                hit = False
            if not hit:
                # A crash here is recoverable from the settled primary journal.
                write_json(path, expected)
            self.audit.append({"as_of_ms": as_of, "input_hash": key, "cache_hit": hit,
                "restored_from_journal": existed and not hit, "risk_supplied": risk is not None,
                "proposal": asdict(proposal)})
            return proposal

    def generate(self, request):
        from .journal import read_ledger
        with budget_lock(self.cache_root / "budget.lock"):
            self.ledger = read_ledger(self.ledger_path)
            proposal = self._obtain(request)
            record = self.ledger[canonical_hash(request)]["response"]
            try:
                raw = json.loads(record.get("text") or "")
            except (ValueError, TypeError):
                raw = None
            return raw, record["latency_ms"], proposal.cost_usd, proposal.model
