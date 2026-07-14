"""Unit tests for Wit's shared asynchronous HTTP transport."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence

import httpx
import pytest
from pydantic import SecretStr

from wit.transport import (
    HttpConnectionError,
    HttpStatusError,
    HttpTimeoutError,
    HttpTransport,
    HttpTransportConfigurationError,
    HttpTransportError,
    MalformedJsonResponseError,
)

_AUTHENTICATION_VALUE = "transport-auth-" + ("x" * 24)


def test_supports_parameters_json_bodies_authentication_and_bounded_timeouts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["X-Test-Token"] == _AUTHENTICATION_VALUE
        assert request.extensions["timeout"] == {
            "connect": 1.5,
            "read": 7.0,
            "write": 7.0,
            "pool": 1.5,
        }
        if request.method == "GET":
            return httpx.Response(200, json={"items": [1, 2]})
        return httpx.Response(201, json=[{"accepted": True}])

    async def scenario() -> None:
        backend = httpx.MockTransport(handler)
        client = HttpTransport(
            base_url="https://service.example.test/prefix",
            service_name="Test service",
            connect_timeout_seconds=1.5,
            read_timeout_seconds=7,
            auth_headers={"X-Test-Token": SecretStr(_AUTHENTICATION_VALUE)},
            transport=backend,
        )
        assert _AUTHENTICATION_VALUE not in repr(client)

        async with client:
            get_result = await client.request_json(
                "GET",
                "/resources",
                params={"page": 2, "tag": ["one", "two"]},
            )
            post_result = await client.request_json(
                "POST",
                "resources/actions",
                json_body={"resourceIds": [3, 5], "enabled": True},
            )

        assert get_result == {"items": [1, 2]}
        assert post_result == [{"accepted": True}]

    caplog.set_level(logging.DEBUG)
    asyncio.run(scenario())

    assert len(requests) == 2
    assert requests[0].url.path == "/prefix/resources"
    assert requests[0].url.params["page"] == "2"
    assert requests[0].url.params.get_list("tag") == ["one", "two"]
    assert requests[1].url.path == "/prefix/resources/actions"
    assert json.loads(requests[1].content) == {
        "resourceIds": [3, 5],
        "enabled": True,
    }
    assert _AUTHENTICATION_VALUE not in caplog.text


def test_translates_connection_failure_without_leaking_authentication() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            f"upstream exposed {_AUTHENTICATION_VALUE}",
            request=request,
        )

    async def scenario() -> None:
        async with HttpTransport(
            base_url="https://service.example.test",
            service_name="Test service",
            auth_headers={"Authorization": _AUTHENTICATION_VALUE},
            transport=httpx.MockTransport(handler),
        ) as client:
            await client.request_json("GET", "resources")

    with pytest.raises(HttpConnectionError) as captured:
        asyncio.run(scenario())

    assert "could not be completed" in str(captured.value)
    assert _AUTHENTICATION_VALUE not in str(captured.value)
    assert captured.value.__cause__ is None


def test_translates_timeout_without_leaking_authentication() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(
            f"timeout included {_AUTHENTICATION_VALUE}",
            request=request,
        )

    async def scenario() -> None:
        async with HttpTransport(
            base_url="https://service.example.test",
            service_name="Test service",
            auth_headers={"X-Test-Token": _AUTHENTICATION_VALUE},
            transport=httpx.MockTransport(handler),
        ) as client:
            await client.request_json("GET", "resources")

    with pytest.raises(HttpTimeoutError) as captured:
        asyncio.run(scenario())

    assert "timed out" in str(captured.value)
    assert _AUTHENTICATION_VALUE not in str(captured.value)
    assert captured.value.__cause__ is None


def test_translates_status_failure_without_response_or_authentication_values() -> None:
    response_marker = "private-upstream-detail"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": response_marker, "received": _AUTHENTICATION_VALUE},
        )

    async def scenario() -> None:
        async with HttpTransport(
            base_url="https://service.example.test",
            service_name="Test service",
            auth_headers={"Authorization": _AUTHENTICATION_VALUE},
            transport=httpx.MockTransport(handler),
        ) as client:
            await client.request_json("GET", "resources")

    with pytest.raises(HttpStatusError) as captured:
        asyncio.run(scenario())

    assert captured.value.status_code == 401
    assert captured.value.service_name == "Test service"
    assert "401" in str(captured.value)
    assert response_marker not in str(captured.value)
    assert _AUTHENTICATION_VALUE not in str(captured.value)


def test_translates_malformed_json_without_leaking_response_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"credential":"' + _AUTHENTICATION_VALUE.encode())

    async def scenario() -> None:
        async with HttpTransport(
            base_url="https://service.example.test",
            service_name="Test service",
            auth_headers={"X-Test-Token": _AUTHENTICATION_VALUE},
            transport=httpx.MockTransport(handler),
        ) as client:
            await client.request_json("GET", "resources")

    with pytest.raises(MalformedJsonResponseError) as captured:
        asyncio.run(scenario())

    assert "malformed JSON" in str(captured.value)
    assert _AUTHENTICATION_VALUE not in str(captured.value)
    assert captured.value.__cause__ is None


@pytest.mark.parametrize(
    ("connect_timeout", "read_timeout"),
    [
        (0.0, 1.0),
        (61.0, 1.0),
        (1.0, 0.0),
        (1.0, 121.0),
        (float("nan"), 1.0),
        (1.0, float("inf")),
    ],
)
def test_rejects_unbounded_timeout_values(
    connect_timeout: float,
    read_timeout: float,
) -> None:
    with pytest.raises(HttpTransportConfigurationError):
        HttpTransport(
            base_url="https://service.example.test",
            service_name="Test service",
            connect_timeout_seconds=connect_timeout,
            read_timeout_seconds=read_timeout,
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
        )


def test_rejects_invalid_authentication_headers_without_echoing_values() -> None:
    invalid_value = f"{_AUTHENTICATION_VALUE}\ncontinuation"

    with pytest.raises(HttpTransportConfigurationError) as captured:
        HttpTransport(
            base_url="https://service.example.test",
            service_name="Test service",
            auth_headers={"Authorization": invalid_value},
        )

    assert _AUTHENTICATION_VALUE not in str(captured.value)
    assert invalid_value not in str(captured.value)


class _BlockingTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.closed = asyncio.Event()
        self._never_released = asyncio.Event()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        del request
        self.started.set()
        await self._never_released.wait()
        return httpx.Response(200, json={})

    async def aclose(self) -> None:
        self.closed.set()


def test_cancellation_propagates_after_transport_cleanup() -> None:
    async def scenario() -> None:
        backend = _BlockingTransport()
        client = HttpTransport(
            base_url="https://service.example.test",
            service_name="Test service",
            auth_headers={"X-Test-Token": _AUTHENTICATION_VALUE},
            transport=backend,
        )

        async def make_request() -> None:
            async with client:
                await client.request_json("GET", "resources")

        request_task = asyncio.create_task(make_request())
        await asyncio.wait_for(backend.started.wait(), timeout=1)
        request_task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await request_task
        assert backend.closed.is_set()

    asyncio.run(scenario())


def test_rejects_absolute_request_urls_before_authentication_can_be_forwarded() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={})

    async def scenario(paths: Sequence[str]) -> None:
        async with HttpTransport(
            base_url="https://service.example.test",
            service_name="Test service",
            auth_headers={"Authorization": _AUTHENTICATION_VALUE},
            transport=httpx.MockTransport(handler),
        ) as client:
            for path in paths:
                with pytest.raises(HttpTransportError) as captured:
                    await client.request_json("GET", path)
                assert _AUTHENTICATION_VALUE not in str(captured.value)

    asyncio.run(scenario(["https://other.example.test/resources", "../resources"]))
    assert calls == []
