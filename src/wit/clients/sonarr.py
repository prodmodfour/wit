"""Minimal read-only Sonarr health client."""

from typing import Annotated, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, RootModel, SecretStr, ValidationError

from wit.clients._base import (
    HttpServiceClient,
    invalid_health_response,
    normalise_transport_failure,
)
from wit.clients.health import ServiceHealthResult, ServiceHealthState, ServiceName, ServiceVersion
from wit.config import DEFAULT_CONNECT_TIMEOUT_SECONDS, DEFAULT_READ_TIMEOUT_SECONDS
from wit.transport import HttpTransport, HttpTransportError

SonarrHealthSeverity = Literal["notice", "warning", "error"]
HealthSource = Annotated[str, Field(min_length=1, max_length=128)]


class _SonarrResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


class _SonarrSystemStatus(_SonarrResponse):
    version: ServiceVersion


class _SonarrHealthIssue(_SonarrResponse):
    source: HealthSource
    type: SonarrHealthSeverity


class _SonarrHealthReport(RootModel[list[_SonarrHealthIssue]]):
    model_config = ConfigDict(strict=True)


class SonarrClient(HttpServiceClient):
    """Read Sonarr's authenticated system and health API endpoints."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: SecretStr,
        connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
        read_timeout_seconds: float = DEFAULT_READ_TIMEOUT_SECONDS,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            HttpTransport(
                base_url=base_url,
                service_name="Sonarr",
                connect_timeout_seconds=connect_timeout_seconds,
                read_timeout_seconds=read_timeout_seconds,
                auth_headers={"X-Api-Key": api_key},
                transport=http_transport,
            )
        )

    async def get_health(self) -> ServiceHealthResult:
        """Return Sonarr version and operational-health state without mutation."""
        version: str | None = None
        try:
            status_payload = await self._transport.request_json(
                "GET",
                "api/v3/system/status",
            )
            system_status = _SonarrSystemStatus.model_validate(status_payload)
            version = system_status.version

            health_payload = await self._transport.request_json("GET", "api/v3/health")
            health_report = _SonarrHealthReport.model_validate(health_payload)
        except HttpTransportError as error:
            return normalise_transport_failure(ServiceName.SONARR, error, version=version)
        except ValidationError:
            return invalid_health_response(ServiceName.SONARR, version=version)

        issue_count = len(health_report.root)
        if issue_count:
            suffix = "issue" if issue_count == 1 else "issues"
            return ServiceHealthResult(
                service=ServiceName.SONARR,
                state=ServiceHealthState.UNHEALTHY,
                version=version,
                summary=f"Sonarr reported {issue_count} health {suffix}",
            )

        return ServiceHealthResult(
            service=ServiceName.SONARR,
            state=ServiceHealthState.HEALTHY,
            version=version,
            summary="Sonarr is healthy",
        )
