"""Unit tests for Wit's typed runtime configuration."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from wit.config import ConfigurationError, load_settings


def _credential_value(label: str) -> str:
    return f"{label}-" + ("x" * 24)


def _clear_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.startswith("WIT_"):
            monkeypatch.delenv(name)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)


def _set_valid_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[str, str]:
    _clear_settings_environment(monkeypatch)
    sonarr_credential_value = _credential_value("sonarr")
    jellyfin_credential_value = _credential_value("jellyfin")
    values = {
        "WIT_SONARR_URL": "http://127.0.0.1:8989",
        "WIT_SONARR_API_KEY": sonarr_credential_value,
        "WIT_SONARR_ROOT_FOLDER_ID": "7",
        "WIT_SONARR_QUALITY_PROFILE_ID": "8",
        "WIT_JELLYFIN_URL": "http://127.0.0.1:8096",
        "WIT_JELLYFIN_API_KEY": jellyfin_credential_value,
        "WIT_SEERR_URL": "http://127.0.0.1:5055",
        "WIT_TVMAZE_URL": "https://metadata.example.test",
        "WIT_HTTP_CONNECT_TIMEOUT_SECONDS": "4.5",
        "WIT_HTTP_READ_TIMEOUT_SECONDS": "45",
        "WIT_STATE_DIR": str(tmp_path / "state"),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return sonarr_credential_value, jellyfin_credential_value


def _write_config_file(path: Path, state_dir: Path) -> tuple[str, str]:
    sonarr_credential_value = _credential_value("file-sonarr")
    jellyfin_credential_value = _credential_value("file-jellyfin")
    path.write_text(
        f"""
state_dir = "{state_dir.as_posix()}"

[sonarr]
url = "http://127.0.0.1:8989"
api_key = "{sonarr_credential_value}"
root_folder_id = 2
quality_profile_id = 3

[jellyfin]
url = "http://127.0.0.1:8096"
api_key = "{jellyfin_credential_value}"

[seerr]
url = "http://127.0.0.1:5055"

[tvmaze]
url = "https://api.tvmaze.com"

