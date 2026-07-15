"""CLI and filesystem tests for read-only Wit diagnostics."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

import wit.cli as cli
import wit.doctor as doctor_module
from wit.clients import ServiceHealthResult, ServiceHealthState, ServiceName
from wit.config import WitSettings, load_settings
from wit.doctor import (
    DoctorReport,
    LocalPathCheck,
    LocalPathName,
    LocalPathState,
    check_state_directory,
)

runner = CliRunner()


def _clear_wit_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.startswith("WIT_"):
            monkeypatch.delenv(name)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)


def _set_valid_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[str, str]:
    _clear_wit_environment(monkeypatch)
    sonarr_credential = "doctor-sonarr-" + ("x" * 24)
    jellyfin_credential = "doctor-jellyfin-" + ("x" * 24)
    values = {
        "WIT_SONARR_URL": "http://127.0.0.1:8989",
        "WIT_SONARR_API_KEY": sonarr_credential,
        "WIT_SONARR_ROOT_FOLDER_ID": "1",
        "WIT_SONARR_QUALITY_PROFILE_ID": "2",
        "WIT_JELLYFIN_URL": "http://127.0.0.1:8096",
        "WIT_JELLYFIN_API_KEY": jellyfin_credential,
        "WIT_SEERR_URL": "http://127.0.0.1:5055",
        "WIT_STATE_DIR": str(tmp_path / "state"),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return sonarr_credential, jellyfin_credential


def _service_result(
    service: ServiceName,
    state: ServiceHealthState,
    summary: str,
    *,
    version: str | None = None,
) -> ServiceHealthResult:
    return ServiceHealthResult(
        service=service,
        state=state,
        summary=summary,
        version=version,
    )


def _doctor_report(
    *,
    path_state: LocalPathState,
    path_summary: str,
    services: tuple[ServiceHealthResult, ...],
) -> DoctorReport:
    return DoctorReport(
        local_paths=(
            LocalPathCheck(
                name=LocalPathName.STATE_DIRECTORY,
                state=path_state,
                summary=path_summary,
            ),
        ),
        services=services,
    )


def test_doctor_reports_healthy_diagnostics_and_returns_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credentials = _set_valid_environment(monkeypatch, tmp_path)
    report = _doctor_report(
        path_state=LocalPathState.READY,
        path_summary="exists with read, write, and search access",
        services=(
            _service_result(
                ServiceName.SONARR,
                ServiceHealthState.HEALTHY,
                "Sonarr is healthy",
                version="4.0.16.2944",
            ),
            _service_result(
                ServiceName.JELLYFIN,
                ServiceHealthState.HEALTHY,
                "Jellyfin is healthy",
                version="10.11.11",
            ),
            _service_result(
                ServiceName.SEERR,
                ServiceHealthState.HEALTHY,
                "Seerr is healthy",
                version="3.3.0",
            ),
        ),
    )

    async def fake_run_doctor(settings: WitSettings) -> DoctorReport:
        assert settings.state_dir == tmp_path / "state"
        return report

    monkeypatch.setattr(cli, "run_doctor", fake_run_doctor)

    result = runner.invoke(cli.app, ["doctor"])

    assert result.exit_code == 0, result.output
    assert "Configuration: OK - required settings are valid" in result.output
    assert "State directory: OK - exists with read, write, and search access" in result.output
    assert "Sonarr: OK - Sonarr is healthy (version 4.0.16.2944)" in result.output
    assert "Jellyfin: OK - Jellyfin is healthy (version 10.11.11)" in result.output
    assert "Seerr: OK - Seerr is healthy (version 3.3.0)" in result.output
    assert "Overall: OK - all required checks passed" in result.output
    assert all(credential not in result.output for credential in credentials)


def test_doctor_json_emits_one_versioned_failure_envelope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credentials = _set_valid_environment(monkeypatch, tmp_path)
    report = _doctor_report(
        path_state=LocalPathState.READY,
        path_summary="exists with read, write, and search access",
        services=(
            _service_result(
                ServiceName.SONARR,
                ServiceHealthState.HEALTHY,
                "Sonarr is healthy",
                version="4.0.16.2944",
            ),
            _service_result(
                ServiceName.JELLYFIN,
                ServiceHealthState.UNAVAILABLE,
                "Jellyfin is unavailable",
            ),
            _service_result(
                ServiceName.SEERR,
                ServiceHealthState.HEALTHY,
                "Seerr is healthy",
            ),
        ),
    )

    async def fake_run_doctor(settings: WitSettings) -> DoctorReport:
        del settings
        return report

    monkeypatch.setattr(cli, "run_doctor", fake_run_doctor)

    result = runner.invoke(cli.app, ["doctor", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert list(payload) == [
        "schema_version",
        "command",
        "success",
        "data",
        "warnings",
        "errors",
    ]
    assert payload["schema_version"] == 1
    assert payload["command"] == "doctor"
    assert payload["success"] is False
    assert payload["warnings"] == []
    assert payload["errors"] == [
        {
            "code": "doctor-checks-failed",
            "message": "One or more required local-path or service checks failed",
        }
    ]
    assert payload["data"]["configuration"] == {"valid": True}
    assert payload["data"]["state"] == "failed"
    assert payload["data"]["local_paths"] == [
        {
            "name": "state-directory",
            "state": "ready",
            "summary": "exists with read, write, and search access",
            "guidance": None,
        }
    ]
    assert [service["name"] for service in payload["data"]["services"]] == [
        "sonarr",
        "jellyfin",
        "seerr",
    ]
    assert payload["data"]["services"][1] == {
        "name": "jellyfin",
        "state": "unavailable",
        "summary": "Jellyfin is unavailable",
        "version": None,
        "guidance": "Verify WIT_JELLYFIN_URL and that Jellyfin is running.",
    }
    assert "Configuration: OK" not in result.stdout
    assert "Overall: FAILED" not in result.stdout
    assert all(credential not in result.stdout for credential in credentials)


def test_doctor_reports_partial_service_health_independently(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_valid_environment(monkeypatch, tmp_path)
    report = _doctor_report(
        path_state=LocalPathState.READY,
        path_summary="exists with read, write, and search access",
        services=(
            _service_result(
                ServiceName.SONARR,
                ServiceHealthState.HEALTHY,
                "Sonarr is healthy",
            ),
            _service_result(
                ServiceName.JELLYFIN,
                ServiceHealthState.UNAVAILABLE,
                "Jellyfin is unavailable",
            ),
            _service_result(
                ServiceName.SEERR,
                ServiceHealthState.HEALTHY,
                "Seerr is healthy",
            ),
        ),
    )

    async def fake_run_doctor(settings: WitSettings) -> DoctorReport:
        del settings
        return report

    monkeypatch.setattr(cli, "run_doctor", fake_run_doctor)

    result = runner.invoke(cli.app, ["doctor"])

    assert result.exit_code == 1
    assert "Sonarr: OK - Sonarr is healthy" in result.output
    assert "Jellyfin: FAILED - Jellyfin is unavailable" in result.output
    assert "Verify WIT_JELLYFIN_URL and that Jellyfin is running." in result.output
    assert "Seerr: OK - Seerr is healthy" in result.output
    assert result.output.index("Sonarr:") < result.output.index("Jellyfin:")
    assert result.output.index("Jellyfin:") < result.output.index("Seerr:")
    assert "Overall: FAILED - one or more required checks failed" in result.output


def test_doctor_reports_failed_paths_and_services_with_actions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credentials = _set_valid_environment(monkeypatch, tmp_path)
    report = _doctor_report(
        path_state=LocalPathState.MISSING,
        path_summary="does not exist",
        services=(
            _service_result(
                ServiceName.SONARR,
                ServiceHealthState.UNAUTHORISED,
                "Sonarr health check was not authorised",
            ),
            _service_result(
                ServiceName.JELLYFIN,
                ServiceHealthState.UNAVAILABLE,
                "Jellyfin is unavailable",
            ),
            _service_result(
                ServiceName.SEERR,
                ServiceHealthState.UNHEALTHY,
                "Seerr returned an invalid health response",
            ),
        ),
    )

    async def fake_run_doctor(settings: WitSettings) -> DoctorReport:
        del settings
        return report

    monkeypatch.setattr(cli, "run_doctor", fake_run_doctor)

    result = runner.invoke(cli.app, ["doctor"])

    assert result.exit_code == 1
    assert "State directory: FAILED - does not exist" in result.output
    assert "Create WIT_STATE_DIR with owner-only permissions" in result.output
    assert "Verify WIT_SONARR_API_KEY and its service permissions." in result.output
    assert "Verify WIT_JELLYFIN_URL and that Jellyfin is running." in result.output
    assert "Inspect the Seerr dashboard and logs for health details." in result.output
    assert all(credential not in result.output for credential in credentials)


def test_doctor_rejects_invalid_configuration_before_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_wit_environment(monkeypatch)
    diagnostics_called = False

    async def fake_run_doctor(settings: WitSettings) -> DoctorReport:
        nonlocal diagnostics_called
        del settings
        diagnostics_called = True
        raise AssertionError("diagnostics must not run with invalid configuration")

    monkeypatch.setattr(cli, "run_doctor", fake_run_doctor)

    result = runner.invoke(cli.app, ["doctor"])

    assert result.exit_code == 1
    assert not diagnostics_called
    assert "Configuration: FAILED - invalid Wit configuration" in result.output
    assert "set the required WIT_* values or WIT_CONFIG_FILE" in result.output
    assert "Sonarr:" not in result.output
    assert "Overall:" not in result.output


def test_state_directory_check_is_read_only_and_reports_access(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"

    missing = check_state_directory(state_dir)

    assert missing.state is LocalPathState.MISSING
    assert not state_dir.exists()

    state_dir.mkdir()
    ready = check_state_directory(state_dir)

    assert ready.state is LocalPathState.READY

    def deny_write(path: Path, mode: int) -> bool:
        assert path == state_dir
        return mode != os.W_OK

    monkeypatch.setattr(os, "access", deny_write)

    inaccessible = check_state_directory(state_dir)

    assert inaccessible.state is LocalPathState.INACCESSIBLE
    assert inaccessible.summary == "does not grant the current user write access"


def test_doctor_application_service_collects_every_health_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_valid_environment(monkeypatch, tmp_path)
    (tmp_path / "state").mkdir()
    settings = load_settings()
    called: list[ServiceName] = []

    async def check_sonarr(config: WitSettings) -> ServiceHealthResult:
        assert config is settings
        called.append(ServiceName.SONARR)
        return _service_result(
            ServiceName.SONARR,
            ServiceHealthState.UNAVAILABLE,
            "Sonarr is unavailable",
        )

    async def check_jellyfin(config: WitSettings) -> ServiceHealthResult:
        assert config is settings
        called.append(ServiceName.JELLYFIN)
        return _service_result(
            ServiceName.JELLYFIN,
            ServiceHealthState.HEALTHY,
            "Jellyfin is healthy",
        )

    async def check_seerr(config: WitSettings) -> ServiceHealthResult:
        assert config is settings
        called.append(ServiceName.SEERR)
        return _service_result(
            ServiceName.SEERR,
            ServiceHealthState.UNHEALTHY,
            "Seerr returned an invalid health response",
        )

    monkeypatch.setattr(doctor_module, "_check_sonarr", check_sonarr)
    monkeypatch.setattr(doctor_module, "_check_jellyfin", check_jellyfin)
    monkeypatch.setattr(doctor_module, "_check_seerr", check_seerr)

    report = asyncio.run(doctor_module.run_doctor(settings))

    assert set(called) == {ServiceName.SONARR, ServiceName.JELLYFIN, ServiceName.SEERR}
    assert [result.service for result in report.services] == [
        ServiceName.SONARR,
        ServiceName.JELLYFIN,
        ServiceName.SEERR,
    ]
    assert not report.successful
