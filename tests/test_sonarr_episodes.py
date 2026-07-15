"""Mocked contracts for Sonarr episode listing and exact coordinate mapping."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr

from wit.clients import (
    InvalidSonarrRequestError,
    InvalidSonarrResponseError,
    SonarrClient,
    SonarrEpisode,
    SonarrEpisodeAirStatus,
    SonarrEpisodeMappingError,
    map_episode_coordinate,
)

_CREDENTIAL = "sonarr-episodes-" + ("x" * 24)
_PRIVATE_RESPONSE_VALUE = "private-upstream-episode-value"
_REFERENCE_TIME = datetime(2025, 1, 10, 12, tzinfo=UTC)


def _episode_payload(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": 101,
        "seriesId": 42,
        "seasonNumber": 1,
        "episodeNumber": 1,
        "title": "The Arrival",
        "airDateUtc": "2025-01-03T20:00:00Z",
        "monitored": False,
        "hasFile": True,
        "overview": _PRIVATE_RESPONSE_VALUE,
        "episodeFile": {"path": "/private/library/episode.mkv"},
    }
    payload.update(updates)
    return payload


def _episode(
    episode_id: int,
    season_number: int,
    episode_number: int,
    *,
    air_status: SonarrEpisodeAirStatus = SonarrEpisodeAirStatus.AIRED,
) -> SonarrEpisode:
    return SonarrEpisode(
        episode_id=episode_id,
        season_number=season_number,
        episode_number=episode_number,
        title=f"Episode {episode_id}",
        air_status=air_status,
        monitored=False,
        has_file=False,
    )


def test_lists_normal_special_and_unaired_episodes_with_bounded_state() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/sonarr/api/v3/episode"
        assert request.url.params["seriesId"] == "42"
        assert request.headers["X-Api-Key"] == _CREDENTIAL
        assert request.content == b""
        return httpx.Response(
            200,
            json=[
                _episode_payload(title="  The Arrival  "),
                _episode_payload(
                    id=7,
                    seasonNumber=0,
                    episodeNumber=1,
                    title="Festival Special",
                    airDateUtc=None,
                    monitored=True,
                    hasFile=False,
                ),
                _episode_payload(
                    id=102,
                    episodeNumber=2,
                    title="Tomorrow's Harbor",
                    airDateUtc="2025-02-01T20:00:00+00:00",
                    monitored=True,
                    hasFile=False,
                ),
            ],
        )

    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test/sonarr",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            episodes = await client.list_episodes(42, as_of=_REFERENCE_TIME)

        assert episodes == (
            SonarrEpisode(
                episode_id=101,
                season_number=1,
                episode_number=1,
                title="The Arrival",
                air_status=SonarrEpisodeAirStatus.AIRED,
                monitored=False,
                has_file=True,
            ),
            SonarrEpisode(
                episode_id=7,
                season_number=0,
                episode_number=1,
                title="Festival Special",
                air_status=SonarrEpisodeAirStatus.UNKNOWN,
                monitored=True,
                has_file=False,
            ),
            SonarrEpisode(
                episode_id=102,
                season_number=1,
                episode_number=2,
                title="Tomorrow's Harbor",
                air_status=SonarrEpisodeAirStatus.UNAIRED,
                monitored=True,
                has_file=False,
            ),
        )
        assert episodes[0].model_dump() == {
            "episode_id": 101,
            "season_number": 1,
            "episode_number": 1,
            "title": "The Arrival",
            "air_status": SonarrEpisodeAirStatus.AIRED,
            "monitored": False,
            "has_file": True,
        }
        assert _PRIVATE_RESPONSE_VALUE not in repr(episodes)
        assert _CREDENTIAL not in repr(episodes)

    asyncio.run(scenario())
    assert len(requests) == 1


def test_maps_normal_special_and_unaired_coordinates_to_episode_ids() -> None:
    episodes = (
        _episode(101, 1, 1),
        _episode(7, 0, 1, air_status=SonarrEpisodeAirStatus.UNKNOWN),
        _episode(102, 1, 2, air_status=SonarrEpisodeAirStatus.UNAIRED),
    )

    assert map_episode_coordinate(episodes, (1, 1)) == 101
    assert map_episode_coordinate(episodes, (0, 1)) == 7
    assert map_episode_coordinate(episodes, (1, 2)) == 102


def test_fails_safely_when_episode_coordinate_is_missing() -> None:
    episodes = (_episode(101, 1, 1), _episode(103, 1, 3))

    with pytest.raises(SonarrEpisodeMappingError) as captured:
        map_episode_coordinate(episodes, (1, 2))

    assert str(captured.value) == "Sonarr episode coordinate S01E02 was not found"


def test_fails_safely_when_episode_coordinate_is_duplicate() -> None:
    episodes = (_episode(101, 1, 1), _episode(999, 1, 1))

    with pytest.raises(SonarrEpisodeMappingError) as captured:
        map_episode_coordinate(episodes, (1, 1))

    assert str(captured.value) == "Sonarr episode coordinate S01E01 is ambiguous"


@pytest.mark.parametrize(
    "payload",
    [
        {"records": []},
        [_episode_payload(seriesId=99)],
        [_episode_payload(), _episode_payload()],
        [_episode_payload(episodeNumber=0)],
        [_episode_payload(airDateUtc="2025-01-03T20:00:00")],
        [_episode_payload(hasFile="yes")],
    ],
)
def test_rejects_invalid_episode_responses_without_raw_details(payload: object) -> None:
    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
        ) as client:
            await client.list_episodes(42, as_of=_REFERENCE_TIME)

    with pytest.raises(InvalidSonarrResponseError) as captured:
        asyncio.run(scenario())

    assert str(captured.value) == "Sonarr returned an invalid episode-list response"
    assert _PRIVATE_RESPONSE_VALUE not in str(captured.value)
    assert captured.value.__cause__ is None


def test_returns_an_empty_typed_episode_list() -> None:
    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[])),
        ) as client:
            assert await client.list_episodes(42, as_of=_REFERENCE_TIME) == ()

    asyncio.run(scenario())


def test_rejects_invalid_series_ids_times_and_coordinates_before_network_access() -> None:
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
            with pytest.raises(InvalidSonarrRequestError, match="series ID"):
                await client.list_episodes(0, as_of=_REFERENCE_TIME)
            with pytest.raises(InvalidSonarrRequestError, match="series ID"):
                await client.list_episodes(True, as_of=_REFERENCE_TIME)
            with pytest.raises(InvalidSonarrRequestError, match="timezone-aware"):
                await client.list_episodes(42, as_of=datetime(2025, 1, 10, 12))

    asyncio.run(scenario())
    assert requests == []

    for coordinate in (
        (-1, 1),
        (1, 0),
        (True, 1),
        (2_147_483_648, 1),
        (1, 2_147_483_648),
    ):
        with pytest.raises(InvalidSonarrRequestError, match="episode coordinate"):
            map_episode_coordinate((), coordinate)
