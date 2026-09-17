from __future__ import annotations

import json
from pathlib import Path

from llm_position_management.cli import main


def _config() -> dict[str, object]:
    return {
        "schema_version": 1,
        "run_id": "fixture",
        "decision_interval_ms": 300000,
        "max_response_age_ms": 60000,
        "max_model_cost_usd": "0",
        "limits": {
            "exposure_anchor_usd": "250",
            "max_position_notional_usd": "250",
            "min_notional_usd": "10",
            "risk_per_position_pct": "10",
            "max_daily_loss_pct": "20",
            "max_drawdown_pct": "25",
            "max_hold_ms": 86400000,
        },
        "costs": {"fee_rate": "0", "spread_bps": "0", "slippage_bps": "0"},
        "initial_account": {"cash": "1000", "equity": "1000"},
        "ticks": [
            {"timestamp_ms": 0, "open_price": "50000", "high_price": "50000", "low_price": "50000", "close_price": "50000"},
            {"timestamp_ms": 300000, "open_price": "51000", "high_price": "51000", "low_price": "51000", "close_price": "51000"},
        ],
        "decisions": {
            "fixture:0": {
                "schema_version": 1,
                "decision_id": "fixture:0",
                "intent": "set_target",
                "target_fraction": "0.5",
                "stop_price": "49000",
                "thesis": "fixture",
                "invalidation": "fixture",
            },
            "fixture:300000": {
                "schema_version": 1,
                "decision_id": "fixture:300000",
                "intent": "hold",
                "target_fraction": None,
                "stop_price": None,
                "thesis": "fixture",
                "invalidation": "fixture",
            },
        },
    }


def test_replay_cli_writes_an_atomic_complete_offline_run(tmp_path: Path, capsys) -> None:
    config = tmp_path / "fixture.json"
    output = tmp_path / "fixture-run"
    config.write_text(json.dumps(_config()), encoding="utf-8")

    assert main(["replay", "--config", str(config), "--output", str(output)]) == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "ok"
    assert summary["decision_count"] == 2
    assert json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))["status"] == "complete"
    assert len((output / "observations.jsonl").read_text(encoding="utf-8").splitlines()) == 2
    assert "final_equity" in (output / "report.md").read_text(encoding="utf-8")


def test_replay_cli_refuses_to_overwrite_a_completed_run(tmp_path: Path) -> None:
    config = tmp_path / "fixture.json"
    output = tmp_path / "fixture-run"
    config.write_text(json.dumps(_config()), encoding="utf-8")
    output.mkdir()
    (output / "run_manifest.json").write_text('{"status":"complete"}\n', encoding="utf-8")

    assert main(["replay", "--config", str(config), "--output", str(output)]) == 2
