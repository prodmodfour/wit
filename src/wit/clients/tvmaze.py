"""Read-only TVmaze show and episode metadata client."""

from __future__ import annotations

from datetime import date, datetime, time
from enum import StrEnum
from typing import Annotated, Literal, Self

import httpx
from pydantic import BaseModel, ConfigDict, Field, RootModel, ValidationError, model_validator

from wit.clients._base import HttpServiceClient
from wit.config import DEFAULT_CONNECT_TIMEOUT_SECONDS, DEFAULT_READ_TIMEOUT_SECONDS
from wit.errors import WitError
from wit.transport import HttpTransport

_MAX_QUERY_LENGTH = 256
_MAX_TITLE_LENGTH = 512
_MAX_IDENTIFIER = 2_147_483_647

TvmazeIdentifier = Annotated[int, Field(gt=0, le=_MAX_IDENTIFIER)]
SeasonNumber = Annotated[int, Field(ge=0)]
EpisodeNumber = Annotated[int, Field(gt=0)]
MetadataTitle = Annotated[str, Field(min_length=1, max_length=_MAX_TITLE_LENGTH)]
SearchScore = Annotated[float, Field(ge=0, allow_inf_nan=False)]
ImdbIdentifier = Annotated[
    str,
    Field(min_length=3, max_length=32, pattern=r"^tt[0-9]+$"),
]


class TvmazeClientError(WitError):
    """Base class for safe TVmaze metadata-client failures."""


class InvalidTvmazeRequestError(TvmazeClientError):
    """A TVmaze metadata request was invalid before any network operation."""


class InvalidTvmazeResponseError(TvmazeClientError):
    """TVmaze returned JSON that did not satisfy the expected metadata contract."""


class TvmazeEpisodeType(StrEnum):
    """Episode kinds exposed by TVmaze."""

    REGULAR = "regular"
    SIGNIFICANT_SPECIAL = "significant_special"
    INSIGNIFICANT_SPECIAL = "insignificant_special"

    @property
    def is_special(self) -> bool:
        """Return whether this kind is either category of special."""
        return self is not TvmazeEpisodeType.REGULAR


class _MetadataModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class TvmazeShow(_MetadataModel):
    """Show identity and planning metadata returned by TVmaze."""

    tvmaze_id: TvmazeIdentifier
    title: MetadataTitle
    premiere_date: date | None
    tvdb_id: TvmazeIdentifier | None
    imdb_id: ImdbIdentifier | None

    @property
    def premiere_year(self) -> int | None:
        """Return the known premiere year used for deterministic disambiguation."""
        return self.premiere_date.year if self.premiere_date is not None else None


class TvmazeShowSearchResult(_MetadataModel):
    """One TVmaze show-search candidate with its upstream relevance score."""

    score: SearchScore
    show: TvmazeShow


class TvmazeEpisode(_MetadataModel):
    """One episode with explicit numbering, kind, and possibly incomplete airing data."""

    tvmaze_id: TvmazeIdentifier
    title: MetadataTitle
    season_number: SeasonNumber
    episode_number: EpisodeNumber | None
    episode_type: TvmazeEpisodeType
    air_date: date | None
    air_time: time | None
    air_timestamp: datetime | None


class TvmazeEpisodeCollection(_MetadataModel):
    """Episodes partitioned into regular and special records in TVmaze order."""

    regular: tuple[TvmazeEpisode, ...]
    specials: tuple[TvmazeEpisode, ...]

    @model_validator(mode="after")
    def _validate_partition(self) -> Self:
        if any(episode.episode_type is not TvmazeEpisodeType.REGULAR for episode in self.regular):
            raise ValueError("regular episode partition contains a special")
        if any(not episode.episode_type.is_special for episode in self.specials):
            raise ValueError("special episode partition contains a regular episode")
        return self


