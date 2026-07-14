"""Typed, secret-safe runtime configuration for Wit."""

from __future__ import annotations

import os
import stat
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    SecretStr,
    ValidationError,
    field_validator,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    SettingsError,
)

CONFIG_FILE_ENV_VAR = "WIT_CONFIG_FILE"
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0
DEFAULT_READ_TIMEOUT_SECONDS = 30.0
MAX_CONNECT_TIMEOUT_SECONDS = 60.0
MAX_READ_TIMEOUT_SECONDS = 120.0


class ConfigurationError(Exception):
    """A configuration failure whose message is safe to show to an operator."""


def _validate_base_url(value: HttpUrl) -> HttpUrl:
    if value.username is not None or value.password is not None:
        raise ValueError("service URL must not contain credentials")
    if value.query is not None or value.fragment is not None:
        raise ValueError("service URL must not contain a query string or fragment")
    return value


def _validate_api_credential(value: SecretStr) -> SecretStr:
    raw_value = value.get_secret_value()
    if not raw_value or raw_value != raw_value.strip():
        raise ValueError("API credential must not be blank or contain surrounding whitespace")
    return value


ServiceUrl = Annotated[HttpUrl, AfterValidator(_validate_base_url)]
ApiCredential = Annotated[SecretStr, AfterValidator(_validate_api_credential)]
PositiveIdentifier = Annotated[int, Field(gt=0)]
ConnectTimeout = Annotated[
    float,
    Field(ge=0.1, le=MAX_CONNECT_TIMEOUT_SECONDS),
]
ReadTimeout = Annotated[
    float,
    Field(ge=0.1, le=MAX_READ_TIMEOUT_SECONDS),
]


class _ConfigurationModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )


class SonarrSettings(_ConfigurationModel):
    """Sonarr connection details and apply defaults."""

    url: ServiceUrl
    api_key: ApiCredential
    root_folder_id: PositiveIdentifier
    quality_profile_id: PositiveIdentifier


class JellyfinSettings(_ConfigurationModel):
    """Jellyfin connection details."""

    url: ServiceUrl
    api_key: ApiCredential


class SeerrSettings(_ConfigurationModel):
    """Seerr connection details for its credential-free health endpoint."""

    url: ServiceUrl


class TvmazeSettings(_ConfigurationModel):
    """TVmaze connection details for read-only public metadata."""

    url: ServiceUrl = HttpUrl("https://api.tvmaze.com")


class HttpTimeoutSettings(_ConfigurationModel):
    """Shared bounded HTTP timeout values."""

    connect_timeout_seconds: ConnectTimeout = DEFAULT_CONNECT_TIMEOUT_SECONDS
    read_timeout_seconds: ReadTimeout = DEFAULT_READ_TIMEOUT_SECONDS


def _default_state_dir() -> Path:
    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg_state_home).expanduser() if xdg_state_home else Path.home() / ".local/state"
    return base / "wit"


class WitSettings(BaseSettings):
    """Complete runtime settings loaded from ``WIT_*`` environment values."""

    model_config = SettingsConfigDict(
        env_prefix="WIT_",
        env_nested_delimiter="_",
        env_nested_max_split=1,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    sonarr: SonarrSettings
    jellyfin: JellyfinSettings
    seerr: SeerrSettings
    tvmaze: TvmazeSettings = Field(default_factory=TvmazeSettings)
    http: HttpTimeoutSettings = Field(default_factory=HttpTimeoutSettings)
    state_dir: Path = Field(default_factory=_default_state_dir)

    @field_validator("state_dir", mode="before")
    @classmethod
    def _expand_state_dir(cls, value: object) -> object:
        if isinstance(value, (str, Path)):
            return Path(value).expanduser()
        return value

    @field_validator("state_dir")
    @classmethod
    def _validate_state_dir(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("state directory must be an absolute local path")
        if ".." in value.parts:
            raise ValueError("state directory must not contain parent traversal")

        normalised = Path(os.path.normpath(value))
        if normalised == Path(normalised.anchor):
            raise ValueError("state directory must not be a filesystem root")
        if normalised.is_symlink():
            raise ValueError("state directory must not be a symbolic link")
        if normalised.exists() and not normalised.is_dir():
            raise ValueError("state directory must be a directory")
        return normalised

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Prefer the process environment over values supplied by a protected file."""
        del settings_cls, dotenv_settings, file_secret_settings
        return env_settings, init_settings


def load_settings(config_file: str | Path | None = None) -> WitSettings:
    """Load settings from the environment and, optionally, a protected TOML file.

    ``config_file`` takes precedence over the ``WIT_CONFIG_FILE`` location when
    choosing a file. Individual ``WIT_*`` environment values take precedence over
    values in that file. The file is never discovered implicitly.
    """

    file_values: Mapping[str, Any] = {}
    selected_file = _select_config_file(config_file)
    if selected_file is not None:
        file_values = _read_protected_toml(selected_file)

    try:
        return WitSettings(**file_values)
    except ValidationError as error:
        raise ConfigurationError(_format_validation_error(error)) from None
    except SettingsError:
        raise ConfigurationError(
            "invalid Wit environment configuration; use individual documented WIT_* values"
        ) from None


def _select_config_file(config_file: str | Path | None) -> Path | None:
    selected: str | Path | None = config_file
    if selected is None and CONFIG_FILE_ENV_VAR in os.environ:
        selected = os.environ[CONFIG_FILE_ENV_VAR]

    if selected is None:
        return None
    if isinstance(selected, str) and not selected.strip():
        raise ConfigurationError(f"{CONFIG_FILE_ENV_VAR} must not be empty")

    path = Path(selected).expanduser()
    if not path.is_absolute():
        raise ConfigurationError("Wit configuration file path must be absolute")
    return path


def _read_protected_toml(path: Path) -> Mapping[str, Any]:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    descriptor: int | None = None
    try:
        if path.is_symlink():
            raise ConfigurationError("Wit configuration file must not be a symbolic link")
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        _validate_config_file_metadata(metadata)

        with os.fdopen(descriptor, "rb") as config_stream:
            descriptor = None
            return tomllib.load(config_stream)
    except ConfigurationError:
        raise
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        raise ConfigurationError("Wit configuration file is not valid TOML") from None
    except (OSError, ValueError):
        raise ConfigurationError("Wit configuration file cannot be opened securely") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _validate_config_file_metadata(metadata: os.stat_result) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise ConfigurationError("Wit configuration path must refer to a regular file")
    if stat.S_IMODE(metadata.st_mode) not in {0o400, 0o600}:
        raise ConfigurationError("Wit configuration file permissions must be 0600 or 0400")

    getuid = getattr(os, "getuid", None)
    if getuid is not None and metadata.st_uid != getuid():
        raise ConfigurationError("Wit configuration file must be owned by the current user")


def _format_validation_error(error: ValidationError) -> str:
    issues: list[str] = []
    for detail in error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    ):
        location = ".".join(str(part) for part in detail["loc"])
        issues.append(f"{location}: {detail['msg']}")
    return "invalid Wit configuration: " + "; ".join(issues)
