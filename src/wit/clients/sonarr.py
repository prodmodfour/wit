"""Typed Sonarr health, library, episode, lookup, and bounded mutation operations."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from enum import StrEnum
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
from wit.errors import WitError
from wit.transport import HttpStatusError, HttpTransport, HttpTransportError, JsonValue

_MAX_IDENTIFIER = 2_147_483_647
_MAX_PATH_LENGTH = 4096
_MAX_TITLE_LENGTH = 512
_MAX_NAME_LENGTH = 256
_POSSIBLE_DUPLICATE_STATUS_CODES = frozenset({400, 409})

SonarrIdentifier = Annotated[int, Field(gt=0, le=_MAX_IDENTIFIER)]
SonarrSeasonNumber = Annotated[int, Field(ge=0, le=_MAX_IDENTIFIER)]
SonarrEpisodeNumber = Annotated[int, Field(gt=0, le=_MAX_IDENTIFIER)]
SonarrYear = Annotated[int, Field(ge=0, le=9999)]
SonarrPath = Annotated[str, Field(min_length=1, max_length=_MAX_PATH_LENGTH)]
SonarrTimestamp = Annotated[str, Field(max_length=64)]
SonarrTitle = Annotated[str, Field(min_length=1, max_length=_MAX_TITLE_LENGTH)]
SonarrName = Annotated[str, Field(min_length=1, max_length=_MAX_NAME_LENGTH)]
SonarrHealthSeverity = Literal["notice", "warning", "error"]
HealthSource = Annotated[str, Field(min_length=1, max_length=128)]


class SonarrClientError(WitError):
    """Base class for safe Sonarr client failures."""


class InvalidSonarrRequestError(SonarrClientError):
    """A Sonarr request was invalid before any network operation."""


class InvalidSonarrResponseError(SonarrClientError):
    """Sonarr returned JSON that did not satisfy the expected API contract."""


class InvalidSonarrDefaultsError(SonarrClientError):
    """Configured Sonarr library defaults are absent or unusable."""


class SonarrSeriesNotFoundError(SonarrClientError):
    """Sonarr could not resolve a requested stable series identity."""


class SonarrEpisodeMappingError(SonarrClientError):
    """A planned episode coordinate did not map to exactly one Sonarr episode."""


class SonarrSeriesType(StrEnum):
    """Numbering modes supported by Sonarr series records."""

    STANDARD = "standard"
    DAILY = "daily"
    ANIME = "anime"


class SonarrEpisodeAirStatus(StrEnum):
    """Whether Sonarr's known UTC air time has passed."""

    AIRED = "aired"
    UNAIRED = "unaired"
    UNKNOWN = "unknown"


class _SonarrModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SonarrRootFolder(_SonarrModel):
    """A configured Sonarr root folder needed to construct an add request."""

    root_folder_id: SonarrIdentifier
    path: SonarrPath
    accessible: bool


class SonarrQualityProfile(_SonarrModel):
    """A configured Sonarr quality-profile selection."""

    quality_profile_id: SonarrIdentifier
    name: SonarrName


class SonarrSeries(_SonarrModel):
    """The stable identity of a series already present in Sonarr."""

    sonarr_id: SonarrIdentifier
    tvdb_id: SonarrIdentifier
    title: SonarrTitle
    year: SonarrYear


class SonarrSeriesLookupResult(_SonarrModel):
    """The fields Wit needs from Sonarr before adding a TVDB series."""

    tvdb_id: SonarrIdentifier
    title: SonarrTitle
    year: SonarrYear
    series_type: SonarrSeriesType
    season_numbers: tuple[SonarrSeasonNumber, ...]


class SonarrLibraryDefaults(_SonarrModel):
    """Validated root-folder and quality-profile choices for a future add."""

    root_folder: SonarrRootFolder
    quality_profile: SonarrQualityProfile


class SonarrSeriesAddResult(_SonarrModel):
    """A newly created or idempotently reused Sonarr series."""

    series: SonarrSeries
    created: bool


class SonarrEpisode(_SonarrModel):
    """The bounded episode state needed for mapping, apply, and status."""

    episode_id: SonarrIdentifier
    season_number: SonarrSeasonNumber
    episode_number: SonarrEpisodeNumber
    title: SonarrTitle
    air_status: SonarrEpisodeAirStatus
    monitored: bool
    has_file: bool


class SonarrEpisodeMonitoringResult(_SonarrModel):
    """Episode IDs Sonarr confirmed as monitored by one bounded mutation."""

    episode_ids: tuple[SonarrIdentifier, ...]
    monitored: Literal[True]


