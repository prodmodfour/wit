"""Pure, deterministic show-title matching for planning."""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum

from wit.clients.tvmaze import TvmazeShowSearchResult


class _TitleMatchKind(IntEnum):
    EXACT_TITLE = 0
    KNOWN_ALIAS = 1
    NONE = 2


@dataclass(frozen=True, slots=True)
class ShowMatchResult:
    """A confident match or deterministically ordered candidates for selection."""

    match: TvmazeShowSearchResult | None
    candidates: tuple[TvmazeShowSearchResult, ...]

    @property
    def requires_selection(self) -> bool:
        """Return whether candidates exist but no candidate is safe to choose automatically."""
        return self.match is None and bool(self.candidates)


@dataclass(frozen=True, slots=True)
class _RankedCandidate:
    result: TvmazeShowSearchResult
    match_kind: _TitleMatchKind
    normalised_title: str


def normalise_show_title(title: str) -> str:
    """Fold harmless case, punctuation, and whitespace differences in a title."""
    if not isinstance(title, str):
        raise TypeError("show title must be text")

    folded = unicodedata.normalize("NFKC", title).casefold()
    normalised = "".join(character for character in folded if character.isalnum())
    if not normalised:
        raise ValueError("show title must contain a letter or number")
    return normalised


def match_show(
    query: str,
    candidates: Iterable[TvmazeShowSearchResult],
    *,
    year: int | None = None,
) -> ShowMatchResult:
    """Rank candidates and select only a uniquely confident title or alias match.

    An explicitly supplied year may resolve candidates in the strongest matching title
    tier. It never turns an inexact title into an automatic match.
    """
    normalised_query = normalise_show_title(query)
    requested_year = _validate_year(year)
    ranked = tuple(
        sorted(
            (_rank_candidate(normalised_query, candidate) for candidate in candidates),
            key=lambda candidate: _candidate_sort_key(candidate, requested_year),
        )
    )

    strongest_matches = _strongest_title_matches(ranked)
    selected = _select_confident_match(strongest_matches, requested_year)
    return ShowMatchResult(
        match=selected.result if selected is not None else None,
        candidates=tuple(candidate.result for candidate in ranked),
    )


def _rank_candidate(
    normalised_query: str,
    candidate: TvmazeShowSearchResult,
) -> _RankedCandidate:
    normalised_title = _normalise_candidate_title(candidate.show.title)
    if normalised_title == normalised_query:
        match_kind = _TitleMatchKind.EXACT_TITLE
    elif any(
        _normalise_candidate_title(alias) == normalised_query for alias in candidate.show.aliases
    ):
        match_kind = _TitleMatchKind.KNOWN_ALIAS
    else:
        match_kind = _TitleMatchKind.NONE

    return _RankedCandidate(
        result=candidate,
        match_kind=match_kind,
        normalised_title=normalised_title,
    )


def _candidate_sort_key(
    candidate: _RankedCandidate,
    requested_year: int | None,
) -> tuple[int, int, float, str, str, int]:
    return (
        int(candidate.match_kind),
        _year_rank(candidate, requested_year),
        -candidate.result.score,
        candidate.normalised_title,
        unicodedata.normalize("NFKC", candidate.result.show.title).casefold(),
        candidate.result.show.tvmaze_id,
    )


def _year_rank(candidate: _RankedCandidate, requested_year: int | None) -> int:
    if requested_year is None or candidate.match_kind is _TitleMatchKind.NONE:
        return 0

    candidate_year = candidate.result.show.premiere_year
    if candidate_year == requested_year:
        return 0
    if candidate_year is None:
        return 1
    return 2


def _strongest_title_matches(
    candidates: tuple[_RankedCandidate, ...],
) -> tuple[_RankedCandidate, ...]:
    exact_matches = tuple(
        candidate for candidate in candidates if candidate.match_kind is _TitleMatchKind.EXACT_TITLE
    )
    if exact_matches:
        return exact_matches
    return tuple(
        candidate for candidate in candidates if candidate.match_kind is _TitleMatchKind.KNOWN_ALIAS
    )


def _select_confident_match(
    candidates: tuple[_RankedCandidate, ...],
    requested_year: int | None,
) -> _RankedCandidate | None:
    if requested_year is None:
        return candidates[0] if len(candidates) == 1 else None

    matching_year = tuple(
        candidate
        for candidate in candidates
        if candidate.result.show.premiere_year == requested_year
    )
    unknown_year = any(candidate.result.show.premiere_year is None for candidate in candidates)
    if len(matching_year) == 1 and not unknown_year:
        return matching_year[0]
    return None


def _normalise_candidate_title(title: str) -> str:
    try:
        return normalise_show_title(title)
    except ValueError:
        return ""


def _validate_year(year: int | None) -> int | None:
    if year is None:
        return None
    if isinstance(year, bool) or not isinstance(year, int) or not 1 <= year <= 9999:
        raise ValueError("show year must be an integer from 1 through 9999")
    return year
