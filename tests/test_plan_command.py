"""CLI tests for read-only metadata planning and secure persistence."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Self

import pytest
from typer.testing import CliRunner

import wit.cli as cli
from wit.clients import (
    TvmazeEpisode,
    TvmazeEpisodeCollection,
    TvmazeEpisodeType,
    TvmazeShow,
    TvmazeShowSearchResult,
)
from wit.plan_store import PlanStore

runner = CliRunner()

_NOW = datetime(2025, 1, 10, 12, 0, tzinfo=UTC)
_PAST_DATE = date(2025, 1, 3)


class _FakeMetadataClient:
    """Async-context-managed TVmaze double exposing read operations only."""

    def __init__(
        self,
        search_results: tuple[TvmazeShowSearchResult, ...],
        episodes_by_show: dict[int, TvmazeEpisodeCollection],
    ) -> None:
        self.search_results = search_results
        self.episodes_by_show = episodes_by_show
        self.calls: list[tuple[str, str | int]] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback

    async def search_shows(self, title: str) -> tuple[TvmazeShowSearchResult, ...]:
        self.calls.append(("search", title))
        return self.search_results

    async def get_episodes(self, show_id: int) -> TvmazeEpisodeCollection:
        self.calls.append(("episodes", show_id))
        return self.episodes_by_show[show_id]


def _set_valid_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[str, str]:
    for name in tuple(os.environ):
        if name.startswith("WIT_"):
            monkeypatch.delenv(name)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)

    sonarr_credential = "plan-sonarr-" + ("x" * 24)
    jellyfin_credential = "plan-jellyfin-" + ("x" * 24)
    values = {
        "WIT_SONARR_URL": "http://127.0.0.1:8989",
        "WIT_SONARR_API_KEY": sonarr_credential,
        "WIT_SONARR_ROOT_FOLDER_ID": "1",
        "WIT_SONARR_QUALITY_PROFILE_ID": "2",
        "WIT_JELLYFIN_URL": "http://127.0.0.1:8096",
        "WIT_JELLYFIN_API_KEY": jellyfin_credential,
        "WIT_SEERR_URL": "http://127.0.0.1:5055",
        "WIT_TVMAZE_URL": "https://metadata.example.test",
        "WIT_STATE_DIR": str(tmp_path / "state"),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return sonarr_credential, jellyfin_credential


def _show(
    tvmaze_id: int,
    title: str = "Clockwork Harbor",
    *,
    year: int | None = 2024,
    tvdb_id: int | None = 31415,
    score: float = 1.0,
) -> TvmazeShowSearchResult:
    return TvmazeShowSearchResult(
        score=score,
        show=TvmazeShow(
            tvmaze_id=tvmaze_id,
            title=title,
            premiere_date=date(year, 1, 1) if year is not None else None,
            tvdb_id=tvdb_id,
            imdb_id=None,
        ),
    )


def _episode(
    tvmaze_id: int,
    season_number: int,
    episode_number: int | None,
    title: str,
    *,
    episode_type: TvmazeEpisodeType = TvmazeEpisodeType.REGULAR,
    air_date: date | None = _PAST_DATE,
) -> TvmazeEpisode:
    return TvmazeEpisode(
        tvmaze_id=tvmaze_id,
        title=title,
        season_number=season_number,
        episode_number=episode_number,
        episode_type=episode_type,
        air_date=air_date,
        air_time=None,
        air_timestamp=None,
    )


def _episodes(*regular: TvmazeEpisode) -> TvmazeEpisodeCollection:
    return TvmazeEpisodeCollection(regular=regular, specials=())


def _install_metadata_double(
    monkeypatch: pytest.MonkeyPatch,
    client: _FakeMetadataClient,
    *,
    plan_id: str = "plan-fixed-001",
) -> None:
    monkeypatch.setattr(cli, "_create_tvmaze_client", lambda settings: client)
    monkeypatch.setattr(cli, "_utc_now", lambda: _NOW)
    monkeypatch.setattr(cli, "generate_plan_identifier", lambda created_at: plan_id)


def test_plan_prints_every_selected_episode_then_saves_a_fixed_time_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credentials = _set_valid_environment(monkeypatch, tmp_path)
    show = _show(101)
    future_date = _NOW.date() + timedelta(days=1)
    client = _FakeMetadataClient(
        (show,),
        {
            101: TvmazeEpisodeCollection(
                regular=(
                    _episode(103, 1, 3, "Tomorrow's Tide", air_date=future_date),
                    _episode(102, 1, 2, "Turning Tide"),
                    _episode(101, 1, 1, "First Light"),
                    _episode(1, 0, 1, "Season Zero Record"),
                ),
                specials=(
                    _episode(
                        900,
                        1,
                        None,
                        "Festival Special",
                        episode_type=TvmazeEpisodeType.SIGNIFICANT_SPECIAL,
                    ),
                ),
            )
        },
    )
    _install_metadata_double(monkeypatch, client)

    result = runner.invoke(cli.app, ["plan", "Clockwork Harbor", "--first", "2"])

    assert result.exit_code == 0, result.output
    assert "Show: Clockwork Harbor (2024)" in result.output
    assert "Selector: first 2 aired regular episodes" in result.output
    assert "Selected episodes (2):" in result.output
    assert "S01E01  First Light" in result.output
    assert "S01E02  Turning Tide" in result.output
    assert "Tomorrow's Tide" not in result.output
    assert "Festival Special" not in result.output
    assert "Season Zero Record" not in result.output
    assert result.output.index("S01E01") < result.output.index("S01E02")
    assert result.output.index("S01E02") < result.output.index("Saved plan ID")
    assert result.output.endswith("Saved plan ID: plan-fixed-001\n")
    assert all(credential not in result.output for credential in credentials)
    assert client.calls == [("search", "Clockwork Harbor"), ("episodes", 101)]

    stored = PlanStore(tmp_path / "state").load("plan-fixed-001")
    assert stored.created_at == _NOW
    assert stored.tvmaze_id == 101
    assert stored.tvdb_id == 31415
    assert [episode.coordinate for episode in stored.episodes] == [(1, 1), (1, 2)]
    persisted = (tmp_path / "state" / "plans" / "plan-fixed-001.json").read_text()
    assert all(credential not in persisted for credential in credentials)


def test_ambiguous_title_lists_candidates_and_accepts_explicit_tvmaze_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_valid_environment(monkeypatch, tmp_path)
    original = _show(101, year=1998, tvdb_id=301, score=0.9)
    remake = _show(202, year=2024, tvdb_id=302, score=0.8)
    client = _FakeMetadataClient(
        (remake, original),
        {
            101: _episodes(_episode(10101, 1, 1, "Original Pilot")),
            202: _episodes(_episode(20201, 1, 1, "Remake Pilot")),
        },
    )
    _install_metadata_double(monkeypatch, client, plan_id="plan-remake")

    ambiguous = runner.invoke(cli.app, ["plan", "Clockwork Harbor", "--all-aired"])

    assert ambiguous.exit_code == 1
    assert "title match is ambiguous" in ambiguous.output
    assert "Candidates:" in ambiguous.output
    assert "TVmaze ID 101: Clockwork Harbor (1998); TVDB ID 301" in ambiguous.output
    assert "TVmaze ID 202: Clockwork Harbor (2024); TVDB ID 302" in ambiguous.output
    assert "Retry with --candidate <TVMAZE-ID>" in ambiguous.output
    assert client.calls == [("search", "Clockwork Harbor")]
    assert not (tmp_path / "state").exists()

    selected = runner.invoke(
        cli.app,
        ["plan", "Clockwork Harbor", "--all-aired", "--candidate", "202"],
    )

    assert selected.exit_code == 0, selected.output
    assert "Show: Clockwork Harbor (2024)" in selected.output
    assert "S01E01  Remake Pilot" in selected.output
    assert "Original Pilot" not in selected.output
    assert selected.output.endswith("Saved plan ID: plan-remake\n")
    assert client.calls == [
        ("search", "Clockwork Harbor"),
        ("search", "Clockwork Harbor"),
        ("episodes", 202),
    ]


@pytest.mark.parametrize(
    ("arguments", "expected_coordinates", "expected_summary"),
    [
        (
            ["--first", "2", "--season", "2"],
            [(2, 1), (2, 2)],
            "first 2 aired regular episodes in season 2",
        ),
        (
            ["--season", "2", "--episodes", "2-3"],
            [(2, 2), (2, 3)],
            "aired regular episodes S02E02-S02E03",
        ),
        (
            ["--all-aired"],
            [(1, 1), (2, 1), (2, 2), (2, 3)],
            "all aired regular episodes",
        ),
    ],
)
def test_plan_wires_each_supported_selector(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    arguments: list[str],
    expected_coordinates: list[tuple[int, int]],
    expected_summary: str,
) -> None:
    _set_valid_environment(monkeypatch, tmp_path)
    client = _FakeMetadataClient(
        (_show(101),),
        {
            101: _episodes(
                _episode(101, 1, 1, "Season One"),
                _episode(201, 2, 1, "Second Season One"),
                _episode(202, 2, 2, "Second Season Two"),
                _episode(203, 2, 3, "Second Season Three"),
            )
        },
    )
    _install_metadata_double(monkeypatch, client)

    result = runner.invoke(cli.app, ["plan", "Clockwork Harbor", *arguments])

    assert result.exit_code == 0, result.output
    stored = PlanStore(tmp_path / "state").load("plan-fixed-001")
    assert [episode.coordinate for episode in stored.episodes] == expected_coordinates
    assert stored.selector_summary == expected_summary


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--first", "1", "--all-aired"],
        ["--episodes", "1-2"],
        ["--season", "1", "--all-aired"],
        ["--first", "1", "--season", "1", "--episodes", "1-2"],
        ["--season", "1", "--episodes", "not-a-range"],
    ],
)
def test_plan_rejects_missing_conflicting_and_incomplete_selectors_before_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    arguments: list[str],
) -> None:
    _set_valid_environment(monkeypatch, tmp_path)
    client = _FakeMetadataClient((_show(101),), {101: _episodes()})
    _install_metadata_double(monkeypatch, client)

    result = runner.invoke(cli.app, ["plan", "Clockwork Harbor", *arguments])

    assert result.exit_code == 2
    assert "Invalid value" in result.output
    assert client.calls == []
    assert not (tmp_path / "state").exists()


def test_plan_requires_tvdb_identity_before_fetching_episodes_or_saving(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_valid_environment(monkeypatch, tmp_path)
    client = _FakeMetadataClient((_show(101, tvdb_id=None),), {})
    _install_metadata_double(monkeypatch, client)

    result = runner.invoke(cli.app, ["plan", "Clockwork Harbor", "--first", "1"])

    assert result.exit_code == 1
    assert "matched show has no TVDB identity" in result.output
    assert "Sonarr mapping" in result.output
    assert client.calls == [("search", "Clockwork Harbor")]
    assert not (tmp_path / "state").exists()
