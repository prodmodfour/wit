"""Command-line entry point for Wit."""

import asyncio
import re
import sys
from datetime import UTC, datetime
from typing import Annotated

import typer

from wit import __version__
from wit.apply import (
    ApplyDiscrepancyConfirmation,
    ApplyPlanDiscrepancy,
    ApplyPlanRejectedError,
    ApplyPlanResult,
    apply_download_plan,
    validate_download_plan_age,
)
from wit.clients import (
    JellyfinClient,
    ServiceHealthResult,
    ServiceHealthState,
    ServiceName,
    SonarrClient,
    TvmazeClient,
)
from wit.config import ConfigurationError, WitSettings, load_settings
from wit.doctor import DoctorReport, LocalPathCheck, LocalPathState, run_doctor
from wit.errors import WitError
from wit.output import (
    JsonObject,
    JsonOutputCommand,
    JsonOutputEnvelope,
    JsonOutputIssue,
    JsonValue,
)
from wit.plan_store import PlanStore
from wit.planning import (
    ShowCandidateSelectionRequiredError,
    build_download_plan,
    generate_plan_identifier,
)
from wit.plans import DownloadPlan
from wit.selection import EpisodeSelector, InvalidEpisodeSelectorError
from wit.status import (
    CombinedRequestStatusResult,
    RequestOverallState,
    get_combined_request_status,
)

