"""Shared asynchronous HTTP transport with secret-safe failures."""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import Mapping, Sequence
from types import TracebackType
from typing import Literal, Protocol, cast
from urllib.parse import unquote

import httpx
from pydantic import SecretStr

from wit.config import (
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_READ_TIMEOUT_SECONDS,
    MAX_CONNECT_TIMEOUT_SECONDS,
    MAX_READ_TIMEOUT_SECONDS,
)
from wit.errors import WitError

type HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
type QueryScalar = str | int | float | bool | None
type QueryParams = Mapping[str, QueryScalar | Sequence[QueryScalar]]
type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type AuthenticationHeaderValue = str | SecretStr

_MIN_TIMEOUT_SECONDS = 0.1
_ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
_SAFE_SERVICE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9 ._-]{0,63}\Z")
_HTTP_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")


class HttpTransportError(WitError):
    """Base class for safe shared-transport failures."""


class HttpTransportConfigurationError(HttpTransportError):
    """The transport could not be configured safely."""


class HttpConnectionError(HttpTransportError):
    """A service connection or HTTP protocol failed."""


class HttpTimeoutError(HttpTransportError):
    """A service request exceeded a configured timeout."""


class HttpStatusError(HttpTransportError):
    """A service returned a non-success HTTP status."""

    def __init__(self, service_name: str, status_code: int) -> None:
        self.service_name = service_name
        self.status_code = status_code
        super().__init__(f"{service_name} returned HTTP status {status_code}")


class MalformedJsonResponseError(HttpTransportError):
    """A successful service response was not valid JSON."""


class HttpRequestEncodingError(HttpTransportError):
    """A request could not be encoded without exposing its values."""


class HttpTransportClosedError(HttpTransportError):
    """A request was attempted after transport cleanup."""


class HttpTransportCleanupError(HttpTransportError):
    """The underlying HTTP resources could not be closed."""


class JsonHttpTransport(Protocol):
    """Injectable interface consumed by typed service clients."""

    async def request_json(
        self,
        method: HttpMethod,
        path: str,
        *,
        params: QueryParams | None = None,
        json_body: JsonValue | None = None,
    ) -> JsonValue:
        """Send one request and return its decoded JSON value."""
        ...


