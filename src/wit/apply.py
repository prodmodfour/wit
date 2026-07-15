"""Idempotent, stale-safe application orchestration for stored download plans."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from wit.clients import (
    SonarrCommandStatus,
    SonarrEpisode,
    SonarrEpisodeMonitoringResult,
    SonarrQueueItem,
    SonarrQueueState,
    SonarrSeries,
    SonarrSeriesAddResult,
    map_episode_coordinate,
)
from wit.errors import WitError
from wit.matching import normalise_show_title
from wit.plans import DownloadPlan, PlanIdentifier, PlannedEpisode

DEFAULT_PLAN_MAX_AGE_DAYS = 7
DEFAULT_PLAN_MAX_AGE = timedelta(days=DEFAULT_PLAN_MAX_AGE_DAYS)

_MAX_IDENTIFIER = 2_147_483_647
_MAX_DISCREPANCY_SUMMARY_LENGTH = 2048
ApplyEpisodeIdentifier = Annotated[int, Field(gt=0, le=_MAX_IDENTIFIER)]
DiscrepancySummary = Annotated[
    str,
    Field(min_length=1, max_length=_MAX_DISCREPANCY_SUMMARY_LENGTH),
]

_ACTIVE_QUEUE_STATES = frozenset(
    {
        SonarrQueueState.QUEUED,
        SonarrQueueState.DOWNLOADING,
        SonarrQueueState.IMPORTING,
    }
)
_REJECTED_QUEUE_STATES = frozenset(
    {
        SonarrQueueState.WARNING,
        SonarrQueueState.FAILED,
    }
)


class ApplyPlanError(WitError):
    """Base class for safe stored-plan apply failures."""


class InvalidApplyPlanRequestError(ApplyPlanError):
    """An apply option or reference time was invalid before Sonarr access."""


class ApplyPlanRejectedError(ApplyPlanError):
    """A plan was safely rejected before episode monitoring or search."""

    def __init__(
        self,
        message: str,
        *,
        skipped_file_count: int = 0,
        skipped_queue_count: int = 0,
        rejected_count: int,
    ) -> None:
        super().__init__(message)
        self.applied_count = 0
        self.skipped_file_count = skipped_file_count
        self.skipped_queue_count = skipped_queue_count
        self.rejected_count = rejected_count


class StaleDownloadPlanError(ApplyPlanRejectedError):
    """A stored plan exceeded the default maximum age without an override."""


class ApplyPlanDiscrepancyKind(StrEnum):
    """Material stored-plan differences that require another confirmation."""

    SERIES_TITLE = "series-title"
    EPISODE_TITLE = "episode-title"
    EPISODE_COORDINATE = "episode-coordinate"


class ApplyPlanDiscrepancy(BaseModel):
    """One safe, human-readable difference between a plan and Sonarr metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: ApplyPlanDiscrepancyKind
    summary: DiscrepancySummary


ApplyDiscrepancyConfirmation = Callable[[tuple[ApplyPlanDiscrepancy, ...]], bool]


class ApplyPlanMismatchError(ApplyPlanRejectedError):
    """Current Sonarr metadata materially differed and was not reconfirmed."""

    def __init__(
        self,
        discrepancies: tuple[ApplyPlanDiscrepancy, ...],
        *,
        skipped_file_count: int,
        skipped_queue_count: int,
        rejected_count: int,
    ) -> None:
        self.discrepancies = discrepancies
        difference_word = "difference" if len(discrepancies) == 1 else "differences"
        super().__init__(
            f"current Sonarr metadata has {len(discrepancies)} material {difference_word} "
            "from the stored plan and was not reconfirmed; no episode monitoring or search "
            "was performed",
            skipped_file_count=skipped_file_count,
            skipped_queue_count=skipped_queue_count,
            rejected_count=rejected_count,
        )


