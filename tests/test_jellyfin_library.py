"""Mocked contracts for bounded, read-only Jellyfin library availability."""

from __future__ import annotations

import asyncio
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

from wit.clients import (
    AmbiguousJellyfinSeriesError,
    JellyfinClient,
    JellyfinEpisodeAvailabilityState,
    JellyfinLibraryLimitError,
    JellyfinLibraryState,
    JellyfinSeriesMatchMethod,
)
from wit.transport import HttpStatusError

_CREDENTIAL = "jellyfin-library-" + ("x" * 24)
_PRIVATE_RESPONSE_VALUE = "private-jellyfin-library-value"
_TARGET_SERIES_ID = "11111111-1111-1111-1111-111111111111"
_FALLBACK_SERIES_ID = "22222222-2222-2222-2222-222222222222"
_OTHER_SERIES_ID = "33333333-3333-3333-3333-333333333333"
_PAGE_SIZE = 200
_MAX_LIBRARY_ITEMS = 5_000


def _page(
    items: list[dict[str, object]],
    *,
    total: int | None = None,
    start_index: int = 0,
) -> dict[str, object]:
    return {
        "Items": items,
        "TotalRecordCount": len(items) if total is None else total,
        "StartIndex": start_index,
    }


def _series(
    item_id: str,
    *,
    name: str = "Clockwork Harbor",
    year: int | None = 2020,
    provider_ids: dict[str, str] | None = None,
) -> dict[str, object]:
    return {
        "Id": item_id,
        "Name": name,
        "Type": "Series",
        "ProductionYear": year,
        "ProviderIds": provider_ids or {},
        "Path": f"/private/{_PRIVATE_RESPONSE_VALUE}",
    }


def _episode(
    item_id: str,
    *,
    season: int | None,
    number: int | None,
    number_end: int | None = None,
) -> dict[str, object]:
    return {
        "Id": item_id,
        "Name": "Episode title not retained by the lookup",
        "Type": "Episode",
        "ParentIndexNumber": season,
        "IndexNumber": number,
        "IndexNumberEnd": number_end,
        "Path": f"/private/{_PRIVATE_RESPONSE_VALUE}",
        "MediaSources": [{"Path": f"/private/{_PRIVATE_RESPONSE_VALUE}.mkv"}],
    }


def test_matches_tvdb_identity_and_lists_visible_episode_coordinates() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/jellyfin/Items"
        assert request.headers["Authorization"] == f'MediaBrowser Token="{_CREDENTIAL}"'
        assert request.content == b""
        assert request.url.params["startIndex"] == "0"
        assert request.url.params["limit"] == str(_PAGE_SIZE)
        assert request.url.params["enableTotalRecordCount"] == "true"

        if len(requests) == 1:
            assert request.url.params["includeItemTypes"] == "Series"
            assert request.url.params["recursive"] == "true"
            assert request.url.params["hasTvdbId"] == "true"
            assert request.url.params["fields"] == "ProviderIds"
            assert "searchTerm" not in request.url.params
            return httpx.Response(
                200,
                json=_page(
                    [
                        _series(
                            _OTHER_SERIES_ID,
                            name="Another Series",
                            provider_ids={"Tvdb": "42"},
                        ),
                        _series(
                            _TARGET_SERIES_ID,
                            provider_ids={"tvDB": "12345", "Imdb": "tt12345"},
                        ),
                    ]
                ),
            )

        assert request.url.params["parentId"] == _TARGET_SERIES_ID
        assert request.url.params["includeItemTypes"] == "Episode"
        assert request.url.params["isMissing"] == "false"
        assert request.url.params["isPlaceHolder"] == "false"
        assert request.url.params["excludeLocationTypes"] == "Virtual"
        return httpx.Response(
            200,
            json=_page(
                [
                    _episode(
                        "44444444-4444-4444-4444-444444444444",
                        season=2,
                        number=3,
                        number_end=4,
                    ),
                    _episode(
                        "55555555-5555-5555-5555-555555555555",
                        season=1,
                        number=2,
                    ),
                    _episode(
                        "66666666-6666-6666-6666-666666666666",
                        season=None,
                        number=None,
                    ),
                    _episode(
                        "77777777-7777-7777-7777-777777777777",
                        season=1,
                        number=2,
                    ),
                ]
            ),
        )

    async def scenario() -> None:
        async with JellyfinClient(
            base_url="https://jellyfin.example.test/jellyfin",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            result = await client.get_library_availability(
                tvdb_id=12345,
                title="Clockwork Harbor",
                year=2020,
            )

        assert result.state is JellyfinLibraryState.AVAILABLE
        assert result.series is not None
        assert result.series.jellyfin_id == UUID(_TARGET_SERIES_ID)
        assert result.series.title == "Clockwork Harbor"
        assert result.series.year == 2020
        assert result.series.matched_by is JellyfinSeriesMatchMethod.TVDB_ID
        assert result.episode_coordinates == ((1, 2), (2, 3), (2, 4))
        assert result.episode_availability(2, 4) is JellyfinEpisodeAvailabilityState.VISIBLE
        assert result.episode_availability(1, 1) is JellyfinEpisodeAvailabilityState.EPISODE_ABSENT
        assert _PRIVATE_RESPONSE_VALUE not in repr(result)
        assert _CREDENTIAL not in repr(result)

    asyncio.run(scenario())
    assert len(requests) == 2
    assert all(request.method == "GET" and request.content == b"" for request in requests)


def test_falls_back_to_one_exact_normalised_title_and_year_without_conflicting_id() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json=_page(
                    [
                        _series(
                            _OTHER_SERIES_ID,
                            name="The Clockwork Harbor",
                            provider_ids={"Tvdb": "99999"},
                        )
                    ]
                ),
            )
        if len(requests) == 2:
            assert request.url.params["searchTerm"] == "The Clockwork Harbor"
            assert request.url.params["years"] == "2020"
            assert "hasTvdbId" not in request.url.params
            return httpx.Response(
                200,
                json=_page(
                    [
                        _series(
                            _OTHER_SERIES_ID,
                            name="The Clockwork Harbor",
                            provider_ids={"Tvdb": "99999"},
                        ),
                        _series(
                            _FALLBACK_SERIES_ID,
                            name="the clockwork: harbor!",
                            provider_ids={"Tmdb": "9876"},
                        ),
                    ]
                ),
            )

        assert request.url.params["parentId"] == _FALLBACK_SERIES_ID
        return httpx.Response(
            200,
            json=_page(
                [
                    _episode(
                        "88888888-8888-8888-8888-888888888888",
                        season=1,
                        number=1,
                    )
                ]
            ),
        )

    async def scenario() -> None:
        async with JellyfinClient(
            base_url="https://jellyfin.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            result = await client.get_library_availability(
                tvdb_id=12345,
                title="The Clockwork Harbor",
                year=2020,
            )

        assert result.state is JellyfinLibraryState.AVAILABLE
        assert result.series is not None
        assert result.series.jellyfin_id == UUID(_FALLBACK_SERIES_ID)
        assert result.series.matched_by is JellyfinSeriesMatchMethod.TITLE_YEAR
        assert result.episode_coordinates == ((1, 1),)

    asyncio.run(scenario())
    assert len(requests) == 3


