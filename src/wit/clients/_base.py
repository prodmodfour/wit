"""Shared lifecycle and failure normalisation for service clients."""

from types import TracebackType
from typing import Self

from wit.clients.health import ServiceHealthResult, ServiceHealthState, ServiceName
from wit.transport import (
    HttpConnectionError,
    HttpStatusError,
    HttpTimeoutError,
    HttpTransport,
    HttpTransportError,
)

_UNAVAILABLE_STATUS_CODES = frozenset({502, 503, 504})
_UNAUTHORISED_STATUS_CODES = frozenset({401, 403})


class HttpServiceClient:
    """Own one shared transport and close it safely with the client."""

    def __init__(self, transport: HttpTransport) -> None:
        self._transport = transport

    async def __aenter__(self) -> Self:
        await self._transport.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._transport.__aexit__(exc_type, exc_value, traceback)

    async def aclose(self) -> None:
        """Close the client's underlying connection pool."""
        await self._transport.aclose()


def normalise_transport_failure(
    service: ServiceName,
    error: HttpTransportError,
    *,
    version: str | None = None,
) -> ServiceHealthResult:
    """Convert safe transport failures into one cross-service state model."""
    label = service.value.capitalize()

    if isinstance(error, HttpStatusError) and error.status_code in _UNAUTHORISED_STATUS_CODES:
        return ServiceHealthResult(
            service=service,
            state=ServiceHealthState.UNAUTHORISED,
            version=version,
            summary=f"{label} health check was not authorised",
        )

    if isinstance(error, (HttpConnectionError, HttpTimeoutError)) or (
        isinstance(error, HttpStatusError) and error.status_code in _UNAVAILABLE_STATUS_CODES
    ):
        return ServiceHealthResult(
            service=service,
            state=ServiceHealthState.UNAVAILABLE,
            version=version,
            summary=f"{label} is unavailable",
        )

    if isinstance(error, HttpStatusError):
        summary = f"{label} health check returned HTTP {error.status_code}"
    else:
        summary = f"{label} returned an invalid health response"
    return ServiceHealthResult(
        service=service,
        state=ServiceHealthState.UNHEALTHY,
        version=version,
        summary=summary,
    )


def invalid_health_response(
    service: ServiceName,
    *,
    version: str | None = None,
) -> ServiceHealthResult:
    """Return a safe result for a response that violates its API contract."""
    label = service.value.capitalize()
    return ServiceHealthResult(
        service=service,
        state=ServiceHealthState.UNHEALTHY,
        version=version,
        summary=f"{label} returned an invalid health response",
    )
