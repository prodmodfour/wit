"""Typed clients for Wit's external services."""

from wit.clients.health import ServiceHealthResult, ServiceHealthState, ServiceName
from wit.clients.jellyfin import JellyfinClient
from wit.clients.seerr import SeerrClient
from wit.clients.sonarr import SonarrClient

__all__ = [
    "JellyfinClient",
    "SeerrClient",
    "ServiceHealthResult",
    "ServiceHealthState",
    "ServiceName",
    "SonarrClient",
]
