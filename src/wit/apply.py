"""Confirmed application orchestration for one stored download plan."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from wit.clients import (
    SonarrCommandStatus,
    SonarrEpisode,
    SonarrEpisodeMonitoringResult,
    SonarrSeries,
    SonarrSeriesAddResult,
    map_episode_coordinate,
)
from wit.plans import DownloadPlan, PlanIdentifier

_MAX_IDENTIFIER = 2_147_483_647
ApplyEpisodeIdentifier = Annotated[int, Field(gt=0, le=_MAX_IDENTIFIER)]


class ApplySonarrClient(Protocol):
    """The narrow Sonarr operations required to apply a complete plan."""

    async def add_series_unmonitored(
        self,
        *,
        tvdb_id: int | None,
        root_folder_id: int,
        quality_profile_id: int,
    ) -> SonarrSeriesAddResult:
        """Find or add the planned series without broad monitoring or search."""
        ...

    async def list_episodes(self, series_id: int) -> tuple[SonarrEpisode, ...]:
        """Fetch the current episodes for the resolved Sonarr series."""
        ...

    async def monitor_episodes(
        self,
        episode_ids: Iterable[int],
    ) -> SonarrEpisodeMonitoringResult:
        """Monitor exactly the supplied episode IDs."""
        ...

    async def submit_episode_search(
        self,
        episode_ids: Iterable[int],
    ) -> SonarrCommandStatus:
        """Submit one targeted search for exactly the supplied episode IDs."""
        ...


class ApplyPlanResult(BaseModel):
    """The bounded result of applying every episode in one plan."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    plan_id: PlanIdentifier
    series: SonarrSeries
    series_created: bool
    episode_ids: tuple[ApplyEpisodeIdentifier, ...]
    command: SonarrCommandStatus

    @field_validator("episode_ids")
    @classmethod
    def _require_unique_episode_ids(
        cls,
        value: tuple[int, ...],
    ) -> tuple[int, ...]:
        if not value:
            raise ValueError("apply result must contain at least one episode ID")
        if len(set(value)) != len(value):
            raise ValueError("apply result must contain unique episode IDs")
        return value

    @property
    def episode_count(self) -> int:
        """Return the number of episodes monitored and submitted for search."""
        return len(self.episode_ids)


async def apply_download_plan(
    sonarr: ApplySonarrClient,
    *,
    plan: DownloadPlan,
    root_folder_id: int,
    quality_profile_id: int,
) -> ApplyPlanResult:
    """Apply one validated plan through narrowly scoped Sonarr operations.

    The series is first found or added unmonitored. Every stored coordinate is
    then mapped against one fresh episode listing before any episode-monitoring
    mutation occurs. A mapping failure therefore cannot leave only a subset of
    the planned episodes monitored or searched.
    """
    series_result = await sonarr.add_series_unmonitored(
        tvdb_id=plan.tvdb_id,
        root_folder_id=root_folder_id,
        quality_profile_id=quality_profile_id,
    )
    episodes = await sonarr.list_episodes(series_result.series.sonarr_id)

    # Materialise every mapping before either episode-level mutation. Tuple
    # construction stops on an error without calling monitor or search.
    episode_ids = tuple(
        map_episode_coordinate(episodes, planned_episode.coordinate)
        for planned_episode in plan.episodes
    )

    await sonarr.monitor_episodes(episode_ids)
    command = await sonarr.submit_episode_search(episode_ids)
    return ApplyPlanResult(
        plan_id=plan.plan_id,
        series=series_result.series,
        series_created=series_result.created,
        episode_ids=episode_ids,
        command=command,
    )
