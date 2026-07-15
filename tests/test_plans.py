"""Tests for the immutable, versioned download-plan contract."""

from __future__ import annotations

import json
from datetime import datetime
from typing import cast

import pytest
from pydantic import ValidationError

from wit.plans import (
    DOWNLOAD_PLAN_SCHEMA_VERSION,
    DownloadPlan,
    InvalidDownloadPlanError,
    PlannedEpisode,
)


def _plan() -> DownloadPlan:
    return DownloadPlan(
        schema_version=DOWNLOAD_PLAN_SCHEMA_VERSION,
        plan_id="plan-20250110-001",
        created_at=datetime.fromisoformat("2025-01-10T07:00:00-05:00"),
        show_title="Clockwork Harbor",
        show_year=2024,
        tvmaze_id=101,
        tvdb_id=201,
        selector_summary="first 2 aired regular episodes",
        episodes=(
            PlannedEpisode(season_number=1, episode_number=2, title="Turning Tide"),
            PlannedEpisode(season_number=1, episode_number=1, title="First Light"),
        ),
    )


def _payload(plan: DownloadPlan | None = None) -> dict[str, object]:
    return cast(dict[str, object], json.loads((plan or _plan()).to_json()))


def test_json_round_trip_is_strict_complete_and_deterministic() -> None:
    plan = _plan()

    encoded = plan.to_json()
    restored = DownloadPlan.from_json(encoded)
    payload = _payload(restored)

    assert restored == plan
    assert restored.to_json() == encoded
    assert payload == {
        "schema_version": 1,
        "plan_id": "plan-20250110-001",
        "created_at": "2025-01-10T12:00:00Z",
        "show_title": "Clockwork Harbor",
        "show_year": 2024,
        "tvmaze_id": 101,
        "tvdb_id": 201,
        "selector_summary": "first 2 aired regular episodes",
        "episodes": [
            {"season_number": 1, "episode_number": 1, "title": "First Light"},
            {"season_number": 1, "episode_number": 2, "title": "Turning Tide"},
        ],
    }


def test_accepts_utf8_json_bytes_and_an_explicit_unknown_show_year() -> None:
    payload = _payload()
    payload["show_title"] = "Île Mystère"
    payload["show_year"] = None

    restored = DownloadPlan.from_json(json.dumps(payload, ensure_ascii=False).encode())

    assert restored.show_title == "Île Mystère"
    assert restored.show_year is None


def test_models_are_deeply_immutable() -> None:
    plan = _plan()
    plan_field = "show_title"
    episode_field = "title"

    with pytest.raises(ValidationError, match="Instance is frozen"):
        setattr(plan, plan_field, "Changed")
    with pytest.raises(ValidationError, match="Instance is frozen"):
        setattr(plan.episodes[0], episode_field, "Changed")

    assert isinstance(plan.episodes, tuple)


def test_human_rendering_is_deterministic_and_includes_episode_count() -> None:
    plan = _plan()

    expected = "\n".join(
        (
            "Download plan: plan-20250110-001",
            "Schema version: 1",
            "Created: 2025-01-10T12:00:00Z",
            "Show: Clockwork Harbor (2024)",
            "TVmaze ID: 101",
            "TVDB ID: 201",
            "Selector: first 2 aired regular episodes",
            "Selected episodes (2):",
            "  S01E01  First Light",
            "  S01E02  Turning Tide",
        )
    )

    assert plan.episode_count == 2
    assert plan.render() == expected
    assert plan.render() == expected


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("schema_version", 2),
        ("schema_version", "1"),
        ("tvmaze_id", "101"),
        ("tvdb_id", True),
        ("show_year", 0),
        ("created_at", "2025-01-10T12:00:00"),
    ],
)
def test_strict_deserialisation_rejects_wrong_versions_types_and_values(
    field: str,
    invalid_value: object,
) -> None:
    payload = _payload()
    payload[field] = invalid_value

    with pytest.raises(InvalidDownloadPlanError, match="schema version 1"):
        DownloadPlan.from_json(json.dumps(payload))


@pytest.mark.parametrize("missing_field", ["schema_version", "plan_id", "show_year", "episodes"])
def test_strict_deserialisation_rejects_missing_schema_fields(missing_field: str) -> None:
    payload = _payload()
    del payload[missing_field]

    with pytest.raises(InvalidDownloadPlanError):
        DownloadPlan.from_json(json.dumps(payload))


@pytest.mark.parametrize("payload", ["", "not-json", "[]", "{}"])
def test_rejects_malformed_or_non_plan_json(payload: str) -> None:
    with pytest.raises(InvalidDownloadPlanError):
        DownloadPlan.from_json(payload)


@pytest.mark.parametrize(
    ("extra_field", "extra_value"),
    [
        ("api_key", "redacted"),
        ("cookies", {"session": "redacted"}),
        ("headers", {"Authorization": "redacted"}),
        ("media_path", "/srv/television"),
        ("raw_response", {"unexpected": True}),
    ],
)
def test_rejects_accidental_secret_service_and_path_fields_without_echoing_values(
    extra_field: str,
    extra_value: object,
) -> None:
    payload = _payload()
    payload[extra_field] = extra_value

    with pytest.raises(InvalidDownloadPlanError) as captured:
        DownloadPlan.from_json(json.dumps(payload))

    assert "redacted" not in str(captured.value)
    assert "/srv/television" not in str(captured.value)


def test_rejects_accidental_fields_inside_episode_records() -> None:
    payload = _payload()
    episodes = cast(list[dict[str, object]], payload["episodes"])
    episodes[0]["headers"] = {"Authorization": "redacted"}

    with pytest.raises(InvalidDownloadPlanError):
        DownloadPlan.from_json(json.dumps(payload))


def test_rejects_empty_and_duplicate_episode_selections() -> None:
    empty_payload = _payload()
    empty_payload["episodes"] = []
    duplicate_payload = _payload()
    episodes = cast(list[dict[str, object]], duplicate_payload["episodes"])
    episodes[1]["episode_number"] = 1

    with pytest.raises(InvalidDownloadPlanError, match="schema version 1"):
        DownloadPlan.from_json(json.dumps(empty_payload))
    with pytest.raises(InvalidDownloadPlanError, match="schema version 1"):
        DownloadPlan.from_json(json.dumps(duplicate_payload))
