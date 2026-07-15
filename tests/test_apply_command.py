"""CLI tests for safe stored-plan apply confirmation and outcome rendering."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

import wit.cli as cli
from wit.apply import (
    ApplyDiscrepancyConfirmation,
    ApplyPlanDiscrepancy,
    ApplyPlanDiscrepancyKind,
    ApplyPlanMismatchError,
    ApplyPlanResult,
)
from wit.clients import SonarrCommandState, SonarrCommandStatus, SonarrSeries
from wit.config import WitSettings
from wit.plan_store import PlanStore
from wit.plans import DOWNLOAD_PLAN_SCHEMA_VERSION, DownloadPlan, PlannedEpisode

runner = CliRunner()
_NOW = datetime(2025, 1, 12, 12, tzinfo=UTC)


def _set_valid_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[str, str]:
    for name in tuple(os.environ):
        if name.startswith("WIT_"):
            monkeypatch.delenv(name)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setattr(cli, "_utc_now", lambda: _NOW)

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


def _plan(*, created_at: datetime | None = None) -> DownloadPlan:
    return DownloadPlan(
        schema_version=DOWNLOAD_PLAN_SCHEMA_VERSION,
        plan_id="plan-cli-apply",
        created_at=created_at or datetime(2025, 1, 10, 12, tzinfo=UTC),
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


def _apply_result(
    plan: DownloadPlan,
    *,
    series_created: bool = False,
    applied: tuple[int, ...] = (101, 102),
    skipped_file: tuple[int, ...] = (),
    skipped_queue: tuple[int, ...] = (),
    rejected: tuple[int, ...] = (),
) -> ApplyPlanResult:
    command = (
        SonarrCommandStatus(
            command_id=501,
            state=SonarrCommandState.QUEUED,
        )
        if applied
        else None
    )
    return ApplyPlanResult(
        plan_id=plan.plan_id,
        series=SonarrSeries(
            sonarr_id=73 if series_created else 42,
            tvdb_id=plan.tvdb_id,
            title=plan.show_title,
            year=plan.show_year or 0,
        ),
        series_created=series_created,
        applied_episode_ids=applied,
        skipped_file_episode_ids=skipped_file,
        skipped_queue_episode_ids=skipped_queue,
        rejected_episode_ids=rejected,
        command=command,
    )


def _fake_apply_signature(
    calls: list[tuple[WitSettings, DownloadPlan, datetime, bool]],
    result_factory: Callable[[DownloadPlan], ApplyPlanResult],
) -> Callable[..., object]:
    async def fake_apply(
        settings: WitSettings,
        loaded_plan: DownloadPlan,
        *,
        as_of: datetime,
        allow_stale: bool,
        confirm_discrepancies: ApplyDiscrepancyConfirmation,
    ) -> ApplyPlanResult:
        del confirm_discrepancies
        calls.append((settings, loaded_plan, as_of, allow_stale))
        return result_factory(loaded_plan)

    return fake_apply


def test_apply_yes_loads_the_plan_and_renders_separate_outcome_counts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credentials = _set_valid_environment(monkeypatch, tmp_path)
    plan = _plan()
    PlanStore(tmp_path / "state").save(plan)
    calls: list[tuple[WitSettings, DownloadPlan, datetime, bool]] = []
    monkeypatch.setattr(
        cli,
        "_apply_plan_through_sonarr",
        _fake_apply_signature(calls, _apply_result),
    )

    result = runner.invoke(cli.app, ["apply", plan.plan_id, "--yes"])

    assert result.exit_code == 0, result.output
    assert "Download plan: plan-cli-apply" in result.output
    assert "S01E01  First Light" in result.output
    assert "S01E02  Turning Tide" in result.output
    assert "Apply this stored plan through Sonarr?" not in result.output
    assert "Processed plan plan-cli-apply using existing Sonarr series 42." in result.output
    assert "Applied: 2" in result.output
    assert "Skipped-file: 0" in result.output
    assert "Skipped-queue: 0" in result.output
    assert "Rejected: 0" in result.output
    assert result.output.endswith("Sonarr command ID: 501 (queued)\n")
    assert len(calls) == 1
    settings, loaded_plan, as_of, allow_stale = calls[0]
    assert settings.sonarr.root_folder_id == 7
    assert settings.sonarr.quality_profile_id == 8
    assert loaded_plan == plan
    assert as_of == _NOW
    assert not allow_stale
    assert all(credential not in result.output for credential in credentials)


def test_apply_json_emits_one_structured_result_without_progress_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credentials = _set_valid_environment(monkeypatch, tmp_path)
    plan = _plan()
    PlanStore(tmp_path / "state").save(plan)
    calls: list[tuple[WitSettings, DownloadPlan, datetime, bool]] = []
    monkeypatch.setattr(
        cli,
        "_apply_plan_through_sonarr",
        _fake_apply_signature(calls, _apply_result),
    )

    result = runner.invoke(cli.app, ["apply", plan.plan_id, "--yes", "--json"])

    assert result.exit_code == 0, result.output
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
    assert payload["command"] == "apply"
    assert payload["success"] is True
    assert payload["warnings"] == []
    assert payload["errors"] == []
    assert payload["data"]["plan"]["plan_id"] == plan.plan_id
    assert payload["data"]["result"] == {
        "series": {
            "sonarr_id": 42,
            "tvdb_id": 31415,
            "title": "Clockwork Harbor",
            "year": 2024,
            "created": False,
        },
        "outcomes": {
            "applied": {"count": 2, "episode_ids": [101, 102]},
            "skipped_file": {"count": 0, "episode_ids": []},
            "skipped_queue": {"count": 0, "episode_ids": []},
            "rejected": {"count": 0, "episode_ids": []},
        },
        "command": {"command_id": 501, "state": "queued"},
        "discrepancies": [],
    }
    assert "Download plan:" not in result.stdout
    assert "Processed plan" not in result.stdout
    assert "Applied:" not in result.stdout
    assert all(credential not in result.stdout for credential in credentials)
    assert len(calls) == 1


def test_apply_accepts_interactive_confirmation_before_contacting_sonarr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_valid_environment(monkeypatch, tmp_path)
    plan = _plan()
    PlanStore(tmp_path / "state").save(plan)
    calls: list[tuple[WitSettings, DownloadPlan, datetime, bool]] = []
    monkeypatch.setattr(cli, "_is_interactive_input", lambda: True)
    monkeypatch.setattr(
        cli,
        "_apply_plan_through_sonarr",
        _fake_apply_signature(
            calls,
            lambda loaded_plan: _apply_result(loaded_plan, series_created=True),
        ),
    )

    result = runner.invoke(cli.app, ["apply", plan.plan_id], input="y\n")

    assert result.exit_code == 0, result.output
    assert "Apply this stored plan through Sonarr? [y/N]: y" in result.output
    assert "using newly added Sonarr series 73" in result.output
    assert len(calls) == 1


def test_apply_requires_yes_when_standard_input_is_not_interactive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_valid_environment(monkeypatch, tmp_path)
    plan = _plan()
    PlanStore(tmp_path / "state").save(plan)
    called = False

    async def unexpected_apply(*args: object, **kwargs: object) -> ApplyPlanResult:
        nonlocal called
        del args, kwargs
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

    async def unexpected_apply(*args: object, **kwargs: object) -> ApplyPlanResult:
        nonlocal called
        del args, kwargs
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

    async def unexpected_apply(*args: object, **kwargs: object) -> ApplyPlanResult:
        nonlocal called
        del args, kwargs
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


def test_apply_rejects_stale_plan_with_counts_before_sonarr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_valid_environment(monkeypatch, tmp_path)
    plan = _plan(created_at=datetime(2025, 1, 1, 12, tzinfo=UTC))
    PlanStore(tmp_path / "state").save(plan)
    called = False

    async def unexpected_apply(*args: object, **kwargs: object) -> ApplyPlanResult:
        nonlocal called
        del args, kwargs
        called = True
        raise AssertionError("Sonarr must not be contacted for a stale plan")

    monkeypatch.setattr(cli, "_apply_plan_through_sonarr", unexpected_apply)

    result = runner.invoke(cli.app, ["apply", plan.plan_id, "--yes"])

    assert result.exit_code == 1
    assert "older than the 7-day apply limit" in result.output
    assert "Applied: 0" in result.output
    assert "Skipped-file: 0" in result.output
    assert "Skipped-queue: 0" in result.output
    assert "Rejected: 2" in result.output
    assert not called


def test_allow_stale_is_an_explicit_override_not_a_confirmation_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_valid_environment(monkeypatch, tmp_path)
    plan = _plan(created_at=datetime(2025, 1, 1, 12, tzinfo=UTC))
    PlanStore(tmp_path / "state").save(plan)
    calls: list[tuple[WitSettings, DownloadPlan, datetime, bool]] = []
    monkeypatch.setattr(
        cli,
        "_apply_plan_through_sonarr",
        _fake_apply_signature(calls, _apply_result),
    )

    without_yes = runner.invoke(cli.app, ["apply", plan.plan_id, "--allow-stale"])
    with_yes = runner.invoke(
        cli.app,
        ["apply", plan.plan_id, "--yes", "--allow-stale"],
    )

    assert without_yes.exit_code == 1
    assert "non-interactive use requires --yes" in without_yes.output
    assert with_yes.exit_code == 0, with_yes.output
    assert len(calls) == 1
    assert calls[0][3]


def test_no_op_apply_reports_skips_and_does_not_claim_a_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_valid_environment(monkeypatch, tmp_path)
    plan = _plan()
    PlanStore(tmp_path / "state").save(plan)
    calls: list[tuple[WitSettings, DownloadPlan, datetime, bool]] = []
    monkeypatch.setattr(
        cli,
        "_apply_plan_through_sonarr",
        _fake_apply_signature(
            calls,
            lambda loaded_plan: _apply_result(
                loaded_plan,
                applied=(),
                skipped_file=(101,),
                skipped_queue=(102,),
            ),
        ),
    )

    result = runner.invoke(cli.app, ["apply", plan.plan_id, "--yes"])

    assert result.exit_code == 0, result.output
    assert "Applied: 0" in result.output
    assert "Skipped-file: 1" in result.output
    assert "Skipped-queue: 1" in result.output
    assert "Rejected: 0" in result.output
    assert result.output.endswith("Sonarr command ID: none (no actionable episodes)\n")


def test_rejected_episode_is_reported_and_returns_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_valid_environment(monkeypatch, tmp_path)
    plan = _plan()
    PlanStore(tmp_path / "state").save(plan)
    calls: list[tuple[WitSettings, DownloadPlan, datetime, bool]] = []
    monkeypatch.setattr(
        cli,
        "_apply_plan_through_sonarr",
        _fake_apply_signature(
            calls,
            lambda loaded_plan: _apply_result(
                loaded_plan,
                applied=(101,),
                rejected=(102,),
            ),
        ),
    )

    result = runner.invoke(cli.app, ["apply", plan.plan_id, "--yes"])

    assert result.exit_code == 1
    assert "Applied: 1" in result.output
    assert "Rejected: 1" in result.output
    assert "Sonarr command ID: 501 (queued)" in result.output


def _mismatch_fake(
    *,
    expected_confirmation: bool,
) -> Callable[..., object]:
    discrepancy = ApplyPlanDiscrepancy(
        kind=ApplyPlanDiscrepancyKind.EPISODE_TITLE,
        summary='S01E01: stored title "First Light"; Sonarr "Arrival".',
    )

    async def fake_apply(
        settings: WitSettings,
        loaded_plan: DownloadPlan,
        *,
        as_of: datetime,
        allow_stale: bool,
        confirm_discrepancies: ApplyDiscrepancyConfirmation,
    ) -> ApplyPlanResult:
        del settings, as_of, allow_stale
        confirmed = confirm_discrepancies((discrepancy,))
        assert confirmed is expected_confirmation
        if not confirmed:
            raise ApplyPlanMismatchError(
                (discrepancy,),
                skipped_file_count=0,
                skipped_queue_count=0,
                rejected_count=loaded_plan.episode_count,
            )
        return _apply_result(loaded_plan)

    return fake_apply


def test_material_metadata_difference_prompts_for_a_second_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_valid_environment(monkeypatch, tmp_path)
    plan = _plan()
    PlanStore(tmp_path / "state").save(plan)
    monkeypatch.setattr(cli, "_is_interactive_input", lambda: True)
    monkeypatch.setattr(
        cli,
        "_apply_plan_through_sonarr",
        _mismatch_fake(expected_confirmation=True),
    )

    result = runner.invoke(cli.app, ["apply", plan.plan_id], input="y\ny\n")

    assert result.exit_code == 0, result.output
    assert "Current Sonarr metadata materially differs" in result.output
    assert 'stored title "First Light"; Sonarr "Arrival"' in result.output
    assert "Continue using these current Sonarr mappings? [y/N]: y" in result.output


def test_noninteractive_metadata_difference_requires_explicit_mismatch_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_valid_environment(monkeypatch, tmp_path)
    plan = _plan()
    PlanStore(tmp_path / "state").save(plan)
    monkeypatch.setattr(cli, "_is_interactive_input", lambda: False)
    monkeypatch.setattr(
        cli,
        "_apply_plan_through_sonarr",
        _mismatch_fake(expected_confirmation=False),
    )

    result = runner.invoke(cli.app, ["apply", plan.plan_id, "--yes"])

    assert result.exit_code == 1
    assert "Reconfirmation requires an interactive terminal or --allow-mismatch." in result.output
    assert "no episode monitoring or search was performed" in result.output
    assert "Applied: 0" in result.output
    assert "Rejected: 2" in result.output


def test_allow_mismatch_explicitly_reconfirms_without_a_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_valid_environment(monkeypatch, tmp_path)
    plan = _plan()
    PlanStore(tmp_path / "state").save(plan)
    monkeypatch.setattr(cli, "_is_interactive_input", lambda: False)
    monkeypatch.setattr(
        cli,
        "_apply_plan_through_sonarr",
        _mismatch_fake(expected_confirmation=True),
    )

    result = runner.invoke(
        cli.app,
        ["apply", plan.plan_id, "--yes", "--allow-mismatch"],
    )

    assert result.exit_code == 0, result.output
    assert "Metadata differences explicitly confirmed by --allow-mismatch." in result.output
    assert "Continue using these current Sonarr mappings?" not in result.output
