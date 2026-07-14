"""Normalised, secret-free service health models."""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

ServiceVersion = Annotated[
    str,
    Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._+~-]*$",
    ),
]
HealthSummary = Annotated[str, Field(min_length=1, max_length=256)]


class ServiceName(StrEnum):
    """Services with a health contract implemented by Wit."""

    SONARR = "sonarr"
    JELLYFIN = "jellyfin"
    SEERR = "seerr"


class ServiceHealthState(StrEnum):
    """Normalised outcomes shared by all service health clients."""

    UNAVAILABLE = "unavailable"
    UNAUTHORISED = "unauthorised"
    UNHEALTHY = "unhealthy"
    HEALTHY = "healthy"


class ServiceHealthResult(BaseModel):
    """A bounded health result that never retains raw service responses."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    service: ServiceName
    state: ServiceHealthState
    version: ServiceVersion | None = None
    summary: HealthSummary
