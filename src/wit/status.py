"""Read-only Sonarr-backed progress reporting for stored download plans."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from wit.clients import (
    SonarrCommandFailedError,
    SonarrCommandRejectedError,
    SonarrCommandState,
    SonarrCommandStatus,
    SonarrEpisode,
    SonarrEpisodeMappingError,
    SonarrQueueItem,
    SonarrQueueState,
    SonarrSeries,
    map_episode_coordinate,
)
from wit.errors import WitError
from wit.plans import DownloadPlan, PlanIdentifier, PlannedEpisode

_MAX_IDENTIFIER = 2_147_483_647
_MAX_ERROR_DETAIL_LENGTH = 1024
StatusIdentifier = Annotated[int, Field(gt=0, le=_MAX_IDENTIFIER)]
StatusErrorDetail = Annotated[str, Field(min_length=1, max_length=_MAX_ERROR_DETAIL_LENGTH)]


class RequestStatusError(WitError):
    """Base class for safe request-status failures."""


class InvalidRequestStatusError(RequestStatusError):
    """A request-status option was invalid before Sonarr access."""


class RequestEpisodeState(StrEnum):
    """Stable progress classifications for one planned episode."""

    PLANNED = "planned"
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    IMPORTED = "imported"
    MISSING = "missing"
    WARNING = "warning"
    FAILED = "failed"


class RequestEpisodeErrorKind(StrEnum):
    """Safe sources of per-episode status errors."""

    MAPPING = "mapping"
    QUEUE_WARNING = "queue-warning"
    QUEUE_FAILED = "queue-failed"
    COMMAND_FAILED = "command-failed"
    COMMAND_REJECTED = "command-rejected"


class _RequestStatusModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        strict=True,
    )


class RequestEpisodeError(_RequestStatusModel):
    """One bounded error derived from typed Sonarr state, never a raw payload."""

    kind: RequestEpisodeErrorKind
    detail: StatusErrorDetail


class RequestEpisodeStatus(_RequestStatusModel):
    """Current Sonarr observations and classification for one plan coordinate."""

    planned_episode: PlannedEpisode
    sonarr_episode_id: StatusIdentifier | None
    monitored: bool | None
    has_file: bool | None
    queue_items: tuple[SonarrQueueItem, ...]
    command_state: SonarrCommandState | None
    state: RequestEpisodeState
    errors: tuple[RequestEpisodeError, ...]

    @model_validator(mode="after")
    def _validate_mapping_observations(self) -> Self:
        mapped = self.sonarr_episode_id is not None
        if mapped != (self.monitored is not None and self.has_file is not None):
            raise ValueError("mapped request status must include monitoring and file state")
        if not mapped and self.queue_items:
            raise ValueError("unmapped request status must not include queue items")
        return self


class RequestStatusResult(_RequestStatusModel):
    """Complete, plan-ordered Sonarr progress for one stored request."""

    plan_id: PlanIdentifier
    series: SonarrSeries | None
    command_id: StatusIdentifier | None
    command_state: SonarrCommandState | None
    episodes: tuple[RequestEpisodeStatus, ...]

    @model_validator(mode="after")
    def _validate_complete_result(self) -> Self:
        if not self.episodes:
            raise ValueError("request status must contain at least one episode")
        coordinates = tuple(item.planned_episode.coordinate for item in self.episodes)
        if len(set(coordinates)) != len(coordinates):
            raise ValueError("request status must contain unique episode coordinates")
        if self.command_state is not None and self.command_id is None:
            raise ValueError("request command state requires a command ID")
        return self


class StatusSonarrClient(Protocol):
    """The narrow read-only Sonarr operations required for request status."""

    async def find_series_by_tvdb_id(self, tvdb_id: int) -> SonarrSeries | None:
        """Find the planned series by stable TVDB identity."""
        ...

    async def list_episodes(self, series_id: int) -> tuple[SonarrEpisode, ...]:
        """List current episode file and monitoring state."""
        ...

    async def list_queue(self) -> tuple[SonarrQueueItem, ...]:
        """List the complete current queue."""
        ...

    async def get_command_status(self, command_id: int) -> SonarrCommandStatus:
        """Read one known apply command when its ID is available."""
        ...


@dataclass(frozen=True, slots=True)
class _MappedEpisode:
    planned: PlannedEpisode
    current: SonarrEpisode | None
    mapping_error: RequestEpisodeError | None = None


@dataclass(frozen=True, slots=True)
class _CommandObservation:
    command_id: int | None
    state: SonarrCommandState | None
    error: RequestEpisodeError | None = None


async def get_request_status(
    sonarr: StatusSonarrClient,
    *,
    plan: DownloadPlan,
    command_id: int | None = None,
) -> RequestStatusResult:
    """Report current Sonarr progress for every episode in one stored plan.

    The optional command ID is the bounded ID returned by the apply operation.
    When supplied, its current state is associated with mapped, monitored plan
    episodes. Concrete file and queue observations take precedence over command
    state, while command failures remain attached as per-episode errors.
    """
    validated_command_id = _validate_optional_command_id(command_id)
    series = await sonarr.find_series_by_tvdb_id(plan.tvdb_id)

    queue: tuple[SonarrQueueItem, ...] = ()
    if series is None:
        mapped_episodes = tuple(
            _MappedEpisode(planned=item, current=None) for item in plan.episodes
        )
    else:
        current_episodes = await sonarr.list_episodes(series.sonarr_id)
        mapped_episodes = _map_plan_episodes(plan, current_episodes)
        queue = await sonarr.list_queue()

    command = await _get_command_observation(sonarr, validated_command_id)
    statuses = tuple(
        _build_episode_status(
            mapped,
            queue,
            series_id=series.sonarr_id if series is not None else None,
            command=command,
        )
        for mapped in mapped_episodes
    )
    return RequestStatusResult(
        plan_id=plan.plan_id,
        series=series,
        command_id=command.command_id,
        command_state=command.state,
        episodes=statuses,
    )


def _map_plan_episodes(
    plan: DownloadPlan,
    current_episodes: tuple[SonarrEpisode, ...],
) -> tuple[_MappedEpisode, ...]:
    episodes_by_id = {episode.episode_id: episode for episode in current_episodes}
    mapped: list[_MappedEpisode] = []
    for planned in plan.episodes:
        try:
            episode_id = map_episode_coordinate(current_episodes, planned.coordinate)
        except SonarrEpisodeMappingError as error:
            mapped.append(
                _MappedEpisode(
                    planned=planned,
                    current=None,
                    mapping_error=RequestEpisodeError(
                        kind=RequestEpisodeErrorKind.MAPPING,
                        detail=str(error),
                    ),
                )
            )
        else:
            mapped.append(_MappedEpisode(planned=planned, current=episodes_by_id[episode_id]))
    return tuple(mapped)


async def _get_command_observation(
    sonarr: StatusSonarrClient,
    command_id: int | None,
) -> _CommandObservation:
    if command_id is None:
        return _CommandObservation(command_id=None, state=None)

    try:
        command = await sonarr.get_command_status(command_id)
    except SonarrCommandFailedError:
        return _CommandObservation(
            command_id=command_id,
            state=SonarrCommandState.FAILED,
            error=RequestEpisodeError(
                kind=RequestEpisodeErrorKind.COMMAND_FAILED,
                detail=f"Sonarr EpisodeSearch command {command_id} failed.",
            ),
        )
    except SonarrCommandRejectedError:
        return _CommandObservation(
            command_id=command_id,
            state=None,
            error=RequestEpisodeError(
                kind=RequestEpisodeErrorKind.COMMAND_REJECTED,
                detail=f"Sonarr EpisodeSearch command {command_id} was rejected or stopped.",
            ),
        )

    if command.command_id != command_id:
        raise RequestStatusError("Sonarr returned an inconsistent request command identity")
    return _CommandObservation(command_id=command_id, state=command.state)


def _build_episode_status(
    mapped: _MappedEpisode,
    queue: tuple[SonarrQueueItem, ...],
    *,
    series_id: int | None,
    command: _CommandObservation,
) -> RequestEpisodeStatus:
    current = mapped.current
    if current is None:
        unmapped_errors = (mapped.mapping_error,) if mapped.mapping_error is not None else ()
        state = (
            RequestEpisodeState.MISSING
            if mapped.mapping_error is not None
            else RequestEpisodeState.PLANNED
        )
        return RequestEpisodeStatus(
            planned_episode=mapped.planned,
            sonarr_episode_id=None,
            monitored=None,
            has_file=None,
            queue_items=(),
            command_state=None,
            state=state,
            errors=unmapped_errors,
        )

    matching_queue = tuple(
        sorted(
            (
                item
                for item in queue
                if item.episode_id == current.episode_id
                and (item.series_id is None or item.series_id == series_id)
            ),
            key=lambda item: item.queue_id,
        )
    )
    mapped_errors = list(_queue_errors(matching_queue))

    episode_command_state: SonarrCommandState | None = None
    if current.monitored:
        episode_command_state = command.state
        if command.error is not None:
            mapped_errors.append(command.error)

    return RequestEpisodeStatus(
        planned_episode=mapped.planned,
        sonarr_episode_id=current.episode_id,
        monitored=current.monitored,
        has_file=current.has_file,
        queue_items=matching_queue,
        command_state=episode_command_state,
        state=_classify_episode(
            current,
            matching_queue,
            command_state=episode_command_state,
            command_error=command.error if current.monitored else None,
        ),
        errors=tuple(mapped_errors),
    )


def _queue_errors(queue_items: tuple[SonarrQueueItem, ...]) -> tuple[RequestEpisodeError, ...]:
    errors: list[RequestEpisodeError] = []
    for item in queue_items:
        if item.state is SonarrQueueState.WARNING:
            errors.append(
                RequestEpisodeError(
                    kind=RequestEpisodeErrorKind.QUEUE_WARNING,
                    detail=f"Sonarr queue item {item.queue_id} reports a warning.",
                )
            )
        elif item.state is SonarrQueueState.FAILED:
            errors.append(
                RequestEpisodeError(
                    kind=RequestEpisodeErrorKind.QUEUE_FAILED,
                    detail=f"Sonarr queue item {item.queue_id} reports a failure.",
                )
            )
    return tuple(errors)


def _classify_episode(
    episode: SonarrEpisode,
    queue_items: tuple[SonarrQueueItem, ...],
    *,
    command_state: SonarrCommandState | None,
    command_error: RequestEpisodeError | None,
) -> RequestEpisodeState:
    if episode.has_file:
        return RequestEpisodeState.IMPORTED

    queue_states = {item.state for item in queue_items}
    if SonarrQueueState.FAILED in queue_states:
        return RequestEpisodeState.FAILED
    if SonarrQueueState.WARNING in queue_states:
        return RequestEpisodeState.WARNING
    if queue_states & {SonarrQueueState.DOWNLOADING, SonarrQueueState.IMPORTING}:
        return RequestEpisodeState.DOWNLOADING
    if SonarrQueueState.QUEUED in queue_states:
        return RequestEpisodeState.QUEUED

    if command_error is not None or command_state in {
        SonarrCommandState.FAILED,
        SonarrCommandState.ABORTED,
        SonarrCommandState.CANCELLED,
        SonarrCommandState.ORPHANED,
    }:
        return RequestEpisodeState.FAILED
    if command_state in {SonarrCommandState.QUEUED, SonarrCommandState.STARTED}:
        return RequestEpisodeState.QUEUED
    if episode.monitored:
        return RequestEpisodeState.MISSING
    return RequestEpisodeState.PLANNED


def _validate_optional_command_id(value: int | None) -> int | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or value > _MAX_IDENTIFIER
    ):
        raise InvalidRequestStatusError("request command ID must be a positive integer")
    return value
