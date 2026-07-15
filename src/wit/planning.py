"""Read-only orchestration for constructing download plans."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from pydantic import ValidationError

from wit.clients import (
    TvmazeEpisodeCollection,
    TvmazeShow,
    TvmazeShowSearchResult,
)
from wit.errors import WitError
from wit.matching import ShowMatchResult, match_show
from wit.plans import (
    DOWNLOAD_PLAN_SCHEMA_VERSION,
    DownloadPlan,
    PlannedEpisode,
)
from wit.selection import EpisodeSelector, select_episodes

PlanningClock = Callable[[], datetime]
PlanIdentifierFactory = Callable[[datetime], str]

_MAX_IDENTIFIER = 2_147_483_647


class PlanningMetadataClient(Protocol):
    """The read-only metadata operations required to construct a plan."""

    async def search_shows(self, title: str) -> tuple[TvmazeShowSearchResult, ...]:
        """Return show candidates for a user-supplied title."""
        ...

    async def get_episodes(self, show_id: int) -> TvmazeEpisodeCollection:
        """Return regular and special episodes for one TVmaze show."""
        ...


class PlanningError(WitError):
    """Base class for safe download-planning failures."""


class InvalidPlanningRequestError(PlanningError):
    """A planning input is invalid before a plan can be constructed."""


class ShowNotFoundError(PlanningError):
    """TVmaze returned no candidate for the supplied show query."""


class ShowCandidateSelectionRequiredError(PlanningError):
    """A title cannot be selected safely without an explicit candidate ID."""

    def __init__(
        self,
        message: str,
        candidates: tuple[TvmazeShowSearchResult, ...],
    ) -> None:
        self.candidates = candidates
        super().__init__(message)


class MissingTvdbIdentityError(PlanningError):
    """The selected TVmaze show cannot be mapped to Sonarr by TVDB identity."""


def generate_plan_identifier(created_at: datetime) -> str:
    """Generate a filesystem-safe, collision-resistant identifier for one plan."""
    timestamp = created_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"plan-{timestamp}-{secrets.token_hex(8)}"


async def build_download_plan(
    metadata_client: PlanningMetadataClient,
    *,
    query: str,
    selector: EpisodeSelector,
    clock: PlanningClock,
    show_year: int | None = None,
    candidate_tvmaze_id: int | None = None,
    plan_identifier_factory: PlanIdentifierFactory = generate_plan_identifier,
) -> DownloadPlan:
    """Resolve metadata and construct a secret-free plan without persisting it.

    The caller owns rendering and persistence so the complete plan can be shown
    before any state file is written.
    """
    _validate_candidate_identifier(candidate_tvmaze_id)

    search_results = await metadata_client.search_shows(query)
    try:
        match_result = match_show(query, search_results, year=show_year)
    except (TypeError, ValueError):
        raise InvalidPlanningRequestError("show query or year is invalid") from None

    selected_show = _resolve_show(match_result, candidate_tvmaze_id)
    if selected_show.tvdb_id is None:
        raise MissingTvdbIdentityError(
            "matched show has no TVDB identity required for later Sonarr mapping"
        )

    episodes = await metadata_client.get_episodes(selected_show.tvmaze_id)
    reference_time = clock()
    selected_episodes = select_episodes(
        episodes,
        selector,
        clock=lambda: reference_time,
    )

    try:
        return DownloadPlan(
            schema_version=DOWNLOAD_PLAN_SCHEMA_VERSION,
            plan_id=plan_identifier_factory(reference_time),
            created_at=reference_time,
            show_title=selected_show.title,
            show_year=selected_show.premiere_year,
            tvmaze_id=selected_show.tvmaze_id,
            tvdb_id=selected_show.tvdb_id,
            selector_summary=render_selector_summary(selector),
            episodes=tuple(
                PlannedEpisode(
                    season_number=episode.season_number,
                    episode_number=_numbered_episode(episode.episode_number),
                    title=episode.title,
                )
                for episode in selected_episodes
            ),
        )
    except (TypeError, ValueError, ValidationError):
        raise PlanningError("download plan could not be constructed safely") from None


def render_selector_summary(selector: EpisodeSelector) -> str:
    """Return a deterministic human-readable summary of one valid selector."""
    if selector.first_count is not None:
        episode_word = "episode" if selector.first_count == 1 else "episodes"
        summary = f"first {selector.first_count} aired regular {episode_word}"
        if selector.season_number is not None:
            summary += f" in season {selector.season_number}"
        return summary

    if selector.range_start is not None and selector.range_end is not None:
        assert selector.season_number is not None
        return (
            "aired regular episodes "
            f"S{selector.season_number:02d}E{selector.range_start:02d}-"
            f"S{selector.season_number:02d}E{selector.range_end:02d}"
        )

    return "all aired regular episodes"


def _resolve_show(
    match_result: ShowMatchResult,
    candidate_tvmaze_id: int | None,
) -> TvmazeShow:
    if candidate_tvmaze_id is None:
        if match_result.match is not None:
            return match_result.match.show
        if not match_result.candidates:
            raise ShowNotFoundError("TVmaze returned no show candidates for that query")
        raise ShowCandidateSelectionRequiredError(
            "title match is ambiguous; select one candidate explicitly",
            match_result.candidates,
        )

    selected = tuple(
        candidate
        for candidate in match_result.candidates
        if candidate.show.tvmaze_id == candidate_tvmaze_id
    )
    if len(selected) == 1:
        return selected[0].show
    if len(selected) > 1:
        raise PlanningError("TVmaze returned duplicate records for the selected candidate")
    if not match_result.candidates:
        raise ShowNotFoundError("TVmaze returned no show candidates for that query")
    raise ShowCandidateSelectionRequiredError(
        "selected TVmaze ID is not a candidate for that query",
        match_result.candidates,
    )


def _validate_candidate_identifier(value: int | None) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= _MAX_IDENTIFIER:
        raise InvalidPlanningRequestError("candidate TVmaze ID must be a positive integer")


def _numbered_episode(value: int | None) -> int:
    if value is None:
        raise PlanningError("selected regular episode has no episode number")
    return value
