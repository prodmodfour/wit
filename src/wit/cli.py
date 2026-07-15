"""Command-line entry point for Wit."""

import asyncio
import re
import sys
from datetime import UTC, datetime
from typing import Annotated

import typer

from wit import __version__
from wit.apply import ApplyPlanResult, apply_download_plan
from wit.clients import (
    ServiceHealthResult,
    ServiceHealthState,
    ServiceName,
    SonarrClient,
    TvmazeClient,
)
from wit.config import ConfigurationError, WitSettings, load_settings
from wit.doctor import DoctorReport, LocalPathCheck, LocalPathState, run_doctor
from wit.errors import WitError
from wit.plan_store import PlanStore
from wit.planning import (
    ShowCandidateSelectionRequiredError,
    build_download_plan,
    generate_plan_identifier,
)
from wit.plans import DownloadPlan
from wit.selection import EpisodeSelector, InvalidEpisodeSelectorError

_MAX_EPISODE_COORDINATE = 2_147_483_647
_EPISODE_RANGE_PATTERN = re.compile(r"([1-9][0-9]{0,9})-([1-9][0-9]{0,9})\Z")

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


@app.command("plan")
def plan_command(
    query: Annotated[
        str,
        typer.Argument(help="Show title to resolve through read-only TVmaze metadata."),
    ],
    first: Annotated[
        int | None,
        typer.Option(
            "--first",
            min=1,
            max=_MAX_EPISODE_COORDINATE,
            help="Select the first N aired regular episodes.",
        ),
    ] = None,
    season: Annotated[
        int | None,
        typer.Option(
            "--season",
            min=1,
            max=_MAX_EPISODE_COORDINATE,
            help="Limit --first or --episodes to this season.",
        ),
    ] = None,
    episode_range: Annotated[
        str | None,
        typer.Option(
            "--episodes",
            metavar="START-END",
            help="Select an inclusive aired episode range; requires --season.",
        ),
    ] = None,
    all_aired: Annotated[
        bool,
        typer.Option("--all-aired", help="Select all currently aired regular episodes."),
    ] = False,
    year: Annotated[
        int | None,
        typer.Option(
            "--year",
            min=1,
            max=9999,
            help="Use a premiere year only to disambiguate matching titles.",
        ),
    ] = None,
    candidate: Annotated[
        int | None,
        typer.Option(
            "--candidate",
            min=1,
            max=_MAX_EPISODE_COORDINATE,
            metavar="TVMAZE-ID",
            help="Explicitly select a TVmaze ID from an ambiguous candidate list.",
        ),
    ] = None,
) -> None:
    """Create and save a read-only, inspectable episode download plan."""
    selector = _build_episode_selector(
        first=first,
        season=season,
        episode_range=episode_range,
        all_aired=all_aired,
    )

    try:
        settings = load_settings()
    except ConfigurationError as error:
        typer.echo(f"Planning failed: {error}")
        typer.echo(
            "Next step: set the required WIT_* values or WIT_CONFIG_FILE; "
            "see docs/configuration.md."
        )
        raise typer.Exit(code=1) from None

    try:
        plan = asyncio.run(
            _build_read_only_plan(
                settings,
                query=query,
                selector=selector,
                show_year=year,
                candidate_tvmaze_id=candidate,
            )
        )
    except ShowCandidateSelectionRequiredError as error:
        _render_show_candidates(error)
        raise typer.Exit(code=1) from None
    except WitError as error:
        typer.echo(f"Planning failed: {error}")
        raise typer.Exit(code=1) from None

    # The complete immutable plan is deliberately rendered before persistence.
    typer.echo(plan.render())
    try:
        PlanStore(settings.state_dir).save(plan)
    except WitError as error:
        typer.echo(f"Planning failed: {error}")
        raise typer.Exit(code=1) from None
    typer.echo(f"Saved plan ID: {plan.plan_id}")


async def _build_read_only_plan(
    settings: WitSettings,
    *,
    query: str,
    selector: EpisodeSelector,
    show_year: int | None,
    candidate_tvmaze_id: int | None,
) -> DownloadPlan:
    async with _create_tvmaze_client(settings) as client:
        return await build_download_plan(
            client,
            query=query,
            selector=selector,
            show_year=show_year,
            candidate_tvmaze_id=candidate_tvmaze_id,
            clock=_utc_now,
            plan_identifier_factory=generate_plan_identifier,
        )