class _SonarrResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


class _SonarrSystemStatus(_SonarrResponse):
    version: ServiceVersion


class _SonarrHealthIssue(_SonarrResponse):
    source: HealthSource
    type: SonarrHealthSeverity


class _SonarrHealthReport(RootModel[list[_SonarrHealthIssue]]):
    model_config = ConfigDict(strict=True)


class _SonarrRootFolder(_SonarrResponse):
    id: SonarrIdentifier
    path: SonarrPath
    accessible: bool


class _SonarrRootFolderResponse(RootModel[list[_SonarrRootFolder]]):
    model_config = ConfigDict(strict=True)


class _SonarrQualityProfile(_SonarrResponse):
    id: SonarrIdentifier
    name: SonarrName


class _SonarrQualityProfileResponse(RootModel[list[_SonarrQualityProfile]]):
    model_config = ConfigDict(strict=True)


class _SonarrExistingSeries(_SonarrResponse):
    id: SonarrIdentifier
    tvdb_id: SonarrIdentifier = Field(alias="tvdbId")
    title: SonarrTitle
    year: SonarrYear


class _SonarrExistingSeriesResponse(RootModel[list[_SonarrExistingSeries]]):
    model_config = ConfigDict(strict=True)


class _SonarrSeason(_SonarrResponse):
    season_number: SonarrSeasonNumber = Field(alias="seasonNumber")


class _SonarrSeriesLookup(_SonarrResponse):
    tvdb_id: SonarrIdentifier = Field(alias="tvdbId")
    title: SonarrTitle
    year: SonarrYear
    series_type: Literal["standard", "daily", "anime"] = Field(alias="seriesType")
    seasons: list[_SonarrSeason] | None = None


class _SonarrSeriesLookupResponse(RootModel[list[_SonarrSeriesLookup]]):
    model_config = ConfigDict(strict=True)


class _SonarrAddedSeason(_SonarrResponse):
    season_number: SonarrSeasonNumber = Field(alias="seasonNumber")
    monitored: bool


class _SonarrAddedSeries(_SonarrExistingSeries):
    monitored: bool
    monitor_new_items: Literal["none"] = Field(alias="monitorNewItems")
    seasons: list[_SonarrAddedSeason]


class _SonarrEpisode(_SonarrResponse):
    id: SonarrIdentifier
    series_id: SonarrIdentifier = Field(alias="seriesId")
    season_number: SonarrSeasonNumber = Field(alias="seasonNumber")
    episode_number: SonarrEpisodeNumber = Field(alias="episodeNumber")
    title: SonarrTitle
    air_date_utc: SonarrTimestamp | None = Field(default=None, alias="airDateUtc")
    monitored: bool
    has_file: bool = Field(alias="hasFile")


class _SonarrEpisodeResponse(RootModel[list[_SonarrEpisode]]):
    model_config = ConfigDict(strict=True)


class _SonarrMonitoredEpisode(_SonarrResponse):
    id: SonarrIdentifier
    monitored: bool


class _SonarrMonitoredEpisodeResponse(RootModel[list[_SonarrMonitoredEpisode]]):
    model_config = ConfigDict(strict=True)


