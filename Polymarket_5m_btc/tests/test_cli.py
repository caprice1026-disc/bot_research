from __future__ import annotations

import subprocess
import sys


def test_cli_help_mentions_research_commands() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "btc5m", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    for command in (
        "collect",
        "compact",
        "validate",
        "fixture",
        "select-5m",
        "lead-lag",
        "event-study",
    ):
        assert command in result.stdout
