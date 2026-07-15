"""Mocked contracts for exact Sonarr episode-monitoring mutations."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr

from wit.clients import (
    InvalidSonarrRequestError,
    InvalidSonarrResponseError,
    SonarrClient,
    SonarrEpisodeMonitoringResult,
)
from wit.transport import HttpStatusError

_CREDENTIAL = "sonarr-monitoring-" + ("x" * 24)
_PRIVATE_RESPONSE_VALUE = "private-upstream-monitoring-value"


def _monitored_episode(episode_id: int, *, monitored: bool = True) -> dict[str, object]:
    return {
        "id": episode_id,
        "monitored": monitored,
        "seriesId": 42,
        "seasonNumber": 1,
        "episodeNumber": episode_id - 100,
        "title": f"Episode {episode_id}",
        "overview": _PRIVATE_RESPONSE_VALUE,
    }


def test_monitors_only_the_explicit_episode_ids_and_verifies_the_response() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "PUT"
        assert request.url.path == "/sonarr/api/v3/episode/monitor"
        assert request.url.query == b""
        assert request.headers["X-Api-Key"] == _CREDENTIAL
        assert json.loads(request.content) == {
            "episodeIds": [101, 102],
            "monitored": True,
        }
        return httpx.Response(
            202,
            json=[
                _monitored_episode(102),
                _monitored_episode(101),
            ],
        )

    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test/sonarr",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            result = await client.monitor_episodes([101, 102])

        assert result == SonarrEpisodeMonitoringResult(
            episode_ids=(101, 102),
            monitored=True,
        )
        assert result.model_dump() == {
            "episode_ids": (101, 102),
            "monitored": True,
        }
        assert _PRIVATE_RESPONSE_VALUE not in repr(result)
        assert _CREDENTIAL not in repr(result)

    asyncio.run(scenario())
    assert len(requests) == 1
    assert "/series" not in requests[0].url.path
    assert "/season" not in requests[0].url.path
    assert "/command" not in requests[0].url.path


@pytest.mark.parametrize(
    ("episode_ids", "expected_message"),
    [
        ([], "Sonarr episode ID list must not be empty"),
        ([101, 101], "Sonarr episode ID list must contain unique IDs"),
        ([0], "Sonarr episode ID must be a positive integer"),
        ([True], "Sonarr episode ID must be a positive integer"),
    ],
)
def test_rejects_invalid_episode_id_lists_before_contacting_sonarr(
    episode_ids: list[int],
    expected_message: str,
) -> None:
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
            await client.monitor_episodes(episode_ids)

    with pytest.raises(InvalidSonarrRequestError) as captured:
        asyncio.run(scenario())

    assert str(captured.value) == expected_message
    assert _CREDENTIAL not in str(captured.value)
    assert requests == []


@pytest.mark.parametrize(
    "payload",
    [
        {"records": [_monitored_episode(101), _monitored_episode(102)]},
        [],
        [_monitored_episode(101)],
        [_monitored_episode(101), _monitored_episode(103)],
        [_monitored_episode(101), _monitored_episode(101)],
        [_monitored_episode(101), _monitored_episode(102, monitored=False)],
        [{"id": "101", "monitored": True}, _monitored_episode(102)],
    ],
)
def test_rejects_malformed_or_inconsistent_monitoring_responses(payload: object) -> None:
    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(lambda request: httpx.Response(202, json=payload)),
        ) as client:
            await client.monitor_episodes([101, 102])

    with pytest.raises(InvalidSonarrResponseError) as captured:
        asyncio.run(scenario())

    assert str(captured.value) == "Sonarr returned an invalid episode-monitor response"
    assert _PRIVATE_RESPONSE_VALUE not in str(captured.value)
    assert _CREDENTIAL not in str(captured.value)
    assert captured.value.__cause__ is None


def test_propagates_a_redacted_sonarr_monitoring_failure() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500, json={"message": _PRIVATE_RESPONSE_VALUE})

    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            await client.monitor_episodes([101])

    with pytest.raises(HttpStatusError) as captured:
        asyncio.run(scenario())

    assert str(captured.value) == "Sonarr returned HTTP status 500"
    assert _PRIVATE_RESPONSE_VALUE not in str(captured.value)
    assert _CREDENTIAL not in str(captured.value)
    assert len(requests) == 1