class SonarrClient(HttpServiceClient):
    """Access the bounded Sonarr API operations used by Wit."""

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

    async def list_root_folders(self) -> tuple[SonarrRootFolder, ...]:
        """List configured root folders without retaining unrelated API fields."""
        payload = await self._transport.request_json("GET", "api/v3/rootfolder")
        try:
            response = _SonarrRootFolderResponse.model_validate(payload)
            root_folders = tuple(
                SonarrRootFolder(
                    root_folder_id=item.id,
                    path=_normalise_text(item.path),
                    accessible=item.accessible,
                )
                for item in response.root
            )
            if len({item.root_folder_id for item in root_folders}) != len(root_folders):
                raise ValueError("duplicate root-folder ID")
            return root_folders
        except (ValidationError, ValueError):
            raise InvalidSonarrResponseError(
                "Sonarr returned an invalid root-folder response"
            ) from None

    async def list_quality_profiles(self) -> tuple[SonarrQualityProfile, ...]:
        """List configured quality profiles without retaining profile internals."""
        payload = await self._transport.request_json("GET", "api/v3/qualityprofile")
        try:
            response = _SonarrQualityProfileResponse.model_validate(payload)
            quality_profiles = tuple(
                SonarrQualityProfile(
                    quality_profile_id=item.id,
                    name=_normalise_text(item.name),
                )
                for item in response.root
            )
            if len({item.quality_profile_id for item in quality_profiles}) != len(quality_profiles):
                raise ValueError("duplicate quality-profile ID")
            return quality_profiles
        except (ValidationError, ValueError):
            raise InvalidSonarrResponseError(
                "Sonarr returned an invalid quality-profile response"
            ) from None

    async def validate_library_defaults(
        self,
        *,
        root_folder_id: int,
        quality_profile_id: int,
    ) -> SonarrLibraryDefaults:
        """Resolve configured IDs and reject missing or inaccessible selections."""
        validated_root_folder_id = _validate_identifier(root_folder_id, "root-folder")
        validated_quality_profile_id = _validate_identifier(
            quality_profile_id,
            "quality-profile",
        )

        root_folders = await self.list_root_folders()
        quality_profiles = await self.list_quality_profiles()
        root_folder = next(
            (item for item in root_folders if item.root_folder_id == validated_root_folder_id),
            None,
        )
        quality_profile = next(
            (
                item
                for item in quality_profiles
                if item.quality_profile_id == validated_quality_profile_id
            ),
            None,
        )

        issues: list[str] = []
        if root_folder is None:
            issues.append(f"root-folder ID {validated_root_folder_id} was not found")
        elif not root_folder.accessible:
            issues.append(f"root-folder ID {validated_root_folder_id} is not accessible")
        if quality_profile is None:
            issues.append(f"quality-profile ID {validated_quality_profile_id} was not found")
        if issues:
            raise InvalidSonarrDefaultsError(
                "Sonarr library defaults are invalid: " + "; ".join(issues)
            )

        assert root_folder is not None
        assert quality_profile is not None
        return SonarrLibraryDefaults(
            root_folder=root_folder,
            quality_profile=quality_profile,
        )

    async def find_series_by_tvdb_id(self, tvdb_id: int) -> SonarrSeries | None:
        """Find one authoritative existing-series record by stable TVDB ID."""
        validated_tvdb_id = _validate_identifier(tvdb_id, "TVDB")
        payload = await self._transport.request_json(
            "GET",
            "api/v3/series",
            params={"tvdbId": validated_tvdb_id},
        )
        try:
            response = _SonarrExistingSeriesResponse.model_validate(payload)
            if not response.root:
                return None
            if len(response.root) != 1 or response.root[0].tvdb_id != validated_tvdb_id:
                raise ValueError("existing-series response was inconsistent")
            return _to_sonarr_series(response.root[0])
        except (ValidationError, ValueError):
            raise InvalidSonarrResponseError(
                "Sonarr returned an invalid existing-series response"
            ) from None

    async def lookup_series_by_tvdb_id(
        self,
        tvdb_id: int,
    ) -> SonarrSeriesLookupResult | None:
        """Look up one not-yet-added series through Sonarr's TVDB search syntax."""
        validated_tvdb_id = _validate_identifier(tvdb_id, "TVDB")
        payload = await self._transport.request_json(
            "GET",
            "api/v3/series/lookup",
            params={"term": f"tvdb:{validated_tvdb_id}"},
        )
        try:
            response = _SonarrSeriesLookupResponse.model_validate(payload)
            if not response.root:
                return None
            if len(response.root) != 1 or response.root[0].tvdb_id != validated_tvdb_id:
                raise ValueError("series-lookup response was inconsistent")

            item = response.root[0]
            season_numbers = tuple(sorted(season.season_number for season in (item.seasons or [])))
            if len(set(season_numbers)) != len(season_numbers):
                raise ValueError("series-lookup response contained duplicate seasons")
            return SonarrSeriesLookupResult(
                tvdb_id=item.tvdb_id,
                title=_normalise_text(item.title),
                year=item.year,
                series_type=SonarrSeriesType(item.series_type),
                season_numbers=season_numbers,
            )
        except (ValidationError, ValueError):
            raise InvalidSonarrResponseError(
                "Sonarr returned an invalid series-lookup response"
            ) from None

    async def list_episodes(
        self,
        series_id: int,
        *,
        as_of: datetime | None = None,
    ) -> tuple[SonarrEpisode, ...]:
        """List every Sonarr episode for one series without changing its state."""
        validated_series_id = _validate_identifier(series_id, "series")
        reference_time = _validate_reference_time(as_of)
        payload = await self._transport.request_json(
            "GET",
            "api/v3/episode",
            params={"seriesId": validated_series_id},
        )

        try:
            response = _SonarrEpisodeResponse.model_validate(payload)
            if any(item.series_id != validated_series_id for item in response.root):
                raise ValueError("episode response contained a different series")
            if len({item.id for item in response.root}) != len(response.root):
                raise ValueError("episode response contained duplicate IDs")
            return tuple(_to_sonarr_episode(item, reference_time) for item in response.root)
        except (ValidationError, ValueError):
            raise InvalidSonarrResponseError(
                "Sonarr returned an invalid episode-list response"
            ) from None

    async def monitor_episodes(
        self,
        episode_ids: Iterable[int],
    ) -> SonarrEpisodeMonitoringResult:
        """Mark exactly the supplied episode IDs as monitored and verify the result."""
        validated_episode_ids = _validate_episode_id_list(episode_ids)
        payload = await self._transport.request_json(
            "PUT",
            "api/v3/episode/monitor",
            json_body={
                "episodeIds": list(validated_episode_ids),
                "monitored": True,
            },
        )

        try:
            response = _SonarrMonitoredEpisodeResponse.model_validate(payload)
            response_ids = tuple(item.id for item in response.root)
            if (
                len(response_ids) != len(validated_episode_ids)
                or len(set(response_ids)) != len(response_ids)
                or set(response_ids) != set(validated_episode_ids)
                or any(not item.monitored for item in response.root)
            ):
                raise ValueError("episode-monitor response was inconsistent")
        except (ValidationError, ValueError):
            raise InvalidSonarrResponseError(
                "Sonarr returned an invalid episode-monitor response"
            ) from None

        return SonarrEpisodeMonitoringResult(
            episode_ids=validated_episode_ids,
            monitored=True,
        )

    async def add_series_unmonitored(
        self,
        *,
        tvdb_id: int | None,
        root_folder_id: int,
        quality_profile_id: int,
    ) -> SonarrSeriesAddResult:
        """Find or add one TVDB series without monitoring or automatic search."""
        validated_tvdb_id = _validate_identifier(tvdb_id, "TVDB")
        validated_root_folder_id = _validate_identifier(root_folder_id, "root-folder")
        validated_quality_profile_id = _validate_identifier(
            quality_profile_id,
            "quality-profile",
        )

        existing = await self.find_series_by_tvdb_id(validated_tvdb_id)
        if existing is not None:
            return SonarrSeriesAddResult(series=existing, created=False)

        resolved = await self.lookup_series_by_tvdb_id(validated_tvdb_id)
        if resolved is None:
            raise SonarrSeriesNotFoundError("Sonarr could not resolve the requested TVDB series")
        defaults = await self.validate_library_defaults(
            root_folder_id=validated_root_folder_id,
            quality_profile_id=validated_quality_profile_id,
        )

        try:
            payload = await self._transport.request_json(
                "POST",
                "api/v3/series",
                json_body=_build_unmonitored_series_payload(resolved, defaults),
            )
        except HttpStatusError as error:
            if error.status_code not in _POSSIBLE_DUPLICATE_STATUS_CODES:
                raise
            existing = await self.find_series_by_tvdb_id(validated_tvdb_id)
            if existing is None:
                raise
            return SonarrSeriesAddResult(series=existing, created=False)

        try:
            response = _SonarrAddedSeries.model_validate(payload)
            if response.tvdb_id != validated_tvdb_id:
                raise ValueError("series-add response identity was inconsistent")
            if response.monitored or response.monitor_new_items != "none":
                raise ValueError("added series was unexpectedly monitored")
            season_numbers = [season.season_number for season in response.seasons]
            if len(set(season_numbers)) != len(season_numbers) or any(
                season.monitored for season in response.seasons
            ):
                raise ValueError("added seasons were unexpectedly monitored or duplicated")
            series = _to_sonarr_series(response)
        except (ValidationError, ValueError):
            raise InvalidSonarrResponseError(
                "Sonarr returned an invalid series-add response"
            ) from None

        return SonarrSeriesAddResult(series=series, created=True)


