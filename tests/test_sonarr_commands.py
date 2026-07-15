"""Mocked contracts for targeted Sonarr EpisodeSearch commands and polling."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from wit.clients import (
    InvalidSonarrRequestError,
    InvalidSonarrResponseError,
    SonarrClient,
    SonarrCommandFailedError,
    SonarrCommandPollingPolicy,
    SonarrCommandRejectedError,
    SonarrCommandState,
    SonarrCommandStatus,
    SonarrCommandTimeoutError,
)

_CREDENTIAL = "sonarr-command-" + ("x" * 24)
_PRIVATE_RESPONSE_VALUE = "private-upstream-command-value"
_COMMAND_ID = 501


def _command_payload(
    *,
    command_id: object = _COMMAND_ID,
    name: object = "EpisodeSearch",
    state: object = "queued",
) -> dict[str, object]:
    return {
        "id": command_id,
        "name": name,
        "status": state,
        "message": _PRIVATE_RESPONSE_VALUE,
        "exception": _PRIVATE_RESPONSE_VALUE,
        "body": {
            "name": "EpisodeSearch",
            "episodeIds": [101, 102],
            "private": _PRIVATE_RESPONSE_VALUE,
        },
    }


def test_submits_only_explicit_episode_ids_and_returns_initial_state() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        assert request.url.path == "/sonarr/api/v3/command"
        assert request.url.query == b""
        assert request.headers["X-Api-Key"] == _CREDENTIAL
        assert json.loads(request.content) == {
            "name": "EpisodeSearch",
            "episodeIds": [101, 102],
        }
        return httpx.Response(201, json=_command_payload())

    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test/sonarr",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            result = await client.submit_episode_search([101, 102])

        assert result == SonarrCommandStatus(
            command_id=_COMMAND_ID,
            state=SonarrCommandState.QUEUED,
        )
        assert result.model_dump() == {
            "command_id": _COMMAND_ID,
            "state": SonarrCommandState.QUEUED,
        }
        assert _PRIVATE_RESPONSE_VALUE not in repr(result)
        assert _CREDENTIAL not in repr(result)

    asyncio.run(scenario())
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("episode_ids", "expected_message"),
    [
        ([], "Sonarr episode ID list must not be empty"),
        ([101, 101], "Sonarr episode ID list must contain unique IDs"),
        ([0], "Sonarr episode ID must be a positive integer"),
        ([True], "Sonarr episode ID must be a positive integer"),
    ],
)
def test_rejects_invalid_search_episode_ids_before_submission(
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
            await client.submit_episode_search(episode_ids)

    with pytest.raises(InvalidSonarrRequestError) as captured:
        asyncio.run(scenario())

    assert str(captured.value) == expected_message
    assert requests == []


def test_polls_command_status_read_only_until_completed() -> None:
    requests: list[httpx.Request] = []
    states = iter(("queued", "started", "completed"))
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == f"/sonarr/api/v3/command/{_COMMAND_ID}"
        assert request.url.query == b""
        assert request.content == b""
        assert request.headers["X-Api-Key"] == _CREDENTIAL
        return httpx.Response(200, json=_command_payload(state=next(states)))

    async def record_sleep(seconds: float) -> None:
        delays.append(seconds)

    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test/sonarr",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            result = await client.poll_command_status(
                _COMMAND_ID,
                policy=SonarrCommandPollingPolicy(
                    max_attempts=3,
                    interval_seconds=0.25,
                ),
                sleeper=record_sleep,
            )

        assert result == SonarrCommandStatus(
            command_id=_COMMAND_ID,
            state=SonarrCommandState.COMPLETED,
        )

    asyncio.run(scenario())
    assert len(requests) == 3
    assert delays == [0.25, 0.25]


def test_stops_polling_at_the_configured_attempt_limit() -> None:
    requests: list[httpx.Request] = []
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_command_payload(state="queued"))

    async def record_sleep(seconds: float) -> None:
        delays.append(seconds)

    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            await client.poll_command_status(
                _COMMAND_ID,
                policy=SonarrCommandPollingPolicy(
                    max_attempts=3,
                    interval_seconds=0.2,
                ),
                sleeper=record_sleep,
            )

    with pytest.raises(SonarrCommandTimeoutError) as captured:
        asyncio.run(scenario())

    assert str(captured.value) == (
        f"Sonarr EpisodeSearch command {_COMMAND_ID} did not complete within 3 status checks"
    )
    assert _PRIVATE_RESPONSE_VALUE not in str(captured.value)
    assert _CREDENTIAL not in str(captured.value)
    assert len(requests) == 3
    assert delays == [0.2, 0.2]


def test_treats_a_failed_polled_command_as_an_explicit_error() -> None:
    requests: list[httpx.Request] = []
    delays: list[float] = []

    async def record_sleep(seconds: float) -> None:
        delays.append(seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_command_payload(state="failed"))

    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            await client.poll_command_status(
                _COMMAND_ID,
                policy=SonarrCommandPollingPolicy(max_attempts=5),
                sleeper=record_sleep,
            )

    with pytest.raises(SonarrCommandFailedError) as captured:
        asyncio.run(scenario())

    assert str(captured.value) == f"Sonarr EpisodeSearch command {_COMMAND_ID} failed"
    assert _PRIVATE_RESPONSE_VALUE not in str(captured.value)
    assert _CREDENTIAL not in str(captured.value)
    assert len(requests) == 1
    assert delays == []


@pytest.mark.parametrize("state", ["aborted", "cancelled", "orphaned"])
def test_treats_terminally_stopped_commands_as_rejected(state: str) -> None:
    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=_command_payload(state=state))
            ),
        ) as client:
            await client.get_command_status(_COMMAND_ID)

    with pytest.raises(SonarrCommandRejectedError) as captured:
        asyncio.run(scenario())

    assert str(captured.value) == (
        f"Sonarr EpisodeSearch command {_COMMAND_ID} was rejected ({state})"
    )
    assert _PRIVATE_RESPONSE_VALUE not in str(captured.value)
    assert _CREDENTIAL not in str(captured.value)


def test_translates_a_rejected_submission_without_exposing_response_details() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(409, json={"message": _PRIVATE_RESPONSE_VALUE})

    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            await client.submit_episode_search([101])

    with pytest.raises(SonarrCommandRejectedError) as captured:
        asyncio.run(scenario())

    assert str(captured.value) == (
        "Sonarr rejected EpisodeSearch command submission with HTTP status 409"
    )
    assert _PRIVATE_RESPONSE_VALUE not in str(captured.value)
    assert _CREDENTIAL not in str(captured.value)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"id": _COMMAND_ID, "status": "queued"},
        _command_payload(name="SeriesSearch"),
        _command_payload(command_id=str(_COMMAND_ID)),
        _command_payload(state="unknown"),
        _command_payload(command_id=_COMMAND_ID + 1),
    ],
)
def test_rejects_malformed_or_inconsistent_command_status(payload: object) -> None:
    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
        ) as client:
            await client.get_command_status(_COMMAND_ID)

    with pytest.raises(InvalidSonarrResponseError) as captured:
        asyncio.run(scenario())

    assert str(captured.value) == "Sonarr returned an invalid EpisodeSearch command response"
    assert _PRIVATE_RESPONSE_VALUE not in str(captured.value)
    assert _CREDENTIAL not in str(captured.value)
    assert captured.value.__cause__ is None


@pytest.mark.parametrize(
    "policy_factory",
    [
        lambda: SonarrCommandPollingPolicy(max_attempts=0),
        lambda: SonarrCommandPollingPolicy(max_attempts=121),
        lambda: SonarrCommandPollingPolicy(interval_seconds=0.09),
        lambda: SonarrCommandPollingPolicy(interval_seconds=10.01),
    ],
)
def test_polling_policy_is_bounded(
    policy_factory: Callable[[], SonarrCommandPollingPolicy],
) -> None:
    with pytest.raises(ValidationError):
        policy_factory()
