from __future__ import annotations

import json
from pathlib import Path

import pytest

from hyperliquid_ai_trader.research.cli import main
from hyperliquid_ai_trader.research.config import (
    ResearchConfigError,
    load_research_config,
    require_paid_api_permission,
)


def _payload(*, allow_paid_api: bool = False, budget_usd: str = "0") -> dict[str, object]:
    return {
        "schema_version": 1,
        "experiment_id": "development-v1",
        "market": {
            "venue": "hyperliquid_mainnet_public",
            "symbol": "BTC",
            "interval": "1m",
        },
        "feature_set": "common_candles_v1",
        "api": {
            "allow_paid_api": allow_paid_api,
            "budget_usd": budget_usd,
            "trader_model": "gemini-2.5-flash-lite",
            "reviewer_model": "gemini-3.6-flash",
        },
        "execution": {
            "model_delay_ms": 1000,
            "max_arrival_delay_ms": 60000,
            "max_hold_ms": 300000,
            "fee_rate": "0.00045",
            "spread_bps": "2",
            "slippage_bps": "1",
        },
    }


def test_config_defaults_to_no_paid_api_and_cli_prints_safe_summary(tmp_path, capsys) -> None:
    path = tmp_path / "development.json"
    path.write_text(json.dumps(_payload()), encoding="utf-8")

    config = load_research_config(path)

    assert config.market_venue == "hyperliquid_mainnet_public"
    assert config.allow_paid_api is False
    with pytest.raises(ResearchConfigError, match="allow_paid_api"):
        require_paid_api_permission(config)
    assert main(["validate-config", "--config", str(path)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary == {
        "allow_paid_api": False,
        "budget_usd": "0",
        "experiment_id": "development-v1",
        "market": "hyperliquid_mainnet_public:BTC",
        "status": "ok",
    }


def test_config_rejects_budget_when_paid_api_is_disabled(tmp_path) -> None:
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(_payload(budget_usd="1")), encoding="utf-8")

    with pytest.raises(ResearchConfigError, match="budget_usd"):
        load_research_config(path)


def test_checked_in_development_config_has_no_paid_api_or_secret_values() -> None:
    path = Path(__file__).resolve().parents[1] / "configs" / "research" / "development.json"

    config = load_research_config(path)

    assert config.allow_paid_api is False
    assert config.budget_usd == 0