_MAX_EPISODE_COORDINATE = 2_147_483_647
_EPISODE_RANGE_PATTERN = re.compile(r"([1-9][0-9]{0,9})-([1-9][0-9]{0,9})\Z")
_JSON_OPTION_HELP = "Write one versioned JSON envelope to standard output."

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
    json_output: Annotated[
        bool,
        typer.Option("--json", help=_JSON_OPTION_HELP),
    ] = False,
) -> None:
    """Create and save a read-only, inspectable episode download plan."""
    try:
        selector = _build_episode_selector(
            first=first,
            season=season,
            episode_range=episode_range,
            all_aired=all_aired,
        )
    except typer.BadParameter as error:
        if not json_output:
            raise
        _emit_json_output(
            command=JsonOutputCommand.PLAN,
            success=False,
            data=_plan_command_json_data(query),
            errors=(_output_issue("invalid-arguments", str(error)),),
        )
        raise typer.Exit(code=2) from None

    try:
        settings = load_settings()
    except ConfigurationError as error:
        if json_output:
            _emit_json_output(
                command=JsonOutputCommand.PLAN,
                success=False,
                data=_plan_command_json_data(query),
                errors=(_configuration_output_issue(error),),
            )
        else:
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
        if json_output:
            _emit_json_output(
                command=JsonOutputCommand.PLAN,
                success=False,
                data=_plan_command_json_data(query, candidate_error=error),
                errors=(_output_issue("candidate-selection-required", str(error)),),
            )
        else:
            _render_show_candidates(error)
        raise typer.Exit(code=1) from None
    except WitError as error:
        if json_output:
            _emit_json_output(
                command=JsonOutputCommand.PLAN,
                success=False,
                data=_plan_command_json_data(query),
                errors=(_output_issue("planning-failed", str(error)),),
            )
        else:
            typer.echo(f"Planning failed: {error}")
        raise typer.Exit(code=1) from None

    # Human output preserves the inspect-before-persistence workflow. JSON mode
    # emits only one final document, which contains the same complete plan.
    if not json_output:
        typer.echo(plan.render())
    try:
        PlanStore(settings.state_dir).save(plan)
    except WitError as error:
        if json_output:
            _emit_json_output(
                command=JsonOutputCommand.PLAN,
                success=False,
                data=_plan_command_json_data(query, plan=plan),
                errors=(_output_issue("plan-save-failed", str(error)),),
            )
        else:
            typer.echo(f"Planning failed: {error}")
        raise typer.Exit(code=1) from None

    if json_output:
        _emit_json_output(
            command=JsonOutputCommand.PLAN,
            success=True,
            data=_plan_command_json_data(query, plan=plan, saved=True),
        )
    else:
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
    allow_stale: Annotated[
        bool,
        typer.Option(
            "--allow-stale",
            help="Explicitly allow a stored plan older than the default seven-day limit.",
        ),
    ] = False,
    allow_mismatch: Annotated[
        bool,
        typer.Option(
            "--allow-mismatch",
            help="Explicitly confirm material current Sonarr metadata differences.",
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help=_JSON_OPTION_HELP),
    ] = False,
) -> None:
    """Confirm and apply one stored plan with targeted Sonarr operations."""
    try:
        settings = load_settings()
    except ConfigurationError as error:
        if json_output:
            _emit_json_output(
                command=JsonOutputCommand.APPLY,
                success=False,
                data=None,
                errors=(_configuration_output_issue(error),),
            )
        else:
            typer.echo(f"Apply failed: {error}")
            typer.echo(
                "Next step: set the required WIT_* values or WIT_CONFIG_FILE; "
                "see docs/configuration.md."
            )
        raise typer.Exit(code=1) from None

    try:
        plan = PlanStore(settings.state_dir).load(plan_id)
    except WitError as error:
        if json_output:
            _emit_json_output(
                command=JsonOutputCommand.APPLY,
                success=False,
                data=None,
                errors=(_output_issue("plan-load-failed", str(error)),),
            )
        else:
            typer.echo(f"Apply failed: {error}")
        raise typer.Exit(code=1) from None

    # Human mode re-renders the immutable stored plan before confirmation. JSON
    # mode carries the complete plan in its one final envelope instead.
    if not json_output:
        typer.echo(plan.render())
    reference_time = _utc_now()
    try:
        validate_download_plan_age(
            plan,
            as_of=reference_time,
            allow_stale=allow_stale,
        )
    except ApplyPlanRejectedError as error:
        if json_output:
            _emit_json_output(
                command=JsonOutputCommand.APPLY,
                success=False,
                data=_apply_command_json_data(plan, rejection=error),
                warnings=_apply_json_warnings(
                    allow_stale=allow_stale,
                    allow_mismatch=allow_mismatch,
                    discrepancies=(),
                ),
                errors=(_output_issue("apply-rejected", str(error)),),
            )
        else:
            _render_apply_rejection(error)
        raise typer.Exit(code=1) from None

    if not yes:
        if json_output:
            _emit_json_output(
                command=JsonOutputCommand.APPLY,
                success=False,
                data=_apply_command_json_data(plan),
                warnings=_apply_json_warnings(
                    allow_stale=allow_stale,
                    allow_mismatch=allow_mismatch,
                    discrepancies=(),
                ),
                errors=(
                    _output_issue(
                        "confirmation-required",
                        "JSON apply requires --yes; no Sonarr changes were made",
                    ),
                ),
            )
            raise typer.Exit(code=1)
        if not _is_interactive_input():
            typer.echo("Apply failed: non-interactive use requires --yes; no Sonarr changes made.")
            raise typer.Exit(code=1)
        if not typer.confirm("Apply this stored plan through Sonarr?", default=False):
            typer.echo("Apply cancelled; no Sonarr changes made.")
            raise typer.Exit(code=1)

    observed_discrepancies: tuple[ApplyPlanDiscrepancy, ...] = ()

    def confirm_discrepancies(
        discrepancies: tuple[ApplyPlanDiscrepancy, ...],
    ) -> bool:
        nonlocal observed_discrepancies
        observed_discrepancies = discrepancies
        if json_output:
            return allow_mismatch
        return _confirm_apply_discrepancies(
            discrepancies,
            allow_mismatch=allow_mismatch,
        )

    try:
        result = asyncio.run(
            _apply_plan_through_sonarr(
                settings,
                plan,
                as_of=reference_time,
                allow_stale=allow_stale,
                confirm_discrepancies=confirm_discrepancies,
            )
        )
    except ApplyPlanRejectedError as error:
        if json_output:
            _emit_json_output(
                command=JsonOutputCommand.APPLY,
                success=False,
                data=_apply_command_json_data(
                    plan,
                    rejection=error,
                    discrepancies=observed_discrepancies,
                ),
                warnings=_apply_json_warnings(
                    allow_stale=allow_stale,
                    allow_mismatch=allow_mismatch,
                    discrepancies=observed_discrepancies,
                ),
                errors=(_output_issue("apply-rejected", str(error)),),
            )
        else:
            _render_apply_rejection(error)
        raise typer.Exit(code=1) from None
    except WitError as error:
        if json_output:
            _emit_json_output(
                command=JsonOutputCommand.APPLY,
                success=False,
                data=_apply_command_json_data(
                    plan,
                    discrepancies=observed_discrepancies,
                ),
                warnings=_apply_json_warnings(
                    allow_stale=allow_stale,
                    allow_mismatch=allow_mismatch,
                    discrepancies=observed_discrepancies,
                ),
                errors=(_output_issue("apply-failed", str(error)),),
            )
        else:
            typer.echo(f"Apply failed: {error}")
        raise typer.Exit(code=1) from None

    if json_output:
        success = result.rejected_count == 0
        errors = (
            ()
            if success
            else (
                _output_issue(
                    "episodes-rejected",
                    f"{result.rejected_count} planned episode(s) were rejected",
                ),
            )
        )
        _emit_json_output(
            command=JsonOutputCommand.APPLY,
            success=success,
            data=_apply_command_json_data(
                plan,
                result=result,
                discrepancies=observed_discrepancies,
            ),
            warnings=_apply_json_warnings(
                allow_stale=allow_stale,
                allow_mismatch=allow_mismatch,
                discrepancies=observed_discrepancies,
            ),
            errors=errors,
        )
    else:
        _render_apply_result(result)
    if result.rejected_count:
        raise typer.Exit(code=1)


