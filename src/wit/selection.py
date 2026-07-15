"""Pure, deterministic episode-selection rules for read-only planning."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from wit.clients.tvmaze import (
    TvmazeEpisode,
    TvmazeEpisodeCollection,
    TvmazeEpisodeType,
)
from wit.errors import WitError

EpisodeClock = Callable[[], datetime]


class EpisodeSelectionError(WitError):
    """Base class for safe episode-selection failures."""


class InvalidEpisodeSelectorError(EpisodeSelectionError):
    """The requested combination of episode selectors is invalid."""


class EmptyEpisodeSelectionError(EpisodeSelectionError):
    """No currently aired regular episode satisfies a valid selector."""


@dataclass(frozen=True, slots=True)
class EpisodeSelector:
    """Exactly one selection rule, with an optional season for first-N requests."""

    first_count: int | None = None
    season_number: int | None = None
    range_start: int | None = None
    range_end: int | None = None
    all_aired: bool = False

    def __post_init__(self) -> None:
        _validate_optional_positive_integer(self.first_count, "first episode count")
        _validate_optional_positive_integer(self.season_number, "season number")
        _validate_optional_positive_integer(self.range_start, "episode range start")
        _validate_optional_positive_integer(self.range_end, "episode range end")
        if not isinstance(self.all_aired, bool):
            raise InvalidEpisodeSelectorError("all-aired selector must be a boolean")

        has_any_range_bound = self.range_start is not None or self.range_end is not None
        has_complete_range = self.range_start is not None and self.range_end is not None
        if has_any_range_bound and not has_complete_range:
            raise InvalidEpisodeSelectorError("episode range requires both a start and an end")
        if (
            has_complete_range
            and self.range_start is not None
            and self.range_end is not None
            and self.range_start > self.range_end
        ):
            raise InvalidEpisodeSelectorError("episode range start must not exceed its end")

        rule_count = sum(
            (
                self.first_count is not None,
                has_complete_range,
                self.all_aired,
            )
        )
        if rule_count != 1:
            raise InvalidEpisodeSelectorError(
                "exactly one of first, episode range, or all-aired must be selected"
            )
        if has_complete_range and self.season_number is None:
            raise InvalidEpisodeSelectorError("episode range requires a season number")
        if self.all_aired and self.season_number is not None:
            raise InvalidEpisodeSelectorError("all-aired cannot be combined with a season selector")


def select_episodes(
    episodes: Iterable[TvmazeEpisode] | TvmazeEpisodeCollection,
    selector: EpisodeSelector,
    *,
    clock: EpisodeClock,
) -> tuple[TvmazeEpisode, ...]:
    """Select currently aired regular episodes in season/episode order.

    A timezone-aware clock is required. A complete TVmaze air timestamp is compared
    precisely; when only an air date is known, that date is considered aired on and
    after the clock's current calendar date.
    """
    if not isinstance(selector, EpisodeSelector):
        raise InvalidEpisodeSelectorError("episode selector is invalid")

    reference_time = _read_clock(clock)
    source = (
        (*episodes.regular, *episodes.specials)
        if isinstance(episodes, TvmazeEpisodeCollection)
        else tuple(episodes)
    )
    candidates = tuple(
        episode
        for episode in source
        if _is_regular_numbered_episode(episode) and _is_in_selector_scope(episode, selector)
    )
    _reject_duplicate_coordinates(candidates)

    aired = tuple(
        sorted(
            (episode for episode in candidates if _has_aired(episode, reference_time)),
            key=_episode_sort_key,
        )
    )
    selected = aired[: selector.first_count] if selector.first_count is not None else aired
    if not selected:
        raise EmptyEpisodeSelectionError(
            "episode selector matched no currently aired regular episodes"
        )
    return selected


def _validate_optional_positive_integer(value: int | None, label: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InvalidEpisodeSelectorError(f"{label} must be a positive integer")


def _read_clock(clock: EpisodeClock) -> datetime:
    if not callable(clock):
        raise EpisodeSelectionError("episode-selection clock must be callable")
    reference_time = clock()
    if not isinstance(reference_time, datetime) or reference_time.tzinfo is None:
        raise EpisodeSelectionError("episode-selection clock must return a timezone-aware datetime")
    try:
        if reference_time.utcoffset() is None:
            raise ValueError("missing UTC offset")
        reference_time.astimezone(UTC)
    except (OverflowError, ValueError):
        raise EpisodeSelectionError(
            "episode-selection clock must return a timezone-aware datetime"
        ) from None
    return reference_time


def _is_regular_numbered_episode(episode: TvmazeEpisode) -> bool:
    return (
        episode.episode_type is TvmazeEpisodeType.REGULAR
        and episode.season_number > 0
        and episode.episode_number is not None
    )


def _is_in_selector_scope(episode: TvmazeEpisode, selector: EpisodeSelector) -> bool:
    if selector.season_number is not None and episode.season_number != selector.season_number:
        return False
    if selector.range_start is None or selector.range_end is None:
        return True
    assert episode.episode_number is not None
    return selector.range_start <= episode.episode_number <= selector.range_end


def _has_aired(episode: TvmazeEpisode, reference_time: datetime) -> bool:
    if episode.air_date is None:
        return False
    if episode.air_timestamp is not None:
        timestamp = episode.air_timestamp
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise EpisodeSelectionError("episode air timestamp must be timezone-aware")
        return timestamp.astimezone(UTC) <= reference_time.astimezone(UTC)
    return episode.air_date <= reference_time.date()


def _reject_duplicate_coordinates(episodes: tuple[TvmazeEpisode, ...]) -> None:
    seen: set[tuple[int, int]] = set()
    for episode in episodes:
        assert episode.episode_number is not None
        coordinate = (episode.season_number, episode.episode_number)
        if coordinate in seen:
            season_number, episode_number = coordinate
            raise EpisodeSelectionError(
                f"episode metadata contains duplicate coordinate "
                f"S{season_number:02d}E{episode_number:02d}"
            )
        seen.add(coordinate)


def _episode_sort_key(episode: TvmazeEpisode) -> tuple[int, int, int]:
    assert episode.episode_number is not None
    return episode.season_number, episode.episode_number, episode.tvmaze_id
