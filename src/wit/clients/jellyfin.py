"""Typed read-only Jellyfin health and library availability operations."""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

from wit.clients._base import (
    HttpServiceClient,
    invalid_health_response,
    normalise_transport_failure,
)
from wit.clients.health import ServiceHealthResult, ServiceHealthState, ServiceName, ServiceVersion
from wit.config import DEFAULT_CONNECT_TIMEOUT_SECONDS, DEFAULT_READ_TIMEOUT_SECONDS
from wit.errors import WitError
from wit.transport import (
    HttpConnectionError,
    HttpStatusError,
    HttpTimeoutError,
    HttpTransport,
    HttpTransportError,
)

_MAX_IDENTIFIER = 2_147_483_647
_MAX_TITLE_LENGTH = 512
_ITEM_PAGE_SIZE = 200
_MAX_ITEM_PAGES = 25
_MAX_LIBRARY_ITEMS = _ITEM_PAGE_SIZE * _MAX_ITEM_PAGES
_MAX_PROVIDER_IDS = 32
_MAX_PROVIDER_NAME_LENGTH = 64
_MAX_PROVIDER_VALUE_LENGTH = 256
_MAX_MULTI_EPISODE_SPAN = 100
_UNAVAILABLE_STATUS_CODES = frozenset({502, 503, 504})
_TVDB_IDENTIFIER = re.compile(r"[0-9]+\Z")

JellyfinTitle = Annotated[str, Field(min_length=1, max_length=_MAX_TITLE_LENGTH)]
JellyfinYear = Annotated[int, Field(ge=1, le=9999)]
JellyfinSeasonNumber = Annotated[int, Field(ge=0, le=_MAX_IDENTIFIER)]
JellyfinEpisodeNumber = Annotated[int, Field(gt=0, le=_MAX_IDENTIFIER)]
type JellyfinEpisodeCoordinate = tuple[int, int]


class JellyfinClientError(WitError):
    """Base class for safe Jellyfin library-client failures."""


class InvalidJellyfinRequestError(JellyfinClientError):
    """A Jellyfin library request was invalid before any network operation."""


class InvalidJellyfinResponseError(JellyfinClientError):
    """Jellyfin returned JSON that did not satisfy the expected library contract."""


class AmbiguousJellyfinSeriesError(JellyfinClientError):
    """More than one Jellyfin series matched the requested identity."""


class JellyfinLibraryLimitError(JellyfinClientError):
    """A library query exceeded Wit's fixed item and request bound."""


class JellyfinLibraryState(StrEnum):
    """Whether a series catalogue can be inspected in Jellyfin."""

    AVAILABLE = "available"
    SERIES_ABSENT = "series-absent"
    UNAVAILABLE = "unavailable"


class JellyfinSeriesMatchMethod(StrEnum):
    """The identity rule that uniquely selected a Jellyfin series."""

    TVDB_ID = "tvdb-id"
    TITLE_YEAR = "title-year"


class JellyfinEpisodeAvailabilityState(StrEnum):
    """Viewer-facing availability of one season and episode coordinate."""

    VISIBLE = "visible"
    SERIES_ABSENT = "series-absent"
    EPISODE_ABSENT = "episode-absent"
    UNAVAILABLE = "unavailable"


class _JellyfinModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        strict=True,
    )


class JellyfinSeries(_JellyfinModel):
    """The bounded Jellyfin identity selected for a library lookup."""

    jellyfin_id: UUID
    title: JellyfinTitle
    year: JellyfinYear | None
    matched_by: JellyfinSeriesMatchMethod


class JellyfinLibraryAvailability(_JellyfinModel):
    """A series-level lookup plus every numbered episode visible in its library."""

    state: JellyfinLibraryState
    series: JellyfinSeries | None
    episode_coordinates: tuple[JellyfinEpisodeCoordinate, ...]

    @model_validator(mode="after")
    def _validate_state(self) -> Self:
        series_is_available = self.state is JellyfinLibraryState.AVAILABLE
        if series_is_available != (self.series is not None):
            raise ValueError("Jellyfin availability state and series are inconsistent")
        if not series_is_available and self.episode_coordinates:
            raise ValueError("unavailable Jellyfin series cannot contain episode coordinates")

        validated_coordinates = tuple(
            _validate_episode_coordinate(coordinate) for coordinate in self.episode_coordinates
        )
        if validated_coordinates != tuple(sorted(validated_coordinates)) or len(
            set(validated_coordinates)
        ) != len(validated_coordinates):
            raise ValueError("Jellyfin episode coordinates must be unique and ordered")
        return self

    def episode_availability(
        self,
        season_number: int,
        episode_number: int,
    ) -> JellyfinEpisodeAvailabilityState:
        """Classify one coordinate without another Jellyfin request."""
        coordinate = _validate_episode_coordinate((season_number, episode_number))
        if self.state is JellyfinLibraryState.UNAVAILABLE:
            return JellyfinEpisodeAvailabilityState.UNAVAILABLE
        if self.state is JellyfinLibraryState.SERIES_ABSENT:
            return JellyfinEpisodeAvailabilityState.SERIES_ABSENT
        if coordinate in self.episode_coordinates:
            return JellyfinEpisodeAvailabilityState.VISIBLE
        return JellyfinEpisodeAvailabilityState.EPISODE_ABSENT


