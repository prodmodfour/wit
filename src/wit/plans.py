"""Immutable, versioned, and secret-free download-plan models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Final, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from wit.errors import WitError

DOWNLOAD_PLAN_SCHEMA_VERSION: Final[Literal[1]] = 1

_MAX_IDENTIFIER = 2_147_483_647
_MAX_PLAN_ID_LENGTH = 128
_MAX_TITLE_LENGTH = 512
_MAX_SELECTOR_SUMMARY_LENGTH = 512

PositiveIdentifier = Annotated[int, Field(gt=0, le=_MAX_IDENTIFIER)]
RegularSeasonNumber = Annotated[int, Field(gt=0, le=_MAX_IDENTIFIER)]
EpisodeNumber = Annotated[int, Field(gt=0, le=_MAX_IDENTIFIER)]
ShowYear = Annotated[int, Field(ge=1, le=9999)]


class DownloadPlanError(WitError):
    """Base class for safe download-plan failures."""


class InvalidDownloadPlanError(DownloadPlanError):
    """Serialized data did not satisfy the supported download-plan schema."""


def _validate_single_line_text(value: str) -> str:
    if value != value.strip() or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        raise ValueError("plan text must not contain surrounding whitespace or control characters")
    return value


PlanIdentifier = Annotated[
    str,
    Field(
        min_length=1,
        max_length=_MAX_PLAN_ID_LENGTH,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    ),
]
PlanTitle = Annotated[
    str,
    Field(min_length=1, max_length=_MAX_TITLE_LENGTH),
    AfterValidator(_validate_single_line_text),
]
SelectorSummary = Annotated[
    str,
    Field(min_length=1, max_length=_MAX_SELECTOR_SUMMARY_LENGTH),
    AfterValidator(_validate_single_line_text),
]


class _DownloadPlanModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        strict=True,
    )


class PlannedEpisode(_DownloadPlanModel):
    """One regular episode selected by stable season and episode coordinates."""

    season_number: RegularSeasonNumber
    episode_number: EpisodeNumber
    title: PlanTitle

    @property
    def coordinate(self) -> tuple[int, int]:
        """Return the coordinate consumed by Sonarr mapping during apply."""
        return self.season_number, self.episode_number

    @property
    def label(self) -> str:
        """Return a deterministic human-readable coordinate label."""
        return f"S{self.season_number:02d}E{self.episode_number:02d}"


class DownloadPlan(_DownloadPlanModel):
    """The complete immutable contract exchanged between planning and apply."""

    schema_version: Literal[1]
    plan_id: PlanIdentifier
    created_at: datetime
    show_title: PlanTitle
    show_year: ShowYear | None
    tvmaze_id: PositiveIdentifier
    tvdb_id: PositiveIdentifier
    selector_summary: SelectorSummary
    episodes: tuple[PlannedEpisode, ...]

    @field_validator("created_at")
    @classmethod
    def _require_aware_creation_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("plan creation time must be timezone-aware")
        try:
            if value.utcoffset() is None:
                raise ValueError("missing UTC offset")
            return value.astimezone(UTC)
        except (OverflowError, ValueError):
            raise ValueError("plan creation time must be timezone-aware") from None

    @field_validator("episodes")
    @classmethod
    def _validate_selected_episodes(
        cls,
        value: tuple[PlannedEpisode, ...],
    ) -> tuple[PlannedEpisode, ...]:
        if not value:
            raise ValueError("download plan must select at least one episode")

        coordinates = tuple(episode.coordinate for episode in value)
        if len(set(coordinates)) != len(coordinates):
            raise ValueError("download plan contains duplicate episode coordinates")
        return tuple(sorted(value, key=lambda episode: episode.coordinate))

    @property
    def episode_count(self) -> int:
        """Return the number of episodes selected by this plan."""
        return len(self.episodes)

    def to_json(self) -> str:
        """Serialize this plan to deterministic, inspectable JSON."""
        return self.model_dump_json(indent=2)

    @classmethod
    def from_json(cls, payload: str | bytes | bytearray) -> Self:
        """Strictly deserialize one supported plan without exposing invalid input."""
        if not isinstance(payload, (str, bytes, bytearray)):
            raise InvalidDownloadPlanError("download plan must be encoded as JSON")
        try:
            return cls.model_validate_json(payload, strict=True)
        except (ValidationError, ValueError, TypeError):
            raise InvalidDownloadPlanError(
                f"download plan JSON does not match schema version {DOWNLOAD_PLAN_SCHEMA_VERSION}"
            ) from None

    def render(self) -> str:
        """Render the plan deterministically for inspection before apply."""
        created_at = self.created_at.isoformat().replace("+00:00", "Z")
        show_year = str(self.show_year) if self.show_year is not None else "year unknown"
        lines = [
            f"Download plan: {self.plan_id}",
            f"Schema version: {self.schema_version}",
            f"Created: {created_at}",
            f"Show: {self.show_title} ({show_year})",
            f"TVmaze ID: {self.tvmaze_id}",
            f"TVDB ID: {self.tvdb_id}",
            f"Selector: {self.selector_summary}",
            f"Selected episodes ({self.episode_count}):",
        ]
        lines.extend(f"  {episode.label}  {episode.title}" for episode in self.episodes)
        return "\n".join(lines)