async def _apply_plan_through_sonarr(
    settings: WitSettings,
    plan: DownloadPlan,
    *,
    as_of: datetime,
    allow_stale: bool,
    confirm_discrepancies: ApplyDiscrepancyConfirmation,
) -> ApplyPlanResult:
    async with _create_sonarr_client(settings) as client:
        return await apply_download_plan(
            client,
            plan=plan,
            root_folder_id=settings.sonarr.root_folder_id,
            quality_profile_id=settings.sonarr.quality_profile_id,
            as_of=as_of,
            allow_stale=allow_stale,
            confirm_discrepancies=confirm_discrepancies,
        )


def _create_sonarr_client(settings: WitSettings) -> SonarrClient:
    credential = settings.sonarr.api_key
    return SonarrClient(
        base_url=str(settings.sonarr.url),
        api_key=credential,
        connect_timeout_seconds=settings.http.connect_timeout_seconds,
        read_timeout_seconds=settings.http.read_timeout_seconds,
    )


def _create_jellyfin_client(settings: WitSettings) -> JellyfinClient:
    credential = settings.jellyfin.api_key
    return JellyfinClient(
        base_url=str(settings.jellyfin.url),
        api_key=credential,
        connect_timeout_seconds=settings.http.connect_timeout_seconds,
        read_timeout_seconds=settings.http.read_timeout_seconds,
    )


def _is_interactive_input() -> bool:
    try:
        return bool(sys.stdin.isatty())
    except (AttributeError, OSError):
        return False


def _confirm_apply_discrepancies(
    discrepancies: tuple[ApplyPlanDiscrepancy, ...],
    *,
    allow_mismatch: bool,
) -> bool:
    typer.echo("Current Sonarr metadata materially differs from the stored plan:")
    for discrepancy in discrepancies:
        typer.echo(f"  - {_safe_terminal_text(discrepancy.summary)}")

    if allow_mismatch:
        typer.echo("Metadata differences explicitly confirmed by --allow-mismatch.")
        return True
    if not _is_interactive_input():
        typer.echo("Reconfirmation requires an interactive terminal or --allow-mismatch.")
        return False
    return typer.confirm("Continue using these current Sonarr mappings?", default=False)


def _render_apply_rejection(error: ApplyPlanRejectedError) -> None:
    typer.echo(f"Apply failed: {error}")
    _render_apply_counts(
        applied=error.applied_count,
        skipped_file=error.skipped_file_count,
        skipped_queue=error.skipped_queue_count,
        rejected=error.rejected_count,
    )


