"""End-to-end contracts for the mutating apply and read-only status paths."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx
import pytest
from typer.testing import CliRunner

import wit.cli as cli
from wit.clients import JellyfinClient, SonarrClient
from wit.config import WitSettings
from wit.plan_store import PlanStore
from wit.plans import DOWNLOAD_PLAN_SCHEMA_VERSION, DownloadPlan, PlannedEpisode

runner = CliRunner()

_NOW = datetime(2025, 1, 12, 12, 0, tzinfo=UTC)
_PLAN_ID = "plan-apply-status-contract"
_TVDB_ID = 31415
_SERIES_ID = 42
_SONARR_HOST = "sonarr.contract.test"
_JELLYFIN_HOST = "jellyfin.contract.test"
_SONARR_BASE_PATH = "/sonarr/api/v3"
_JELLYFIN_ITEMS_PATH = "/jellyfin/Items"
_SERIES_ITEM_ID = "11111111-1111-1111-1111-111111111111"
_EPISODE_ITEM_ID = "22222222-2222-2222-2222-222222222222"

type JsonObject = dict[str, object]
type RequestRecord = tuple[str, str]
type MutationRecord = tuple[str, str, JsonObject]


@dataclass(slots=True)
class _EpisodeState:
    episode_id: int
    episode_number: int
    title: str
    monitored: bool = False
    has_file: bool = False


class _FakeMediaServices:
    """Stateful in-process Sonarr and Jellyfin HTTP contracts."""

    def __init__(
        self,
        *,
        sonarr_credential: str,
        jellyfin_credential: str,
        existing_series: bool = False,
        missing_episode_numbers: frozenset[int] = frozenset(),
    ) -> None:
        self._sonarr_credential = sonarr_credential
        self._jellyfin_credential = jellyfin_credential
        self._series_exists = existing_series
        self._episodes = {
            number: _EpisodeState(100 + number, number, title)
            for number, title in (
                (1, "First Light"),
                (2, "Turning Tide"),
                (3, "Open Water"),
            )
            if number not in missing_episode_numbers
        }
        self._queue_records: list[JsonObject] = []
        self._visible_coordinates: tuple[tuple[int, int], ...] = ()
        self._command_id = 500
        self.requests: list[RequestRecord] = []
        self.mutations: list[MutationRecord] = []
        self.authentication_results: list[tuple[str, bool]] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def set_mixed_repeat_state(self) -> None:
        """Model one imported, one queued, and one still-missing episode."""
        for episode in self._episodes.values():
            episode.monitored = True
            episode.has_file = False
        self._episodes[1].has_file = True
        self._queue_records = [
            {
                "id": 802,
                "seriesId": _SERIES_ID,
                "episodeId": 102,
                "status": "queued",
                "trackedDownloadStatus": "ok",
            }
        ]
        self._visible_coordinates = ((1, 1),)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path))
        if request.url.host == _SONARR_HOST:
            authenticated = request.headers.get("x-api-key") == self._sonarr_credential
            self.authentication_results.append((_SONARR_HOST, authenticated))
            if not authenticated:
                return httpx.Response(401, json={"error": "unauthorised"})
            return self._handle_sonarr(request)
        if request.url.host == _JELLYFIN_HOST:
            expected = f'MediaBrowser Token="{self._jellyfin_credential}"'
            authenticated = request.headers.get("authorization") == expected
            self.authentication_results.append((_JELLYFIN_HOST, authenticated))
            if not authenticated:
                return httpx.Response(401, json={"error": "unauthorised"})
            return self._handle_jellyfin(request)
        return httpx.Response(404, json={"error": "unknown fake service"})

    def _handle_sonarr(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        series_path = f"{_SONARR_BASE_PATH}/series"
        if request.method == "GET" and path == series_path:
            if request.url.params.get("tvdbId") != str(_TVDB_ID):
                return httpx.Response(400, json={"error": "unexpected TVDB lookup"})
            payload = [self._existing_series_payload()] if self._series_exists else []
            return httpx.Response(200, json=payload)

        if request.method == "GET" and path == f"{series_path}/lookup":
            if request.url.params.get("term") != f"tvdb:{_TVDB_ID}":
                return httpx.Response(400, json={"error": "unexpected series lookup"})
            return httpx.Response(
                200,
                json=[
                    {
                        "tvdbId": _TVDB_ID,
                        "title": "Clockwork Harbor",
                        "year": 2024,
                        "seriesType": "standard",
                        "seasons": [{"seasonNumber": 0}, {"seasonNumber": 1}],
                        "privateDiagnostic": self._sonarr_credential,
                    }
                ],
            )

        if request.method == "GET" and path == f"{_SONARR_BASE_PATH}/rootfolder":
            return httpx.Response(
                200,
                json=[{"id": 7, "path": "/tv", "accessible": True}],
            )

        if request.method == "GET" and path == f"{_SONARR_BASE_PATH}/qualityprofile":
            return httpx.Response(200, json=[{"id": 8, "name": "Contract profile"}])

        if request.method == "POST" and path == series_path:
            body = _request_json_body(request)
            self.mutations.append((request.method, path, body))
            self._series_exists = True
            return httpx.Response(201, json=self._added_series_payload())

        if request.method == "GET" and path == f"{_SONARR_BASE_PATH}/episode":
            if request.url.params.get("seriesId") != str(_SERIES_ID):
                return httpx.Response(400, json={"error": "unexpected episode series"})
            return httpx.Response(200, json=self._episode_payloads())

        if request.method == "GET" and path == f"{_SONARR_BASE_PATH}/queue":
            if (
                request.url.params.get("page") != "1"
                or request.url.params.get("pageSize") != "100"
                or request.url.params.get("includeUnknownSeriesItems") != "true"
            ):
                return httpx.Response(400, json={"error": "unexpected queue page"})
            return httpx.Response(
                200,
                json={
                    "page": 1,
                    "pageSize": 100,
                    "totalRecords": len(self._queue_records),
                    "records": self._queue_records,
                },
            )

        if request.method == "PUT" and path == f"{_SONARR_BASE_PATH}/episode/monitor":
            body = _request_json_body(request)
            self.mutations.append((request.method, path, body))
            episode_ids = body.get("episodeIds")
            if not isinstance(episode_ids, list):
                return httpx.Response(400, json={"error": "invalid monitor payload"})
            for episode_id in episode_ids:
                for episode in self._episodes.values():
                    if episode.episode_id == episode_id:
                        episode.monitored = True
            return httpx.Response(
                200,
                json=[{"id": episode_id, "monitored": True} for episode_id in episode_ids],
            )

        if request.method == "POST" and path == f"{_SONARR_BASE_PATH}/command":
            body = _request_json_body(request)
            self.mutations.append((request.method, path, body))
            self._command_id += 1
            return httpx.Response(
                201,
                json={
                    "id": self._command_id,
                    "name": "EpisodeSearch",
                    "status": "queued",
                    "privateDiagnostic": self._sonarr_credential,
                },
            )

        return httpx.Response(404, json={"error": "unknown Sonarr endpoint"})

    def _handle_jellyfin(self, request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            return httpx.Response(405, json={"error": "Jellyfin status must be read-only"})
        if request.url.path != _JELLYFIN_ITEMS_PATH:
            return httpx.Response(404, json={"error": "unknown Jellyfin endpoint"})

        item_type = request.url.params.get("includeItemTypes")
        if item_type == "Series":
            return httpx.Response(
                200,
                json=_jellyfin_page(
                    [
                        {
                            "Id": _SERIES_ITEM_ID,
                            "Name": "Clockwork Harbor",
                            "Type": "Series",
                            "ProductionYear": 2024,
                            "ProviderIds": {"Tvdb": str(_TVDB_ID)},
                            "PrivateDiagnostic": self._jellyfin_credential,
                        }
                    ]
                ),
            )
        if item_type == "Episode":
            if request.url.params.get("parentId") != _SERIES_ITEM_ID:
                return httpx.Response(400, json={"error": "unexpected episode parent"})
            items: list[JsonObject] = []
            for season_number, episode_number in self._visible_coordinates:
                items.append(
                    {
                        "Id": _EPISODE_ITEM_ID,
                        "Name": "First Light",
                        "Type": "Episode",
                        "ParentIndexNumber": season_number,
                        "IndexNumber": episode_number,
                        "ProviderIds": {},
                        "PrivateDiagnostic": self._jellyfin_credential,
                    }
                )
            return httpx.Response(200, json=_jellyfin_page(items))
        return httpx.Response(400, json={"error": "unexpected Jellyfin item query"})

    def _existing_series_payload(self) -> JsonObject:
        return {
            "id": _SERIES_ID,
            "tvdbId": _TVDB_ID,
            "title": "Clockwork Harbor",
            "year": 2024,
            "privateDiagnostic": self._sonarr_credential,
        }

    def _added_series_payload(self) -> JsonObject:
        return {
            **self._existing_series_payload(),
            "monitored": False,
            "monitorNewItems": "none",
            "seasons": [
                {"seasonNumber": 0, "monitored": False},
                {"seasonNumber": 1, "monitored": False},
            ],
        }

    def _episode_payloads(self) -> list[JsonObject]:
        payloads: list[JsonObject] = []
        for episode_number in (3, 1, 2):
            episode = self._episodes.get(episode_number)
            if episode is None:
                continue
            payloads.append(
                {
                    "id": episode.episode_id,
                    "seriesId": _SERIES_ID,
                    "seasonNumber": 1,
                    "episodeNumber": episode.episode_number,
                    "title": episode.title,
                    "airDateUtc": "2025-01-01T00:00:00Z",
                    "monitored": episode.monitored,
                    "hasFile": episode.has_file,
                    "privateDiagnostic": self._sonarr_credential,
                }
            )
        return payloads


def _request_json_body(request: httpx.Request) -> JsonObject:
    decoded: object = json.loads(request.content)
    if not isinstance(decoded, dict):
        raise AssertionError("contract request body must be a JSON object")
    return cast(JsonObject, decoded)


def _jellyfin_page(items: list[JsonObject]) -> JsonObject:
    return {
        "Items": items,
        "TotalRecordCount": len(items),
        "StartIndex": 0,
    }


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
        "WIT_JELLYFIN_URL": f"https://{_JELLYFIN_HOST}/jellyfin",
        "WIT_JELLYFIN_API_KEY": jellyfin_credential,
        "WIT_SEERR_URL": "https://seerr.contract.test/seerr",
        "WIT_TVMAZE_URL": "https://tvmaze.contract.test/metadata",
        "WIT_STATE_DIR": str(tmp_path / "state"),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(cli, "_utc_now", lambda: _NOW)
    return sonarr_credential, jellyfin_credential


def _install_fake_http_services(
    monkeypatch: pytest.MonkeyPatch,
    services: _FakeMediaServices,
) -> None:
    def create_sonarr_client(settings: WitSettings) -> SonarrClient:
        credential = settings.sonarr.api_key
        return SonarrClient(
            base_url=str(settings.sonarr.url),
            api_key=credential,
            connect_timeout_seconds=settings.http.connect_timeout_seconds,
            read_timeout_seconds=settings.http.read_timeout_seconds,
            http_transport=services.transport(),
        )

    def create_jellyfin_client(settings: WitSettings) -> JellyfinClient:
        credential = settings.jellyfin.api_key
        return JellyfinClient(
            base_url=str(settings.jellyfin.url),
            api_key=credential,
            connect_timeout_seconds=settings.http.connect_timeout_seconds,
            read_timeout_seconds=settings.http.read_timeout_seconds,
            http_transport=services.transport(),
        )

    monkeypatch.setattr(cli, "_create_sonarr_client", create_sonarr_client)
    monkeypatch.setattr(cli, "_create_jellyfin_client", create_jellyfin_client)


def _plan() -> DownloadPlan:
    return DownloadPlan(
        schema_version=DOWNLOAD_PLAN_SCHEMA_VERSION,
        plan_id=_PLAN_ID,
        created_at=datetime(2025, 1, 10, 12, tzinfo=UTC),
        show_title="Clockwork Harbor",
        show_year=2024,
        tvmaze_id=2718,
        tvdb_id=_TVDB_ID,
        selector_summary="first 3 aired regular episodes",
        episodes=(
            PlannedEpisode(season_number=1, episode_number=1, title="First Light"),
            PlannedEpisode(season_number=1, episode_number=2, title="Turning Tide"),
            PlannedEpisode(season_number=1, episode_number=3, title="Open Water"),
        ),
    )


def _assert_secret_free(
    *,
    raw_plan: str,
    output: str,
    credentials: tuple[str, str],
) -> None:
    for forbidden in (
        *credentials,
        "x-api-key",
        "authorization",
        "privateDiagnostic",
    ):
        assert forbidden.casefold() not in raw_plan.casefold()
        assert forbidden.casefold() not in output.casefold()


def test_apply_repeat_and_status_use_complete_fake_service_contracts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credentials = _set_contract_environment(monkeypatch, tmp_path)
    services = _FakeMediaServices(
        sonarr_credential=credentials[0],
        jellyfin_credential=credentials[1],
    )
    _install_fake_http_services(monkeypatch, services)
    plan_path = PlanStore(tmp_path / "state").save(_plan())
    original_plan_state = plan_path.read_text(encoding="utf-8")

    initial_apply = runner.invoke(cli.app, ["apply", _PLAN_ID, "--yes", "--json"])

    assert initial_apply.exit_code == 0, initial_apply.output
    initial_payload = json.loads(initial_apply.stdout)
    assert initial_payload["data"]["result"]["series"]["created"] is True
    assert initial_payload["data"]["result"]["outcomes"] == {
        "applied": {"count": 3, "episode_ids": [101, 102, 103]},
        "skipped_file": {"count": 0, "episode_ids": []},
        "skipped_queue": {"count": 0, "episode_ids": []},
        "rejected": {"count": 0, "episode_ids": []},
    }
    assert initial_payload["data"]["result"]["command"] == {
        "command_id": 501,
        "state": "queued",
    }
    assert services.requests == [
        ("GET", f"{_SONARR_BASE_PATH}/series"),
        ("GET", f"{_SONARR_BASE_PATH}/series/lookup"),
        ("GET", f"{_SONARR_BASE_PATH}/rootfolder"),
        ("GET", f"{_SONARR_BASE_PATH}/qualityprofile"),
        ("POST", f"{_SONARR_BASE_PATH}/series"),
        ("GET", f"{_SONARR_BASE_PATH}/episode"),
        ("GET", f"{_SONARR_BASE_PATH}/queue"),
        ("PUT", f"{_SONARR_BASE_PATH}/episode/monitor"),
        ("POST", f"{_SONARR_BASE_PATH}/command"),
    ]
    assert services.mutations == [
        (
            "POST",
            f"{_SONARR_BASE_PATH}/series",
            {
                "tvdbId": _TVDB_ID,
                "title": "Clockwork Harbor",
                "year": 2024,
                "seriesType": "standard",
                "rootFolderPath": "/tv",
                "qualityProfileId": 8,
                "seasonFolder": True,
                "monitored": False,
                "monitorNewItems": "none",
                "seasons": [
                    {"seasonNumber": 0, "monitored": False},
                    {"seasonNumber": 1, "monitored": False},
                ],
                "tags": [],
                "addOptions": {
                    "monitor": "none",
                    "searchForMissingEpisodes": False,
                    "searchForCutoffUnmetEpisodes": False,
                },
            },
        ),
        (
            "PUT",
            f"{_SONARR_BASE_PATH}/episode/monitor",
            {"episodeIds": [101, 102, 103], "monitored": True},
        ),
        (
            "POST",
            f"{_SONARR_BASE_PATH}/command",
            {"name": "EpisodeSearch", "episodeIds": [101, 102, 103]},
        ),
    ]

    services.set_mixed_repeat_state()
    repeat_request_start = len(services.requests)
    repeat_mutation_start = len(services.mutations)

    repeat_apply = runner.invoke(cli.app, ["apply", _PLAN_ID, "--yes", "--json"])

    assert repeat_apply.exit_code == 0, repeat_apply.output
    repeat_payload = json.loads(repeat_apply.stdout)
    assert repeat_payload["data"]["result"]["series"]["created"] is False
    assert repeat_payload["data"]["result"]["outcomes"] == {
        "applied": {"count": 1, "episode_ids": [103]},
        "skipped_file": {"count": 1, "episode_ids": [101]},
        "skipped_queue": {"count": 1, "episode_ids": [102]},
        "rejected": {"count": 0, "episode_ids": []},
    }
    assert repeat_payload["data"]["result"]["command"] == {
        "command_id": 502,
        "state": "queued",
    }
    assert services.requests[repeat_request_start:] == [
        ("GET", f"{_SONARR_BASE_PATH}/series"),
        ("GET", f"{_SONARR_BASE_PATH}/episode"),
        ("GET", f"{_SONARR_BASE_PATH}/queue"),
        ("PUT", f"{_SONARR_BASE_PATH}/episode/monitor"),
        ("POST", f"{_SONARR_BASE_PATH}/command"),
    ]
    assert services.mutations[repeat_mutation_start:] == [
        (
            "PUT",
            f"{_SONARR_BASE_PATH}/episode/monitor",
            {"episodeIds": [103], "monitored": True},
        ),
        (
            "POST",
            f"{_SONARR_BASE_PATH}/command",
            {"name": "EpisodeSearch", "episodeIds": [103]},
        ),
    ]

    status_request_start = len(services.requests)
    status_mutation_count = len(services.mutations)

    status = runner.invoke(cli.app, ["status", _PLAN_ID, "--json"])

    assert status.exit_code == 0, status.output
    status_payload = json.loads(status.stdout)
    result = status_payload["data"]["result"]
    assert result["state"] == "active"
    assert result["imported_count"] == 1
    assert result["visible_count"] == 1
    assert [episode["sonarr"]["state"] for episode in result["episodes"]] == [
        "imported",
        "queued",
        "missing",
    ]
    assert [episode["jellyfin_state"] for episode in result["episodes"]] == [
        "visible",
        None,
        None,
    ]
    assert services.requests[status_request_start:] == [
        ("GET", f"{_SONARR_BASE_PATH}/series"),
        ("GET", f"{_SONARR_BASE_PATH}/episode"),
        ("GET", f"{_SONARR_BASE_PATH}/queue"),
        ("GET", _JELLYFIN_ITEMS_PATH),
        ("GET", _JELLYFIN_ITEMS_PATH),
    ]
    assert len(services.mutations) == status_mutation_count
    assert services.authentication_results
    assert all(authenticated for _, authenticated in services.authentication_results)
    assert plan_path.read_text(encoding="utf-8") == original_plan_state
    _assert_secret_free(
        raw_plan=original_plan_state,
        output=initial_apply.output + repeat_apply.output + status.output,
        credentials=credentials,
    )


def test_mapping_error_fails_before_any_mutation_over_fake_http(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credentials = _set_contract_environment(monkeypatch, tmp_path)
    services = _FakeMediaServices(
        sonarr_credential=credentials[0],
        jellyfin_credential=credentials[1],
        existing_series=True,
        missing_episode_numbers=frozenset({3}),
    )
    _install_fake_http_services(monkeypatch, services)
    plan_path = PlanStore(tmp_path / "state").save(_plan())
    original_plan_state = plan_path.read_text(encoding="utf-8")

    failed_apply = runner.invoke(cli.app, ["apply", _PLAN_ID, "--yes", "--json"])

    assert failed_apply.exit_code == 1
    payload = json.loads(failed_apply.stdout)
    assert payload["success"] is False
    assert payload["errors"] == [
        {
            "code": "apply-failed",
            "message": "Sonarr episode coordinate S01E03 was not found",
        }
    ]
    assert services.requests == [
        ("GET", f"{_SONARR_BASE_PATH}/series"),
        ("GET", f"{_SONARR_BASE_PATH}/episode"),
    ]
    assert services.mutations == []
    assert all(authenticated for _, authenticated in services.authentication_results)
    assert plan_path.read_text(encoding="utf-8") == original_plan_state
    _assert_secret_free(
        raw_plan=original_plan_state,
        output=failed_apply.output,
        credentials=credentials,
    )
