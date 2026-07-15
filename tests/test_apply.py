"""Application-service tests for complete, fail-safe Sonarr plan orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import UTC, datetime

import pytest

from wit.apply import ApplyPlanResult, apply_download_plan
from wit.clients import (
    SonarrCommandState,
    SonarrCommandStatus,
    SonarrEpisode,
    SonarrEpisodeAirStatus,
    SonarrEpisodeMappingError,
    SonarrEpisodeMonitoringResult,
    SonarrSeries,
    SonarrSeriesAddResult,
)
from wit.plans import DOWNLOAD_PLAN_SCHEMA_VERSION, DownloadPlan, PlannedEpisode


class _FakeApplySonarrClient:
    """Records the narrow orchestration boundary without contacting Sonarr."""

    def __init__(
        self,
        *,
        series_created: bool,
        episodes: tuple[SonarrEpisode, ...],
    ) -> None:
        self.series_created = series_created
        self.episodes = episodes
        self.calls: list[tuple[str, object]] = []
        self.series = SonarrSeries(
            sonarr_id=73 if series_created else 42,
            tvdb_id=31415,
            title="Clockwork Harbor",
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


def _plan() -> DownloadPlan:
    return DownloadPlan(
        schema_version=DOWNLOAD_PLAN_SCHEMA_VERSION,
        plan_id="plan-apply-001",
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


def _episode(
    episode_id: int,
    season_number: int,
    episode_number: int,
) -> SonarrEpisode:
    return SonarrEpisode(
        episode_id=episode_id,
        season_number=season_number,
        episode_number=episode_number,
        title=f"Sonarr episode {episode_id}",
        air_status=SonarrEpisodeAirStatus.AIRED,
        monitored=False,
        has_file=False,
    )


def _run_successful_apply(
    *, series_created: bool
) -> tuple[ApplyPlanResult, _FakeApplySonarrClient]:
    client = _FakeApplySonarrClient(
        series_created=series_created,
        episodes=(
            _episode(102, 1, 2),
            _episode(999, 2, 1),
            _episode(101, 1, 1),
        ),
    )

    result = asyncio.run(
        apply_download_plan(
            client,
            plan=_plan(),
            root_folder_id=7,
            quality_profile_id=8,
        )
    )
    return result, client


def _assert_successful_apply(
    result: ApplyPlanResult,
    client: _FakeApplySonarrClient,
    *,
    expected_created: bool,
) -> None:
    assert result == ApplyPlanResult(
        plan_id="plan-apply-001",
        series=client.series,
        series_created=expected_created,
        episode_ids=(101, 102),
        command=SonarrCommandStatus(
            command_id=501,
            state=SonarrCommandState.QUEUED,
        ),
    )
    assert result.episode_count == 2
    assert client.calls == [
        ("find-or-add-series-unmonitored", (31415, 7, 8)),
        ("list-episodes", client.series.sonarr_id),
        ("monitor-episodes", (101, 102)),
        ("submit-episode-search", (101, 102)),
    ]


def test_applies_a_plan_to_an_existing_series_in_strict_operation_order() -> None:
    result, client = _run_successful_apply(series_created=False)

    _assert_successful_apply(result, client, expected_created=False)
    assert result.series.sonarr_id == 42


def test_adds_a_new_series_then_applies_the_complete_plan() -> None:
    result, client = _run_successful_apply(series_created=True)

    _assert_successful_apply(result, client, expected_created=True)
    assert result.series.sonarr_id == 73


@pytest.mark.parametrize("series_created", [False, True], ids=["existing-series", "new-series"])
def test_maps_every_coordinate_before_any_episode_mutation(series_created: bool) -> None:
    client = _FakeApplySonarrClient(
        series_created=series_created,
        episodes=(_episode(101, 1, 1),),
    )

    with pytest.raises(SonarrEpisodeMappingError) as captured:
        asyncio.run(
            apply_download_plan(
                client,
                plan=_plan(),
                root_folder_id=7,
                quality_profile_id=8,
            )
        )

    assert str(captured.value) == "Sonarr episode coordinate S01E02 was not found"
    assert client.calls == [
        ("find-or-add-series-unmonitored", (31415, 7, 8)),
        ("list-episodes", client.series.sonarr_id),
    ]
