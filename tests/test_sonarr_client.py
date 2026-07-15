"""Mocked contracts for Sonarr library defaults and TVDB series lookups."""

from __future__ import annotations

import asyncio

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
    SonarrSeriesLookupResult,
    SonarrSeriesType,
)

_CREDENTIAL = "sonarr-contract-" + ("x" * 24)
_PRIVATE_RESPONSE_VALUE = "private-upstream-sonarr-value"


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