class _TvmazeResponseModel(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


class _TvmazeExternalIds(_TvmazeResponseModel):
    thetvdb: TvmazeIdentifier | None = None
    imdb: ImdbIdentifier | None = None


class _TvmazeShow(_TvmazeResponseModel):
    id: TvmazeIdentifier
    name: MetadataTitle
    premiered: str | None = None
    externals: _TvmazeExternalIds | None = None


class _TvmazeShowSearchResult(_TvmazeResponseModel):
    score: SearchScore
    show: _TvmazeShow


class _TvmazeShowSearchResponse(RootModel[list[_TvmazeShowSearchResult]]):
    model_config = ConfigDict(strict=True)


class _TvmazeEpisode(_TvmazeResponseModel):
    id: TvmazeIdentifier
    name: MetadataTitle
    season: SeasonNumber
    number: EpisodeNumber | None
    type: Literal["regular", "significant_special", "insignificant_special"]
    airdate: str | None = None
    airtime: str | None = None
    airstamp: str | None = None


class _TvmazeEpisodeResponse(RootModel[list[_TvmazeEpisode]]):
    model_config = ConfigDict(strict=True)


class TvmazeClient(HttpServiceClient):
    """Retrieve public TVmaze metadata without mutating any service."""

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
                service_name="TVmaze",
                connect_timeout_seconds=connect_timeout_seconds,
                read_timeout_seconds=read_timeout_seconds,
                transport=http_transport,
            )
        )

    async def search_shows(self, title: str) -> tuple[TvmazeShowSearchResult, ...]:
        """Search TVmaze by title and preserve its relevance ordering."""
        query = _validate_search_title(title)
        payload = await self._transport.request_json(
            "GET",
            "search/shows",
            params={"q": query},
        )

        try:
            response = _TvmazeShowSearchResponse.model_validate(payload)
            return tuple(_map_search_result(item) for item in response.root)
        except (ValidationError, ValueError):
            raise InvalidTvmazeResponseError(
                "TVmaze returned an invalid show-search response"
            ) from None

    async def get_episodes(self, show_id: int) -> TvmazeEpisodeCollection:
        """Fetch all known episodes and partition regular episodes from specials."""
        validated_show_id = _validate_show_id(show_id)
        payload = await self._transport.request_json(
            "GET",
            f"shows/{validated_show_id}/episodes",
            params={"specials": 1},
        )

        try:
            response = _TvmazeEpisodeResponse.model_validate(payload)
            regular: list[TvmazeEpisode] = []
            specials: list[TvmazeEpisode] = []
            for item in response.root:
                episode = _map_episode(item)
                if episode.episode_type is TvmazeEpisodeType.REGULAR:
                    regular.append(episode)
                else:
                    specials.append(episode)
            return TvmazeEpisodeCollection(regular=tuple(regular), specials=tuple(specials))
        except (ValidationError, ValueError):
            raise InvalidTvmazeResponseError(
                "TVmaze returned an invalid episode-list response"
            ) from None


def _validate_search_title(title: str) -> str:
    if not isinstance(title, str):
        raise InvalidTvmazeRequestError("TVmaze show title must be text")

    normalised = title.strip()
    if (
        not normalised
        or len(normalised) > _MAX_QUERY_LENGTH
        or any(ord(character) < 32 or ord(character) == 127 for character in normalised)
    ):
        raise InvalidTvmazeRequestError("TVmaze show title is invalid")
    return normalised


def _validate_show_id(show_id: int) -> int:
    if (
        isinstance(show_id, bool)
        or not isinstance(show_id, int)
        or show_id <= 0
        or show_id > _MAX_IDENTIFIER
    ):
        raise InvalidTvmazeRequestError("TVmaze show ID must be a positive integer")
    return show_id


def _map_search_result(result: _TvmazeShowSearchResult) -> TvmazeShowSearchResult:
    external_ids = result.show.externals
    return TvmazeShowSearchResult(
        score=result.score,
        show=TvmazeShow(
            tvmaze_id=result.show.id,
            title=_normalise_metadata_title(result.show.name),
            premiere_date=_parse_optional_date(result.show.premiered),
            tvdb_id=external_ids.thetvdb if external_ids is not None else None,
            imdb_id=external_ids.imdb if external_ids is not None else None,
        ),
    )


def _map_episode(episode: _TvmazeEpisode) -> TvmazeEpisode:
    episode_type = TvmazeEpisodeType(episode.type)
    if episode_type is TvmazeEpisodeType.REGULAR and episode.number is None:
        raise ValueError("regular episode is missing its episode number")

    return TvmazeEpisode(
        tvmaze_id=episode.id,
        title=_normalise_metadata_title(episode.name),
        season_number=episode.season,
        episode_number=episode.number,
        episode_type=episode_type,
        air_date=_parse_optional_date(episode.airdate),
        air_time=_parse_optional_time(episode.airtime),
        air_timestamp=_parse_optional_timestamp(episode.airstamp),
    )


def _normalise_metadata_title(value: str) -> str:
    normalised = value.strip()
    if not normalised:
        raise ValueError("metadata title is blank")
    return normalised


def _parse_optional_date(value: str | None) -> date | None:
    if value is None or value == "":
        return None
    if value != value.strip():
        raise ValueError("date contains surrounding whitespace")
    return date.fromisoformat(value)


def _parse_optional_time(value: str | None) -> time | None:
    if value is None or value == "":
        return None
    if value != value.strip():
        raise ValueError("time contains surrounding whitespace")
    return time.fromisoformat(value)


def _parse_optional_timestamp(value: str | None) -> datetime | None:
    if value is None or value == "":
        return None
    if value != value.strip():
        raise ValueError("timestamp contains surrounding whitespace")

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp has no timezone")
    return parsed