def map_episode_coordinate(
    episodes: Iterable[SonarrEpisode],
    coordinate: tuple[int, int],
) -> int:
    """Map one ``(season, episode)`` coordinate to exactly one Sonarr episode ID."""
    season_number, episode_number = _validate_episode_coordinate(coordinate)
    matches = [
        episode.episode_id
        for episode in episodes
        if episode.season_number == season_number and episode.episode_number == episode_number
    ]
    label = f"S{season_number:02d}E{episode_number:02d}"
    if not matches:
        raise SonarrEpisodeMappingError(f"Sonarr episode coordinate {label} was not found")
    if len(matches) != 1:
        raise SonarrEpisodeMappingError(f"Sonarr episode coordinate {label} is ambiguous")
    return matches[0]


def _build_unmonitored_series_payload(
    series: SonarrSeriesLookupResult,
    defaults: SonarrLibraryDefaults,
) -> dict[str, JsonValue]:
    seasons: list[JsonValue] = [
        {"seasonNumber": season_number, "monitored": False}
        for season_number in series.season_numbers
    ]
    return {
        "tvdbId": series.tvdb_id,
        "title": series.title,
        "year": series.year,
        "seriesType": series.series_type.value,
        "rootFolderPath": defaults.root_folder.path,
        "qualityProfileId": defaults.quality_profile.quality_profile_id,
        "seasonFolder": True,
        "monitored": False,
        "monitorNewItems": "none",
        "seasons": seasons,
        "tags": [],
        "addOptions": {
            "monitor": "none",
            "searchForMissingEpisodes": False,
            "searchForCutoffUnmetEpisodes": False,
        },
    }


