"""End-to-end contracts for the complete read-only planning path."""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

import wit.cli as cli
from wit.clients import SonarrClient, TvmazeClient
from wit.config import WitSettings
from wit.plan_store import PlanStore

runner = CliRunner()

_NOW = datetime(2025, 6, 15, 12, 0, tzinfo=UTC)
_PLAN_ID = "plan-contract-fixed"
_TVMAZE_HOST = "tvmaze.contract.test"
_SONARR_HOST = "sonarr.contract.test"
_TVMAZE_BASE_PATH = "/metadata"
_RAW_HEADER_VALUE = "fake-upstream-authentication-header"
_EPISODE_PATH = re.compile(r"/metadata/shows/([1-9][0-9]*)/episodes\Z")

type JsonObject = dict[str, object]


class _FakeHttpServices:
    """Deterministic in-process HTTP services used instead of network access."""

    def __init__(
        self,
        *,
        search_payload: list[JsonObject],
        episodes_by_show: dict[int, list[JsonObject]],
    ) -> None:
        self._search_payload = search_payload
        self._episodes_by_show = episodes_by_show
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def requests_for(self, host: str) -> tuple[httpx.Request, ...]:
        return tuple(request for request in self.requests if request.url.host == host)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)

        if request.url.host == _SONARR_HOST:
            return httpx.Response(
                500,
                json={"error": "planning must not contact Sonarr"},
            )
        if request.url.host != _TVMAZE_HOST:
            return httpx.Response(404, json={"error": "unknown fake service"})
        if request.method != "GET":
            return httpx.Response(405, json={"error": "TVmaze planning is read-only"})
        if request.url.path == f"{_TVMAZE_BASE_PATH}/search/shows":
            return httpx.Response(200, json=self._search_payload)

        episode_match = _EPISODE_PATH.fullmatch(request.url.path)
        if episode_match is None:
            return httpx.Response(404, json={"error": "unknown TVmaze endpoint"})
        show_id = int(episode_match.group(1))
        if show_id not in self._episodes_by_show:
            return httpx.Response(404, json={"error": "unknown fake show"})
        return httpx.Response(200, json=self._episodes_by_show[show_id])


def _credential(label: str) -> str:
    return f"contract-{label}-" + ("x" * 24)