[http]
connect_timeout_seconds = 3
read_timeout_seconds = 20
""".strip()
        + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return sonarr_credential_value, jellyfin_credential_value


def test_loads_valid_settings_from_environment_and_redacts_credential_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sonarr_credential_value, jellyfin_credential_value = _set_valid_environment(
        monkeypatch, tmp_path
    )

    settings = load_settings()

    assert str(settings.sonarr.url) == "http://127.0.0.1:8989/"
    assert settings.sonarr.api_key.get_secret_value() == sonarr_credential_value
    assert settings.sonarr.root_folder_id == 7
    assert settings.sonarr.quality_profile_id == 8
    assert str(settings.jellyfin.url) == "http://127.0.0.1:8096/"
    assert settings.jellyfin.api_key.get_secret_value() == jellyfin_credential_value
    assert str(settings.seerr.url) == "http://127.0.0.1:5055/"
    assert str(settings.tvmaze.url) == "https://metadata.example.test/"
    assert settings.http.connect_timeout_seconds == 4.5
    assert settings.http.read_timeout_seconds == 45
    assert settings.state_dir == tmp_path / "state"

    rendered = repr(settings) + settings.model_dump_json()
    assert "**********" in rendered
    assert sonarr_credential_value not in rendered
    assert jellyfin_credential_value not in rendered


def test_uses_xdg_state_home_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_valid_environment(monkeypatch, tmp_path)
    monkeypatch.delenv("WIT_STATE_DIR")
    monkeypatch.delenv("WIT_TVMAZE_URL")
    monkeypatch.delenv("WIT_HTTP_CONNECT_TIMEOUT_SECONDS")
    monkeypatch.delenv("WIT_HTTP_READ_TIMEOUT_SECONDS")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))

    settings = load_settings()

    assert settings.state_dir == tmp_path / "xdg-state" / "wit"
    assert str(settings.tvmaze.url) == "https://api.tvmaze.com/"
    assert settings.http.connect_timeout_seconds == 5
    assert settings.http.read_timeout_seconds == 30


def test_loads_protected_toml_with_environment_precedence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_settings_environment(monkeypatch)
    config_path = tmp_path / "wit.toml"
    sonarr_credential_value, jellyfin_credential_value = _write_config_file(
        config_path, tmp_path / "state"
    )
    monkeypatch.setenv("WIT_SONARR_ROOT_FOLDER_ID", "11")

    settings = load_settings(config_path)

    assert settings.sonarr.root_folder_id == 11
    assert settings.sonarr.quality_profile_id == 3
    assert settings.sonarr.api_key.get_secret_value() == sonarr_credential_value
    assert settings.jellyfin.api_key.get_secret_value() == jellyfin_credential_value
    assert settings.state_dir == tmp_path / "state"


def test_reads_explicit_config_location_from_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_settings_environment(monkeypatch)
    config_path = tmp_path / "wit.toml"
    _write_config_file(config_path, tmp_path / "state")
    monkeypatch.setenv("WIT_CONFIG_FILE", str(config_path))

    settings = load_settings()

    assert settings.sonarr.root_folder_id == 2


def test_reports_missing_required_configuration_without_input_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_settings_environment(monkeypatch)

    with pytest.raises(ConfigurationError) as captured:
        load_settings()

    message = str(captured.value)
    assert "sonarr: Field required" in message
    assert "jellyfin: Field required" in message
    assert "seerr: Field required" in message
    assert "input_value" not in message


@pytest.mark.parametrize(
    ("variable", "expected_field"),
    [
        ("WIT_SONARR_API_KEY", "sonarr.api_key"),
        ("WIT_JELLYFIN_API_KEY", "jellyfin.api_key"),
    ],
)
def test_requires_service_api_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    variable: str,
    expected_field: str,
) -> None:
    _set_valid_environment(monkeypatch, tmp_path)
    monkeypatch.delenv(variable)

    with pytest.raises(ConfigurationError) as captured:
        load_settings()

    assert expected_field in str(captured.value)


@pytest.mark.parametrize(
    ("name", "value", "expected_field"),
    [
        ("WIT_SONARR_URL", "ftp://example.test", "sonarr.url"),
        ("WIT_SONARR_ROOT_FOLDER_ID", "0", "sonarr.root_folder_id"),
        ("WIT_SONARR_QUALITY_PROFILE_ID", "-1", "sonarr.quality_profile_id"),
        ("WIT_HTTP_CONNECT_TIMEOUT_SECONDS", "0", "http.connect_timeout_seconds"),
        ("WIT_HTTP_READ_TIMEOUT_SECONDS", "121", "http.read_timeout_seconds"),
        ("WIT_STATE_DIR", "relative/state", "state_dir"),
        ("WIT_STATE_DIR", "/", "state_dir"),
    ],
)
def test_rejects_malformed_configuration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    name: str,
    value: str,
    expected_field: str,
) -> None:
    sonarr_credential_value, jellyfin_credential_value = _set_valid_environment(
        monkeypatch, tmp_path
    )
    monkeypatch.setenv(name, value)

    with pytest.raises(ConfigurationError) as captured:
        load_settings()

    message = str(captured.value)
    assert expected_field in message
    assert sonarr_credential_value not in message
    assert jellyfin_credential_value not in message
    assert "input_value" not in message


@pytest.mark.parametrize(
    "url",
    [
        "http://user:password@example.test",
        "https://example.test?token=value",
        "https://example.test/#fragment",
    ],
)
def test_rejects_credentials_and_suffix_data_in_service_urls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    url: str,
) -> None:
    _set_valid_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("WIT_SEERR_URL", url)

    with pytest.raises(ConfigurationError) as captured:
        load_settings()

    assert "seerr.url" in str(captured.value)
    assert url not in str(captured.value)


def test_redacts_api_credentials_from_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sonarr_credential_value, _ = _set_valid_environment(monkeypatch, tmp_path)
    malformed_credential_value = f" {sonarr_credential_value}"
    monkeypatch.setenv("WIT_SONARR_API_KEY", malformed_credential_value)

    with pytest.raises(ConfigurationError) as captured:
        load_settings()

    assert sonarr_credential_value not in str(captured.value)
    assert malformed_credential_value not in str(captured.value)
    assert "sonarr.api_key" in str(captured.value)


def test_rejects_insecure_or_indirect_config_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_settings_environment(monkeypatch)
    config_path = tmp_path / "wit.toml"
    _write_config_file(config_path, tmp_path / "state")
    config_path.chmod(0o644)

    with pytest.raises(ConfigurationError, match="permissions"):
        load_settings(config_path)

    config_path.chmod(0o600)
    symlink_path = tmp_path / "wit-link.toml"
    symlink_path.symlink_to(config_path)

    with pytest.raises(ConfigurationError, match="symbolic link"):
        load_settings(symlink_path)


def test_rejects_relative_or_malformed_config_file_without_echoing_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_settings_environment(monkeypatch)

    with pytest.raises(ConfigurationError, match="path must be absolute"):
        load_settings(Path("wit.toml"))

    marker = _credential_value("invalid-file-content")
    config_path = tmp_path / "wit.toml"
    config_path.write_text(f'invalid = "{marker}\n', encoding="utf-8")
    config_path.chmod(0o600)

    with pytest.raises(ConfigurationError) as captured:
        load_settings(config_path)

    assert marker not in str(captured.value)
    assert "not valid TOML" in str(captured.value)
