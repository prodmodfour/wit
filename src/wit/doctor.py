"""Read-only configuration, filesystem, and service diagnostics."""

from __future__ import annotations

import asyncio
import os
import stat
from enum import StrEnum
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from wit.clients import (
    JellyfinClient,
    SeerrClient,
    ServiceHealthResult,
    ServiceHealthState,
    ServiceName,
    SonarrClient,
)
from wit.config import WitSettings
from wit.errors import WitError

PathCheckSummary = Annotated[str, Field(min_length=1, max_length=256)]


class LocalPathName(StrEnum):
    """Local paths required by current Wit commands."""

    STATE_DIRECTORY = "state directory"


class LocalPathState(StrEnum):
    """Normalised local-path diagnostic outcomes."""

    READY = "ready"
    MISSING = "missing"
    INACCESSIBLE = "inaccessible"
    INVALID = "invalid"


class LocalPathCheck(BaseModel):
    """A safe diagnostic result for one required local path."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    name: LocalPathName
    state: LocalPathState
    summary: PathCheckSummary


class DoctorReport(BaseModel):
    """Complete read-only diagnostics after settings validation succeeds."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    local_paths: tuple[LocalPathCheck, ...]
    services: tuple[ServiceHealthResult, ...]

    @property
    def successful(self) -> bool:
        """Return whether every required path and service check passed."""
        return all(check.state is LocalPathState.READY for check in self.local_paths) and all(
            result.state is ServiceHealthState.HEALTHY for result in self.services
        )


def check_state_directory(path: Path) -> LocalPathCheck:
    """Inspect the configured state directory without creating or changing it."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return LocalPathCheck(
            name=LocalPathName.STATE_DIRECTORY,
            state=LocalPathState.MISSING,
            summary="does not exist",
        )
    except NotADirectoryError:
        return LocalPathCheck(
            name=LocalPathName.STATE_DIRECTORY,
            state=LocalPathState.INVALID,
            summary="has a parent path that is not a directory",
        )
    except OSError:
        return LocalPathCheck(
            name=LocalPathName.STATE_DIRECTORY,
            state=LocalPathState.INACCESSIBLE,
            summary="cannot be inspected by the current user",
        )

    if stat.S_ISLNK(metadata.st_mode):
        return LocalPathCheck(
            name=LocalPathName.STATE_DIRECTORY,
            state=LocalPathState.INVALID,
            summary="must not be a symbolic link",
        )
    if not stat.S_ISDIR(metadata.st_mode):
        return LocalPathCheck(
            name=LocalPathName.STATE_DIRECTORY,
            state=LocalPathState.INVALID,
            summary="is not a directory",
        )

    required_access = (
        (os.R_OK, "read"),
        (os.W_OK, "write"),
        (os.X_OK, "search"),
    )
    missing_access = [label for flag, label in required_access if not os.access(path, flag)]
    if missing_access:
        return LocalPathCheck(
            name=LocalPathName.STATE_DIRECTORY,
            state=LocalPathState.INACCESSIBLE,
            summary=f"does not grant the current user {_format_access_list(missing_access)} access",
        )

    return LocalPathCheck(
        name=LocalPathName.STATE_DIRECTORY,
        state=LocalPathState.READY,
        summary="exists with read, write, and search access",
    )


async def run_doctor(settings: WitSettings) -> DoctorReport:
    """Check all configured services independently and inspect required paths."""
    state_directory = check_state_directory(settings.state_dir)
    sonarr, jellyfin, seerr = await asyncio.gather(
        _check_sonarr(settings),
        _check_jellyfin(settings),
        _check_seerr(settings),
    )
    return DoctorReport(
        local_paths=(state_directory,),
        services=(sonarr, jellyfin, seerr),
    )


async def _check_sonarr(settings: WitSettings) -> ServiceHealthResult:
    credential = settings.sonarr.api_key
    try:
        async with SonarrClient(
            base_url=str(settings.sonarr.url),
            api_key=credential,
            connect_timeout_seconds=settings.http.connect_timeout_seconds,
            read_timeout_seconds=settings.http.read_timeout_seconds,
        ) as client:
            return await client.get_health()
    except WitError:
        return _incomplete_health_check(ServiceName.SONARR)


async def _check_jellyfin(settings: WitSettings) -> ServiceHealthResult:
    credential = settings.jellyfin.api_key
    try:
        async with JellyfinClient(
            base_url=str(settings.jellyfin.url),
            api_key=credential,
            connect_timeout_seconds=settings.http.connect_timeout_seconds,
            read_timeout_seconds=settings.http.read_timeout_seconds,
        ) as client:
            return await client.get_health()
    except WitError:
        return _incomplete_health_check(ServiceName.JELLYFIN)


async def _check_seerr(settings: WitSettings) -> ServiceHealthResult:
    try:
        async with SeerrClient(
            base_url=str(settings.seerr.url),
            connect_timeout_seconds=settings.http.connect_timeout_seconds,
            read_timeout_seconds=settings.http.read_timeout_seconds,
        ) as client:
            return await client.get_health()
    except WitError:
        return _incomplete_health_check(ServiceName.SEERR)


def _incomplete_health_check(service: ServiceName) -> ServiceHealthResult:
    label = service.value.capitalize()
    return ServiceHealthResult(
        service=service,
        state=ServiceHealthState.UNHEALTHY,
        summary=f"{label} health check could not be completed",
    )


def _format_access_list(labels: list[str]) -> str:
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} or {labels[1]}"
    return f"{', '.join(labels[:-1])}, or {labels[-1]}"
