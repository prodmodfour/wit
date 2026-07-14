"""Minimal read-only Seerr health client."""

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from wit.clients._base import (
    HttpServiceClient,
    invalid_health_response,
    normalise_transport_failure,
)
from wit.clients.health import ServiceHealthResult, ServiceHealthState, ServiceName, ServiceVersion
from wit.config import DEFAULT_CONNECT_TIMEOUT_SECONDS, DEFAULT_READ_TIMEOUT_SECONDS
from wit.transport import HttpTransport, HttpTransportError


class _SeerrStatus(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    version: ServiceVersion
    restart_required: bool = Field(alias="restartRequired")


class SeerrClient(HttpServiceClient):
    """Read Seerr's public status endpoint without service credentials."""

    def __init__(
        self,
        *,
        base_url: str,
        connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
        read_timeout_seconds: float = DEFAULT_READ_TIMEOUT_SECONDS,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            HttpTransport(
                base_url=base_url,
                service_name="Seerr",
                connect_timeout_seconds=connect_timeout_seconds,
                read_timeout_seconds=read_timeout_seconds,
                transport=http_transport,
            )
        )

    async def get_health(self) -> ServiceHealthResult:
        """Return Seerr version and restart state without mutation."""
        try:
            payload = await self._transport.request_json("GET", "api/v1/status")
            status = _SeerrStatus.model_validate(payload)
        except HttpTransportError as error:
            return normalise_transport_failure(ServiceName.SEERR, error)
        except ValidationError:
            return invalid_health_response(ServiceName.SEERR)

        if status.restart_required:
            return ServiceHealthResult(
                service=ServiceName.SEERR,
                state=ServiceHealthState.UNHEALTHY,
                version=status.version,
                summary="Seerr reports that a restart is required",
            )

        return ServiceHealthResult(
            service=ServiceName.SEERR,
            state=ServiceHealthState.HEALTHY,
            version=status.version,
            summary="Seerr is healthy",
        )
