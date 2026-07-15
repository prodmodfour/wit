"""Application-service tests for idempotent, stale-safe Sonarr plan apply."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from datetime import UTC, datetime

import pytest

from wit.apply import (
    ApplyPlanDiscrepancy,
    ApplyPlanDiscrepancyKind,
    ApplyPlanMismatchError,
    ApplyPlanResult,
    StaleDownloadPlanError,
    apply_download_plan,
)
from wit.clients import (
    SonarrCommandState,
    SonarrCommandStatus,
    SonarrEpisode,
    SonarrEpisodeAirStatus,
    SonarrEpisodeMappingError,
    SonarrEpisodeMonitoringResult,
    SonarrQueueItem,
    SonarrQueueState,
    SonarrSeries,
    SonarrSeriesAddResult,
)
from wit.plans import DOWNLOAD_PLAN_SCHEMA_VERSION, DownloadPlan, PlannedEpisode

_PLAN_CREATED_AT = datetime(2025, 1, 10, 12, tzinfo=UTC)
_APPLY_TIME = datetime(2025, 1, 12, 12, tzinfo=UTC)


class _FakeApplySonarrClient:
    """Records the narrow orchestration boundary without contacting Sonarr."""

    def __init__(
        self,
        *,
        series_created: bool = False,
        episodes: tuple[SonarrEpisode, ...],
        queue: tuple[SonarrQueueItem, ...] = (),
        series_title: str = "Clockwork Harbor",
    ) -> None:
        self.series_created = series_created
        self.episodes = episodes
        self.queue = queue
        self.calls: list[tuple[str, object]] = []
        self.series = SonarrSeries(
            sonarr_id=73 if series_created else 42,
            tvdb_id=31415,
            title=series_title,
            year=2024,
        )

    async def add_series_unmonitored(
        self,
        *,
        tvdb_id: int | None,
        root_folder_id: int,
        quality_profile_id: int,
    ) -> SonarrSeriesAddResult:
        self.calls.append(
            (
                "find-or-add-series-unmonitored",
                (tvdb_id, root_folder_id, quality_profile_id),
            )
        )
        return SonarrSeriesAddResult(series=self.series, created=self.series_created)

    async def list_episodes(self, series_id: int) -> tuple[SonarrEpisode, ...]:
        self.calls.append(("list-episodes", series_id))
        return self.episodes

    async def list_queue(self) -> tuple[SonarrQueueItem, ...]:
        self.calls.append(("list-queue", None))
        return self.queue

    async def monitor_episodes(
        self,
        episode_ids: Iterable[int],
    ) -> SonarrEpisodeMonitoringResult:
        materialised_ids = tuple(episode_ids)
        self.calls.append(("monitor-episodes", materialised_ids))
        return SonarrEpisodeMonitoringResult(
            episode_ids=materialised_ids,
            monitored=True,
        )

    async def submit_episode_search(
        self,
        episode_ids: Iterable[int],
    ) -> SonarrCommandStatus:
        materialised_ids = tuple(episode_ids)
        self.calls.append(("submit-episode-search", materialised_ids))
        return SonarrCommandStatus(
            command_id=501,
            state=SonarrCommandState.QUEUED,
        )


def _planned_episode(
    episode_number: int,
    title: str,
    *,
    season_number: int = 1,
) -> PlannedEpisode:
    return PlannedEpisode(
        season_number=season_number,
        episode_number=episode_number,
        title=title,
    )


def _plan(
    *,
    created_at: datetime = _PLAN_CREATED_AT,
    episodes: tuple[PlannedEpisode, ...] | None = None,
) -> DownloadPlan:
    selected = episodes or (
        _planned_episode(1, "First Light"),
        _planned_episode(2, "Turning Tide"),
    )
    return DownloadPlan(
        schema_version=DOWNLOAD_PLAN_SCHEMA_VERSION,
        plan_id="plan-apply-001",
        created_at=created_at,
        show_title="Clockwork Harbor",
        show_year=2024,
        tvmaze_id=2718,
        tvdb_id=31415,
        selector_summary=f"first {len(selected)} aired regular episodes",
        episodes=selected,
    )


def _episode(
    episode_id: int,
    episode_number: int,
    title: str,
    *,
    season_number: int = 1,
    has_file: bool = False,
) -> SonarrEpisode:
    return SonarrEpisode(
        episode_id=episode_id,
        season_number=season_number,
        episode_number=episode_number,
        title=title,
        air_status=SonarrEpisodeAirStatus.AIRED,
        monitored=False,
        has_file=has_file,
    )


def _queue_item(
    queue_id: int,
    episode_id: int,
    state: SonarrQueueState,
    *,
    series_id: int = 42,
) -> SonarrQueueItem:
    return SonarrQueueItem(
        queue_id=queue_id,
        series_id=series_id,
        episode_id=episode_id,
        state=state,
    )


def _standard_episodes() -> tuple[SonarrEpisode, ...]:
    return (
        _episode(102, 2, "Turning Tide"),
        _episode(999, 1, "Second Season", season_number=2),
        _episode(101, 1, "First Light"),
    )


def _run_apply(
    client: _FakeApplySonarrClient,
    *,
    plan: DownloadPlan | None = None,
    allow_stale: bool = False,
    confirm_discrepancies: Callable[[tuple[ApplyPlanDiscrepancy, ...]], bool] | None = None,
) -> ApplyPlanResult:
    return asyncio.run(
        apply_download_plan(
            client,
            plan=plan or _plan(),
            root_folder_id=7,
            quality_profile_id=8,
            as_of=_APPLY_TIME,
            allow_stale=allow_stale,
            confirm_discrepancies=confirm_discrepancies,
        )
    )


@pytest.mark.parametrize("series_created", [False, True], ids=["existing-series", "new-series"])
def test_applies_only_actionable_episodes_in_strict_operation_order(
    series_created: bool,
) -> None:
    client = _FakeApplySonarrClient(
        series_created=series_created,
        episodes=_standard_episodes(),
    )

    result = _run_apply(client)

    assert result == ApplyPlanResult(
        plan_id="plan-apply-001",
        series=client.series,
        series_created=series_created,
        applied_episode_ids=(101, 102),
        skipped_file_episode_ids=(),
        skipped_queue_episode_ids=(),
        rejected_episode_ids=(),
        command=SonarrCommandStatus(
            command_id=501,
            state=SonarrCommandState.QUEUED,
        ),
    )
    assert result.applied_count == 2
    assert result.skipped_file_count == 0
    assert result.skipped_queue_count == 0
    assert result.rejected_count == 0
    assert client.calls == [
        ("find-or-add-series-unmonitored", (31415, 7, 8)),
        ("list-episodes", client.series.sonarr_id),
        ("list-queue", None),
        ("monitor-episodes", (101, 102)),
        ("submit-episode-search", (101, 102)),
    ]


@pytest.mark.parametrize("series_created", [False, True], ids=["existing-series", "new-series"])
def test_maps_every_coordinate_before_queue_inspection_or_episode_mutation(
    series_created: bool,
) -> None:
    client = _FakeApplySonarrClient(
        series_created=series_created,
        episodes=(_episode(101, 1, "First Light"),),
    )

    with pytest.raises(SonarrEpisodeMappingError) as captured:
        _run_apply(client)

    assert str(captured.value) == "Sonarr episode coordinate S01E02 was not found"
    assert client.calls == [
        ("find-or-add-series-unmonitored", (31415, 7, 8)),
        ("list-episodes", client.series.sonarr_id),
    ]


def test_repeat_apply_skips_every_episode_already_in_the_active_queue() -> None:
    client = _FakeApplySonarrClient(
        episodes=_standard_episodes(),
        queue=(
            _queue_item(801, 101, SonarrQueueState.QUEUED),
            _queue_item(802, 102, SonarrQueueState.DOWNLOADING),
        ),
    )

    result = _run_apply(client)

    assert result.applied_episode_ids == ()
    assert result.skipped_file_episode_ids == ()
    assert result.skipped_queue_episode_ids == (101, 102)
    assert result.rejected_episode_ids == ()
    assert result.command is None
    assert client.calls == [
        ("find-or-add-series-unmonitored", (31415, 7, 8)),
        ("list-episodes", 42),
        ("list-queue", None),
    ]


def test_mixed_states_are_counted_separately_and_only_actionable_ids_are_searched() -> None:
    plan = _plan(
        episodes=(
            _planned_episode(1, "First Light"),
            _planned_episode(2, "Turning Tide"),
            _planned_episode(3, "Open Water"),
            _planned_episode(4, "Safe Harbor"),
        )
    )
    client = _FakeApplySonarrClient(
        episodes=(
            _episode(101, 1, "First Light", has_file=True),
            _episode(102, 2, "Turning Tide"),
            _episode(103, 3, "Open Water"),
            _episode(104, 4, "Safe Harbor"),
        ),
        queue=(
            _queue_item(801, 102, SonarrQueueState.IMPORTING),
            _queue_item(802, 104, SonarrQueueState.FAILED),
        ),
    )

    result = _run_apply(client, plan=plan)

    assert result.applied_episode_ids == (103,)
    assert result.skipped_file_episode_ids == (101,)
    assert result.skipped_queue_episode_ids == (102,)
    assert result.rejected_episode_ids == (104,)
    assert (
        result.applied_count,
        result.skipped_file_count,
        result.skipped_queue_count,
        result.rejected_count,
    ) == (1, 1, 1, 1)
    assert result.command == SonarrCommandStatus(
        command_id=501,
        state=SonarrCommandState.QUEUED,
    )
    assert client.calls[-2:] == [
        ("monitor-episodes", (103,)),
        ("submit-episode-search", (103,)),
    ]


def test_no_op_apply_does_not_monitor_or_submit_an_empty_search() -> None:
    client = _FakeApplySonarrClient(
        episodes=(
            _episode(101, 1, "First Light", has_file=True),
            _episode(102, 2, "Turning Tide"),
        ),
        queue=(_queue_item(801, 102, SonarrQueueState.QUEUED),),
    )

    result = _run_apply(client)

    assert result.applied_count == 0
    assert result.skipped_file_count == 1
    assert result.skipped_queue_count == 1
    assert result.rejected_count == 0
    assert result.command is None
    assert all(
        call[0] not in {"monitor-episodes", "submit-episode-search"} for call in client.calls
    )


def test_rejects_a_stale_plan_before_any_sonarr_operation() -> None:
    stale_plan = _plan(created_at=datetime(2025, 1, 1, 12, tzinfo=UTC))
    client = _FakeApplySonarrClient(episodes=_standard_episodes())

    with pytest.raises(StaleDownloadPlanError) as captured:
        _run_apply(client, plan=stale_plan)

    assert str(captured.value) == (
        "download plan plan-apply-001 is older than the 7-day apply limit; "
        "review it and retry with --allow-stale to override"
    )
    assert captured.value.rejected_count == 2
    assert client.calls == []


def test_explicit_stale_override_runs_the_normal_safety_checks() -> None:
    stale_plan = _plan(created_at=datetime(2025, 1, 1, 12, tzinfo=UTC))
    client = _FakeApplySonarrClient(episodes=_standard_episodes())

    result = _run_apply(client, plan=stale_plan, allow_stale=True)

    assert result.applied_count == 2
    assert client.calls[-2:] == [
        ("monitor-episodes", (101, 102)),
        ("submit-episode-search", (101, 102)),
    ]


def test_material_title_and_coordinate_differences_require_reconfirmation() -> None:
    client = _FakeApplySonarrClient(
        episodes=(
            _episode(101, 1, "Arrival"),
            _episode(102, 2, "Turning Tide"),
            _episode(103, 3, "First Light"),
        ),
        series_title="Clockwork Harbour",
    )
    observed: list[tuple[ApplyPlanDiscrepancy, ...]] = []

    def confirm(discrepancies: tuple[ApplyPlanDiscrepancy, ...]) -> bool:
        observed.append(discrepancies)
        return True

    result = _run_apply(client, confirm_discrepancies=confirm)

    assert result.applied_count == 2
    assert len(observed) == 1
    assert tuple(item.kind for item in observed[0]) == (
        ApplyPlanDiscrepancyKind.SERIES_TITLE,
        ApplyPlanDiscrepancyKind.EPISODE_COORDINATE,
    )
    assert "Sonarr assigns this title to S01E03" in observed[0][1].summary


def test_unconfirmed_metadata_difference_rejects_actionable_episodes_without_mutation() -> None:
    client = _FakeApplySonarrClient(
        episodes=(
            _episode(101, 1, "Renamed Premiere"),
            _episode(102, 2, "Turning Tide", has_file=True),
        )
    )

    with pytest.raises(ApplyPlanMismatchError) as captured:
        _run_apply(client, confirm_discrepancies=lambda discrepancies: False)

    assert captured.value.skipped_file_count == 1
    assert captured.value.skipped_queue_count == 0
    assert captured.value.rejected_count == 1
    assert len(captured.value.discrepancies) == 1
    assert client.calls == [
        ("find-or-add-series-unmonitored", (31415, 7, 8)),
        ("list-episodes", 42),
        ("list-queue", None),
    ]


def test_harmless_title_case_and_punctuation_do_not_require_reconfirmation() -> None:
    client = _FakeApplySonarrClient(
        episodes=(
            _episode(101, 1, "FIRST-LIGHT!"),
            _episode(102, 2, "Turning... Tide"),
        ),
        series_title="clockwork: harbor",
    )
    confirmation_called = False

    def unexpected_confirmation(discrepancies: tuple[ApplyPlanDiscrepancy, ...]) -> bool:
        nonlocal confirmation_called
        del discrepancies
        confirmation_called = True
        return False

    result = _run_apply(client, confirm_discrepancies=unexpected_confirmation)

    assert result.applied_count == 2
    assert not confirmation_called
