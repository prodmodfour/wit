"""Minimal read-only Jellyfin health client."""

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from wit.clients._base import (
    HttpServiceClient,
    invalid_health_response,
    normalise_transport_failure,
)
from wit.clients.health import ServiceHealthResult, ServiceHealthState, ServiceName, ServiceVersion
from wit.config import DEFAULT_CONNECT_TIMEOUT_SECONDS, DEFAULT_READ_TIMEOUT_SECONDS
from wit.transport import HttpTransport, HttpTransportError


class _JellyfinSystemInfo(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    version: ServiceVersion = Field(alias="Version")
    startup_wizard_completed: bool = Field(alias="StartupWizardCompleted")
    has_pending_restart: bool = Field(default=False, alias="HasPendingRestart")
    is_shutting_down: bool = Field(default=False, alias="IsShuttingDown")


class JellyfinClient(HttpServiceClient):
    """Read Jellyfin's authenticated system-information endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: SecretStr,
        connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
        read_timeout_seconds: float = DEFAULT_READ_TIMEOUT_SECONDS,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        authorization = SecretStr(f'MediaBrowser Token="{api_key.get_secret_value()}"')
        super().__init__(
            HttpTransport(
                base_url=base_url,
                service_name="Jellyfin",
                connect_timeout_seconds=connect_timeout_seconds,
                read_timeout_seconds=read_timeout_seconds,
                auth_headers={"Authorization": authorization},
                transport=http_transport,
            )
        )

    async def get_health(self) -> ServiceHealthResult:
        """Return Jellyfin version and server-readiness state without mutation."""
        try:
            payload = await self._transport.request_json("GET", "System/Info")
            system_info = _JellyfinSystemInfo.model_validate(payload)
        except HttpTransportError as error:
            return normalise_transport_failure(ServiceName.JELLYFIN, error)
        except ValidationError:
            return invalid_health_response(ServiceName.JELLYFIN)

        version = system_info.version
        if system_info.is_shutting_down:
            return ServiceHealthResult(
                service=ServiceName.JELLYFIN,
                state=ServiceHealthState.UNHEALTHY,
                version=version,
                summary="Jellyfin is shutting down",
            )
        if not system_info.startup_wizard_completed:
            return ServiceHealthResult(
                service=ServiceName.JELLYFIN,
                state=ServiceHealthState.UNHEALTHY,
                version=version,
                summary="Jellyfin initial setup is incomplete",
            )
        if system_info.has_pending_restart:
            return ServiceHealthResult(
                service=ServiceName.JELLYFIN,
                state=ServiceHealthState.UNHEALTHY,
                version=version,
                summary="Jellyfin reports a pending restart",
            )

        return ServiceHealthResult(
            service=ServiceName.JELLYFIN,
            state=ServiceHealthState.HEALTHY,
            version=version,
            summary="Jellyfin is healthy",
        )
