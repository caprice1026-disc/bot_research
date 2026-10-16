"""Strict no-tools usage accounting for the frozen text-only Gemini experiment."""
from decimal import Decimal
import json

USAGE_FIELDS = ("prompt_token_count", "candidates_token_count", "thoughts_token_count",
                "tool_use_prompt_token_count", "total_token_count", "cached_content_token_count")


def capture_usage(usage):
    if usage is None:
        return None
    result = {}
    for field in USAGE_FIELDS:
        value = getattr(usage, field, None)
        result[field] = value if value is None or isinstance(value, (str, int, float, bool)) else {
            "invalid_type": type(value).__name__}
    return result


def count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def validate_usage(raw, config):
    if not isinstance(raw, dict):
        raise ValueError("usage missing")
    prompt, candidates, total = (raw.get(name) for name in (
        "prompt_token_count", "candidates_token_count", "total_token_count"))
    if not all(count(value) for value in (prompt, candidates, total)):
        raise ValueError("required usage counters missing or invalid")
    tool = raw.get("tool_use_prompt_token_count")
    if tool is not None and (not count(tool) or tool != 0):
        raise ValueError("unexpected tool usage in no-tools request")
    thoughts = raw.get("thoughts_token_count")
    inferred = thoughts is None
    if inferred:
        if total != prompt + candidates:
            raise ValueError("omitted thinking is not provably zero")
        thoughts = 0
    if not count(thoughts) or total != prompt + candidates + thoughts:
        raise ValueError("usage total does not match component sum")
    cached = raw.get("cached_content_token_count")
    if cached is not None and (not count(cached) or cached > prompt):
        raise ValueError("cached tokens are not a valid subset of prompt")
    output = candidates + thoughts
    if prompt > config["max_input_bytes"] or output > config["max_output_tokens"]:
        raise ValueError("reported usage exceeds frozen request bound")
    # Cached tokens remain conservatively charged at the full prompt rate.
    return {"input_tokens": prompt, "output_tokens_including_thinking": output,
            "total_tokens": total, "thinking_inferred_zero": inferred,
            "cached_prompt_tokens": cached or 0, "tool_prompt_tokens": 0}


def usage_cost(normalized, pricing):
    return (Decimal(normalized["input_tokens"]) * Decimal(pricing["input_usd_per_million"]) +
            Decimal(normalized["output_tokens_including_thinking"]) * Decimal(pricing["output_usd_per_million"])) / 1_000_000


def observed_liability(raw, pricing):
    """Never shrink a reservation on malformed usage; use any larger observed bound."""
    if not isinstance(raw, dict):
        return Decimal(0)
    components = sum(value for key, value in raw.items()
                     if key in USAGE_FIELDS[:4] and count(value))
    total = raw.get("total_token_count")
    tokens = max(components, total if count(total) else 0)
    maximum_rate = max(Decimal(pricing["input_usd_per_million"]), Decimal(pricing["output_usd_per_million"]))
    return Decimal(tokens) * maximum_rate / 1_000_000


def read_ledger(path):
    ledger = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(ledger, dict):
        raise ValueError("invalid budget journal")
    for entry in ledger.values():
        if not isinstance(entry, dict):
            raise ValueError("invalid budget entry")
        amount = Decimal(entry["cost_or_reservation_usd"])
        if not amount.is_finite() or amount < 0:
            raise ValueError("invalid budget amount")
    return ledger


def ledger_total(ledger):
    return sum((Decimal(entry["cost_or_reservation_usd"]) for entry in ledger.values()), Decimal(0))
