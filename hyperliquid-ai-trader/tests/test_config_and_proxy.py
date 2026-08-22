from __future__ import annotations

from decimal import Decimal

import pytest

from hyperliquid_ai_trader.config import ConfigError, Settings
from hyperliquid_ai_trader.proxy import clear_invalid_loopback_proxies


def test_settings_apply_aggressive_testnet_defaults() -> None:
    settings = Settings.from_mapping(
        {
            "HL_test_wallet": "0x" + "1" * 40,
            "HL_test_wallet_private_key": "0x" + "2" * 64,
            "GEMINI_API_KEY": "gemini-test-key",
        }
    )

    assert settings.network == "testnet"
    assert settings.coin == "BTC"
    assert settings.margin_mode == "isolated"
    assert settings.leverage == 5
    assert settings.risk_per_trade_pct == Decimal("1.0")
    assert settings.max_position_notional_usd == Decimal("250")
    assert settings.max_daily_loss_pct == Decimal("20")
    assert settings.max_drawdown_pct == Decimal("25")
    assert settings.trader_model == "gemini-3.6-flash"
    assert settings.execution_mode == "dry_run"


def test_settings_reject_mainnet_even_when_explicitly_configured() -> None:
    with pytest.raises(ConfigError, match="testnet only"):
        Settings.from_mapping(
            {
                "HL_test_wallet": "0x" + "1" * 40,
                "HL_test_wallet_private_key": "0x" + "2" * 64,
                "GEMINI_API_KEY": "gemini-test-key",
                "HL_NETWORK": "mainnet",
            }
        )


def test_settings_reject_invalid_stop_loss_range() -> None:
    with pytest.raises(ConfigError, match="stop loss"):
        Settings.from_mapping(
            {
                "HL_test_wallet": "0x" + "1" * 40,
                "HL_test_wallet_private_key": "0x" + "2" * 64,
                "GEMINI_API_KEY": "gemini-test-key",
                "MIN_STOP_LOSS_PCT": "2",
                "MAX_STOP_LOSS_PCT": "1",
            }
        )


def test_shared_gemini_model_from_env_applies_to_both_agents() -> None:
    settings = Settings.from_mapping(
        {
            "HL_test_wallet": "0x" + "1" * 40,
            "HL_test_wallet_private_key": "0x" + "2" * 64,
            "GEMINI_API_KEY": "gemini-test-key",
            "GEMINI_MODEL": "gemini-custom-free-tier",
        }
    )

    assert settings.trader_model == "gemini-custom-free-tier"
    assert settings.reviewer_model == "gemini-custom-free-tier"


def test_proxy_cleanup_removes_only_known_dead_loopback_values() -> None:
    environ = {
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "ALL_PROXY": "http://127.0.0.1:9",
        "NO_PROXY": "localhost,127.0.0.1,::1",
        "CORPORATE_PROXY": "http://proxy.example:8080",
    }

    removed = clear_invalid_loopback_proxies(environ)

    assert removed == ["ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY"]
    assert environ == {
        "NO_PROXY": "localhost,127.0.0.1,::1",
        "CORPORATE_PROXY": "http://proxy.example:8080",
    }


def test_proxy_cleanup_preserves_nonmatching_proxy() -> None:
    environ = {"HTTPS_PROXY": "http://proxy.example:8080"}

    assert clear_invalid_loopback_proxies(environ) == []
    assert environ["HTTPS_PROXY"] == "http://proxy.example:8080"