def test_distinguishes_absent_series_and_absent_episode() -> None:
    requests: list[httpx.Request] = []

    def absent_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_page([]))

    async def absent_series_scenario() -> None:
        async with JellyfinClient(
            base_url="https://jellyfin.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(absent_handler),
        ) as client:
            result = await client.get_library_availability(
                tvdb_id=12345,
                title="Clockwork Harbor",
                year=2020,
            )

        assert result.state is JellyfinLibraryState.SERIES_ABSENT
        assert result.series is None
        assert result.episode_coordinates == ()
        assert result.episode_availability(1, 1) is JellyfinEpisodeAvailabilityState.SERIES_ABSENT

    asyncio.run(absent_series_scenario())
    assert len(requests) == 2

    episode_request_count = 0

    def absent_episode_handler(request: httpx.Request) -> httpx.Response:
        nonlocal episode_request_count
        episode_request_count += 1
        if episode_request_count == 1:
            return httpx.Response(
                200,
                json=_page(
                    [
                        _series(
                            _TARGET_SERIES_ID,
                            provider_ids={"Tvdb": "12345"},
                        )
                    ]
                ),
            )
        return httpx.Response(
            200,
            json=_page(
                [
                    _episode(
                        "99999999-9999-9999-9999-999999999999",
                        season=1,
                        number=1,
                    )
                ]
            ),
        )

    async def absent_episode_scenario() -> None:
        async with JellyfinClient(
            base_url="https://jellyfin.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(absent_episode_handler),
        ) as client:
            result = await client.get_library_availability(
                tvdb_id=12345,
                title="Clockwork Harbor",
                year=2020,
            )

        assert result.state is JellyfinLibraryState.AVAILABLE
        assert result.episode_availability(1, 2) is JellyfinEpisodeAvailabilityState.EPISODE_ABSENT

    asyncio.run(absent_episode_scenario())
    assert episode_request_count == 2


def test_distinguishes_unavailable_jellyfin_without_retaining_transport_details() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(_PRIVATE_RESPONSE_VALUE, request=request)

    async def scenario() -> None:
        async with JellyfinClient(
            base_url="https://jellyfin.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            result = await client.get_library_availability(
                tvdb_id=12345,
                title="Clockwork Harbor",
                year=2020,
            )

        assert result.state is JellyfinLibraryState.UNAVAILABLE
        assert result.series is None
        assert result.episode_coordinates == ()
        assert result.episode_availability(1, 1) is JellyfinEpisodeAvailabilityState.UNAVAILABLE
        assert _PRIVATE_RESPONSE_VALUE not in repr(result)
        assert _CREDENTIAL not in repr(result)

    asyncio.run(scenario())


