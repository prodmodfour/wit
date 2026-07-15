"""Deterministic application-service tests for Sonarr-backed request status."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from wit.clients import (
    SonarrCommandFailedError,
    SonarrCommandRejectedError,
    SonarrCommandState,
    SonarrCommandStatus,
    SonarrEpisode,
    SonarrEpisodeAirStatus,
    SonarrQueueItem,
    SonarrQueueState,
    SonarrSeries,
)
from wit.plans import DOWNLOAD_PLAN_SCHEMA_VERSION, DownloadPlan, PlannedEpisode
from wit.status import (
    InvalidRequestStatusError,
    RequestEpisodeError,
    RequestEpisodeErrorKind,
    RequestEpisodeState,
    RequestStatusResult,
    get_request_status,
)

_PRIVATE_COMMAND_DETAIL = "private-command-" + ("x" * 24)


class _FakeStatusSonarrClient:
    """Records only the read-only Sonarr operations used by status."""

    def __init__(
        self,
        *,
        series: SonarrSeries | None,
        episodes: tuple[SonarrEpisode, ...] = (),
        queue: tuple[SonarrQueueItem, ...] = (),
        command: SonarrCommandStatus | None = None,
        command_error: Exception | None = None,
    ) -> None:
        self.series = series
        self.episodes = episodes
        self.queue = queue
        self.command = command
        self.command_error = command_error
        self.calls: list[tuple[str, int | None]] = []

    async def find_series_by_tvdb_id(self, tvdb_id: int) -> SonarrSeries | None:
        self.calls.append(("find-series", tvdb_id))
        return self.series

    async def list_episodes(self, series_id: int) -> tuple[SonarrEpisode, ...]:
        self.calls.append(("list-episodes", series_id))
        return self.episodes

    async def list_queue(self) -> tuple[SonarrQueueItem, ...]:
        self.calls.append(("list-queue", None))
        return self.queue

    async def get_command_status(self, command_id: int) -> SonarrCommandStatus:
        self.calls.append(("get-command", command_id))
        if self.command_error is not None:
            raise self.command_error
        if self.command is None:
            raise AssertionError("a command result was not configured")
        return self.command


def _planned_episode(episode_number: int) -> PlannedEpisode:
    return PlannedEpisode(
        season_number=1,
        episode_number=episode_number,
        title=f"Episode {episode_number}",
    )


def _plan(episode_count: int = 7) -> DownloadPlan:
    return DownloadPlan(
        schema_version=DOWNLOAD_PLAN_SCHEMA_VERSION,
        plan_id="plan-status-001",
        created_at=datetime(2025, 1, 10, 12, tzinfo=UTC),
        show_title="Clockwork Harbor",
        show_year=2024,
        tvmaze_id=2718,
        tvdb_id=31415,
        selector_summary=f"first {episode_count} aired regular episodes",
        episodes=tuple(_planned_episode(number) for number in range(1, episode_count + 1)),
    )


def _series() -> SonarrSeries:
    return SonarrSeries(
        sonarr_id=42,
        tvdb_id=31415,
        title="Clockwork Harbor",
        year=2024,
    )


def _episode(
    episode_number: int,
    *,
    episode_id: int | None = None,
    monitored: bool = True,
    has_file: bool = False,
) -> SonarrEpisode:
    return SonarrEpisode(
        episode_id=episode_id or 100 + episode_number,
        season_number=1,
        episode_number=episode_number,
        title=f"Current episode {episode_number}",
        air_status=SonarrEpisodeAirStatus.AIRED,
        monitored=monitored,
        has_file=has_file,
    )


def _queue_item(
    queue_id: int,
    episode_id: int,
    state: SonarrQueueState,
    *,
    series_id: int | None = 42,
) -> SonarrQueueItem:
    return SonarrQueueItem(
        queue_id=queue_id,
        series_id=series_id,
        episode_id=episode_id,
        state=state,
    )


def _run_status(
    client: _FakeStatusSonarrClient,
    *,
    plan: DownloadPlan | None = None,
    command_id: int | None = None,
) -> RequestStatusResult:
    return asyncio.run(
        get_request_status(
            client,
            plan=plan or _plan(),
            command_id=command_id,
        )
    )


def test_classifies_every_mixed_plan_state_from_current_sonarr_observations() -> None:
    episodes = (
        _episode(7),
        _episode(3),
        _episode(1, monitored=False),
        _episode(5),
        _episode(2),
        _episode(4, has_file=True),
        _episode(6),
    )
    queue = (
        _queue_item(807, 107, SonarrQueueState.FAILED),
        _queue_item(802, 102, SonarrQueueState.QUEUED),
        _queue_item(804, 104, SonarrQueueState.FAILED),
        _queue_item(803, 103, SonarrQueueState.IMPORTING),
        _queue_item(806, 106, SonarrQueueState.WARNING),
        _queue_item(900, 999, SonarrQueueState.FAILED),
        _queue_item(901, 105, SonarrQueueState.FAILED, series_id=99),
    )
    client = _FakeStatusSonarrClient(
        series=_series(),
        episodes=episodes,
        queue=queue,
        command=SonarrCommandStatus(
            command_id=501,
            state=SonarrCommandState.COMPLETED,
        ),
    )

    result = _run_status(client, command_id=501)

    assert [item.state for item in result.episodes] == [
        RequestEpisodeState.PLANNED,
        RequestEpisodeState.QUEUED,
        RequestEpisodeState.DOWNLOADING,
        RequestEpisodeState.IMPORTED,
        RequestEpisodeState.MISSING,
        RequestEpisodeState.WARNING,
        RequestEpisodeState.FAILED,
    ]
    assert result.plan_id == "plan-status-001"
    assert result.series == _series()
    assert result.command_id == 501
    assert result.command_state is SonarrCommandState.COMPLETED
    assert [item.sonarr_episode_id for item in result.episodes] == list(range(101, 108))
    assert result.episodes[0].monitored is False
    assert result.episodes[0].has_file is False
    assert result.episodes[0].command_state is None
    assert all(item.command_state is SonarrCommandState.COMPLETED for item in result.episodes[1:])
    assert result.episodes[2].queue_items == (_queue_item(803, 103, SonarrQueueState.IMPORTING),)
    assert result.episodes[3].errors == (
        RequestEpisodeError(
            kind=RequestEpisodeErrorKind.QUEUE_FAILED,
            detail="Sonarr queue item 804 reports a failure.",
        ),
    )
    assert result.episodes[5].errors == (
        RequestEpisodeError(
            kind=RequestEpisodeErrorKind.QUEUE_WARNING,
            detail="Sonarr queue item 806 reports a warning.",
        ),
    )
    assert result.episodes[6].errors == (
        RequestEpisodeError(
            kind=RequestEpisodeErrorKind.QUEUE_FAILED,
            detail="Sonarr queue item 807 reports a failure.",
        ),
    )
    assert client.calls == [
        ("find-series", 31415),
        ("list-episodes", 42),
        ("list-queue", None),
        ("get-command", 501),
    ]


@pytest.mark.parametrize("command_state", [SonarrCommandState.QUEUED, SonarrCommandState.STARTED])
def test_uses_active_command_state_when_no_file_or_queue_state_is_available(
    command_state: SonarrCommandState,
) -> None:
    client = _FakeStatusSonarrClient(
        series=_series(),
        episodes=(_episode(1),),
        command=SonarrCommandStatus(command_id=501, state=command_state),
    )

    result = _run_status(client, plan=_plan(1), command_id=501)

    assert result.episodes[0].state is RequestEpisodeState.QUEUED
    assert result.episodes[0].command_state is command_state
    assert result.episodes[0].errors == ()


@pytest.mark.parametrize(
    ("command_error", "kind", "expected_detail", "expected_command_state"),
    [
        (
            SonarrCommandFailedError(f"unsafe upstream detail: {_PRIVATE_COMMAND_DETAIL}"),
            RequestEpisodeErrorKind.COMMAND_FAILED,
            "Sonarr EpisodeSearch command 501 failed.",
            SonarrCommandState.FAILED,
        ),
        (
            SonarrCommandRejectedError(f"unsafe upstream detail: {_PRIVATE_COMMAND_DETAIL}"),
            RequestEpisodeErrorKind.COMMAND_REJECTED,
            "Sonarr EpisodeSearch command 501 was rejected or stopped.",
            None,
        ),
    ],
)
def test_preserves_safe_per_episode_command_errors_without_raw_exception_details(
    command_error: Exception,
    kind: RequestEpisodeErrorKind,
    expected_detail: str,
    expected_command_state: SonarrCommandState | None,
) -> None:
    client = _FakeStatusSonarrClient(
        series=_series(),
        episodes=(_episode(1),),
        command_error=command_error,
    )

    result = _run_status(client, plan=_plan(1), command_id=501)

    assert result.command_state is expected_command_state
    assert result.episodes[0].state is RequestEpisodeState.FAILED
    assert result.episodes[0].errors == (RequestEpisodeError(kind=kind, detail=expected_detail),)
    assert _PRIVATE_COMMAND_DETAIL not in repr(result)


def test_reports_missing_and_ambiguous_coordinates_per_episode_without_losing_other_states() -> (
    None
):
    client = _FakeStatusSonarrClient(
        series=_series(),
        episodes=(
            _episode(1, episode_id=201),
            _episode(1, episode_id=202),
            _episode(3, episode_id=203, monitored=False),
        ),
    )

    result = _run_status(client, plan=_plan(3))

    assert [item.state for item in result.episodes] == [
        RequestEpisodeState.MISSING,
        RequestEpisodeState.MISSING,
        RequestEpisodeState.PLANNED,
    ]
    assert result.episodes[0].errors == (
        RequestEpisodeError(
            kind=RequestEpisodeErrorKind.MAPPING,
            detail="Sonarr episode coordinate S01E01 is ambiguous",
        ),
    )
    assert result.episodes[1].errors == (
        RequestEpisodeError(
            kind=RequestEpisodeErrorKind.MAPPING,
            detail="Sonarr episode coordinate S01E02 was not found",
        ),
    )
    assert result.episodes[2].sonarr_episode_id == 203
    assert result.episodes[2].errors == ()
    assert client.calls == [
        ("find-series", 31415),
        ("list-episodes", 42),
        ("list-queue", None),
    ]


def test_series_not_yet_in_sonarr_leaves_every_stored_episode_planned() -> None:
    client = _FakeStatusSonarrClient(series=None)

    result = _run_status(client, plan=_plan(2))

    assert result.series is None
    assert [item.state for item in result.episodes] == [
        RequestEpisodeState.PLANNED,
        RequestEpisodeState.PLANNED,
    ]
    assert all(item.sonarr_episode_id is None for item in result.episodes)
    assert all(item.monitored is None and item.has_file is None for item in result.episodes)
    assert all(item.errors == () for item in result.episodes)
    assert client.calls == [("find-series", 31415)]


@pytest.mark.parametrize("command_id", [0, -1, True, 2_147_483_648])
def test_rejects_invalid_optional_command_ids_before_sonarr_access(command_id: int) -> None:
    client = _FakeStatusSonarrClient(series=_series())

    with pytest.raises(InvalidRequestStatusError) as captured:
        _run_status(client, plan=_plan(1), command_id=command_id)

    assert str(captured.value) == "request command ID must be a positive integer"
    assert client.calls == []
