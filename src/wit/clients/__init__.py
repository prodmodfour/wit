"""Typed clients for Wit's external services."""

from wit.clients.health import ServiceHealthResult, ServiceHealthState, ServiceName
from wit.clients.jellyfin import JellyfinClient
from wit.clients.seerr import SeerrClient
from wit.clients.sonarr import SonarrClient
from wit.clients.tvmaze import (
    InvalidTvmazeRequestError,
    InvalidTvmazeResponseError,
    TvmazeClient,
    TvmazeClientError,
    TvmazeEpisode,
    TvmazeEpisodeCollection,
    TvmazeEpisodeType,
    TvmazeShow,
    TvmazeShowSearchResult,
)

__all__ = [
    "InvalidTvmazeRequestError",
    "InvalidTvmazeResponseError",
    "JellyfinClient",
    "SeerrClient",
    "ServiceHealthResult",
    "ServiceHealthState",
    "ServiceName",
    "SonarrClient",
    "TvmazeClient",
    "TvmazeClientError",
    "TvmazeEpisode",
    "TvmazeEpisodeCollection",
    "TvmazeEpisodeType",
    "TvmazeShow",
    "TvmazeShowSearchResult",
]
