"""Mocked contracts for Sonarr library, lookup, and bounded add operations."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr

from wit.clients import (
    InvalidSonarrDefaultsError,
    InvalidSonarrRequestError,
    InvalidSonarrResponseError,
    SonarrClient,
    SonarrQualityProfile,
    SonarrRootFolder,
    SonarrSeries,
    SonarrSeriesAddResult,
    SonarrSeriesLookupResult,
    SonarrSeriesNotFoundError,
    SonarrSeriesType,
)

_CREDENTIAL = "sonarr-contract-" + ("x" * 24)
_PRIVATE_RESPONSE_VALUE = "private-upstream-sonarr-value"


def _new_series_prerequisite_response(request: httpx.Request) -> httpx.Response:
    assert request.method == "GET"
    assert request.content == b""
    if request.url.path == "/api/v3/series":
        assert request.url.params["tvdbId"] == "31415"
        return httpx.Response(200, json=[])
    if request.url.path == "/api/v3/series/lookup":
        assert request.url.params["term"] == "tvdb:31415"
        return httpx.Response(
            200,
            json=[
                {
                    "tvdbId": 31415,
                    "title": "Clockwork Harbor",
                    "year": 2021,
                    "seriesType": "standard",
                    "seasons": [
                        {"seasonNumber": 2, "monitored": True},
                        {"seasonNumber": 0, "monitored": True},
                        {"seasonNumber": 1, "monitored": True},
                    ],
                    "overview": _PRIVATE_RESPONSE_VALUE,
                }
            ],
        )
    if request.url.path == "/api/v3/rootfolder":
        return httpx.Response(
            200,
            json=[{"id": 7, "path": "/television", "accessible": True}],
        )
    if request.url.path == "/api/v3/qualityprofile":
        return httpx.Response(200, json=[{"id": 8, "name": "Balanced"}])
    raise AssertionError(f"unexpected Sonarr path: {request.url.path}")


def _added_series_response(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": 42,
        "tvdbId": 31415,
        "title": "Clockwork Harbor",
        "year": 2021,
        "monitored": False,
        "monitorNewItems": "none",
        "seasons": [
            {"seasonNumber": 0, "monitored": False},
            {"seasonNumber": 1, "monitored": False},
            {"seasonNumber": 2, "monitored": False},
        ],
        "path": "/television/Clockwork Harbor",
        "overview": _PRIVATE_RESPONSE_VALUE,
    }
    payload.update(updates)
    return payload


def test_lists_and_validates_library_defaults_with_bounded_models() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.headers["X-Api-Key"] == _CREDENTIAL
        assert request.content == b""
        if request.url.path == "/sonarr/api/v3/rootfolder":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 7,
                        "path": "/television",
                        "accessible": True,
                        "freeSpace": 987654321,
                        "unmappedFolders": [{"name": _PRIVATE_RESPONSE_VALUE, "path": "/ignored"}],
                    },
                    {
                        "id": 9,
                        "path": "/archive",
                        "accessible": False,
                        "freeSpace": None,
                    },
                ],
            )
        if request.url.path == "/sonarr/api/v3/qualityprofile":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 8,
                        "name": "Balanced",
                        "upgradeAllowed": True,
                        "items": [{"name": _PRIVATE_RESPONSE_VALUE}],
                    },
                    {
                        "id": 10,
                        "name": "Archive",
                        "upgradeAllowed": False,
                        "items": [],
                    },
                ],
            )
        raise AssertionError(f"unexpected Sonarr path: {request.url.path}")

    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test/sonarr",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            root_folders = await client.list_root_folders()
            quality_profiles = await client.list_quality_profiles()
            defaults = await client.validate_library_defaults(
                root_folder_id=7,
                quality_profile_id=8,
            )

        assert root_folders == (
            SonarrRootFolder(root_folder_id=7, path="/television", accessible=True),
            SonarrRootFolder(root_folder_id=9, path="/archive", accessible=False),
        )
        assert quality_profiles == (
            SonarrQualityProfile(quality_profile_id=8, name="Balanced"),
            SonarrQualityProfile(quality_profile_id=10, name="Archive"),
        )
        assert defaults.root_folder == root_folders[0]
        assert defaults.quality_profile == quality_profiles[0]
        assert defaults.root_folder.model_dump() == {
            "root_folder_id": 7,
            "path": "/television",
            "accessible": True,
        }
        assert defaults.quality_profile.model_dump() == {
            "quality_profile_id": 8,
            "name": "Balanced",
        }
        assert _PRIVATE_RESPONSE_VALUE not in repr(root_folders)
        assert _PRIVATE_RESPONSE_VALUE not in repr(quality_profiles)
        assert _CREDENTIAL not in repr(defaults)

    asyncio.run(scenario())
    assert [request.url.path for request in requests] == [
        "/sonarr/api/v3/rootfolder",
        "/sonarr/api/v3/qualityprofile",
        "/sonarr/api/v3/rootfolder",
        "/sonarr/api/v3/qualityprofile",
    ]


def test_finds_existing_series_by_tvdb_id() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/api/v3/series"
        assert request.url.params["tvdbId"] == "31415"
        assert request.headers["X-Api-Key"] == _CREDENTIAL
        return httpx.Response(
            200,
            json=[
                {
                    "id": 42,
                    "tvdbId": 31415,
                    "title": "Clockwork Harbor",
                    "year": 2021,
                    "path": "/private/library/path",
                    "overview": _PRIVATE_RESPONSE_VALUE,
                    "monitored": True,
                }
            ],
        )

    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            series = await client.find_series_by_tvdb_id(31415)

        assert series == SonarrSeries(
            sonarr_id=42,
            tvdb_id=31415,
            title="Clockwork Harbor",
            year=2021,
        )
        assert series is not None
        assert series.model_dump() == {
            "sonarr_id": 42,
            "tvdb_id": 31415,
            "title": "Clockwork Harbor",
            "year": 2021,
        }
        assert _PRIVATE_RESPONSE_VALUE not in repr(series)

    asyncio.run(scenario())
    assert len(requests) == 1


def test_returns_none_when_existing_series_and_lookup_are_missing() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        return httpx.Response(200, json=[])

    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            existing = await client.find_series_by_tvdb_id(27182)
            lookup = await client.lookup_series_by_tvdb_id(27182)

        assert existing is None
        assert lookup is None

    asyncio.run(scenario())
    assert requests[0].url.path == "/api/v3/series"
    assert requests[0].url.params["tvdbId"] == "27182"
    assert requests[1].url.path == "/api/v3/series/lookup"
    assert requests[1].url.params["term"] == "tvdb:27182"


def test_looks_up_not_yet_added_series_by_tvdb_id() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/sonarr/api/v3/series/lookup"
        assert request.url.params["term"] == "tvdb:31415"
        assert request.headers["X-Api-Key"] == _CREDENTIAL
        return httpx.Response(
            200,
            json=[
                {
                    "id": 0,
                    "tvdbId": 31415,
                    "title": "Clockwork Harbor",
                    "year": 2021,
                    "seriesType": "standard",
                    "titleSlug": "clockwork-harbor",
                    "overview": _PRIVATE_RESPONSE_VALUE,
                    "images": [{"remoteUrl": "https://images.example.test/private"}],
                    "seasons": [
                        {"seasonNumber": 2, "monitored": True},
                        {"seasonNumber": 0, "monitored": True},
                        {"seasonNumber": 1, "monitored": True},
                    ],
                }
            ],
        )

    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test/sonarr",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            result = await client.lookup_series_by_tvdb_id(31415)

        assert result == SonarrSeriesLookupResult(
            tvdb_id=31415,
            title="Clockwork Harbor",
            year=2021,
            series_type=SonarrSeriesType.STANDARD,
            season_numbers=(0, 1, 2),
        )
        assert result is not None
        assert result.model_dump() == {
            "tvdb_id": 31415,
            "title": "Clockwork Harbor",
            "year": 2021,
            "series_type": SonarrSeriesType.STANDARD,
            "season_numbers": (0, 1, 2),
        }
        assert _PRIVATE_RESPONSE_VALUE not in repr(result)

    asyncio.run(scenario())
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("root_folders", "quality_profiles", "expected_detail"),
    [
        (
            [{"id": 1, "path": "/other", "accessible": True}],
            [{"id": 8, "name": "Balanced"}],
            "root-folder ID 7 was not found",
        ),
        (
            [{"id": 7, "path": "/television", "accessible": False}],
            [{"id": 8, "name": "Balanced"}],
            "root-folder ID 7 is not accessible",
        ),
        (
            [{"id": 7, "path": "/television", "accessible": True}],
            [{"id": 1, "name": "Other"}],
            "quality-profile ID 8 was not found",
        ),
        (
            [],
            [],
            "root-folder ID 7 was not found; quality-profile ID 8 was not found",
        ),
    ],
)
def test_rejects_missing_or_inaccessible_library_defaults(
    root_folders: list[dict[str, object]],
    quality_profiles: list[dict[str, object]],
    expected_detail: str,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        if request.url.path == "/api/v3/rootfolder":
            return httpx.Response(200, json=root_folders)
        if request.url.path == "/api/v3/qualityprofile":
            return httpx.Response(200, json=quality_profiles)
        raise AssertionError(f"unexpected Sonarr path: {request.url.path}")

    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            await client.validate_library_defaults(
                root_folder_id=7,
                quality_profile_id=8,
            )

    with pytest.raises(InvalidSonarrDefaultsError) as captured:
        asyncio.run(scenario())

    assert str(captured.value) == f"Sonarr library defaults are invalid: {expected_detail}"
    assert _CREDENTIAL not in str(captured.value)
    assert [request.url.path for request in requests] == [
        "/api/v3/rootfolder",
        "/api/v3/qualityprofile",
    ]


@pytest.mark.parametrize(
    ("operation", "payload", "expected_message"),
    [
        (
            "root",
            [
                {"id": 7, "path": "/television", "accessible": True},
                {"id": 7, "path": "/duplicate", "accessible": True},
            ],
            "Sonarr returned an invalid root-folder response",
        ),
        (
            "existing",
            [
                {
                    "id": 42,
                    "tvdbId": 99999,
                    "title": "Wrong Series",
                    "year": 2021,
                    "overview": _PRIVATE_RESPONSE_VALUE,
                }
            ],
            "Sonarr returned an invalid existing-series response",
        ),
        (
            "lookup",
            [
                {
                    "tvdbId": 31415,
                    "title": "Clockwork Harbor",
                    "year": 2021,
                    "seriesType": "standard",
                    "seasons": [
                        {"seasonNumber": 1},
                        {"seasonNumber": 1},
                    ],
                    "overview": _PRIVATE_RESPONSE_VALUE,
                }
            ],
            "Sonarr returned an invalid series-lookup response",
        ),
    ],
)
def test_rejects_inconsistent_responses_without_raw_details(
    operation: str,
    payload: object,
    expected_message: str,
) -> None:
    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
        ) as client:
            if operation == "root":
                await client.list_root_folders()
            elif operation == "existing":
                await client.find_series_by_tvdb_id(31415)
            else:
                await client.lookup_series_by_tvdb_id(31415)

    with pytest.raises(InvalidSonarrResponseError) as captured:
        asyncio.run(scenario())

    assert str(captured.value) == expected_message
    assert _PRIVATE_RESPONSE_VALUE not in str(captured.value)
    assert captured.value.__cause__ is None


def test_rejects_invalid_identifiers_before_contacting_sonarr() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[])

    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            with pytest.raises(InvalidSonarrRequestError, match="TVDB"):
                await client.find_series_by_tvdb_id(0)
            with pytest.raises(InvalidSonarrRequestError, match="TVDB"):
                await client.lookup_series_by_tvdb_id(True)
            with pytest.raises(InvalidSonarrRequestError, match="root-folder"):
                await client.validate_library_defaults(
                    root_folder_id=-1,
                    quality_profile_id=8,
                )

    asyncio.run(scenario())
    assert requests == []


def test_adds_resolved_series_fully_unmonitored_without_automatic_search() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["X-Api-Key"] == _CREDENTIAL
        if request.method == "POST":
            assert request.url.path == "/api/v3/series"
            assert request.url.query == b""
            assert json.loads(request.content) == {
                "tvdbId": 31415,
                "title": "Clockwork Harbor",
                "year": 2021,
                "seriesType": "standard",
                "rootFolderPath": "/television",
                "qualityProfileId": 8,
                "seasonFolder": True,
                "monitored": False,
                "monitorNewItems": "none",
                "seasons": [
                    {"seasonNumber": 0, "monitored": False},
                    {"seasonNumber": 1, "monitored": False},
                    {"seasonNumber": 2, "monitored": False},
                ],
                "tags": [],
                "addOptions": {
                    "monitor": "none",
                    "searchForMissingEpisodes": False,
                    "searchForCutoffUnmetEpisodes": False,
                },
            }
            return httpx.Response(201, json=_added_series_response())
        return _new_series_prerequisite_response(request)

    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            result = await client.add_series_unmonitored(
                tvdb_id=31415,
                root_folder_id=7,
                quality_profile_id=8,
            )

        expected_series = SonarrSeries(
            sonarr_id=42,
            tvdb_id=31415,
            title="Clockwork Harbor",
            year=2021,
        )
        assert result == SonarrSeriesAddResult(series=expected_series, created=True)
        assert result.model_dump() == {
            "series": {
                "sonarr_id": 42,
                "tvdb_id": 31415,
                "title": "Clockwork Harbor",
                "year": 2021,
            },
            "created": True,
        }
        assert _PRIVATE_RESPONSE_VALUE not in repr(result)
        assert _CREDENTIAL not in repr(result)

    asyncio.run(scenario())
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/api/v3/series"),
        ("GET", "/api/v3/series/lookup"),
        ("GET", "/api/v3/rootfolder"),
        ("GET", "/api/v3/qualityprofile"),
        ("POST", "/api/v3/series"),
    ]
    assert all("command" not in request.url.path for request in requests)
    assert all("episode" not in request.url.path for request in requests)


def test_returns_existing_series_idempotently_without_posting() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/api/v3/series"
        assert request.url.params["tvdbId"] == "31415"
        return httpx.Response(
            200,
            json=[
                {
                    "id": 91,
                    "tvdbId": 31415,
                    "title": "Clockwork Harbor",
                    "year": 2021,
                    "monitored": True,
                    "overview": _PRIVATE_RESPONSE_VALUE,
                }
            ],
        )

    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            result = await client.add_series_unmonitored(
                tvdb_id=31415,
                root_folder_id=7,
                quality_profile_id=8,
            )

        assert result == SonarrSeriesAddResult(
            series=SonarrSeries(
                sonarr_id=91,
                tvdb_id=31415,
                title="Clockwork Harbor",
                year=2021,
            ),
            created=False,
        )
        assert _PRIVATE_RESPONSE_VALUE not in repr(result)

    asyncio.run(scenario())
    assert len(requests) == 1


def test_treats_a_concurrent_existing_series_as_idempotent() -> None:
    requests: list[httpx.Request] = []
    existing_checks = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal existing_checks
        requests.append(request)
        if request.method == "GET" and request.url.path == "/api/v3/series":
            existing_checks += 1
            if existing_checks == 1:
                return httpx.Response(200, json=[])
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 73,
                        "tvdbId": 31415,
                        "title": "Clockwork Harbor",
                        "year": 2021,
                    }
                ],
            )
        if request.method == "POST":
            assert request.url.path == "/api/v3/series"
            return httpx.Response(409, json={"message": _PRIVATE_RESPONSE_VALUE})
        return _new_series_prerequisite_response(request)

    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            result = await client.add_series_unmonitored(
                tvdb_id=31415,
                root_folder_id=7,
                quality_profile_id=8,
            )

        assert result.series.sonarr_id == 73
        assert result.created is False
        assert _PRIVATE_RESPONSE_VALUE not in repr(result)

    asyncio.run(scenario())
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/api/v3/series"),
        ("GET", "/api/v3/series/lookup"),
        ("GET", "/api/v3/rootfolder"),
        ("GET", "/api/v3/qualityprofile"),
        ("POST", "/api/v3/series"),
        ("GET", "/api/v3/series"),
    ]


def test_rejects_missing_plan_tvdb_identity_before_contacting_sonarr() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            await client.add_series_unmonitored(
                tvdb_id=None,
                root_folder_id=7,
                quality_profile_id=8,
            )

    with pytest.raises(InvalidSonarrRequestError, match="TVDB ID") as captured:
        asyncio.run(scenario())

    assert _CREDENTIAL not in str(captured.value)
    assert requests == []


def test_rejects_a_tvdb_series_that_sonarr_cannot_resolve() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        return httpx.Response(200, json=[])

    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            await client.add_series_unmonitored(
                tvdb_id=31415,
                root_folder_id=7,
                quality_profile_id=8,
            )

    with pytest.raises(
        SonarrSeriesNotFoundError,
        match="could not resolve the requested TVDB series",
    ) as captured:
        asyncio.run(scenario())

    assert _CREDENTIAL not in str(captured.value)
    assert [request.url.path for request in requests] == [
        "/api/v3/series",
        "/api/v3/series/lookup",
    ]


@pytest.mark.parametrize(
    "response_updates",
    [
        {"tvdbId": 99999},
        {"monitored": True},
        {"monitorNewItems": "all"},
        {"seasons": [{"seasonNumber": 1, "monitored": True}]},
        {
            "seasons": [
                {"seasonNumber": 1, "monitored": False},
                {"seasonNumber": 1, "monitored": False},
            ]
        },
    ],
)
def test_rejects_inconsistent_or_monitored_series_add_responses(
    response_updates: dict[str, object],
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(201, json=_added_series_response(**response_updates))
        return _new_series_prerequisite_response(request)

    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            await client.add_series_unmonitored(
                tvdb_id=31415,
                root_folder_id=7,
                quality_profile_id=8,
            )

    with pytest.raises(InvalidSonarrResponseError) as captured:
        asyncio.run(scenario())

    assert str(captured.value) == "Sonarr returned an invalid series-add response"
    assert _PRIVATE_RESPONSE_VALUE not in str(captured.value)
    assert captured.value.__cause__ is None
    assert requests[-1].method == "POST"