def _render_apply_result(result: ApplyPlanResult) -> None:
    series_source = "newly added" if result.series_created else "existing"
    typer.echo(
        f"Processed plan {result.plan_id} using {series_source} Sonarr series "
        f"{result.series.sonarr_id}."
    )
    _render_apply_counts(
        applied=result.applied_count,
        skipped_file=result.skipped_file_count,
        skipped_queue=result.skipped_queue_count,
        rejected=result.rejected_count,
    )
    if result.command is None:
        typer.echo("Sonarr command ID: none (no actionable episodes)")
    else:
        typer.echo(f"Sonarr command ID: {result.command.command_id} ({result.command.state.value})")


def _render_apply_counts(
    *,
    applied: int,
    skipped_file: int,
    skipped_queue: int,
    rejected: int,
) -> None:
    typer.echo(f"Applied: {applied}")
    typer.echo(f"Skipped-file: {skipped_file}")
    typer.echo(f"Skipped-queue: {skipped_queue}")
    typer.echo(f"Rejected: {rejected}")


@app.command("status")
def status_command(
    plan_id: Annotated[
        str,
        typer.Argument(help="Stored download-plan ID whose progress should be inspected."),
    ],
    json_output: Annotated[
        bool,
        typer.Option("--json", help=_JSON_OPTION_HELP),
    ] = False,
) -> None:
    """Show read-only Sonarr progress and Jellyfin visibility for one plan."""
    try:
        settings = load_settings()
    except ConfigurationError as error:
        if json_output:
            _emit_json_output(
                command=JsonOutputCommand.STATUS,
                success=False,
                data=None,
                errors=(_configuration_output_issue(error),),
            )
        else:
            typer.echo(f"Status failed: {error}")
            typer.echo(
                "Next step: set the required WIT_* values or WIT_CONFIG_FILE; "
                "see docs/configuration.md."
            )
        raise typer.Exit(code=1) from None

    try:
        plan = PlanStore(settings.state_dir).load(plan_id)
    except WitError as error:
        if json_output:
            _emit_json_output(
                command=JsonOutputCommand.STATUS,
                success=False,
                data=None,
                errors=(_output_issue("plan-load-failed", str(error)),),
            )
        else:
            typer.echo(f"Status failed: {error}")
        raise typer.Exit(code=1) from None

    try:
        result = asyncio.run(_get_status_for_plan(settings, plan))
    except WitError as error:
        if json_output:
            _emit_json_output(
                command=JsonOutputCommand.STATUS,
                success=False,
                data=_status_command_json_data(plan),
                errors=(_output_issue("status-read-failed", str(error)),),
            )
        else:
            typer.echo(f"Status failed: {error}")
        raise typer.Exit(code=1) from None

    if json_output:
        success = not result.operational_failure
        warnings = (
            (
                _output_issue(
                    "jellyfin-unavailable",
                    "Jellyfin is unavailable; Sonarr progress is still available",
                ),
            )
            if result.state is RequestOverallState.DEGRADED
            else ()
        )
        errors = (
            ()
            if success
            else (
                _output_issue(
                    "request-status-failed",
                    "Sonarr reports a failure, warning, or inconsistent episode mapping",
                ),
            )
        )
        _emit_json_output(
            command=JsonOutputCommand.STATUS,
            success=success,
            data=_status_command_json_data(plan, result=result),
            warnings=warnings,
            errors=errors,
        )
    else:
        _render_request_status(plan, result)
    if result.operational_failure:
        raise typer.Exit(code=1)


async def _get_status_for_plan(
    settings: WitSettings,
    plan: DownloadPlan,
) -> CombinedRequestStatusResult:
    async with _create_sonarr_client(settings) as sonarr:
        async with _create_jellyfin_client(settings) as jellyfin:
            return await get_combined_request_status(sonarr, jellyfin, plan=plan)


def _render_request_status(
    plan: DownloadPlan,
    result: CombinedRequestStatusResult,
) -> None:
    show_year = str(plan.show_year) if plan.show_year is not None else "year unknown"
    typer.echo(f"Plan status: {result.plan_id}")
    typer.echo(f"Show: {_safe_terminal_text(plan.show_title)} ({show_year})")
    typer.echo(f"Episodes ({len(result.episodes)}):")
    for combined in result.episodes:
        episode = combined.sonarr
        planned = episode.planned_episode
        typer.echo(f"  {planned.label}  {_safe_terminal_text(planned.title)}")
        typer.echo(f"    Sonarr: {episode.state.value}")
        if combined.jellyfin_state is None:
            typer.echo("    Jellyfin: not checked (Sonarr has not imported this episode)")
        else:
            jellyfin_state = combined.jellyfin_state.value.replace("-", " ")
            typer.echo(f"    Jellyfin: {jellyfin_state}")
        for error in episode.errors:
            typer.echo(f"    Detail ({error.kind.value}): {_safe_terminal_text(error.detail)}")

    typer.echo(f"Sonarr imported: {result.imported_count}/{len(result.episodes)}")
    typer.echo(f"Jellyfin visible: {result.visible_count}/{result.imported_count} imported")
    typer.echo(f"Overall: {result.state.value.upper()} - {_request_status_summary(result.state)}")