class HttpTransport:
    """An owned ``httpx.AsyncClient`` configured for one service.

    Authentication headers are installed once for the service and are never
    retained in a public attribute or included in errors. Use this transport as
    an async context manager so connection pools are closed on success, failure,
    or task cancellation. An ``httpx`` transport can be injected for offline
    tests.
    """

    def __init__(
        self,
        *,
        base_url: str,
        service_name: str,
        connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
        read_timeout_seconds: float = DEFAULT_READ_TIMEOUT_SECONDS,
        auth_headers: Mapping[str, AuthenticationHeaderValue] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._service_name = _validate_service_name(service_name)
        normalised_base_url = _normalise_base_url(base_url)
        connect_timeout = _validate_timeout(
            connect_timeout_seconds,
            label="connect",
            maximum=MAX_CONNECT_TIMEOUT_SECONDS,
        )
        read_timeout = _validate_timeout(
            read_timeout_seconds,
            label="read",
            maximum=MAX_READ_TIMEOUT_SECONDS,
        )
        headers = _build_authentication_headers(auth_headers)
        timeout = httpx.Timeout(
            connect=connect_timeout,
            read=read_timeout,
            write=read_timeout,
            pool=connect_timeout,
        )

        try:
            self._client = httpx.AsyncClient(
                base_url=normalised_base_url,
                headers=headers,
                timeout=timeout,
                follow_redirects=False,
                transport=transport,
                trust_env=False,
            )
        except (httpx.InvalidURL, TypeError, ValueError, UnicodeError):
            raise HttpTransportConfigurationError(
                "HTTP transport configuration is invalid"
            ) from None

        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    def __repr__(self) -> str:
        state = "closed" if self._closed else "open"
        return f"HttpTransport(service_name={self._service_name!r}, state={state!r})"

    async def __aenter__(self) -> HttpTransport:
        if self._closed:
            raise HttpTransportClosedError(f"{self._service_name} HTTP transport is already closed")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        await self.aclose()

    async def aclose(self) -> None:
        """Close the client once, shielding cleanup from request cancellation."""
        close_task = self._close_task
        if close_task is None:
            self._closed = True
            close_task = asyncio.create_task(self._close_client())
            self._close_task = close_task

        try:
            await asyncio.shield(close_task)
        except asyncio.CancelledError:
            # Shielding lets cleanup continue. Await it before preserving the
            # caller's cancellation so no connection-pool task is abandoned.
            try:
                await close_task
            except (HttpTransportCleanupError, asyncio.CancelledError):
                pass
            raise

    async def request_json(
        self,
        method: HttpMethod,
        path: str,
        *,
        params: QueryParams | None = None,
        json_body: JsonValue | None = None,
    ) -> JsonValue:
        """Send a bounded request and decode a successful JSON response."""
        if self._closed:
            raise HttpTransportClosedError(f"{self._service_name} HTTP transport is closed")
        if method not in _ALLOWED_METHODS:
            raise HttpRequestEncodingError(
                f"{self._service_name} request uses an unsupported HTTP method"
            )
        request_path = _normalise_request_path(path, self._service_name)

        try:
            if json_body is None:
                response = await self._client.request(method, request_path, params=params)
            else:
                response = await self._client.request(
                    method,
                    request_path,
                    params=params,
                    json=json_body,
                )
        except httpx.TimeoutException:
            raise HttpTimeoutError(
                f"{self._service_name} request timed out within the configured limit"
            ) from None
        except httpx.RequestError:
            raise HttpConnectionError(
                f"{self._service_name} request could not be completed"
            ) from None
        except (TypeError, ValueError, UnicodeError, OverflowError):
            raise HttpRequestEncodingError(
                f"{self._service_name} request could not be encoded"
            ) from None

        if not 200 <= response.status_code < 300:
            raise HttpStatusError(self._service_name, response.status_code)

        try:
            decoded: object = response.json()
        except (ValueError, UnicodeError):
            raise MalformedJsonResponseError(
                f"{self._service_name} returned malformed JSON"
            ) from None
        return cast(JsonValue, decoded)

    async def _close_client(self) -> None:
        try:
            await self._client.aclose()
        except asyncio.CancelledError:
            raise
        except Exception:
            raise HttpTransportCleanupError(
                f"{self._service_name} HTTP transport could not be closed"
            ) from None


def _validate_service_name(service_name: str) -> str:
    if not isinstance(service_name, str) or _SAFE_SERVICE_NAME.fullmatch(service_name) is None:
        raise HttpTransportConfigurationError("HTTP service name is invalid")
    return service_name


def _validate_timeout(value: float, *, label: str, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HttpTransportConfigurationError(f"HTTP {label} timeout is invalid")
    numeric_value = float(value)
    if (
        not math.isfinite(numeric_value)
        or numeric_value < _MIN_TIMEOUT_SECONDS
        or numeric_value > maximum
    ):
        raise HttpTransportConfigurationError(
            f"HTTP {label} timeout must be between {_MIN_TIMEOUT_SECONDS} and {maximum} seconds"
        )
    return numeric_value


def _normalise_base_url(base_url: str) -> httpx.URL:
    if not isinstance(base_url, str):
        raise HttpTransportConfigurationError("HTTP base URL is invalid")
    try:
        url = httpx.URL(base_url)
    except (httpx.InvalidURL, TypeError, ValueError, UnicodeError):
        raise HttpTransportConfigurationError("HTTP base URL is invalid") from None

    if (
        url.scheme not in {"http", "https"}
        or not url.host
        or url.userinfo
        or url.query
        or url.fragment
    ):
        raise HttpTransportConfigurationError("HTTP base URL is invalid")

    raw_path = url.raw_path
    if not raw_path.endswith(b"/"):
        raw_path += b"/"
    return url.copy_with(raw_path=raw_path)


def _normalise_request_path(path: str, service_name: str) -> str:
    if not isinstance(path, str) or "?" in path or "#" in path or path.startswith("//"):
        raise HttpRequestEncodingError(f"{service_name} request path is invalid")
    try:
        parsed_path = httpx.URL(path)
    except (httpx.InvalidURL, TypeError, ValueError, UnicodeError):
        raise HttpRequestEncodingError(f"{service_name} request path is invalid") from None
    if parsed_path.scheme or parsed_path.host:
        raise HttpRequestEncodingError(f"{service_name} request path is invalid")

    relative_path = path.lstrip("/")
    if any(unquote(segment) in {".", ".."} for segment in relative_path.split("/")):
        raise HttpRequestEncodingError(f"{service_name} request path is invalid")
    return relative_path


def _build_authentication_headers(
    values: Mapping[str, AuthenticationHeaderValue] | None,
) -> httpx.Headers:
    if values is None:
        return httpx.Headers()

    revealed: dict[str, str] = {}
    for name, protected_value in values.items():
        if not isinstance(name, str) or _HTTP_HEADER_NAME.fullmatch(name) is None:
            raise HttpTransportConfigurationError("HTTP authentication headers are invalid")
        if isinstance(protected_value, SecretStr):
            value = protected_value.get_secret_value()
        elif isinstance(protected_value, str):
            value = protected_value
        else:
            raise HttpTransportConfigurationError("HTTP authentication headers are invalid")

        if not value or any(ord(character) < 32 or ord(character) > 126 for character in value):
            raise HttpTransportConfigurationError("HTTP authentication headers are invalid")
        revealed[name] = value

    try:
        return httpx.Headers(revealed)
    except (TypeError, ValueError, UnicodeError):
        raise HttpTransportConfigurationError("HTTP authentication headers are invalid") from None
