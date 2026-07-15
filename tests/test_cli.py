"""Smoke tests for the Wit command-line interface."""

import re

from typer.testing import CliRunner

from wit import __version__
from wit.cli import app

runner = CliRunner()
_ANSI_STYLE_PATTERN = re.compile(r"\x1b\[[0-9;]*m")


def test_cli_help_and_version() -> None:
    help_result = runner.invoke(app, ["--help"], env={"FORCE_COLOR": "1"})

    assert help_result.exit_code == 0, help_result.output
    help_output = _ANSI_STYLE_PATTERN.sub("", help_result.output)
    assert "Safe, local-first television library operations." in help_output
    assert "--version" in help_output

    version_result = runner.invoke(app, ["--version"])

    assert version_result.exit_code == 0, version_result.output
    assert version_result.output == f"wit {__version__}\n"
