"""CLI tests for stored-plan loading, confirmation, and apply rendering."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

import wit.cli as cli
from wit.apply import ApplyPlanResult
from wit.clients import SonarrCommandState, SonarrCommandStatus, SonarrSeries
from wit.config import WitSettings
from wit.plan_store import PlanStore
from wit.plans import DOWNLOAD_PLAN_SCHEMA_VERSION, DownloadPlan, PlannedEpisode

runner = CliRunner()


def _set_valid_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[str, str]:
    for name in tuple(os.environ):
        if name.startswith("WIT_"):
            monkeypatch.delenv(name)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)

    sonarr_credential = "apply-sonarr-" + ("x" * 24)
    jellyfin_credential = "apply-jellyfin-" + ("x" * 24)
    values = {
        "WIT_SONARR_URL": "http://127.0.0.1:8989",
        "WIT_SONARR_API_KEY": sonarr_credential,
        "WIT_SONARR_ROOT_FOLDER_ID": "7",
        "WIT_SONARR_QUALITY_PROFILE_ID": "8",
        "WIT_JELLYFIN_URL": "http://127.0.0.1:8096",
        "WIT_JELLYFIN_API_KEY": jellyfin_credential,
        "WIT_SEERR_URL": "http://127.0.0.1:5055",
        "WIT_STATE_DIR": str(tmp_path / "state"),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return sonarr_credential, jellyfin_credential


def _plan() -> DownloadPlan:
    return DownloadPlan(
        schema_version=DOWNLOAD_PLAN_SCHEMA_VERSION,
        plan_id="plan-cli-apply",
        created_at=datetime(2025, 1, 10, 12, tzinfo=UTC),
        show_title="Clockwork Harbor",
        show_year=2024,
        tvmaze_id=2718,
        tvdb_id=31415,
        selector_summary="first 2 aired regular episodes",
        episodes=(
            PlannedEpisode(season_number=1, episode_number=1, title="First Light"),
            PlannedEpisode(season_number=1, episode_number=2, title="Turning Tide"),
        ),
    )


def _apply_result(plan: DownloadPlan, *, series_created: bool = False) -> ApplyPlanResult:
    return ApplyPlanResult(
        plan_id=plan.plan_id,
        series=SonarrSeries(
            sonarr_id=73 if series_created else 42,
            tvdb_id=plan.tvdb_id,
            title=plan.show_title,
            year=plan.show_year or 0,
        ),
        series_created=series_created,
        episode_ids=(101, 102),
        command=SonarrCommandStatus(
            command_id=501,
            state=SonarrCommandState.QUEUED,
        ),
    )


def test_apply_yes_loads_the_stored_plan_and_prints_the_command_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credentials = _set_valid_environment(monkeypatch, tmp_path)
    plan = _plan()
    PlanStore(tmp_path / "state").save(plan)
    calls: list[tuple[WitSettings, DownloadPlan]] = []

    async def fake_apply(
        settings: WitSettings,
        loaded_plan: DownloadPlan,
    ) -> ApplyPlanResult:
        calls.append((settings, loaded_plan))
        return _apply_result(loaded_plan)

    monkeypatch.setattr(cli, "_apply_plan_through_sonarr", fake_apply)

    result = runner.invoke(cli.app, ["apply", plan.plan_id, "--yes"])

    assert result.exit_code == 0, result.output
    assert "Download plan: plan-cli-apply" in result.output
    assert "S01E01  First Light" in result.output
    assert "S01E02  Turning Tide" in result.output
    assert "Apply this stored plan through Sonarr?" not in result.output
    assert (
        "Applied plan plan-cli-apply: monitored 2 episodes and submitted one targeted search "
        "using existing Sonarr series 42."
    ) in result.output
    assert result.output.endswith("Sonarr command ID: 501 (queued)\n")
    assert len(calls) == 1
    settings, loaded_plan = calls[0]
    assert settings.sonarr.root_folder_id == 7
    assert settings.sonarr.quality_profile_id == 8
    assert loaded_plan == plan
    assert all(credential not in result.output for credential in credentials)


def test_apply_accepts_interactive_confirmation_before_contacting_sonarr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_valid_environment(monkeypatch, tmp_path)
    plan = _plan()
    PlanStore(tmp_path / "state").save(plan)
    applied: list[DownloadPlan] = []

    async def fake_apply(
        settings: WitSettings,
        loaded_plan: DownloadPlan,
    ) -> ApplyPlanResult:
        del settings
        applied.append(loaded_plan)
        return _apply_result(loaded_plan, series_created=True)

    monkeypatch.setattr(cli, "_is_interactive_input", lambda: True)
    monkeypatch.setattr(cli, "_apply_plan_through_sonarr", fake_apply)

    result = runner.invoke(cli.app, ["apply", plan.plan_id], input="y\n")

    assert result.exit_code == 0, result.output
    assert "Apply this stored plan through Sonarr? [y/N]: y" in result.output
    assert "using newly added Sonarr series 73" in result.output
    assert applied == [plan]


def test_apply_requires_yes_when_standard_input_is_not_interactive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_valid_environment(monkeypatch, tmp_path)
    plan = _plan()
    PlanStore(tmp_path / "state").save(plan)
    called = False

    async def unexpected_apply(
        settings: WitSettings,
        loaded_plan: DownloadPlan,
    ) -> ApplyPlanResult:
        nonlocal called
        del settings, loaded_plan
        called = True
        raise AssertionError("Sonarr must not be contacted without confirmation")

    monkeypatch.setattr(cli, "_is_interactive_input", lambda: False)
    monkeypatch.setattr(cli, "_apply_plan_through_sonarr", unexpected_apply)

    result = runner.invoke(cli.app, ["apply", plan.plan_id])

    assert result.exit_code == 1
    assert "non-interactive use requires --yes; no Sonarr changes made" in result.output
    assert "Apply this stored plan through Sonarr?" not in result.output
    assert not called


def test_apply_decline_exits_before_contacting_sonarr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_valid_environment(monkeypatch, tmp_path)
    plan = _plan()
    PlanStore(tmp_path / "state").save(plan)
    called = False

    async def unexpected_apply(
        settings: WitSettings,
        loaded_plan: DownloadPlan,
    ) -> ApplyPlanResult:
        nonlocal called
        del settings, loaded_plan
        called = True
        raise AssertionError("Sonarr must not be contacted after a declined confirmation")

    monkeypatch.setattr(cli, "_is_interactive_input", lambda: True)
    monkeypatch.setattr(cli, "_apply_plan_through_sonarr", unexpected_apply)

    result = runner.invoke(cli.app, ["apply", plan.plan_id], input="n\n")

    assert result.exit_code == 1
    assert "Apply this stored plan through Sonarr? [y/N]: n" in result.output
    assert "Apply cancelled; no Sonarr changes made." in result.output
    assert not called


def test_apply_rejects_a_corrupt_stored_plan_before_confirmation_or_sonarr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credentials = _set_valid_environment(monkeypatch, tmp_path)
    plan = _plan()
    plan_path = PlanStore(tmp_path / "state").save(plan)
    plan_path.write_text('{"schema_version": 999}', encoding="utf-8")
    called = False

    async def unexpected_apply(
        settings: WitSettings,
        loaded_plan: DownloadPlan,
    ) -> ApplyPlanResult:
        nonlocal called
        del settings, loaded_plan
        called = True
        raise AssertionError("Sonarr must not be contacted for an invalid plan")

    monkeypatch.setattr(cli, "_apply_plan_through_sonarr", unexpected_apply)

    result = runner.invoke(cli.app, ["apply", plan.plan_id, "--yes"])

    assert result.exit_code == 1
    assert "stored download plan is corrupt or uses an unsupported schema version" in result.output
    assert "Download plan:" not in result.output
    assert "Sonarr command ID:" not in result.output
    assert not called
    assert all(credential not in result.output for credential in credentials)