def _set_contract_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[str, str]:
    for name in tuple(os.environ):
        if name.startswith("WIT_"):
            monkeypatch.delenv(name)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)

    sonarr_credential = _credential("sonarr")
    jellyfin_credential = _credential("jellyfin")
    values = {
        "WIT_SONARR_URL": f"https://{_SONARR_HOST}/sonarr",
        "WIT_SONARR_API_KEY": sonarr_credential,
        "WIT_SONARR_ROOT_FOLDER_ID": "7",
        "WIT_SONARR_QUALITY_PROFILE_ID": "8",
        "WIT_JELLYFIN_URL": "https://jellyfin.contract.test/jellyfin",
        "WIT_JELLYFIN_API_KEY": jellyfin_credential,
        "WIT_SEERR_URL": "https://seerr.contract.test/seerr",
        "WIT_TVMAZE_URL": f"https://{_TVMAZE_HOST}{_TVMAZE_BASE_PATH}",
        "WIT_STATE_DIR": str(tmp_path / "state"),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return sonarr_credential, jellyfin_credential


def _install_fake_http_services(
    monkeypatch: pytest.MonkeyPatch,
    services: _FakeHttpServices,
) -> None:
    def create_tvmaze_client(settings: WitSettings) -> TvmazeClient:
        return TvmazeClient(
            base_url=str(settings.tvmaze.url),
            connect_timeout_seconds=settings.http.connect_timeout_seconds,
            read_timeout_seconds=settings.http.read_timeout_seconds,
            http_transport=services.transport(),
        )

    def create_sonarr_client(settings: WitSettings) -> SonarrClient:
        credential = settings.sonarr.api_key
        return SonarrClient(
            base_url=str(settings.sonarr.url),
            api_key=credential,
            connect_timeout_seconds=settings.http.connect_timeout_seconds,
            read_timeout_seconds=settings.http.read_timeout_seconds,
            http_transport=services.transport(),
        )

    monkeypatch.setattr(cli, "_create_tvmaze_client", create_tvmaze_client)
    monkeypatch.setattr(cli, "_create_sonarr_client", create_sonarr_client)
    monkeypatch.setattr(cli, "_utc_now", lambda: _NOW)
    monkeypatch.setattr(cli, "generate_plan_identifier", lambda created_at: _PLAN_ID)


def _show_payload(
    tvmaze_id: int,
    *,
    year: int,
    tvdb_id: int,
    score: float = 1.0,
) -> JsonObject:
    return {
        "score": score,
        "show": {
            "id": tvmaze_id,
            "name": "Clockwork Harbor",
            "premiered": f"{year:04d}-01-01",
            "externals": {"thetvdb": tvdb_id, "imdb": None},
            "requestHeaders": {
                "X-Api-Key": _RAW_HEADER_VALUE,
                "Authorization": f"Bearer {_RAW_HEADER_VALUE}",
            },
            "upstreamOnly": "must-not-be-persisted",
        },
    }


def _episode_payload(
    tvmaze_id: int,
    season_number: int,
    episode_number: int,
    title: str,
    *,
    episode_type: str = "regular",
    air_date: str | None = "2025-06-01",
) -> JsonObject:
    return {
        "id": tvmaze_id,
        "name": title,
        "season": season_number,
        "number": episode_number,
        "type": episode_type,
        "airdate": air_date,
        "airtime": "",
        "airstamp": None,
        "upstreamOnly": "must-not-be-persisted",
    }


def _complete_episode_payload() -> list[JsonObject]:
    return [
        _episode_payload(202, 2, 2, "Second Season Two"),
        _episode_payload(101, 1, 1, "First Light"),
        _episode_payload(
            900,
            1,
            50,
            "Festival Special",
            episode_type="significant_special",
        ),
        _episode_payload(203, 2, 3, "Second Season Three"),
        _episode_payload(102, 1, 2, "Turning Tide"),
        _episode_payload(204, 2, 4, "Future Tide", air_date="2025-06-16"),
        _episode_payload(201, 2, 1, "Second Season One"),
        _episode_payload(1, 0, 1, "Season Zero Record"),
        _episode_payload(205, 2, 5, "Unscheduled Tide", air_date=None),
    ]


def _assert_read_only_http_contract(
    services: _FakeHttpServices,
    *,
    expected_paths: list[str],
    expected_query: str,
) -> None:
    assert services.requests_for(_SONARR_HOST) == ()
    tvmaze_requests = services.requests_for(_TVMAZE_HOST)
    assert [request.url.path for request in tvmaze_requests] == expected_paths
    assert all(request.method == "GET" for request in tvmaze_requests)
    for request in tvmaze_requests:
        if request.url.path == f"{_TVMAZE_BASE_PATH}/search/shows":
            assert request.url.params["q"] == expected_query
        else:
            assert request.url.params["specials"] == "1"
    assert all("x-api-key" not in request.headers for request in tvmaze_requests)
    assert all("authorization" not in request.headers for request in tvmaze_requests)


def _assert_persisted_plan_is_secret_free(
    state_dir: Path,
    *,
    credentials: tuple[str, str],
) -> None:
    plan_path = state_dir / "plans" / f"{_PLAN_ID}.json"
    raw_plan = plan_path.read_text(encoding="utf-8")
    folded_plan = raw_plan.casefold()
    for forbidden in (
        *credentials,
        "x-api-key",
        "authorization",
        "api_key",
        "request_headers",
        "requestheaders",
        _RAW_HEADER_VALUE,
        "upstreamonly",
        "must-not-be-persisted",
    ):
        assert forbidden.casefold() not in folded_plan

    payload = json.loads(raw_plan)
    assert set(payload) == {
        "schema_version",
        "plan_id",
        "created_at",
        "show_title",
        "show_year",
        "tvmaze_id",
        "tvdb_id",
        "selector_summary",
        "episodes",
    }
    assert all(
        set(episode) == {"season_number", "episode_number", "title"}
        for episode in payload["episodes"]
    )


@pytest.mark.parametrize(
    ("selector_arguments", "expected_coordinates", "expected_summary"),
    [
        (
            ["--first", "2"],
            ((1, 1), (1, 2)),
            "first 2 aired regular episodes",
        ),
        (
            ["--season", "2", "--episodes", "2-3"],
            ((2, 2), (2, 3)),
            "aired regular episodes S02E02-S02E03",
        ),
        (
            ["--all-aired"],
            ((1, 1), (1, 2), (2, 1), (2, 2), (2, 3)),
            "all aired regular episodes",
        ),
    ],
    ids=("first-n", "season-range", "all-aired"),
)
def test_exact_title_planning_flows_use_fake_http_and_persist_only_selected_episodes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    selector_arguments: list[str],
    expected_coordinates: tuple[tuple[int, int], ...],
    expected_summary: str,
) -> None:
    credentials = _set_contract_environment(monkeypatch, tmp_path)
    services = _FakeHttpServices(
        search_payload=[_show_payload(101, year=2024, tvdb_id=31415)],
        episodes_by_show={101: _complete_episode_payload()},
    )
    _install_fake_http_services(monkeypatch, services)

    result = runner.invoke(
        cli.app,
        ["plan", "Clockwork Harbor", *selector_arguments],
    )

    assert result.exit_code == 0, result.output
    stored = PlanStore(tmp_path / "state").load(_PLAN_ID)
    assert stored.show_title == "Clockwork Harbor"
    assert stored.selector_summary == expected_summary
    assert tuple(episode.coordinate for episode in stored.episodes) == expected_coordinates
    assert all(credential not in result.output for credential in credentials)
    assert "Festival Special" not in result.output
    assert "Future Tide" not in result.output
    assert "Season Zero Record" not in result.output
    assert "Unscheduled Tide" not in result.output
    _assert_read_only_http_contract(
        services,
        expected_paths=[
            f"{_TVMAZE_BASE_PATH}/search/shows",
            f"{_TVMAZE_BASE_PATH}/shows/101/episodes",
        ],
        expected_query="Clockwork Harbor",
    )
    _assert_persisted_plan_is_secret_free(tmp_path / "state", credentials=credentials)


