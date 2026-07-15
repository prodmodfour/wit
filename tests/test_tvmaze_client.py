"""Mocked API contract tests for the read-only TVmaze metadata client."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, time

import httpx
import pytest

from wit.clients import (
    InvalidTvmazeRequestError,
    InvalidTvmazeResponseError,
    TvmazeClient,
    TvmazeEpisodeType,
)
from wit.transport import MalformedJsonResponseError

_PRIVATE_RESPONSE_VALUE = "private-upstream-metadata"


def test_searches_shows_and_maps_external_ids_and_premiere_dates() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/metadata/search/shows"
        assert request.url.params["q"] == "Clockwork & Harbor"
        assert "Authorization" not in request.headers
        assert "X-Api-Key" not in request.headers
        return httpx.Response(
            200,
            json=[
                {
                    "score": 0.98,
                    "show": {
                        "id": 42,
                        "name": "Clockwork Harbor",
                        "premiered": "2021-03-14",
                        "externals": {
                            "tvrage": None,
                            "thetvdb": 31415,
                            "imdb": "tt1234567",
                        },
                        "summary": _PRIVATE_RESPONSE_VALUE,
                    },
                },
                {
                    "score": 0.75,
                    "show": {
                        "id": 43,
                        "name": "Clockwork Harbor: Rebuilt",
                        "premiered": "",
                        "externals": {"thetvdb": None, "imdb": None},
                    },
                },
                {
                    "score": 0.5,
                    "show": {
                        "id": 44,
                        "name": "Harbor Clock",
                    },
                },
            ],
        )

    async def scenario() -> None:
        async with TvmazeClient(
            base_url="https://tvmaze.example.test/metadata",
            http_transport=httpx.MockTransport(handler),
        ) as client:
            results = await client.search_shows("  Clockwork & Harbor  ")

        assert [result.score for result in results] == [0.98, 0.75, 0.5]

        exact = results[0].show
        assert exact.tvmaze_id == 42
        assert exact.title == "Clockwork Harbor"
        assert exact.premiere_date == date(2021, 3, 14)
        assert exact.premiere_year == 2021
        assert exact.tvdb_id == 31415
        assert exact.imdb_id == "tt1234567"

        missing_values = results[1].show
        assert missing_values.premiere_date is None
        assert missing_values.premiere_year is None
        assert missing_values.tvdb_id is None
        assert missing_values.imdb_id is None

        missing_object = results[2].show
        assert missing_object.premiere_date is None
        assert missing_object.tvdb_id is None
        assert missing_object.imdb_id is None
        assert _PRIVATE_RESPONSE_VALUE not in repr(results)

    asyncio.run(scenario())
    assert len(requests) == 1


def test_fetches_and_partitions_regular_and_special_episodes() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/metadata/shows/42/episodes"
        assert request.url.params["specials"] == "1"
        return httpx.Response(
            200,
            json=[
                {
                    "id": 101,
                    "name": "The Arrival",
                    "season": 1,
                    "number": 1,
                    "type": "regular",
                    "airdate": "2024-01-02",
                    "airtime": "20:30",
                    "airstamp": "2024-01-02T20:30:00+00:00",
                    "runtime": 45,
                },
                {
                    "id": 102,
                    "name": "A Date Without a Time",
                    "season": 1,
                    "number": 2,
                    "type": "regular",
                    "airdate": "2024-01-09",
                    "airtime": "",
                    "airstamp": None,
                },
                {
                    "id": 103,
                    "name": "To Be Scheduled",
                    "season": 1,
                    "number": 3,
                    "type": "regular",
                },
                {
                    "id": 201,
                    "name": "Festival Special",
                    "season": 1,
                    "number": None,
                    "type": "significant_special",
                    "airdate": "2024-01-05",
                    "airtime": "21:00",
                    "airstamp": "2024-01-05T21:00:00Z",
                },
                {
                    "id": 202,
                    "name": "Behind the Clock",
                    "season": 0,
                    "number": None,
                    "type": "insignificant_special",
                    "airdate": "",
                    "airtime": "",
                    "airstamp": "",
                },
            ],
        )

    async def scenario() -> None:
        async with TvmazeClient(
            base_url="https://tvmaze.example.test/metadata",
            http_transport=httpx.MockTransport(handler),
        ) as client:
            episodes = await client.get_episodes(42)

        assert [episode.tvmaze_id for episode in episodes.regular] == [101, 102, 103]
        assert [episode.tvmaze_id for episode in episodes.specials] == [201, 202]
        assert all(
            episode.episode_type is TvmazeEpisodeType.REGULAR for episode in episodes.regular
        )
        assert all(episode.episode_type.is_special for episode in episodes.specials)

        complete = episodes.regular[0]
        assert complete.season_number == 1
        assert complete.episode_number == 1
        assert complete.air_date == date(2024, 1, 2)
        assert complete.air_time == time(20, 30)
        assert complete.air_timestamp == datetime(2024, 1, 2, 20, 30, tzinfo=UTC)

        date_only = episodes.regular[1]
        assert date_only.air_date == date(2024, 1, 9)
        assert date_only.air_time is None
        assert date_only.air_timestamp is None

        undated = episodes.regular[2]
        assert undated.air_date is None
        assert undated.air_time is None
        assert undated.air_timestamp is None

        assert episodes.specials[0].episode_number is None
        assert episodes.specials[0].episode_type is TvmazeEpisodeType.SIGNIFICANT_SPECIAL
        assert episodes.specials[0].air_timestamp == datetime(
            2024,
            1,
            5,
            21,
            0,
            tzinfo=UTC,
        )
        assert episodes.specials[1].episode_type is TvmazeEpisodeType.INSIGNIFICANT_SPECIAL
        assert episodes.specials[1].air_date is None

    asyncio.run(scenario())
    assert len(requests) == 1


def test_returns_typed_empty_search_and_episode_results() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json=[])

    async def scenario() -> None:
        async with TvmazeClient(
            base_url="https://tvmaze.example.test",
            http_transport=httpx.MockTransport(handler),
        ) as client:
            search_results = await client.search_shows("No Such Fictional Show")
            episodes = await client.get_episodes(42)

        assert search_results == ()
        assert episodes.regular == ()
        assert episodes.specials == ()

    asyncio.run(scenario())
    assert paths == ["/search/shows", "/shows/42/episodes"]


@pytest.mark.parametrize(
    "payload",
    [
        {"score": 1.0, "show": {}},
        [{"score": "high", "show": {"id": 42, "name": "Clockwork Harbor"}}],
        [
            {
                "score": 1.0,
                "show": {
                    "id": 42,
                    "name": "Clockwork Harbor",
                    "externals": {"thetvdb": "31415", "imdb": "not-an-imdb-id"},
                    "summary": _PRIVATE_RESPONSE_VALUE,
                },
            }
        ],
    ],
)
def test_rejects_malformed_show_search_responses_without_raw_details(
    payload: object,
) -> None:
    async def scenario() -> None:
        async with TvmazeClient(
            base_url="https://tvmaze.example.test",
            http_transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
        ) as client:
            await client.search_shows("Clockwork Harbor")

    with pytest.raises(InvalidTvmazeResponseError) as captured:
        asyncio.run(scenario())

    assert str(captured.value) == "TVmaze returned an invalid show-search response"
    assert _PRIVATE_RESPONSE_VALUE not in str(captured.value)
    assert captured.value.__cause__ is None


@pytest.mark.parametrize(
    "episode",
    [
        {
            "id": 101,
            "name": "Missing Number",
            "season": 1,
            "number": None,
            "type": "regular",
        },
        {
            "id": 101,
            "name": "Unknown Kind",
            "season": 1,
            "number": 1,
            "type": "movie",
        },
        {
            "id": 101,
            "name": "Invalid Date",
            "season": 1,
            "number": 1,
            "type": "regular",
            "airdate": "not-a-date",
            "summary": _PRIVATE_RESPONSE_VALUE,
        },
        {
            "id": 101,
            "name": "Naive Timestamp",
            "season": 1,
            "number": 1,
            "type": "regular",
            "airstamp": "2024-01-02T20:30:00",
        },
    ],
)
def test_rejects_malformed_episode_responses_without_raw_details(
    episode: dict[str, object],
) -> None:
    async def scenario() -> None:
        async with TvmazeClient(
            base_url="https://tvmaze.example.test",
            http_transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[episode])),
        ) as client:
            await client.get_episodes(42)

    with pytest.raises(InvalidTvmazeResponseError) as captured:
        asyncio.run(scenario())

    assert str(captured.value) == "TVmaze returned an invalid episode-list response"
    assert _PRIVATE_RESPONSE_VALUE not in str(captured.value)
    assert captured.value.__cause__ is None


def test_propagates_shared_transport_malformed_json_failure() -> None:
    async def scenario() -> None:
        async with TvmazeClient(
            base_url="https://tvmaze.example.test",
            http_transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    content=f'{{"private":"{_PRIVATE_RESPONSE_VALUE}"'.encode(),
                )
            ),
        ) as client:
            await client.search_shows("Clockwork Harbor")

    with pytest.raises(MalformedJsonResponseError) as captured:
        asyncio.run(scenario())

    assert str(captured.value) == "TVmaze returned malformed JSON"
    assert _PRIVATE_RESPONSE_VALUE not in str(captured.value)


def test_rejects_invalid_requests_before_contacting_tvmaze() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[])

    async def scenario() -> None:
        async with TvmazeClient(
            base_url="https://tvmaze.example.test",
            http_transport=httpx.MockTransport(handler),
        ) as client:
            with pytest.raises(InvalidTvmazeRequestError):
                await client.search_shows(" \t ")
            with pytest.raises(InvalidTvmazeRequestError):
                await client.get_episodes(0)
            with pytest.raises(InvalidTvmazeRequestError):
                await client.get_episodes(True)

    asyncio.run(scenario())
    assert requests == []