class ApplySonarrClient(Protocol):
    """The narrow Sonarr operations required to safely apply one plan."""

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

    async def list_queue(self) -> tuple[SonarrQueueItem, ...]:
        """Fetch the complete current Sonarr queue without mutation."""
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
    """The disjoint episode outcomes from one safe plan application."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    plan_id: PlanIdentifier
    series: SonarrSeries
    series_created: bool
    applied_episode_ids: tuple[ApplyEpisodeIdentifier, ...]
    skipped_file_episode_ids: tuple[ApplyEpisodeIdentifier, ...]
    skipped_queue_episode_ids: tuple[ApplyEpisodeIdentifier, ...]
    rejected_episode_ids: tuple[ApplyEpisodeIdentifier, ...]
    command: SonarrCommandStatus | None

    @field_validator(
        "applied_episode_ids",
        "skipped_file_episode_ids",
        "skipped_queue_episode_ids",
        "rejected_episode_ids",
    )
    @classmethod
    def _require_unique_outcome_ids(
        cls,
        value: tuple[int, ...],
    ) -> tuple[int, ...]:
        if len(set(value)) != len(value):
            raise ValueError("apply outcome must contain unique episode IDs")
        return value

    @model_validator(mode="after")
    def _validate_disjoint_complete_outcomes(self) -> Self:
        groups = (
            self.applied_episode_ids,
            self.skipped_file_episode_ids,
            self.skipped_queue_episode_ids,
            self.rejected_episode_ids,
        )
        all_ids = tuple(episode_id for group in groups for episode_id in group)
        if not all_ids:
            raise ValueError("apply result must contain at least one episode outcome")
        if len(set(all_ids)) != len(all_ids):
            raise ValueError("apply episode outcomes must be disjoint")
        if bool(self.applied_episode_ids) != (self.command is not None):
            raise ValueError("apply command must exist exactly when episodes were applied")
        return self

    @property
    def applied_count(self) -> int:
        """Return the number of episodes monitored and submitted for search."""
        return len(self.applied_episode_ids)

    @property
    def skipped_file_count(self) -> int:
        """Return the number skipped because Sonarr already has a file."""
        return len(self.skipped_file_episode_ids)

    @property
    def skipped_queue_count(self) -> int:
        """Return the number skipped because an active queue item exists."""
        return len(self.skipped_queue_episode_ids)

    @property
    def rejected_count(self) -> int:
        """Return the number blocked by a warning or failed queue item."""
        return len(self.rejected_episode_ids)


@dataclass(frozen=True, slots=True)
class _MappedPlanEpisode:
    planned: PlannedEpisode
    current: SonarrEpisode


@dataclass(frozen=True, slots=True)
class _EpisodeOutcomes:
    applied: tuple[int, ...]
    skipped_file: tuple[int, ...]
    skipped_queue: tuple[int, ...]
    rejected: tuple[int, ...]


async def apply_download_plan(
    sonarr: ApplySonarrClient,
    *,
    plan: DownloadPlan,
    root_folder_id: int,
    quality_profile_id: int,
    as_of: datetime | None = None,
    allow_stale: bool = False,
    confirm_discrepancies: ApplyDiscrepancyConfirmation | None = None,
) -> ApplyPlanResult:
    """Apply one plan after complete mapping, queue checks, and safety confirmation.

    Age validation occurs before Sonarr access. Every coordinate is then mapped
    against one fresh episode listing and the complete queue is inspected before
    either episode-level mutation. Files and active queue items are skipped,
    warning or failed queue items are rejected, and only the remaining IDs are
    monitored and sent in one targeted search.
    """
    validate_download_plan_age(
        plan,
        as_of=as_of,
        allow_stale=allow_stale,
    )

    series_result = await sonarr.add_series_unmonitored(
        tvdb_id=plan.tvdb_id,
        root_folder_id=root_folder_id,
        quality_profile_id=quality_profile_id,
    )
    episodes = await sonarr.list_episodes(series_result.series.sonarr_id)

    # Materialise every mapping before queue inspection or either episode-level
    # mutation. A missing or duplicate coordinate therefore still fails as one
    # complete preflight operation.
    mapped_episodes = _map_plan_episodes(plan, episodes)
    queue = await sonarr.list_queue()
    outcomes = _classify_episode_outcomes(
        mapped_episodes,
        queue,
        series_id=series_result.series.sonarr_id,
    )

    discrepancies = _find_metadata_discrepancies(
        plan,
        series_result.series,
        mapped_episodes,
        episodes,
    )
    if outcomes.applied and discrepancies:
        confirmed = (
            confirm_discrepancies(discrepancies) if confirm_discrepancies is not None else False
        )
        if confirmed is not True:
            raise ApplyPlanMismatchError(
                discrepancies,
                skipped_file_count=len(outcomes.skipped_file),
                skipped_queue_count=len(outcomes.skipped_queue),
                rejected_count=len(outcomes.rejected) + len(outcomes.applied),
            )

    command: SonarrCommandStatus | None = None
    if outcomes.applied:
        await sonarr.monitor_episodes(outcomes.applied)
        command = await sonarr.submit_episode_search(outcomes.applied)

    return ApplyPlanResult(
        plan_id=plan.plan_id,
        series=series_result.series,
        series_created=series_result.created,
        applied_episode_ids=outcomes.applied,
        skipped_file_episode_ids=outcomes.skipped_file,
        skipped_queue_episode_ids=outcomes.skipped_queue,
        rejected_episode_ids=outcomes.rejected,
        command=command,
    )


def validate_download_plan_age(
    plan: DownloadPlan,
    *,
    as_of: datetime | None = None,
    allow_stale: bool = False,
) -> None:
    """Reject a plan outside the default seven-day apply window."""
    if not isinstance(allow_stale, bool):
        raise InvalidApplyPlanRequestError("allow-stale must be a boolean")
    reference_time = _validate_reference_time(as_of)
    if not allow_stale and reference_time - plan.created_at > DEFAULT_PLAN_MAX_AGE:
        raise StaleDownloadPlanError(
            f"download plan {plan.plan_id} is older than the "
            f"{DEFAULT_PLAN_MAX_AGE_DAYS}-day apply limit; review it and retry with "
            "--allow-stale to override",
            rejected_count=plan.episode_count,
        )


def _map_plan_episodes(
    plan: DownloadPlan,
    episodes: tuple[SonarrEpisode, ...],
) -> tuple[_MappedPlanEpisode, ...]:
    episodes_by_id = {episode.episode_id: episode for episode in episodes}
    return tuple(
        _MappedPlanEpisode(
            planned=planned_episode,
            current=episodes_by_id[map_episode_coordinate(episodes, planned_episode.coordinate)],
        )
        for planned_episode in plan.episodes
    )


def _classify_episode_outcomes(
    mapped_episodes: tuple[_MappedPlanEpisode, ...],
    queue: tuple[SonarrQueueItem, ...],
    *,
    series_id: int,
) -> _EpisodeOutcomes:
    planned_ids = {mapped.current.episode_id for mapped in mapped_episodes}
    queue_states: dict[int, set[SonarrQueueState]] = {}
    for item in queue:
        if item.episode_id not in planned_ids:
            continue
        if item.series_id is not None and item.series_id != series_id:
            continue
        assert item.episode_id is not None
        queue_states.setdefault(item.episode_id, set()).add(item.state)

    applied: list[int] = []
    skipped_file: list[int] = []
    skipped_queue: list[int] = []
    rejected: list[int] = []
    for mapped in mapped_episodes:
        episode = mapped.current
        states = queue_states.get(episode.episode_id, set())
        if episode.has_file:
            skipped_file.append(episode.episode_id)
        elif states & _ACTIVE_QUEUE_STATES:
            skipped_queue.append(episode.episode_id)
        elif states & _REJECTED_QUEUE_STATES:
            rejected.append(episode.episode_id)
        else:
            applied.append(episode.episode_id)

    return _EpisodeOutcomes(
        applied=tuple(applied),
        skipped_file=tuple(skipped_file),
        skipped_queue=tuple(skipped_queue),
        rejected=tuple(rejected),
    )


def _find_metadata_discrepancies(
    plan: DownloadPlan,
    series: SonarrSeries,
    mapped_episodes: tuple[_MappedPlanEpisode, ...],
    all_episodes: tuple[SonarrEpisode, ...],
) -> tuple[ApplyPlanDiscrepancy, ...]:
    discrepancies: list[ApplyPlanDiscrepancy] = []
    if not _titles_materially_equal(plan.show_title, series.title):
        discrepancies.append(
            ApplyPlanDiscrepancy(
                kind=ApplyPlanDiscrepancyKind.SERIES_TITLE,
                summary=(f'Series title: stored "{plan.show_title}"; Sonarr "{series.title}".'),
            )
        )

    for mapped in mapped_episodes:
        planned = mapped.planned
        current = mapped.current
        if _titles_materially_equal(planned.title, current.title):
            continue

        matching_coordinates = tuple(
            (episode.season_number, episode.episode_number)
            for episode in all_episodes
            if _titles_materially_equal(planned.title, episode.title)
        )
        if len(matching_coordinates) == 1 and matching_coordinates[0] != planned.coordinate:
            current_label = _coordinate_label(matching_coordinates[0])
            discrepancies.append(
                ApplyPlanDiscrepancy(
                    kind=ApplyPlanDiscrepancyKind.EPISODE_COORDINATE,
                    summary=(
                        f'{planned.label} "{planned.title}": Sonarr assigns this title to '
                        f'{current_label}; {planned.label} is "{current.title}".'
                    ),
                )
            )
            continue

        discrepancies.append(
            ApplyPlanDiscrepancy(
                kind=ApplyPlanDiscrepancyKind.EPISODE_TITLE,
                summary=(
                    f'{planned.label}: stored title "{planned.title}"; Sonarr "{current.title}".'
                ),
            )
        )

    return tuple(discrepancies)


def _titles_materially_equal(first: str, second: str) -> bool:
    try:
        return normalise_show_title(first) == normalise_show_title(second)
    except ValueError:
        return first.casefold() == second.casefold()


def _coordinate_label(coordinate: tuple[int, int]) -> str:
    season_number, episode_number = coordinate
    return f"S{season_number:02d}E{episode_number:02d}"


def _validate_reference_time(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise InvalidApplyPlanRequestError("apply reference time must be timezone-aware")
    try:
        if value.utcoffset() is None:
            raise ValueError("missing UTC offset")
        return value.astimezone(UTC)
    except (OverflowError, ValueError):
        raise InvalidApplyPlanRequestError("apply reference time must be timezone-aware") from None
