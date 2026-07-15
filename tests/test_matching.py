"""Tests for deterministic, side-effect-free show matching."""

from __future__ import annotations

from datetime import date

import pytest

from wit.clients import TvmazeShow, TvmazeShowSearchResult
from wit.matching import match_show, normalise_show_title


def _candidate(
    tvmaze_id: int,
    title: str,
    *,
    year: int | None = None,
    aliases: tuple[str, ...] = (),
    score: float = 0.5,
) -> TvmazeShowSearchResult:
    return TvmazeShowSearchResult(
        score=score,
        show=TvmazeShow(
            tvmaze_id=tvmaze_id,
            title=title,
            aliases=aliases,
            premiere_date=date(year, 1, 1) if year is not None else None,
            tvdb_id=tvmaze_id + 10_000,
            imdb_id=None,
        ),
    )


def _candidate_ids(result_candidates: tuple[TvmazeShowSearchResult, ...]) -> list[int]:
    return [candidate.show.tvmaze_id for candidate in result_candidates]


def test_normalises_case_punctuation_and_whitespace_for_exact_titles() -> None:
    exact = _candidate(10, "Clockwork: Harbor!", score=0.1)
    inexact = _candidate(20, "Clockwork Harbor Patrol", score=1.0)

    result = match_show("  CLOCKWORK---harbor\n", (inexact, exact))

    assert normalise_show_title("Clockwork: Harbor!") == "clockworkharbor"
    assert result.match is exact
    assert not result.requires_selection
    assert _candidate_ids(result.candidates) == [10, 20]


def test_prefers_exact_title_then_matches_a_unique_known_alias() -> None:
    alias_match = _candidate(
        20,
        "Harbor Night Watch",
        aliases=("Clockwork Harbor", "The Night Watch"),
        score=1.0,
    )
    exact_match = _candidate(10, "Clockwork Harbor", score=0.1)

    exact_result = match_show("clockwork harbor", (alias_match, exact_match))
    alias_result = match_show("the-night watch", (alias_match,))

    assert exact_result.match is exact_match
    assert _candidate_ids(exact_result.candidates) == [10, 20]
    assert alias_result.match is alias_match
    assert not alias_result.requires_selection


def test_requires_a_year_to_disambiguate_remakes() -> None:
    original = _candidate(10, "Signal House", year=1998, score=0.9)
    remake = _candidate(20, "Signal House", year=2024, score=0.8)

    ambiguous = match_show("Signal House", (remake, original))
    resolved = match_show("Signal House", (original, remake), year=2024)

    assert ambiguous.match is None
    assert ambiguous.requires_selection
    assert _candidate_ids(ambiguous.candidates) == [10, 20]
    assert resolved.match is remake
    assert _candidate_ids(resolved.candidates) == [20, 10]


def test_year_never_creates_an_inexact_title_match() -> None:
    higher_relevance = _candidate(10, "Clockwork Bay", year=2001, score=0.9)
    requested_year = _candidate(20, "Harbor Clock", year=2024, score=0.2)

    result = match_show(
        "Clockwork Harbor",
        (requested_year, higher_relevance),
        year=2024,
    )

    assert result.match is None
    assert result.requires_selection
    assert _candidate_ids(result.candidates) == [10, 20]


def test_missing_year_keeps_otherwise_matching_remakes_ambiguous() -> None:
    known_year = _candidate(10, "Signal House", year=2024)
    unknown_year = _candidate(20, "Signal House")

    result = match_show("Signal House", (known_year, unknown_year), year=2024)

    assert result.match is None
    assert result.requires_selection
    assert _candidate_ids(result.candidates) == [10, 20]


def test_orders_ambiguous_alias_candidates_deterministically() -> None:
    lower_id = _candidate(10, "Harbor Stories", aliases=("Port Tales",), score=0.5)
    higher_id = _candidate(20, "Stories from Harbor", aliases=("Port Tales",), score=0.5)

    result = match_show("Port Tales", (higher_id, lower_id))

    assert result.match is None
    assert result.requires_selection
    assert _candidate_ids(result.candidates) == [10, 20]


def test_returns_no_match_or_candidates_without_guessing() -> None:
    unrelated = _candidate(10, "Distant Shore")

    empty = match_show("Missing Program", ())
    inexact = match_show("Missing Program", (unrelated,))

    assert empty.match is None
    assert empty.candidates == ()
    assert not empty.requires_selection
    assert inexact.match is None
    assert inexact.candidates == (unrelated,)
    assert inexact.requires_selection


@pytest.mark.parametrize("invalid_year", [True, 0, 10_000])
def test_rejects_invalid_disambiguation_years(invalid_year: int) -> None:
    with pytest.raises(ValueError, match="show year"):
        match_show("Signal House", (), year=invalid_year)
