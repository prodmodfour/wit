"""Typed Sonarr health, library-default, and series-lookup operations."""

from __future__ import annotations

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
from wit.transport import HttpTransport, HttpTransportError

_MAX_IDENTIFIER = 2_147_483_647
_MAX_PATH_LENGTH = 4096
_MAX_TITLE_LENGTH = 512
_MAX_NAME_LENGTH = 256

SonarrIdentifier = Annotated[int, Field(gt=0, le=_MAX_IDENTIFIER)]
SonarrSeasonNumber = Annotated[int, Field(ge=0)]
SonarrYear = Annotated[int, Field(ge=0, le=9999)]
SonarrPath = Annotated[str, Field(min_length=1, max_length=_MAX_PATH_LENGTH)]
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


class SonarrSeriesType(StrEnum):
    """Numbering modes supported by Sonarr series records."""

    STANDARD = "standard"
    DAILY = "daily"
    ANIME = "anime"


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
            item = response.root[0]
            return SonarrSeries(
                sonarr_id=item.id,
                tvdb_id=item.tvdb_id,
                title=_normalise_text(item.title),
                year=item.year,
            )
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


def _validate_identifier(value: int, label: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or value > _MAX_IDENTIFIER
    ):
        raise InvalidSonarrRequestError(f"Sonarr {label} ID must be a positive integer")
    return value


def _normalise_text(value: str) -> str:
    normalised = value.strip()
    if not normalised or any(
        ord(character) < 32 or ord(character) == 127 for character in normalised
    ):
        raise ValueError("Sonarr text field is invalid")
    return normalised
