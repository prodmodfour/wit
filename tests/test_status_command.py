"""CLI tests for combined, read-only Sonarr and Jellyfin plan status."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

import wit.cli as cli
from wit.clients import (
    JellyfinEpisodeAvailabilityState,
    JellyfinLibraryState,
    SonarrQueueItem,
    SonarrQueueState,
    SonarrSeries,
)
from wit.config import WitSettings
from wit.errors import WitError
from wit.plan_store import PlanStore
from wit.plans import DOWNLOAD_PLAN_SCHEMA_VERSION, DownloadPlan, PlannedEpisode
from wit.status import (
    CombinedRequestEpisodeStatus,
    CombinedRequestStatusResult,
    RequestEpisodeError,
    RequestEpisodeErrorKind,
    RequestEpisodeState,
    RequestEpisodeStatus,
    RequestOverallState,
    RequestStatusResult,
)

runner = CliRunner()


def _set_valid_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[str, str]:
    for name in tuple(os.environ):
        if name.startswith("WIT_"):
            monkeypatch.delenv(name)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)

    sonarr_credential = "status-sonarr-" + ("x" * 24)
    jellyfin_credential = "status-jellyfin-" + ("x" * 24)
    values = {
        "WIT_SONARR_URL": "http://127.0.0.1:8989",
        "WIT_SONARR_API_KEY": sonarr_credential,
        "WIT_SONARR_ROOT_FOLDER_ID": "7",
        "WIT_SONARR_QUALITY_PROFILE_ID": "8",
        "WIT_JELLYFIN_URL": "http://127.0.0.1:8096",
        "WIT_JELLYFIN_API_KEY": jellyfin_credential,
        "WIT_SEERR_URL": "http://127.0.0.1:5055",
        "WIT_STATE_DIR": str(tmp_path / "state"),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return sonarr_credential, jellyfin_credential


def _plan() -> DownloadPlan:
    return DownloadPlan(
        schema_version=DOWNLOAD_PLAN_SCHEMA_VERSION,
        plan_id="plan-cli-status",
        created_at=datetime(2025, 1, 10, 12, tzinfo=UTC),
        show_title="Clockwork Harbor",
        show_year=2024,
        tvmaze_id=2718,
        tvdb_id=31415,
        selector_summary="first 2 aired regular episodes",
        episodes=(
            PlannedEpisode(season_number=1, episode_number=1, title="First Light"),
            PlannedEpisode(season_number=1, episode_number=2, title="Turning Tide"),
        ),
    )


def _request_episode_status(
    planned: PlannedEpisode,
    state: RequestEpisodeState,
) -> RequestEpisodeStatus:
    queue_state = {
        RequestEpisodeState.QUEUED: SonarrQueueState.QUEUED,
        RequestEpisodeState.DOWNLOADING: SonarrQueueState.DOWNLOADING,
        RequestEpisodeState.WARNING: SonarrQueueState.WARNING,
        RequestEpisodeState.FAILED: SonarrQueueState.FAILED,
    }.get(state)
    episode_id = 100 + planned.episode_number
    queue_items = (
        (
            SonarrQueueItem(
                queue_id=700 + planned.episode_number,
                series_id=42,
                episode_id=episode_id,
                state=queue_state,
            ),
        )
        if queue_state is not None
        else ()
    )
    errors: tuple[RequestEpisodeError, ...] = ()
    if state is RequestEpisodeState.WARNING:
        errors = (
            RequestEpisodeError(
                kind=RequestEpisodeErrorKind.QUEUE_WARNING,
                detail=f"Sonarr queue item {700 + planned.episode_number} reports a warning.",
            ),
        )
    elif state is RequestEpisodeState.FAILED:
        errors = (
            RequestEpisodeError(
                kind=RequestEpisodeErrorKind.QUEUE_FAILED,
                detail=f"Sonarr queue item {700 + planned.episode_number} reports a failure.",
            ),
        )

    return RequestEpisodeStatus(
        planned_episode=planned,
        sonarr_episode_id=episode_id,
        monitored=True,
        has_file=state is RequestEpisodeState.IMPORTED,
        queue_items=queue_items,
        command_state=None,
        state=state,
        errors=errors,
    )


def _combined_result(
    plan: DownloadPlan,
    *,
    overall: RequestOverallState,
    sonarr_states: tuple[RequestEpisodeState, RequestEpisodeState],
    jellyfin_states: tuple[
        JellyfinEpisodeAvailabilityState | None,
        JellyfinEpisodeAvailabilityState | None,
    ],
    jellyfin_library_state: JellyfinLibraryState | None,
) -> CombinedRequestStatusResult:
    sonarr_episodes = tuple(
        _request_episode_status(planned, state)
        for planned, state in zip(plan.episodes, sonarr_states, strict=True)
    )
    sonarr = RequestStatusResult(
        plan_id=plan.plan_id,
        series=SonarrSeries(
            sonarr_id=42,
            tvdb_id=plan.tvdb_id,
            title=plan.show_title,
            year=plan.show_year or 0,
        ),
        command_id=None,
        command_state=None,
        episodes=sonarr_episodes,
    )
    episodes = tuple(
        CombinedRequestEpisodeStatus(sonarr=status, jellyfin_state=jellyfin_state)
        for status, jellyfin_state in zip(sonarr_episodes, jellyfin_states, strict=True)
    )
    return CombinedRequestStatusResult(
        sonarr=sonarr,
        jellyfin_state=jellyfin_library_state,
        state=overall,
        episodes=episodes,
    )


def _install_status_result(
    monkeypatch: pytest.MonkeyPatch,
    expected_plan: DownloadPlan,
    result: CombinedRequestStatusResult,
) -> list[WitSettings]:
    calls: list[WitSettings] = []

    async def fake_status(
        settings: WitSettings,
        loaded_plan: DownloadPlan,
    ) -> CombinedRequestStatusResult:
        assert loaded_plan == expected_plan
        calls.append(settings)
        return result

    monkeypatch.setattr(cli, "_get_status_for_plan", fake_status)
    return calls


def test_status_renders_every_active_sonarr_episode_and_exits_successfully(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credentials = _set_valid_environment(monkeypatch, tmp_path)
    plan = _plan()
    PlanStore(tmp_path / "state").save(plan)
    status = _combined_result(
        plan,
        overall=RequestOverallState.ACTIVE,
        sonarr_states=(RequestEpisodeState.QUEUED, RequestEpisodeState.DOWNLOADING),
        jellyfin_states=(None, None),
        jellyfin_library_state=None,
    )
    calls = _install_status_result(monkeypatch, plan, status)

    result = runner.invoke(cli.app, ["status", plan.plan_id])

    assert result.exit_code == 0, result.output
    assert "Plan status: plan-cli-status" in result.output
    assert "Show: Clockwork Harbor (2024)" in result.output
    assert "S01E01  First Light" in result.output
    assert "Sonarr: queued" in result.output
    assert "S01E02  Turning Tide" in result.output
    assert "Sonarr: downloading" in result.output
    assert result.output.count("Jellyfin: not checked") == 2
    assert "Sonarr imported: 0/2" in result.output
    assert "Overall: ACTIVE - the request is incomplete" in result.output
    assert len(calls) == 1
    assert all(credential not in result.output for credential in credentials)


def test_status_reports_complete_only_when_imports_are_visible_in_jellyfin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_valid_environment(monkeypatch, tmp_path)
    plan = _plan()
    PlanStore(tmp_path / "state").save(plan)
    status = _combined_result(
        plan,
        overall=RequestOverallState.COMPLETE,
        sonarr_states=(RequestEpisodeState.IMPORTED, RequestEpisodeState.IMPORTED),
        jellyfin_states=(
            JellyfinEpisodeAvailabilityState.VISIBLE,
            JellyfinEpisodeAvailabilityState.VISIBLE,
        ),
        jellyfin_library_state=JellyfinLibraryState.AVAILABLE,
    )
    _install_status_result(monkeypatch, plan, status)

    result = runner.invoke(cli.app, ["status", plan.plan_id])

    assert result.exit_code == 0, result.output
    assert result.output.count("Sonarr: imported") == 2
    assert result.output.count("Jellyfin: visible") == 2
    assert "Sonarr imported: 2/2" in result.output
    assert "Jellyfin visible: 2/2 imported" in result.output
    assert "Overall: COMPLETE - all planned episodes are imported and visible" in result.output


def test_status_reports_unavailable_jellyfin_as_successful_degraded_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_valid_environment(monkeypatch, tmp_path)
    plan = _plan()
    PlanStore(tmp_path / "state").save(plan)
    status = _combined_result(
        plan,
        overall=RequestOverallState.DEGRADED,
        sonarr_states=(RequestEpisodeState.IMPORTED, RequestEpisodeState.IMPORTED),
        jellyfin_states=(
            JellyfinEpisodeAvailabilityState.UNAVAILABLE,
            JellyfinEpisodeAvailabilityState.UNAVAILABLE,
        ),
        jellyfin_library_state=JellyfinLibraryState.UNAVAILABLE,
    )
    _install_status_result(monkeypatch, plan, status)

    result = runner.invoke(cli.app, ["status", plan.plan_id])

    assert result.exit_code == 0, result.output
    assert result.output.count("Jellyfin: unavailable") == 2
    assert "Overall: DEGRADED - Jellyfin is unavailable; Sonarr progress is still shown" in (
        result.output
    )


def test_status_reports_failed_plan_details_and_returns_operational_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_valid_environment(monkeypatch, tmp_path)
    plan = _plan()
    PlanStore(tmp_path / "state").save(plan)
    status = _combined_result(
        plan,
        overall=RequestOverallState.FAILED,
        sonarr_states=(RequestEpisodeState.FAILED, RequestEpisodeState.QUEUED),
        jellyfin_states=(None, None),
        jellyfin_library_state=None,
    )
    _install_status_result(monkeypatch, plan, status)

    result = runner.invoke(cli.app, ["status", plan.plan_id])

    assert result.exit_code == 1
    assert "Sonarr: failed" in result.output
    assert "Detail (queue-failed): Sonarr queue item 701 reports a failure." in result.output
    assert "Sonarr: queued" in result.output
    assert "Overall: FAILED - Sonarr reports a failure" in result.output


def test_status_returns_nonzero_when_the_read_operation_cannot_complete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credentials = _set_valid_environment(monkeypatch, tmp_path)
    plan = _plan()
    PlanStore(tmp_path / "state").save(plan)

    async def failed_status(
        settings: WitSettings,
        loaded_plan: DownloadPlan,
    ) -> CombinedRequestStatusResult:
        del settings, loaded_plan
        raise WitError("Sonarr status could not be read")

    monkeypatch.setattr(cli, "_get_status_for_plan", failed_status)

    result = runner.invoke(cli.app, ["status", plan.plan_id])

    assert result.exit_code == 1
    assert result.output == "Status failed: Sonarr status could not be read\n"
    assert all(credential not in result.output for credential in credentials)
