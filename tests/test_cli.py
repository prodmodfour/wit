"""Smoke tests for the Wit command-line interface."""

from typer.testing import CliRunner

from wit import __version__
from wit.cli import app

runner = CliRunner()


def test_cli_help_and_version() -> None:
    help_result = runner.invoke(app, ["--help"])

    assert help_result.exit_code == 0, help_result.output
    assert "Safe, local-first television library operations." in help_result.output
    assert "--version" in help_result.output

    version_result = runner.invoke(app, ["--version"])

    assert version_result.exit_code == 0, version_result.output
    assert version_result.output == f"wit {__version__}\n"