def _request_status_summary(state: RequestOverallState) -> str:
    if state is RequestOverallState.COMPLETE:
        return "all planned episodes are imported and visible in Jellyfin"
    if state is RequestOverallState.DEGRADED:
        return "Jellyfin is unavailable; Sonarr progress is still shown"
    if state is RequestOverallState.FAILED:
        return "Sonarr reports a failure, warning, or inconsistent episode mapping"
    return "the request is incomplete; no operational failure was detected"


@app.command()
def doctor(
    json_output: Annotated[
        bool,
        typer.Option("--json", help=_JSON_OPTION_HELP),
    ] = False,
) -> None:
    """Validate configuration, local paths, and service connectivity."""
    try:
        settings = load_settings()
    except ConfigurationError as error:
        if json_output:
            _emit_json_output(
                command=JsonOutputCommand.DOCTOR,
                success=False,
                data=_doctor_json_data(None, configuration_valid=False),
                errors=(_configuration_output_issue(error),),
            )
        else:
            typer.echo(f"Configuration: FAILED - {error}")
            typer.echo(
                "Next step: set the required WIT_* values or WIT_CONFIG_FILE; "
                "see docs/configuration.md."
            )
        raise typer.Exit(code=1) from None

    report = asyncio.run(run_doctor(settings))
    if json_output:
        _emit_json_output(
            command=JsonOutputCommand.DOCTOR,
            success=report.successful,
            data=_doctor_json_data(report, configuration_valid=True),
            errors=(
                ()
                if report.successful
                else (
                    _output_issue(
                        "doctor-checks-failed",
                        "One or more required local-path or service checks failed",
                    ),
                )
            ),
        )
    else:
        typer.echo("Configuration: OK - required settings are valid")
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


def _emit_json_output(
    *,
    command: JsonOutputCommand,
    success: bool,
    data: JsonObject | None,
    warnings: tuple[JsonOutputIssue, ...] = (),
    errors: tuple[JsonOutputIssue, ...] = (),
) -> None:
    """Write exactly one versioned JSON document to standard output."""
    envelope = JsonOutputEnvelope(
        command=command,
        success=success,
        data=data,
        warnings=warnings,
        errors=errors,
    )
    typer.echo(envelope.render())


def _output_issue(code: str, message: str) -> JsonOutputIssue:
    return JsonOutputIssue(code=code, message=message)


def _configuration_output_issue(error: ConfigurationError) -> JsonOutputIssue:
    return _output_issue(
        "configuration-invalid",
        f"{error}. Set the required WIT_* values or WIT_CONFIG_FILE; see docs/configuration.md.",
    )


def _download_plan_json_data(plan: DownloadPlan) -> JsonObject:
    episodes: list[JsonValue] = []
    for episode in plan.episodes:
        episode_data: JsonObject = {
            "season_number": episode.season_number,
            "episode_number": episode.episode_number,
            "label": episode.label,
            "title": episode.title,
        }
        episodes.append(episode_data)

    show: JsonObject = {
        "title": plan.show_title,
        "year": plan.show_year,
        "tvmaze_id": plan.tvmaze_id,
        "tvdb_id": plan.tvdb_id,
    }
    return {
        "schema_version": plan.schema_version,
        "plan_id": plan.plan_id,
        "created_at": plan.created_at.isoformat().replace("+00:00", "Z"),
        "show": show,
        "selector_summary": plan.selector_summary,
        "episode_count": plan.episode_count,
        "episodes": episodes,
    }


