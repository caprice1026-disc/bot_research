import pytest

from weather_research.cli import build_parser, main


def test_cli_help_lists_research_commands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["--help"])

    assert raised.value.code == 0
    assert "source-audit" in capsys.readouterr().out


def test_cli_parser_accepts_gefs_target_day_collection() -> None:
    args = build_parser().parse_args(
        [
            "collect-gefs-target",
            "--target-date",
            "2026-01-06",
            "--issue-time",
            "2026-01-05T12:00:00Z",
        ]
    )

    assert args.command == "collect-gefs-target"
    assert args.target_date == "2026-01-06"
    assert args.issue_time == "2026-01-05T12:00:00Z"


def test_cli_parser_accepts_price_target_day_collection() -> None:
    args = build_parser().parse_args(
        [
            "collect-prices-target",
            "--target-date",
            "2026-01-06",
            "--start-time",
            "2026-01-05T12:00:00Z",
            "--end-time",
            "2026-01-07T05:00:00Z",
        ]
    )

    assert args.command == "collect-prices-target"
    assert args.target_date == "2026-01-06"
    assert args.start_time == "2026-01-05T12:00:00Z"
    assert args.end_time == "2026-01-07T05:00:00Z"


def test_cli_parser_accepts_all_market_day_gefs_collection_and_both_backtest_levels() -> None:
    gefs_args = build_parser().parse_args(["collect-gefs-market-days", "--max-days", "2"])
    backtest_args = build_parser().parse_args(["run-backtest", "--level", "both"])

    assert gefs_args.max_days == 2
    assert gefs_args.issue_cycle_hour == 12
    assert backtest_args.level == "both"
