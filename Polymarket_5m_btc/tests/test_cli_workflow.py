from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_fixture_cli_writes_quality_and_lead_lag_reports(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "btc5m",
            "fixture",
            "--output-root",
            str(tmp_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    quality = json.loads((tmp_path / "reports" / "fixture_quality.json").read_text())
    lead_lag = json.loads((tmp_path / "reports" / "fixture_lead_lag.json").read_text())
    assert quality["binance"]["valid"] is True
    assert lead_lag["status"] == "exploratory"
    assert lead_lag["event_count"] == 1
    assert (tmp_path / "normalized" / "binance.parquet").exists()
    assert (tmp_path / "normalized" / "polymarket.parquet").exists()
    markdown = (tmp_path / "reports" / "fixture_lead_lag.md").read_text()
    assert "exploratory" in markdown


def test_validate_cli_preserves_insufficient_historical_receive_time(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "historical.jsonl"
    input_path.write_text(
        json.dumps(
            {
                "source": "binance",
                "local_receive_ts": None,
                "source_event_ts": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "quality.json"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "btc5m",
            "validate",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    quality = json.loads(output_path.read_text(encoding="utf-8"))
    assert quality["valid"] is False
    assert quality["missing_local_receive_count"] == 1