def _plan_command_json_data(
    query: str,
    *,
    plan: DownloadPlan | None = None,
    saved: bool = False,
    candidate_error: ShowCandidateSelectionRequiredError | None = None,
) -> JsonObject:
    candidates: list[JsonValue] = []
    if candidate_error is not None:
        for candidate in candidate_error.candidates:
            show = candidate.show
            candidate_data: JsonObject = {
                "tvmaze_id": show.tvmaze_id,
                "title": show.title,
                "year": show.premiere_year,
                "tvdb_id": show.tvdb_id,
            }
            candidates.append(candidate_data)

    plan_data: JsonValue = _download_plan_json_data(plan) if plan is not None else None
    return {
        "query": query,
        "saved": saved,
        "plan": plan_data,
        "candidates": candidates,
    }


def _apply_command_json_data(
    plan: DownloadPlan,
    *,
    result: ApplyPlanResult | None = None,
    rejection: ApplyPlanRejectedError | None = None,
    discrepancies: tuple[ApplyPlanDiscrepancy, ...] = (),
) -> JsonObject:
    if result is not None and rejection is not None:
        raise ValueError("apply JSON data cannot contain a result and rejection")

    result_data: JsonValue = None
    if result is not None:
        result_data = _apply_result_json_data(result, discrepancies=discrepancies)
    elif rejection is not None:
        outcomes: JsonObject = {
            "applied": _apply_outcome_json_data(rejection.applied_count, None),
            "skipped_file": _apply_outcome_json_data(rejection.skipped_file_count, None),
            "skipped_queue": _apply_outcome_json_data(rejection.skipped_queue_count, None),
            "rejected": _apply_outcome_json_data(rejection.rejected_count, None),
        }
        result_data = {
            "series": None,
            "outcomes": outcomes,
            "command": None,
            "discrepancies": _apply_discrepancy_json_data(discrepancies),
        }

    return {
        "plan": _download_plan_json_data(plan),
        "result": result_data,
    }


def _apply_result_json_data(
    result: ApplyPlanResult,
    *,
    discrepancies: tuple[ApplyPlanDiscrepancy, ...],
) -> JsonObject:
    series: JsonObject = {
        "sonarr_id": result.series.sonarr_id,
        "tvdb_id": result.series.tvdb_id,
        "title": result.series.title,
        "year": result.series.year,
        "created": result.series_created,
    }
    outcomes: JsonObject = {
        "applied": _apply_outcome_json_data(
            result.applied_count,
            result.applied_episode_ids,
        ),
        "skipped_file": _apply_outcome_json_data(
            result.skipped_file_count,
            result.skipped_file_episode_ids,
        ),
        "skipped_queue": _apply_outcome_json_data(
            result.skipped_queue_count,
            result.skipped_queue_episode_ids,
        ),
        "rejected": _apply_outcome_json_data(
            result.rejected_count,
            result.rejected_episode_ids,
        ),
    }
    command: JsonValue = None
    if result.command is not None:
        command = {
            "command_id": result.command.command_id,
            "state": result.command.state.value,
        }
    return {
        "series": series,
        "outcomes": outcomes,
        "command": command,
        "discrepancies": _apply_discrepancy_json_data(discrepancies),
    }


def _apply_outcome_json_data(
    count: int,
    episode_ids: tuple[int, ...] | None,
) -> JsonObject:
    identifiers: list[JsonValue] | None = (
        None if episode_ids is None else [episode_id for episode_id in episode_ids]
    )
    return {
        "count": count,
        "episode_ids": identifiers,
    }


def _apply_discrepancy_json_data(
    discrepancies: tuple[ApplyPlanDiscrepancy, ...],
) -> list[JsonValue]:
    entries: list[JsonValue] = []
    for discrepancy in discrepancies:
        entry: JsonObject = {
            "kind": discrepancy.kind.value,
            "summary": discrepancy.summary,
        }
        entries.append(entry)
    return entries


def _apply_json_warnings(
    *,
    allow_stale: bool,
    allow_mismatch: bool,
    discrepancies: tuple[ApplyPlanDiscrepancy, ...],
) -> tuple[JsonOutputIssue, ...]:
    warnings: list[JsonOutputIssue] = []
    if allow_stale:
        warnings.append(
            _output_issue(
                "stale-plan-override",
                "The default stored-plan age limit was explicitly overridden",
            )
        )
    if allow_mismatch and discrepancies:
        warnings.append(
            _output_issue(
                "metadata-mismatch-override",
                f"{len(discrepancies)} material Sonarr metadata difference(s) "
                "were explicitly confirmed",
            )
        )
    return tuple(warnings)


