import pytest

from weather_research.cli import main


def test_cli_help_lists_research_commands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["--help"])

    assert raised.value.code == 0
    assert "source-audit" in capsys.readouterr().out
