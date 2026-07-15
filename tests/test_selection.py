"""Boundary tests for pure, deterministic episode-selection rules."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import pytest

from wit.clients import TvmazeEpisode, TvmazeEpisodeCollection, TvmazeEpisodeType
from wit.selection import (
    EmptyEpisodeSelectionError,
    EpisodeSelectionError,
    EpisodeSelector,
    InvalidEpisodeSelectorError,
    select_episodes,
)

_NOW = datetime(2025, 1, 10, 12, tzinfo=UTC)
_PAST_DATE = date(2025, 1, 3)


def _episode(
    tvmaze_id: int,
    season_number: int,
    episode_number: int | None,
    *,
    episode_type: TvmazeEpisodeType = TvmazeEpisodeType.REGULAR,
    air_date: date | None = _PAST_DATE,
    air_timestamp: datetime | None = None,
) -> TvmazeEpisode:
    return TvmazeEpisode(
        tvmaze_id=tvmaze_id,
        title=f"Episode {tvmaze_id}",
        season_number=season_number,
        episode_number=episode_number,
        episode_type=episode_type,
        air_date=air_date,
        air_time=None,
        air_timestamp=air_timestamp,
    )


def _coordinates(episodes: tuple[TvmazeEpisode, ...]) -> list[tuple[int, int | None]]:
    return [(episode.season_number, episode.episode_number) for episode in episodes]


def test_selects_first_aired_regular_episodes_across_series_in_coordinate_order() -> None:
    clock_calls = 0

    def clock() -> datetime:
        nonlocal clock_calls
        clock_calls += 1
        return _NOW

    episodes = TvmazeEpisodeCollection(
        regular=(
            _episode(201, 2, 1),
            _episode(104, 1, 4, air_date=None),
            _episode(102, 1, 2, air_timestamp=_NOW),
            _episode(1, 0, 1),
            _episode(103, 1, 3, air_timestamp=_NOW + timedelta(microseconds=1)),
            _episode(101, 1, 1),
            _episode(202, 2, 2, air_date=_NOW.date() + timedelta(days=1)),
        ),
        specials=(
            _episode(
                900,
                1,
                3,
                episode_type=TvmazeEpisodeType.SIGNIFICANT_SPECIAL,
            ),
        ),
    )

    selected = select_episodes(
        episodes,
        EpisodeSelector(first_count=3),
        clock=clock,
    )

    assert _coordinates(selected) == [(1, 1), (1, 2), (2, 1)]
    assert clock_calls == 1


def test_selects_first_aired_episodes_only_within_the_requested_season() -> None:
    episodes = (
        _episode(203, 2, 3),
        _episode(101, 1, 1),
        _episode(201, 2, 1),
        _episode(202, 2, 2),
    )

    selected = select_episodes(
        episodes,
        EpisodeSelector(first_count=2, season_number=2),
        clock=lambda: _NOW,
    )

    assert _coordinates(selected) == [(2, 1), (2, 2)]


def test_first_count_returns_all_available_episodes_when_fewer_have_aired() -> None:
    episodes = (
        _episode(101, 1, 1),
        _episode(102, 1, 2, air_date=_NOW.date() + timedelta(days=1)),
    )

    selected = select_episodes(
        episodes,
        EpisodeSelector(first_count=4),
        clock=lambda: _NOW,
    )

    assert _coordinates(selected) == [(1, 1)]


def test_selects_an_inclusive_episode_range_and_keeps_default_exclusions() -> None:
    episodes = (
        _episode(205, 2, 5),
        _episode(204, 2, 4),
        _episode(203, 2, 3, air_timestamp=_NOW + timedelta(seconds=1)),
        _episode(202, 2, 2),
        _episode(201, 2, 1),
        _episode(302, 3, 2),
        _episode(
            902,
            2,
            2,
            episode_type=TvmazeEpisodeType.INSIGNIFICANT_SPECIAL,
        ),
    )

    selected = select_episodes(
        episodes,
        EpisodeSelector(season_number=2, range_start=2, range_end=4),
        clock=lambda: _NOW,
    )

    assert _coordinates(selected) == [(2, 2), (2, 4)]


def test_all_aired_uses_timestamp_boundaries_and_date_only_fallback() -> None:
    equivalent_offset = datetime.fromisoformat("2025-01-10T07:00:00-05:00")
    episodes = (
        _episode(101, 1, 1, air_date=_NOW.date(), air_timestamp=equivalent_offset),
        _episode(102, 1, 2, air_date=_NOW.date(), air_timestamp=None),
        _episode(
            103,
            1,
            3,
            air_date=_NOW.date(),
            air_timestamp=_NOW + timedelta(seconds=1),
        ),
        _episode(104, 1, 4, air_date=None, air_timestamp=_NOW - timedelta(days=1)),
    )

    selected = select_episodes(
        episodes,
        EpisodeSelector(all_aired=True),
        clock=lambda: _NOW,
    )

    assert _coordinates(selected) == [(1, 1), (1, 2)]


@pytest.mark.parametrize(
    "factory",
    [
        lambda: EpisodeSelector(),
        lambda: EpisodeSelector(season_number=1),
        lambda: EpisodeSelector(first_count=1, all_aired=True),
        lambda: EpisodeSelector(
            first_count=1,
            season_number=1,
            range_start=1,
            range_end=2,
        ),
        lambda: EpisodeSelector(
            season_number=1,
            range_start=1,
            range_end=2,
            all_aired=True,
        ),
        lambda: EpisodeSelector(season_number=1, range_start=1),
        lambda: EpisodeSelector(season_number=1, range_end=2),
        lambda: EpisodeSelector(range_start=1, range_end=2),
        lambda: EpisodeSelector(season_number=1, all_aired=True),
    ],
)
def test_rejects_missing_incomplete_and_conflicting_selectors(
    factory: Callable[[], EpisodeSelector],
) -> None:
    with pytest.raises(InvalidEpisodeSelectorError):
        factory()


@pytest.mark.parametrize("invalid_value", [0, -1, True])
def test_rejects_zero_negative_and_boolean_counts(invalid_value: int) -> None:
    with pytest.raises(InvalidEpisodeSelectorError, match="positive integer"):
        EpisodeSelector(first_count=invalid_value)


@pytest.mark.parametrize("invalid_value", [0, -1, True])
def test_rejects_invalid_seasons_and_range_bounds(invalid_value: int) -> None:
    with pytest.raises(InvalidEpisodeSelectorError, match="positive integer"):
        EpisodeSelector(first_count=1, season_number=invalid_value)
    with pytest.raises(InvalidEpisodeSelectorError, match="positive integer"):
        EpisodeSelector(season_number=1, range_start=invalid_value, range_end=2)
    with pytest.raises(InvalidEpisodeSelectorError, match="positive integer"):
        EpisodeSelector(season_number=1, range_start=1, range_end=invalid_value)


def test_rejects_a_reversed_episode_range() -> None:
    with pytest.raises(InvalidEpisodeSelectorError, match="must not exceed"):
        EpisodeSelector(season_number=2, range_start=4, range_end=2)


def test_rejects_an_empty_selection() -> None:
    episodes = (
        _episode(1, 0, 1),
        _episode(101, 1, 1, air_date=None),
        _episode(102, 1, 2, air_date=_NOW.date() + timedelta(days=1)),
        _episode(
            900,
            1,
            None,
            episode_type=TvmazeEpisodeType.SIGNIFICANT_SPECIAL,
        ),
    )

    with pytest.raises(EmptyEpisodeSelectionError, match="no currently aired"):
        select_episodes(
            episodes,
            EpisodeSelector(all_aired=True),
            clock=lambda: _NOW,
        )


def test_requires_a_timezone_aware_clock() -> None:
    with pytest.raises(EpisodeSelectionError, match="timezone-aware"):
        select_episodes(
            (_episode(101, 1, 1),),
            EpisodeSelector(all_aired=True),
            clock=lambda: datetime(2025, 1, 10, 12),
        )


def test_rejects_duplicate_regular_episode_coordinates() -> None:
    episodes = (
        _episode(101, 1, 1),
        _episode(999, 1, 1),
    )

    with pytest.raises(EpisodeSelectionError, match="duplicate coordinate S01E01"):
        select_episodes(
            episodes,
            EpisodeSelector(all_aired=True),
            clock=lambda: _NOW,
        )
