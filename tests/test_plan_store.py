"""Tests for secure XDG download-plan persistence."""

from __future__ import annotations

import errno
import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from wit.plan_store import (
    InvalidPlanIdentifierError,
    InvalidStoredPlanError,
    PlanStore,
    PlanStoreError,
    StoredPlanNotFoundError,
    UnsafePlanStoreError,
)
from wit.plans import DOWNLOAD_PLAN_SCHEMA_VERSION, DownloadPlan, PlannedEpisode


def _plan(
    plan_id: str = "plan-20250110-001",
    *,
    show_title: str = "Clockwork Harbor",
) -> DownloadPlan:
    return DownloadPlan(
        schema_version=DOWNLOAD_PLAN_SCHEMA_VERSION,
        plan_id=plan_id,
        created_at=datetime(2025, 1, 10, 12, 0, tzinfo=UTC),
        show_title=show_title,
        show_year=2024,
        tvmaze_id=101,
        tvdb_id=201,
        selector_summary="first 2 aired regular episodes",
        episodes=(
            PlannedEpisode(season_number=1, episode_number=1, title="First Light"),
            PlannedEpisode(season_number=1, episode_number=2, title="Turning Tide"),
        ),
    )


def _prepare_raw_store(store: PlanStore) -> None:
    store.plans_dir.mkdir(mode=0o700, parents=True)
    store.state_dir.chmod(0o700)
    store.plans_dir.chmod(0o700)


def _write_raw_plan(store: PlanStore, plan_id: str, payload: str) -> Path:
    _prepare_raw_store(store)
    path = store.plans_dir / f"{plan_id}.json"
    path.write_text(payload, encoding="utf-8")
    path.chmod(0o600)
    return path


def test_default_store_uses_xdg_state_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    xdg_state_home = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg_state_home))

    store = PlanStore()

    assert store.state_dir == xdg_state_home / "wit"
    assert store.plans_dir == xdg_state_home / "wit" / "plans"


def test_default_store_falls_back_to_home_local_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(home))

    store = PlanStore()

    assert store.state_dir == home / ".local" / "state" / "wit"
    assert store.plans_dir == home / ".local" / "state" / "wit" / "plans"