def _to_sonarr_series(item: _SonarrExistingSeries) -> SonarrSeries:
    return SonarrSeries(
        sonarr_id=item.id,
        tvdb_id=item.tvdb_id,
        title=_normalise_text(item.title),
        year=item.year,
    )


def _to_sonarr_episode(item: _SonarrEpisode, reference_time: datetime) -> SonarrEpisode:
    air_date_utc = _parse_optional_timestamp(item.air_date_utc)
    if air_date_utc is None:
        air_status = SonarrEpisodeAirStatus.UNKNOWN
    elif air_date_utc <= reference_time:
        air_status = SonarrEpisodeAirStatus.AIRED
    else:
        air_status = SonarrEpisodeAirStatus.UNAIRED

    return SonarrEpisode(
        episode_id=item.id,
        season_number=item.season_number,
        episode_number=item.episode_number,
        title=_normalise_text(item.title),
        air_status=air_status,
        monitored=item.monitored,
        has_file=item.has_file,
    )


def _validate_identifier(value: object, label: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or value > _MAX_IDENTIFIER
    ):
        raise InvalidSonarrRequestError(f"Sonarr {label} ID must be a positive integer")
    return value


def _validate_episode_id_list(episode_ids: Iterable[int]) -> tuple[int, ...]:
    try:
        values = tuple(episode_ids)
    except TypeError:
        raise InvalidSonarrRequestError("Sonarr episode ID list is invalid") from None

    if not values:
        raise InvalidSonarrRequestError("Sonarr episode ID list must not be empty")
    validated = tuple(_validate_identifier(value, "episode") for value in values)
    if len(set(validated)) != len(validated):
        raise InvalidSonarrRequestError("Sonarr episode ID list must contain unique IDs")
    return validated


def _validate_episode_coordinate(coordinate: object) -> tuple[int, int]:
    if not isinstance(coordinate, tuple) or len(coordinate) != 2:
        raise InvalidSonarrRequestError(
            "Sonarr episode coordinate must be a (season, episode) integer pair"
        )
    season_number, episode_number = coordinate
    if (
        isinstance(season_number, bool)
        or not isinstance(season_number, int)
        or season_number < 0
        or season_number > _MAX_IDENTIFIER
        or isinstance(episode_number, bool)
        or not isinstance(episode_number, int)
        or episode_number <= 0
        or episode_number > _MAX_IDENTIFIER
    ):
        raise InvalidSonarrRequestError(
            "Sonarr episode coordinate must contain a non-negative season and positive episode"
        )
    return season_number, episode_number


def _validate_reference_time(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise InvalidSonarrRequestError("Sonarr episode as-of time must be timezone-aware")
    try:
        if value.utcoffset() is None:
            raise ValueError("missing UTC offset")
        return value.astimezone(UTC)
    except (OverflowError, ValueError):
        raise InvalidSonarrRequestError(
            "Sonarr episode as-of time must be timezone-aware"
        ) from None


def _parse_optional_timestamp(value: str | None) -> datetime | None:
    if value is None or value == "":
        return None
    if value != value.strip():
        raise ValueError("timestamp contains surrounding whitespace")
    normalised = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalised)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp has no timezone")
    return parsed.astimezone(UTC)


def _normalise_text(value: str) -> str:
    normalised = value.strip()
    if not normalised or any(
        ord(character) < 32 or ord(character) == 127 for character in normalised
    ):
        raise ValueError("Sonarr text field is invalid")
    return normalised
