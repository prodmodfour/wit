"""Command-line entry point for Wit."""

import asyncio
from typing import Annotated

import typer

from wit import __version__
from wit.clients import ServiceHealthResult, ServiceHealthState, ServiceName
from wit.config import ConfigurationError, load_settings
from wit.doctor import DoctorReport, LocalPathCheck, LocalPathState, run_doctor

app = typer.Typer(
    help="Safe, local-first television library operations.",
    no_args_is_help=True,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"wit {__version__}")
        raise typer.Exit


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Show the version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Run Wit television library operations."""


@app.command()
def doctor() -> None:
    """Validate configuration, local paths, and service connectivity."""
    try:
        settings = load_settings()
    except ConfigurationError as error:
        typer.echo(f"Configuration: FAILED - {error}")
        typer.echo(
            "Next step: set the required WIT_* values or WIT_CONFIG_FILE; "
            "see docs/configuration.md."
        )
        raise typer.Exit(code=1) from None

    typer.echo("Configuration: OK - required settings are valid")
    report = asyncio.run(run_doctor(settings))
    _render_doctor_report(report)
    if not report.successful:
        raise typer.Exit(code=1)


def _render_doctor_report(report: DoctorReport) -> None:
    for path_check in report.local_paths:
        _render_local_path_check(path_check)
    for service_result in report.services:
        _render_service_health(service_result)

    if report.successful:
        typer.echo("Overall: OK - all required checks passed")
    else:
        typer.echo("Overall: FAILED - one or more required checks failed")


def _render_local_path_check(check: LocalPathCheck) -> None:
    label = check.name.value.capitalize()
    if check.state is LocalPathState.READY:
        typer.echo(f"{label}: OK - {check.summary}")
        return

    typer.echo(f"{label}: FAILED - {check.summary}. {_local_path_failure_guidance(check.state)}")


def _local_path_failure_guidance(state: LocalPathState) -> str:
    if state is LocalPathState.MISSING:
        return "Create WIT_STATE_DIR with owner-only permissions before using Wit."
    if state is LocalPathState.INACCESSIBLE:
        return "Grant the current user read, write, and search access to WIT_STATE_DIR."
    return "Set WIT_STATE_DIR to an existing, non-symlink local directory."


def _render_service_health(result: ServiceHealthResult) -> None:
    label = result.service.value.capitalize()
    version = f" (version {result.version})" if result.version is not None else ""
    if result.state is ServiceHealthState.HEALTHY:
        typer.echo(f"{label}: OK - {result.summary}{version}")
        return

    typer.echo(
        f"{label}: FAILED - {result.summary}{version}. "
        f"{_service_failure_guidance(result.service, result.state)}"
    )


def _service_failure_guidance(
    service: ServiceName,
    state: ServiceHealthState,
) -> str:
    label = service.value.capitalize()
    setting_prefix = service.value.upper()

    if state is ServiceHealthState.UNAVAILABLE:
        return f"Verify WIT_{setting_prefix}_URL and that {label} is running."
    if state is ServiceHealthState.UNAUTHORISED:
        if service is ServiceName.SEERR:
            return "Verify that WIT_SEERR_URL exposes Seerr's status endpoint."
        return f"Verify WIT_{setting_prefix}_API_KEY and its service permissions."
    return f"Inspect the {label} dashboard and logs for health details."