def _create_tvmaze_client(settings: WitSettings) -> TvmazeClient:
    return TvmazeClient(
        base_url=str(settings.tvmaze.url),
        connect_timeout_seconds=settings.http.connect_timeout_seconds,
        read_timeout_seconds=settings.http.read_timeout_seconds,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _build_episode_selector(
    *,
    first: int | None,
    season: int | None,
    episode_range: str | None,
    all_aired: bool,
) -> EpisodeSelector:
    range_start: int | None = None
    range_end: int | None = None
    if episode_range is not None:
        match = _EPISODE_RANGE_PATTERN.fullmatch(episode_range)
        if match is None:
            raise typer.BadParameter(
                "--episodes must use the positive inclusive form START-END",
                param_hint="--episodes",
            )
        range_start, range_end = (int(value) for value in match.groups())
        if range_start > _MAX_EPISODE_COORDINATE or range_end > _MAX_EPISODE_COORDINATE:
            raise typer.BadParameter(
                f"--episodes values must not exceed {_MAX_EPISODE_COORDINATE}",
                param_hint="--episodes",
            )

    try:
        return EpisodeSelector(
            first_count=first,
            season_number=season,
            range_start=range_start,
            range_end=range_end,
            all_aired=all_aired,
        )
    except InvalidEpisodeSelectorError as error:
        raise typer.BadParameter(str(error), param_hint="episode selector") from None


def _render_show_candidates(error: ShowCandidateSelectionRequiredError) -> None:
    typer.echo(f"Planning failed: {error}")
    typer.echo("Candidates:")
    for candidate in error.candidates:
        show = candidate.show
        year = str(show.premiere_year) if show.premiere_year is not None else "year unknown"
        tvdb_id = str(show.tvdb_id) if show.tvdb_id is not None else "missing"
        typer.echo(
            f"  TVmaze ID {show.tvmaze_id}: {_safe_terminal_text(show.title)} "
            f"({year}); TVDB ID {tvdb_id}"
        )
    typer.echo("Retry with --candidate <TVMAZE-ID> from this list.")


def _safe_terminal_text(value: str) -> str:
    return "".join(
        character if ord(character) >= 32 and ord(character) != 127 else "?" for character in value
    )


@app.command("apply")
def apply_command(
    plan_id: Annotated[
        str,
        typer.Argument(help="Stored download-plan ID to apply through Sonarr."),
    ],
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            help="Confirm without prompting; required when standard input is not interactive.",
        ),
    ] = False,
) -> None:
    """Confirm and apply one stored plan with targeted Sonarr operations."""
    try:
        settings = load_settings()
    except ConfigurationError as error:
        typer.echo(f"Apply failed: {error}")
        typer.echo(
            "Next step: set the required WIT_* values or WIT_CONFIG_FILE; "
            "see docs/configuration.md."
        )
        raise typer.Exit(code=1) from None

    try:
        plan = PlanStore(settings.state_dir).load(plan_id)
    except WitError as error:
        typer.echo(f"Apply failed: {error}")
        raise typer.Exit(code=1) from None

    # Re-render the immutable stored plan immediately before confirmation so an
    # operator can verify every coordinate that will be sent to Sonarr.
    typer.echo(plan.render())
    if not yes:
        if not _is_interactive_input():
            typer.echo("Apply failed: non-interactive use requires --yes; no Sonarr changes made.")
            raise typer.Exit(code=1)
        if not typer.confirm("Apply this stored plan through Sonarr?", default=False):
            typer.echo("Apply cancelled; no Sonarr changes made.")
            raise typer.Exit(code=1)

    try:
        result = asyncio.run(_apply_plan_through_sonarr(settings, plan))
    except WitError as error:
        typer.echo(f"Apply failed: {error}")
        raise typer.Exit(code=1) from None

    _render_apply_result(result)


async def _apply_plan_through_sonarr(
    settings: WitSettings,
    plan: DownloadPlan,
) -> ApplyPlanResult:
    async with _create_sonarr_client(settings) as client:
        return await apply_download_plan(
            client,
            plan=plan,
            root_folder_id=settings.sonarr.root_folder_id,
            quality_profile_id=settings.sonarr.quality_profile_id,
        )


def _create_sonarr_client(settings: WitSettings) -> SonarrClient:
    credential = settings.sonarr.api_key
    return SonarrClient(
        base_url=str(settings.sonarr.url),
        api_key=credential,
        connect_timeout_seconds=settings.http.connect_timeout_seconds,
        read_timeout_seconds=settings.http.read_timeout_seconds,
    )


def _is_interactive_input() -> bool:
    try:
        return bool(sys.stdin.isatty())
    except (AttributeError, OSError):
        return False


def _render_apply_result(result: ApplyPlanResult) -> None:
    series_source = "newly added" if result.series_created else "existing"
    episode_word = "episode" if result.episode_count == 1 else "episodes"
    typer.echo(
        f"Applied plan {result.plan_id}: monitored {result.episode_count} {episode_word} "
        f"and submitted one targeted search using {series_source} Sonarr series "
        f"{result.series.sonarr_id}."
    )
    typer.echo(f"Sonarr command ID: {result.command.command_id} ({result.command.state.value})")


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
