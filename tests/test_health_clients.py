"""Mocked API contract tests for read-only service health clients."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from pydantic import SecretStr

from wit.clients import (
    JellyfinClient,
    SeerrClient,
    ServiceHealthState,
    ServiceName,
    SonarrClient,
)

_CREDENTIAL = "health-contract-" + ("x" * 24)
_PRIVATE_RESPONSE_VALUE = "private-upstream-value"


def test_sonarr_combines_authenticated_system_status_and_health_contracts() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.headers["X-Api-Key"] == _CREDENTIAL
        if request.url.path == "/sonarr/api/v3/system/status":
            return httpx.Response(
                200,
                json={
                    "appName": "Sonarr",
                    "instanceName": "Sonarr",
                    "version": "4.0.16.2944",
                    "isProduction": True,
                },
            )
        if request.url.path == "/sonarr/api/v3/health":
            return httpx.Response(200, json=[])
        raise AssertionError(f"unexpected Sonarr path: {request.url.path}")

    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test/sonarr",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            assert _CREDENTIAL not in repr(client)
            result = await client.get_health()

        assert result.service is ServiceName.SONARR
        assert result.state is ServiceHealthState.HEALTHY
        assert result.version == "4.0.16.2944"
        assert result.summary == "Sonarr is healthy"
        assert _CREDENTIAL not in repr(result)

    asyncio.run(scenario())
    assert [request.url.path for request in requests] == [
        "/sonarr/api/v3/system/status",
        "/sonarr/api/v3/health",
    ]


def test_sonarr_normalises_reported_health_issues_without_raw_messages() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/system/status"):
            return httpx.Response(200, json={"version": "4.0.16.2944"})
        return httpx.Response(
            200,
            json=[
                {
                    "source": "DownloadClientCheck",
                    "type": "error",
                    "message": _PRIVATE_RESPONSE_VALUE,
                    "wikiUrl": "https://docs.example.test/private",
                },
                {
                    "source": "IndexerSearchCheck",
                    "type": "warning",
                    "message": "No search provider is available",
                },
            ],
        )

    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            result = await client.get_health()

        assert result.state is ServiceHealthState.UNHEALTHY
        assert result.version == "4.0.16.2944"
        assert result.summary == "Sonarr reported 2 health issues"
        assert _PRIVATE_RESPONSE_VALUE not in repr(result)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("status_code", "expected_state"),
    [
        (401, ServiceHealthState.UNAUTHORISED),
        (403, ServiceHealthState.UNAUTHORISED),
        (500, ServiceHealthState.UNHEALTHY),
        (503, ServiceHealthState.UNAVAILABLE),
    ],
)
def test_sonarr_normalises_http_failure_states(
    status_code: int,
    expected_state: ServiceHealthState,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            status_code,
            json={"message": _PRIVATE_RESPONSE_VALUE, "credential": _CREDENTIAL},
        )

    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            result = await client.get_health()

        assert result.state is expected_state
        assert result.version is None
        assert _PRIVATE_RESPONSE_VALUE not in repr(result)
        assert _CREDENTIAL not in repr(result)

    asyncio.run(scenario())


def test_sonarr_normalises_connection_failure_as_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(_PRIVATE_RESPONSE_VALUE, request=request)

    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            result = await client.get_health()

        assert result.state is ServiceHealthState.UNAVAILABLE
        assert result.summary == "Sonarr is unavailable"
        assert _PRIVATE_RESPONSE_VALUE not in repr(result)

    asyncio.run(scenario())


def test_jellyfin_queries_system_information_with_standard_api_key_header() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/jellyfin/System/Info"
        assert request.headers["Authorization"] == f'MediaBrowser Token="{_CREDENTIAL}"'
        assert "X-Emby-Token" not in request.headers
        return httpx.Response(
            200,
            json={
                "ServerName": _PRIVATE_RESPONSE_VALUE,
                "Version": "10.11.11",
                "ProductName": "Jellyfin Server",
                "StartupWizardCompleted": True,
                "HasPendingRestart": False,
                "IsShuttingDown": False,
                "ProgramDataPath": "/private/server/path",
            },
        )

    async def scenario() -> None:
        async with JellyfinClient(
            base_url="https://jellyfin.example.test/jellyfin",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            result = await client.get_health()

        assert result.service is ServiceName.JELLYFIN
        assert result.state is ServiceHealthState.HEALTHY
        assert result.version == "10.11.11"
        assert result.summary == "Jellyfin is healthy"
        assert _PRIVATE_RESPONSE_VALUE not in repr(result)
        assert _CREDENTIAL not in repr(result)

    asyncio.run(scenario())
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("overrides", "expected_summary"),
    [
        ({"StartupWizardCompleted": False}, "Jellyfin initial setup is incomplete"),
        ({"HasPendingRestart": True}, "Jellyfin reports a pending restart"),
        ({"IsShuttingDown": True}, "Jellyfin is shutting down"),
    ],
)
def test_jellyfin_normalises_unready_system_states(
    overrides: dict[str, bool],
    expected_summary: str,
) -> None:
    payload: dict[str, object] = {
        "Version": "10.11.11",
        "StartupWizardCompleted": True,
        "HasPendingRestart": False,
        "IsShuttingDown": False,
    }
    payload.update(overrides)

    async def scenario() -> None:
        async with JellyfinClient(
            base_url="https://jellyfin.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
        ) as client:
            result = await client.get_health()

        assert result.state is ServiceHealthState.UNHEALTHY
        assert result.version == "10.11.11"
        assert result.summary == expected_summary

    asyncio.run(scenario())


def test_jellyfin_distinguishes_unauthorised_and_invalid_responses() -> None:
    async def check(response: httpx.Response) -> ServiceHealthState:
        async with JellyfinClient(
            base_url="https://jellyfin.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(lambda request: response),
        ) as client:
            result = await client.get_health()
        assert _CREDENTIAL not in repr(result)
        return result.state

    unauthorised = asyncio.run(
        check(httpx.Response(403, json={"message": _PRIVATE_RESPONSE_VALUE}))
    )
    invalid = asyncio.run(check(httpx.Response(200, json={"Version": "10.11.11"})))

    assert unauthorised is ServiceHealthState.UNAUTHORISED
    assert invalid is ServiceHealthState.UNHEALTHY


def test_seerr_queries_public_status_without_authentication() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/seerr/api/v1/status"
        assert "Authorization" not in request.headers
        assert "X-Api-Key" not in request.headers
        return httpx.Response(
            200,
            json={
                "version": "3.3.0",
                "commitTag": "local",
                "updateAvailable": False,
                "commitsBehind": 0,
                "restartRequired": False,
            },
        )

    async def scenario() -> None:
        async with SeerrClient(
            base_url="https://seerr.example.test/seerr",
            http_transport=httpx.MockTransport(handler),
        ) as client:
            result = await client.get_health()

        assert result.service is ServiceName.SEERR
        assert result.state is ServiceHealthState.HEALTHY
        assert result.version == "3.3.0"
        assert result.summary == "Seerr is healthy"

    asyncio.run(scenario())
    assert len(requests) == 1


def test_seerr_normalises_restart_required_as_unhealthy() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "version": "3.3.0",
                "restartRequired": True,
            },
        )

    async def scenario() -> None:
        async with SeerrClient(
            base_url="https://seerr.example.test",
            http_transport=httpx.MockTransport(handler),
        ) as client:
            result = await client.get_health()

        assert result.state is ServiceHealthState.UNHEALTHY
        assert result.version == "3.3.0"
        assert result.summary == "Seerr reports that a restart is required"

    asyncio.run(scenario())


def test_seerr_distinguishes_unavailable_unauthorised_and_malformed_states() -> None:
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(_PRIVATE_RESPONSE_VALUE, request=request)

    async def check(handler: httpx.MockTransport) -> ServiceHealthState:
        async with SeerrClient(
            base_url="https://seerr.example.test",
            http_transport=handler,
        ) as client:
            result = await client.get_health()
        assert _PRIVATE_RESPONSE_VALUE not in repr(result)
        return result.state

    unavailable = asyncio.run(check(httpx.MockTransport(timeout_handler)))
    unauthorised = asyncio.run(
        check(
            httpx.MockTransport(
                lambda request: httpx.Response(
                    401,
                    json={"message": _PRIVATE_RESPONSE_VALUE},
                )
            )
        )
    )
    malformed = asyncio.run(
        check(
            httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    content=f'{{"version":"{_PRIVATE_RESPONSE_VALUE}"'.encode(),
                )
            )
        )
    )

    assert unavailable is ServiceHealthState.UNAVAILABLE
    assert unauthorised is ServiceHealthState.UNAUTHORISED
    assert malformed is ServiceHealthState.UNHEALTHY