def test_gateway_unavailability_is_degraded_but_authentication_failure_is_not() -> None:
    async def lookup(status_code: int) -> JellyfinLibraryState:
        async with JellyfinClient(
            base_url="https://jellyfin.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    status_code,
                    json={"message": _PRIVATE_RESPONSE_VALUE},
                )
            ),
        ) as client:
            result = await client.get_library_availability(
                tvdb_id=12345,
                title="Clockwork Harbor",
                year=2020,
            )
        return result.state

    assert asyncio.run(lookup(503)) is JellyfinLibraryState.UNAVAILABLE

    with pytest.raises(HttpStatusError) as captured:
        asyncio.run(lookup(403))
    assert str(captured.value) == "Jellyfin returned HTTP status 403"
    assert _PRIVATE_RESPONSE_VALUE not in str(captured.value)
    assert _CREDENTIAL not in str(captured.value)


def test_rejects_duplicate_external_id_candidates_before_listing_episodes() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=_page(
                [
                    _series(
                        _TARGET_SERIES_ID,
                        provider_ids={"Tvdb": "12345"},
                    ),
                    _series(
                        _OTHER_SERIES_ID,
                        provider_ids={"Tvdb": "12345"},
                    ),
                ]
            ),
        )

    async def scenario() -> None:
        async with JellyfinClient(
            base_url="https://jellyfin.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            await client.get_library_availability(
                tvdb_id=12345,
                title="Clockwork Harbor",
                year=2020,
            )

    with pytest.raises(AmbiguousJellyfinSeriesError) as captured:
        asyncio.run(scenario())
    assert str(captured.value) == ("Jellyfin contains multiple series with the requested TVDB ID")
    assert len(requests) == 1


def test_rejects_duplicate_title_year_fallback_candidates() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, json=_page([]))
        return httpx.Response(
            200,
            json=_page(
                [
                    _series(_FALLBACK_SERIES_ID),
                    _series(_OTHER_SERIES_ID, name="clockwork: harbor!"),
                ]
            ),
        )

    async def scenario() -> None:
        async with JellyfinClient(
            base_url="https://jellyfin.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            await client.get_library_availability(
                tvdb_id=12345,
                title="Clockwork Harbor",
                year=2020,
            )

    with pytest.raises(AmbiguousJellyfinSeriesError) as captured:
        asyncio.run(scenario())
    assert str(captured.value) == (
        "Jellyfin contains multiple series matching the requested title and year"
    )
    assert len(requests) == 2


def test_paginates_the_external_id_scan_without_silent_truncation() -> None:
    requests: list[httpx.Request] = []
    first_page = [
        _series(
            str(UUID(int=item_number)),
            name=f"Unrelated Series {item_number}",
            provider_ids={"Tvdb": str(10_000 + item_number)},
        )
        for item_number in range(1, _PAGE_SIZE + 1)
    ]
    target = _series(
        _TARGET_SERIES_ID,
        provider_ids={"Tvdb": "999999"},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.params["includeItemTypes"] == "Episode":
            return httpx.Response(200, json=_page([]))

        start_index = int(request.url.params["startIndex"])
        if start_index == 0:
            return httpx.Response(
                200,
                json=_page(first_page, total=_PAGE_SIZE + 1),
            )
        if start_index == _PAGE_SIZE:
            return httpx.Response(
                200,
                json=_page([target], total=_PAGE_SIZE + 1, start_index=_PAGE_SIZE),
            )
        raise AssertionError(f"unexpected start index: {start_index}")

    async def scenario() -> None:
        async with JellyfinClient(
            base_url="https://jellyfin.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            result = await client.get_library_availability(
                tvdb_id=999999,
                title="Clockwork Harbor",
                year=2020,
            )

        assert result.state is JellyfinLibraryState.AVAILABLE
        assert result.series is not None
        assert result.series.jellyfin_id == UUID(_TARGET_SERIES_ID)

    asyncio.run(scenario())
    assert [request.url.params["startIndex"] for request in requests] == ["0", "200", "0"]


def test_rejects_a_library_larger_than_the_fixed_lookup_bound() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=_page([], total=_MAX_LIBRARY_ITEMS + 1),
        )

    async def scenario() -> None:
        async with JellyfinClient(
            base_url="https://jellyfin.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            await client.get_library_availability(
                tvdb_id=12345,
                title="Clockwork Harbor",
                year=2020,
            )

    with pytest.raises(JellyfinLibraryLimitError) as captured:
        asyncio.run(scenario())
    assert str(captured.value) == (
        f"Jellyfin series lookup exceeded the {_MAX_LIBRARY_ITEMS}-item safety bound"
    )
    assert len(requests) == 1