class _JellyfinResponseModel(BaseModel):
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True, strict=True)


class _JellyfinSystemInfo(_JellyfinResponseModel):
    version: ServiceVersion = Field(alias="Version")
    startup_wizard_completed: bool = Field(alias="StartupWizardCompleted")
    has_pending_restart: bool = Field(default=False, alias="HasPendingRestart")
    is_shutting_down: bool = Field(default=False, alias="IsShuttingDown")


_JellyfinResponseYear = Annotated[int, Field(ge=0, le=9999)]
_JellyfinRawItemId = Annotated[str, Field(min_length=32, max_length=36)]


class _JellyfinItem(_JellyfinResponseModel):
    item_id: _JellyfinRawItemId = Field(alias="Id")
    name: JellyfinTitle = Field(alias="Name")
    item_type: Literal["Series", "Episode"] = Field(alias="Type")
    production_year: _JellyfinResponseYear | None = Field(default=None, alias="ProductionYear")
    provider_ids: dict[str, str] = Field(default_factory=dict, alias="ProviderIds")
    index_number: JellyfinEpisodeNumber | None = Field(default=None, alias="IndexNumber")
    index_number_end: JellyfinEpisodeNumber | None = Field(default=None, alias="IndexNumberEnd")
    parent_index_number: JellyfinSeasonNumber | None = Field(
        default=None,
        alias="ParentIndexNumber",
    )

    @field_validator("provider_ids")
    @classmethod
    def _bound_provider_ids(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > _MAX_PROVIDER_IDS or any(
            not name
            or len(name) > _MAX_PROVIDER_NAME_LENGTH
            or not identifier
            or len(identifier) > _MAX_PROVIDER_VALUE_LENGTH
            for name, identifier in value.items()
        ):
            raise ValueError("provider IDs exceed the response bound")
        return value


class _JellyfinItemPage(_JellyfinResponseModel):
    items: list[_JellyfinItem] = Field(alias="Items")
    total_record_count: Annotated[int, Field(ge=0)] = Field(alias="TotalRecordCount")
    start_index: Annotated[int, Field(ge=0)] = Field(alias="StartIndex")


class JellyfinClient(HttpServiceClient):
    """Read authenticated Jellyfin health and bounded library endpoints."""

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

    async def get_library_availability(
        self,
        *,
        tvdb_id: int,
        title: str,
        year: int | None,
    ) -> JellyfinLibraryAvailability:
        """Find one series and list its visible, numbered episode coordinates.

        Jellyfin 10.11 can return TVDB provider IDs but cannot filter ``Items`` by
        one exact provider-ID value. Wit therefore reads a bounded, paginated set
        of TVDB-tagged series and compares the IDs locally. Only when no TVDB
        match exists and a year is known does it fall back to one uniquely matching
        normalised title/year candidate that has no conflicting TVDB ID.
        """
        validated_tvdb_id = _validate_tvdb_id(tvdb_id)
        validated_title = _validate_title(title)
        validated_year = _validate_year(year)

        try:
            match = await self._find_series(
                tvdb_id=validated_tvdb_id,
                title=validated_title,
                year=validated_year,
            )
            if match is None:
                return JellyfinLibraryAvailability(
                    state=JellyfinLibraryState.SERIES_ABSENT,
                    series=None,
                    episode_coordinates=(),
                )

            item, match_method = match
            episode_items = await self._list_items(
                expected_type="Episode",
                collection_name="episode",
                params={
                    "parentId": str(_parse_item_id(item.item_id)),
                    "includeItemTypes": "Episode",
                    "recursive": True,
                    "isMissing": False,
                    "isPlaceHolder": False,
                    "excludeLocationTypes": "Virtual",
                    "enableImages": False,
                    "enableUserData": False,
                    "sortBy": "ParentIndexNumber,IndexNumber",
                    "sortOrder": "Ascending,Ascending",
                },
            )
        except (HttpConnectionError, HttpTimeoutError):
            return _unavailable_library()
        except HttpStatusError as error:
            if error.status_code in _UNAVAILABLE_STATUS_CODES:
                return _unavailable_library()
            raise

        return JellyfinLibraryAvailability(
            state=JellyfinLibraryState.AVAILABLE,
            series=_to_jellyfin_series(item, match_method),
            episode_coordinates=_episode_coordinates(episode_items),
        )

    async def _find_series(
        self,
        *,
        tvdb_id: int,
        title: str,
        year: int | None,
    ) -> tuple[_JellyfinItem, JellyfinSeriesMatchMethod] | None:
        external_id_candidates = await self._list_items(
            expected_type="Series",
            collection_name="series",
            params={
                "includeItemTypes": "Series",
                "recursive": True,
                "hasTvdbId": True,
                "fields": "ProviderIds",
                "excludeLocationTypes": "Virtual",
                "enableImages": False,
                "enableUserData": False,
                "sortBy": "SortName",
                "sortOrder": "Ascending",
            },
        )
        external_id_matches = tuple(
            candidate
            for candidate in external_id_candidates
            if _provider_tvdb_id(candidate.provider_ids) == (True, tvdb_id)
        )
        if len(external_id_matches) > 1:
            raise AmbiguousJellyfinSeriesError(
                "Jellyfin contains multiple series with the requested TVDB ID"
            )
        if external_id_matches:
            return external_id_matches[0], JellyfinSeriesMatchMethod.TVDB_ID
        if year is None:
            return None

        fallback_candidates = await self._list_items(
            expected_type="Series",
            collection_name="series",
            params={
                "includeItemTypes": "Series",
                "recursive": True,
                "searchTerm": title,
                "years": year,
                "fields": "ProviderIds",
                "excludeLocationTypes": "Virtual",
                "enableImages": False,
                "enableUserData": False,
                "sortBy": "SortName",
                "sortOrder": "Ascending",
            },
        )
        normalised_title = _normalise_title(title)
        fallback_matches = tuple(
            candidate
            for candidate in fallback_candidates
            if candidate.production_year == year
            and _normalise_title(candidate.name) == normalised_title
            and _provider_id_is_compatible(candidate.provider_ids, tvdb_id)
        )
        if len(fallback_matches) > 1:
            raise AmbiguousJellyfinSeriesError(
                "Jellyfin contains multiple series matching the requested title and year"
            )
        if fallback_matches:
            return fallback_matches[0], JellyfinSeriesMatchMethod.TITLE_YEAR
        return None

    async def _list_items(
        self,
        *,
        expected_type: Literal["Series", "Episode"],
        collection_name: Literal["series", "episode"],
        params: dict[str, str | int | bool],
    ) -> tuple[_JellyfinItem, ...]:
        items: list[_JellyfinItem] = []
        seen_item_ids: set[UUID] = set()
        expected_total: int | None = None

        for _ in range(_MAX_ITEM_PAGES):
            requested_start_index = len(items)
            page_params = dict(params)
            page_params.update(
                {
                    "startIndex": requested_start_index,
                    "limit": _ITEM_PAGE_SIZE,
                    "enableTotalRecordCount": True,
                }
            )
            payload = await self._transport.request_json(
                "GET",
                "Items",
                params=page_params,
            )

            try:
                response = _JellyfinItemPage.model_validate(payload)
            except ValidationError:
                raise InvalidJellyfinResponseError(
                    "Jellyfin returned an invalid item-list response"
                ) from None

            if response.total_record_count > _MAX_LIBRARY_ITEMS:
                raise JellyfinLibraryLimitError(
                    f"Jellyfin {collection_name} lookup exceeded the "
                    f"{_MAX_LIBRARY_ITEMS}-item safety bound"
                )

            try:
                if response.start_index != requested_start_index:
                    raise ValueError("item response returned an unexpected start index")
                if len(response.items) > _ITEM_PAGE_SIZE:
                    raise ValueError("item response exceeded the requested page size")
                if any(item.item_type != expected_type for item in response.items):
                    raise ValueError("item response contained an unexpected item type")

                if expected_total is None:
                    expected_total = response.total_record_count
                elif response.total_record_count != expected_total:
                    raise ValueError("item pagination metadata changed")

                remaining_items = expected_total - len(items)
                expected_item_count = min(_ITEM_PAGE_SIZE, remaining_items)
                if len(response.items) != expected_item_count:
                    raise ValueError("item page did not contain all declared records")

                page_ids = {_parse_item_id(item.item_id) for item in response.items}
                if len(page_ids) != len(response.items) or page_ids & seen_item_ids:
                    raise ValueError("item response contained duplicate IDs")
            except ValueError:
                raise InvalidJellyfinResponseError(
                    "Jellyfin returned an invalid item-list response"
                ) from None

            items.extend(response.items)
            seen_item_ids.update(page_ids)
            if len(items) == expected_total:
                return tuple(items)

        raise InvalidJellyfinResponseError("Jellyfin returned an invalid item-list response")


def _validate_tvdb_id(tvdb_id: int) -> int:
    if (
        isinstance(tvdb_id, bool)
        or not isinstance(tvdb_id, int)
        or tvdb_id <= 0
        or tvdb_id > _MAX_IDENTIFIER
    ):
        raise InvalidJellyfinRequestError("Jellyfin TVDB ID must be a positive integer")
    return tvdb_id


def _validate_title(title: str) -> str:
    if not isinstance(title, str):
        raise InvalidJellyfinRequestError("Jellyfin series title must be text")
    normalised = title.strip()
    if (
        not normalised
        or len(normalised) > _MAX_TITLE_LENGTH
        or any(ord(character) < 32 or ord(character) == 127 for character in normalised)
    ):
        raise InvalidJellyfinRequestError("Jellyfin series title is invalid")
    if not _normalise_title(normalised):
        raise InvalidJellyfinRequestError("Jellyfin series title is invalid")
    return normalised


def _validate_year(year: int | None) -> int | None:
    if year is None:
        return None
    if isinstance(year, bool) or not isinstance(year, int) or not 1 <= year <= 9999:
        raise InvalidJellyfinRequestError("Jellyfin series year must be from 1 through 9999")
    return year


def _validate_episode_coordinate(
    coordinate: JellyfinEpisodeCoordinate,
) -> JellyfinEpisodeCoordinate:
    if (
        not isinstance(coordinate, tuple)
        or len(coordinate) != 2
        or isinstance(coordinate[0], bool)
        or not isinstance(coordinate[0], int)
        or not 0 <= coordinate[0] <= _MAX_IDENTIFIER
        or isinstance(coordinate[1], bool)
        or not isinstance(coordinate[1], int)
        or not 1 <= coordinate[1] <= _MAX_IDENTIFIER
    ):
        raise InvalidJellyfinRequestError(
            "Jellyfin episode coordinate must contain a non-negative season and positive episode"
        )
    return coordinate


def _parse_item_id(value: str) -> UUID:
    try:
        item_id = UUID(value)
    except (AttributeError, TypeError, ValueError):
        raise ValueError("invalid Jellyfin item ID") from None
    if item_id.int == 0:
        raise ValueError("invalid Jellyfin item ID")
    return item_id


def _provider_tvdb_id(provider_ids: dict[str, str]) -> tuple[bool, int | None]:
    values = [value for name, value in provider_ids.items() if name.casefold() == "tvdb"]
    if not values:
        return False, None
    if len(values) != 1 or _TVDB_IDENTIFIER.fullmatch(values[0]) is None:
        return True, None

    identifier = int(values[0])
    if not 1 <= identifier <= _MAX_IDENTIFIER:
        return True, None
    return True, identifier


def _provider_id_is_compatible(provider_ids: dict[str, str], tvdb_id: int) -> bool:
    present, candidate_tvdb_id = _provider_tvdb_id(provider_ids)
    return not present or candidate_tvdb_id == tvdb_id


def _normalise_title(title: str) -> str:
    folded = unicodedata.normalize("NFKC", title).casefold()
    return "".join(character for character in folded if character.isalnum())


def _normalise_response_title(title: str) -> str:
    normalised = title.strip()
    if not normalised or any(
        ord(character) < 32 or ord(character) == 127 for character in normalised
    ):
        raise InvalidJellyfinResponseError("Jellyfin returned an invalid series response")
    return normalised


def _to_jellyfin_series(
    item: _JellyfinItem,
    match_method: JellyfinSeriesMatchMethod,
) -> JellyfinSeries:
    try:
        item_id = _parse_item_id(item.item_id)
    except ValueError:
        raise InvalidJellyfinResponseError("Jellyfin returned an invalid series response") from None
    return JellyfinSeries(
        jellyfin_id=item_id,
        title=_normalise_response_title(item.name),
        year=item.production_year if item.production_year not in {None, 0} else None,
        matched_by=match_method,
    )


def _episode_coordinates(
    items: tuple[_JellyfinItem, ...],
) -> tuple[JellyfinEpisodeCoordinate, ...]:
    coordinates: set[JellyfinEpisodeCoordinate] = set()
    for item in items:
        if item.parent_index_number is None or item.index_number is None:
            continue

        end_number = item.index_number_end or item.index_number
        span = end_number - item.index_number + 1
        if span <= 0 or span > _MAX_MULTI_EPISODE_SPAN:
            raise InvalidJellyfinResponseError("Jellyfin returned an invalid episode-number range")
        coordinates.update(
            (item.parent_index_number, episode_number)
            for episode_number in range(item.index_number, end_number + 1)
        )
    return tuple(sorted(coordinates))


def _unavailable_library() -> JellyfinLibraryAvailability:
    return JellyfinLibraryAvailability(
        state=JellyfinLibraryState.UNAVAILABLE,
        series=None,
        episode_coordinates=(),
    )
