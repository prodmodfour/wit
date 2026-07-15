"""Versioned, command-neutral JSON output contracts for automation."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

OUTPUT_SCHEMA_VERSION: Final[Literal[1]] = 1

OutputCode = Annotated[
    str,
    Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$",
    ),
]
OutputMessage = Annotated[str, Field(min_length=1, max_length=4096)]

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


class JsonOutputCommand(StrEnum):
    """Commands that expose Wit's versioned machine-readable contract."""

    DOCTOR = "doctor"
    PLAN = "plan"
    APPLY = "apply"
    STATUS = "status"


class JsonOutputIssue(BaseModel):
    """One stable warning or error without raw service response data."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        strict=True,
    )

    code: OutputCode
    message: OutputMessage


class JsonOutputEnvelope(BaseModel):
    """The schema-versioned envelope emitted once by a command in JSON mode."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        strict=True,
    )

    schema_version: Literal[1] = OUTPUT_SCHEMA_VERSION
    command: JsonOutputCommand
    success: bool
    data: JsonObject | None
    warnings: tuple[JsonOutputIssue, ...] = ()
    errors: tuple[JsonOutputIssue, ...] = ()

    @model_validator(mode="after")
    def _validate_success_and_errors(self) -> Self:
        if self.success and self.errors:
            raise ValueError("successful JSON output must not contain errors")
        if not self.success and not self.errors:
            raise ValueError("failed JSON output must contain at least one error")
        return self

    def render(self) -> str:
        """Serialize one deterministic JSON document for standard output."""
        return self.model_dump_json(indent=2)