def test_ambiguous_title_requires_then_honours_an_explicit_candidate_over_fake_http(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credentials = _set_contract_environment(monkeypatch, tmp_path)
    services = _FakeHttpServices(
        search_payload=[
            _show_payload(202, year=2024, tvdb_id=302, score=0.8),
            _show_payload(101, year=1998, tvdb_id=301, score=0.9),
        ],
        episodes_by_show={
            101: [_episode_payload(10101, 1, 1, "Original Pilot")],
            202: [_episode_payload(20201, 1, 1, "Remake Pilot")],
        },
    )
    _install_fake_http_services(monkeypatch, services)

    ambiguous = runner.invoke(
        cli.app,
        ["plan", "Clockwork Harbor", "--first", "1"],
    )

    assert ambiguous.exit_code == 1
    assert "title match is ambiguous" in ambiguous.output
    assert "TVmaze ID 101: Clockwork Harbor (1998); TVDB ID 301" in ambiguous.output
    assert "TVmaze ID 202: Clockwork Harbor (2024); TVDB ID 302" in ambiguous.output
    assert not (tmp_path / "state").exists()

    selected = runner.invoke(
        cli.app,
        ["plan", "Clockwork Harbor", "--first", "1", "--candidate", "202"],
    )

    assert selected.exit_code == 0, selected.output
    assert "Show: Clockwork Harbor (2024)" in selected.output
    assert "S01E01  Remake Pilot" in selected.output
    assert "Original Pilot" not in selected.output
    assert all(credential not in ambiguous.output + selected.output for credential in credentials)
    stored = PlanStore(tmp_path / "state").load(_PLAN_ID)
    assert stored.tvmaze_id == 202
    assert stored.tvdb_id == 302
    assert tuple(episode.title for episode in stored.episodes) == ("Remake Pilot",)
    _assert_read_only_http_contract(
        services,
        expected_paths=[
            f"{_TVMAZE_BASE_PATH}/search/shows",
            f"{_TVMAZE_BASE_PATH}/search/shows",
            f"{_TVMAZE_BASE_PATH}/shows/202/episodes",
        ],
        expected_query="Clockwork Harbor",
    )
    _assert_persisted_plan_is_secret_free(tmp_path / "state", credentials=credentials)