def _status_command_json_data(
    plan: DownloadPlan,
    *,
    result: CombinedRequestStatusResult | None = None,
) -> JsonObject:
    result_data: JsonValue = _status_result_json_data(result) if result is not None else None
    return {
        "plan": _download_plan_json_data(plan),
        "result": result_data,
    }


def _status_result_json_data(result: CombinedRequestStatusResult) -> JsonObject:
    series: JsonValue = None
    if result.sonarr.series is not None:
        series = {
            "sonarr_id": result.sonarr.series.sonarr_id,
            "tvdb_id": result.sonarr.series.tvdb_id,
            "title": result.sonarr.series.title,
            "year": result.sonarr.series.year,
        }

    command: JsonValue = None
    if result.sonarr.command_id is not None:
        command = {
            "command_id": result.sonarr.command_id,
            "state": (
                result.sonarr.command_state.value
                if result.sonarr.command_state is not None
                else None
            ),
        }

    episodes: list[JsonValue] = []
    for combined in result.episodes:
        episode = combined.sonarr
        planned = episode.planned_episode
        queue_items: list[JsonValue] = []
        for item in episode.queue_items:
            queue_item: JsonObject = {
                "queue_id": item.queue_id,
                "series_id": item.series_id,
                "episode_id": item.episode_id,
                "state": item.state.value,
            }
            queue_items.append(queue_item)

        episode_errors: list[JsonValue] = []
        for error in episode.errors:
            error_data: JsonObject = {
                "kind": error.kind.value,
                "detail": error.detail,
            }
            episode_errors.append(error_data)

        sonarr: JsonObject = {
            "state": episode.state.value,
            "episode_id": episode.sonarr_episode_id,
            "monitored": episode.monitored,
            "has_file": episode.has_file,
            "queue": queue_items,
            "command_state": (
                episode.command_state.value if episode.command_state is not None else None
            ),
            "errors": episode_errors,
        }
        episode_data: JsonObject = {
            "season_number": planned.season_number,
            "episode_number": planned.episode_number,
            "label": planned.label,
            "title": planned.title,
            "sonarr": sonarr,
            "jellyfin_state": (
                combined.jellyfin_state.value if combined.jellyfin_state is not None else None
            ),
        }
        episodes.append(episode_data)

    return {
        "state": result.state.value,
        "summary": _request_status_summary(result.state),
        "operational_failure": result.operational_failure,
        "episode_count": len(result.episodes),
        "imported_count": result.imported_count,
        "visible_count": result.visible_count,
        "jellyfin_library_state": (
            result.jellyfin_state.value if result.jellyfin_state is not None else None
        ),
        "sonarr_series": series,
        "sonarr_command": command,
        "episodes": episodes,
    }


def _doctor_json_data(
    report: DoctorReport | None,
    *,
    configuration_valid: bool,
) -> JsonObject:
    local_paths: list[JsonValue] = []
    services: list[JsonValue] = []
    if report is not None:
        for check in report.local_paths:
            guidance: JsonValue = (
                None
                if check.state is LocalPathState.READY
                else _local_path_failure_guidance(check.state)
            )
            path_data: JsonObject = {
                "name": check.name.value.replace(" ", "-"),
                "state": check.state.value,
                "summary": check.summary,
                "guidance": guidance,
            }
            local_paths.append(path_data)

        for result in report.services:
            guidance = (
                None
                if result.state is ServiceHealthState.HEALTHY
                else _service_failure_guidance(result.service, result.state)
            )
            service_data: JsonObject = {
                "name": result.service.value,
                "state": result.state.value,
                "summary": result.summary,
                "version": result.version,
                "guidance": guidance,
            }
            services.append(service_data)

    successful = configuration_valid and report is not None and report.successful
    return {
        "configuration": {"valid": configuration_valid},
        "state": "ok" if successful else "failed",
        "local_paths": local_paths,
        "services": services,
    }