def test_save_creates_private_directories_and_file_then_loads_round_trip(
    tmp_path: Path,
) -> None:
    store = PlanStore(tmp_path / "state" / "wit")
    plan = _plan()
    previous_umask = os.umask(0)
    try:
        stored_path = store.save(plan)
    finally:
        os.umask(previous_umask)

    assert stored_path == store.plans_dir / f"{plan.plan_id}.json"
    assert stat.S_IMODE(store.state_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.plans_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(stored_path.stat().st_mode) == 0o600
    assert stored_path.read_text(encoding="utf-8") == plan.to_json()
    assert store.load(plan.plan_id) == plan
    assert not tuple(store.plans_dir.glob(".plan-*.tmp"))


def test_missing_store_lists_nothing_and_load_does_not_create_it(tmp_path: Path) -> None:
    store = PlanStore(tmp_path / "missing" / "wit")

    assert store.list_plans() == ()
    with pytest.raises(StoredPlanNotFoundError, match="not found"):
        store.load("safe-plan-id")

    assert not store.state_dir.exists()


@pytest.mark.parametrize(
    "plan_id",
    [
        "",
        ".",
        "..",
        "../escape",
        "/absolute",
        "nested/plan",
        r"nested\plan",
        "plan.json",
        "-leading-dash",
        "contains space",
        "a" * 129,
    ],
)
def test_load_rejects_unsafe_plan_identifiers_without_filesystem_access(
    tmp_path: Path,
    plan_id: str,
) -> None:
    store = PlanStore(tmp_path / "state" / "wit")

    with pytest.raises(InvalidPlanIdentifierError) as captured:
        store.load(plan_id)

    if plan_id:
        assert plan_id not in str(captured.value)
    assert not store.state_dir.exists()


@pytest.mark.parametrize(
    "state_dir",
    [Path("relative/state"), Path("/"), Path("/tmp/parent/../state")],
)
def test_rejects_unsafe_state_directories(state_dir: Path) -> None:
    with pytest.raises(UnsafePlanStoreError):
        PlanStore(state_dir)


def test_listing_returns_valid_plans_in_id_order_and_ignores_unrelated_entries(
    tmp_path: Path,
) -> None:
    store = PlanStore(tmp_path / "state" / "wit")
    later = _plan("z-plan")
    earlier = _plan("a-plan")
    store.save(later)
    store.save(earlier)

    (store.plans_dir / "operator-notes.txt").write_text("unrelated", encoding="utf-8")
    (store.plans_dir / "not a plan.json").write_text("unrelated", encoding="utf-8")
    (store.plans_dir / ".plan-abandoned.tmp").write_text("partial", encoding="utf-8")
    (store.plans_dir / "archive").mkdir()

    listed = store.list_plans()

    assert listed == (earlier, later)


def test_load_and_listing_reject_corrupt_or_unsupported_plan_json(tmp_path: Path) -> None:
    corrupt_store = PlanStore(tmp_path / "corrupt" / "wit")
    _write_raw_plan(corrupt_store, "corrupt-plan", "{not-json")

    with pytest.raises(InvalidStoredPlanError, match="corrupt or uses an unsupported"):
        corrupt_store.load("corrupt-plan")
    with pytest.raises(InvalidStoredPlanError, match="corrupt or uses an unsupported"):
        corrupt_store.list_plans()

    unsupported_store = PlanStore(tmp_path / "unsupported" / "wit")
    payload = cast(dict[str, object], json.loads(_plan("unsupported-plan").to_json()))
    payload["schema_version"] = 2
    _write_raw_plan(unsupported_store, "unsupported-plan", json.dumps(payload))

    with pytest.raises(InvalidStoredPlanError, match="unsupported schema version"):
        unsupported_store.load("unsupported-plan")


def test_load_rejects_plan_whose_embedded_id_does_not_match_filename(tmp_path: Path) -> None:
    store = PlanStore(tmp_path / "state" / "wit")
    _write_raw_plan(store, "expected-id", _plan("different-id").to_json())

    with pytest.raises(InvalidStoredPlanError, match="does not match"):
        store.load("expected-id")


def test_plan_file_symlinks_are_never_loaded_listed_or_replaced(tmp_path: Path) -> None:
    store = PlanStore(tmp_path / "state" / "wit")
    _prepare_raw_store(store)
    external_path = tmp_path / "external-plan.json"
    external_payload = _plan("linked-plan").to_json()
    external_path.write_text(external_payload, encoding="utf-8")
    external_path.chmod(0o600)
    linked_path = store.plans_dir / "linked-plan.json"
    linked_path.symlink_to(external_path)

    with pytest.raises(UnsafePlanStoreError, match="symbolic link"):
        store.load("linked-plan")
    with pytest.raises(UnsafePlanStoreError, match="symbolic link"):
        store.list_plans()
    with pytest.raises(UnsafePlanStoreError, match="symbolic link"):
        store.save(_plan("linked-plan", show_title="Replacement"))

    assert linked_path.is_symlink()
    assert external_path.read_text(encoding="utf-8") == external_payload


def test_state_and_plan_directory_symlinks_are_rejected(tmp_path: Path) -> None:
    real_state = tmp_path / "real-state"
    real_state.mkdir()
    linked_state = tmp_path / "linked-state"
    linked_state.symlink_to(real_state, target_is_directory=True)

    with pytest.raises(UnsafePlanStoreError, match="symbolic link"):
        PlanStore(linked_state)

    state_dir = tmp_path / "state" / "wit"
    state_dir.mkdir(parents=True, mode=0o700)
    state_dir.chmod(0o700)
    outside_plans = tmp_path / "outside-plans"
    outside_plans.mkdir(mode=0o700)
    (state_dir / "plans").symlink_to(outside_plans, target_is_directory=True)
    store = PlanStore(state_dir)

    with pytest.raises(UnsafePlanStoreError, match="symbolic link"):
        store.save(_plan())
    assert not tuple(outside_plans.iterdir())


def test_world_writable_plan_files_are_rejected(tmp_path: Path) -> None:
    store = PlanStore(tmp_path / "state" / "wit")
    stored_path = store.save(_plan())
    stored_path.chmod(0o666)

    with pytest.raises(UnsafePlanStoreError, match="writable by other users"):
        store.load("plan-20250110-001")


def test_failed_atomic_replace_preserves_previous_plan_and_removes_temporary_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = PlanStore(tmp_path / "state" / "wit")
    original = _plan()
    replacement = _plan(show_title="Clockwork Harbor Revised")
    store.save(original)

    def fail_replace(
        source: str,
        destination: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        del source, destination, src_dir_fd, dst_dir_fd
        raise OSError(errno.EIO, "simulated replacement failure")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_replace)
        with pytest.raises(PlanStoreError, match="could not be stored safely"):
            store.save(replacement)

    assert store.load(original.plan_id) == original
    assert sorted(path.name for path in store.plans_dir.iterdir()) == [f"{original.plan_id}.json"]

    store.save(replacement)
    assert store.load(replacement.plan_id) == replacement
    assert stat.S_IMODE((store.plans_dir / f"{replacement.plan_id}.json").stat().st_mode) == 0o600
