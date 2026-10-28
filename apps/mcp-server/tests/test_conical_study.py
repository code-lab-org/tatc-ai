"""Staged conical study through MCP: acceptance, events, and provenance."""

import asyncio
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest
from fastmcp import Client

from src import conical_study, generic_coverage, result_store
from src import server as server_module
from src import coverage_core
from src.conical_config import ConicalConfiguration

CONFIG_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "amsr2_conical_configuration.json"
)
REFERENCE_PATH = Path(__file__).resolve().parent / "fixtures" / "amsr2_conical_reference.json"
NOW = datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc)


def _config():
    return json.loads(CONFIG_PATH.read_text())


def _reference():
    return json.loads(REFERENCE_PATH.read_text())


def _omm():
    return [_reference()["orbit"]["omm"]]


def _sat_info(_identifier):
    return {
        "norad_id": 38337,
        "name": "GCOM-W1 (SHIZUKU)",
        "omm": _omm(),
    }


class FixtureClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is None else NOW.astimezone(tz)


@pytest.fixture(autouse=True)
def _clean_store(monkeypatch):
    result_store.clear()
    monkeypatch.setattr(
        generic_coverage.celestrak_client, "get_satellite_info", _sat_info
    )
    monkeypatch.setattr(coverage_core, "datetime", FixtureClock)
    yield
    result_store.clear()


def _call(name, arguments):
    async def run():
        async with Client(server_module.mcp) as client:
            return await client.call_tool(name, arguments, raise_on_error=False)

    result = asyncio.run(run())
    text = "\n".join(getattr(part, "text", "") for part in result.content)
    payload = None
    if not result.is_error:
        try:
            payload = json.loads(text)
        except ValueError:
            payload = None
    return result.is_error, text, payload


def _begin(start="2026-10-05T00:00:00Z", end="2026-10-05T01:00:00Z"):
    error, text, payload = _call(
        "begin_access_run",
        {"satellite_identifier": "GCOM-W1", "start_time": start, "end_time": end},
    )
    assert not error, text
    return payload


def _set_region(draft_id, bbox):
    error, text, payload = _call(
        "set_access_region",
        {
            "draft_id": draft_id,
            "west_lon": bbox[0],
            "south_lat": bbox[1],
            "east_lon": bbox[2],
            "north_lat": bbox[3],
        },
    )
    assert not error, text
    return payload


def _prepare(draft_id, rows=2, columns=2, configuration=None):
    error, text, payload = _call(
        "prepare_conical_study",
        {
            "draft_id": draft_id,
            "grid_rows": rows,
            "grid_columns": columns,
            "configuration": configuration or _config(),
        },
    )
    assert not error, text
    return payload


def _finish(draft_id, revision, accept=True):
    return _call(
        "finish_conical_study",
        {
            "draft_id": draft_id,
            "accepted_setup_revision": revision,
            "accept_setup": accept,
        },
    )


def test_full_mcp_workflow_preserves_provenance_and_pages_events():
    draft = _begin()
    _set_region(draft["draft_id"], [0.0, -65.0, 10.0, -55.0])
    proposal = _prepare(draft["draft_id"])
    assert proposal["status"] == "awaiting_acceptance"
    assert "result_id" not in proposal
    assert proposal["configuration_id"] == "amsr2-gcom-w1-conical-product-sector"
    assert proposal["setup_revision"].startswith("sha256:")
    assert proposal["acceptance"]["required"] is True
    assert proposal["grid"]["cell_count"] == 4
    assert proposal["configuration"]["tatc_mapping"]["tatc_version"] == "3.6.0"
    assert proposal["source_conflicts"][0]["chosen_option_id"] == "product-75deg"
    assert any("product sector" in warning for warning in proposal["warnings"])
    assert any("24 hours" in limit for limit in proposal["limits"])

    error, text, summary = _finish(draft["draft_id"], proposal["setup_revision"])
    assert not error, text
    assert summary["result_kind"] == "conical_study"
    assert summary["accounting_method"] == "conical_events_v1"
    assert summary["summary_counts"]["event_count"] == 4
    assert summary["summary_counts"]["envelope_period_count"] == 4
    assert summary["completeness_status"] == "complete"
    assert summary["omitted_configurations"] == []
    assert summary["unresolved_dates"] == []
    assert summary["modeled_configurations"][0]["status"] == "modeled"
    assert summary["approximation_status"] == "approximate"
    assert summary["approximations"]
    assert any(
        conflict["chosen_option_id"] == "product-75deg"
        for conflict in summary["source_conflicts"]
    )
    assert summary["geometry_checks"]
    assert summary["provenance"]["setup_snapshot"]["configuration"]["evidence"]
    assert summary["metrics"]["continuous_viewing_duration_available"] is False
    assert summary["metrics"]["continuous_uncovered_duration_available"] is False
    assert "envelope" in summary["definitions"]
    assert "daily_cell" in summary["definitions"]
    assert summary["provenance"]["setup_revision"] == proposal["setup_revision"]
    assert (
        summary["provenance"]["setup_snapshot"]["configuration"]["configuration_id"]
        == "amsr2-gcom-w1-conical-product-sector"
    )
    assert summary["provenance"]["accepted_choices"][0]["chosen_option_id"] == "product-75deg"
    assert summary["storage"]["retention_hours"] == 24
    result_id = summary["result_id"]

    error, text, events = _call(
        "get_access_events", {"result_id": result_id, "offset": 0, "limit": 2}
    )
    assert not error, text
    assert events["total_events"] == 4
    assert len(events["events"]) == 2
    assert events["events"][0]["envelope_start_utc"]
    assert events["events"][0]["configuration_id"] == "amsr2-gcom-w1-conical-product-sector"
    assert events["provenance"]["setup_revision"] == proposal["setup_revision"]
    error, text, second = _call(
        "get_access_events", {"result_id": result_id, "offset": 2, "limit": 2}
    )
    assert not error, text
    assert len(second["events"]) == 2
    assert {event["event_id"] for event in events["events"]}.isdisjoint(
        {event["event_id"] for event in second["events"]}
    )

    error, text, daily = _call("get_access_daily", {"result_id": result_id})
    assert not error, text
    assert daily["total_days"] == 1
    assert daily["daily"][0]["event_count"] == 4
    assert daily["daily"][0]["observed_cell_ids"] == [0, 1, 2, 3]
    assert daily["provenance"]["setup_snapshot"]["configuration"]["configuration_id"]
    assert daily["provenance"]["setup_snapshot"]["configuration"]["evidence"]

    error, text, cells = _call("get_access_cells", {"result_id": result_id})
    assert not error, text
    assert cells["cell_count"] == 4
    assert all(
        feature["properties"]["event_count"] == 1
        for feature in cells["geojson"]["features"]
    )
    assert cells["geojson"]["metadata"]["accounting_method"] == "conical_events_v1"

    error, text, spacing = _call(
        "get_access_event_spacing", {"result_id": result_id, "offset": 0, "limit": 2}
    )
    assert not error, text
    assert spacing["total_cells"] == 4
    assert len(spacing["event_spacing"]) == 2
    assert spacing["event_spacing"][0]["mean_event_spacing_hours"] is None

    error, text, revisit = _call("get_access_revisit", {"result_id": result_id})
    assert not error, text
    assert revisit["event_spacing"]["event_count"] == 4
    assert revisit["provenance"]["setup_revision"] == proposal["setup_revision"]
    assert "continuous_viewing_duration" in revisit["unavailable_metrics"]
    assert "revisit" not in revisit


def test_stale_region_and_unaccepted_setup_fail_before_calculation(monkeypatch):
    draft = _begin()
    _set_region(draft["draft_id"], [0.0, -65.0, 10.0, -55.0])
    proposal = _prepare(draft["draft_id"])

    with mock.patch.object(
        conical_study, "collect_cell_events", side_effect=AssertionError("calculated")
    ):
        error, text, _ = _finish(draft["draft_id"], proposal["setup_revision"], accept=False)
        assert error
        assert "explicitly accepted" in text

        error, text, _ = _finish(draft["draft_id"], "sha256:stale")
        assert error
        assert "does not match" in text

        _set_region(draft["draft_id"], [20.0, -10.0, 22.0, -8.0])
        error, text, _ = _finish(draft["draft_id"], proposal["setup_revision"])
        assert error
        assert "stale" in text


def test_changed_grid_requires_a_new_acceptance():
    draft = _begin()
    _set_region(draft["draft_id"], [0.0, -65.0, 10.0, -55.0])
    first = _prepare(draft["draft_id"], 2, 2)
    second = _prepare(draft["draft_id"], 1, 1)
    assert first["setup_revision"] != second["setup_revision"]
    with mock.patch.object(
        conical_study, "collect_cell_events", side_effect=AssertionError("calculated")
    ):
        error, text, _ = _finish(draft["draft_id"], first["setup_revision"])
    assert error
    assert "does not match" in text or "stale" in text


def test_uncovered_applicability_fails_before_calculation(monkeypatch):
    configuration = copy.deepcopy(_config())
    configuration["applicability"]["end_utc_exclusive"] = "2026-10-05T00:30:00Z"
    draft = _begin()
    _set_region(draft["draft_id"], [0.0, -65.0, 10.0, -55.0])
    with mock.patch.object(
        conical_study, "collect_cell_events", side_effect=AssertionError("calculated")
    ):
        error, text, _ = _call(
            "prepare_conical_study",
            {
                "draft_id": draft["draft_id"],
                "grid_rows": 2,
                "grid_columns": 2,
                "configuration": configuration,
            },
        )
    assert error
    assert "not fully covered" in text
    assert "after applicability" in text
    assert "partial-study" in text

    early = copy.deepcopy(_config())
    early["applicability"]["start_utc"] = "2026-10-05T00:30:00Z"
    error, text, _ = _call(
        "prepare_conical_study",
        {
            "draft_id": draft["draft_id"],
            "grid_rows": 2,
            "grid_columns": 2,
            "configuration": early,
        },
    )
    assert error
    assert "before applicability" in text


def test_configuration_mismatch_and_missing_region_fail_before_calculation():
    draft = _begin()
    # No region yet.
    error, text, _ = _call(
        "prepare_conical_study",
        {
            "draft_id": draft["draft_id"],
            "grid_rows": 2,
            "grid_columns": 2,
            "configuration": _config(),
        },
    )
    assert error
    assert "no region" in text

    _set_region(draft["draft_id"], [0.0, -65.0, 10.0, -55.0])
    other = copy.deepcopy(_config())
    other["spacecraft"]["norad_id"] = 99999
    error, text, _ = _call(
        "prepare_conical_study",
        {
            "draft_id": draft["draft_id"],
            "grid_rows": 2,
            "grid_columns": 2,
            "configuration": other,
        },
    )
    assert error
    assert "does not match" in text

    named = copy.deepcopy(_config())
    named["spacecraft"]["name"] = "NOT-GCOM"
    named["spacecraft"]["aliases"] = []
    error, text, _ = _call(
        "prepare_conical_study",
        {
            "draft_id": draft["draft_id"],
            "grid_rows": 2,
            "grid_columns": 2,
            "configuration": named,
        },
    )
    assert error
    assert "orbit-provider name" in text


def test_long_window_grid_limit_and_single_configuration_limit():
    draft = _begin("2026-10-05T00:00:00Z", "2026-10-09T00:00:00Z")
    _set_region(draft["draft_id"], [0.0, -65.0, 10.0, -55.0])
    error, text, _ = _call(
        "prepare_conical_study",
        {
            "draft_id": draft["draft_id"],
            "grid_rows": 7,
            "grid_columns": 7,
            "configuration": _config(),
        },
    )
    assert error
    assert "36 cells" in text

    with pytest.raises(ValueError, match="multi-configuration"):
        conical_study.prepare_conical_study(
            draft["draft_id"], 2, 2, [_config(), _config()]
        )


def test_two_crossings_are_preserved_through_workflow():
    configuration = copy.deepcopy(_config())
    configuration["configuration_id"] = "synthetic-full-rotation-test"
    configuration["revision"] = "test"
    configuration["instrument"] = "Synthetic full rotation"
    configuration["geometry"]["scan_half_width"] = {"value": 180.0, "unit": "degree"}
    configuration["source_conflicts"] = []
    draft = _begin()
    _set_region(draft["draft_id"], [4.5, -60.5, 5.5, -59.5])
    proposal = _prepare(draft["draft_id"], 1, 1, configuration)
    error, text, summary = _finish(draft["draft_id"], proposal["setup_revision"])
    assert not error, text
    assert summary["summary_counts"]["event_count"] == 2
    assert summary["summary_counts"]["envelope_period_count"] == 1
    assert summary["summary_counts"]["observed_cell_count"] == 1
    assert summary["metrics"]["daily_cell_visit_count"] == 1
    assert summary["metrics"]["event_spacing_sample_count"] == 1
    assert summary["metrics"]["mean_event_spacing_hours"] == pytest.approx(
        0.070, abs=0.005
    )
    result_id = summary["result_id"]
    error, text, events = _call("get_access_events", {"result_id": result_id})
    assert not error, text
    assert len(events["events"]) == 2
    assert events["events"][0]["envelope_start_utc"] == events["events"][1]["envelope_start_utc"]
    assert events["events"][0]["epoch_utc"] != events["events"][1]["epoch_utc"]
    error, text, daily = _call("get_access_daily", {"result_id": result_id})
    assert not error, text
    assert daily["daily"][0]["event_count"] == 2
    assert daily["daily"][0]["observed_cell_ids"] == [0]
    error, text, spacing = _call(
        "get_access_event_spacing", {"result_id": result_id}
    )
    assert not error, text
    assert spacing["event_spacing"][0]["event_spacing_sample_count"] == 1


def test_midnight_envelope_workflow_does_not_count_next_day():
    draft = _begin("2026-10-05T23:50:00Z", "2026-10-06T00:10:00Z")
    _set_region(draft["draft_id"], [-170.0, 38.0, -160.0, 42.0])
    proposal = _prepare(draft["draft_id"])
    error, text, summary = _finish(draft["draft_id"], proposal["setup_revision"])
    assert not error, text
    result_id = summary["result_id"]
    error, text, daily = _call("get_access_daily", {"result_id": result_id})
    assert not error, text
    assert daily["total_days"] == 2
    first, second = daily["daily"]
    assert first["utc_date"] == "2026-10-05"
    assert second["utc_date"] == "2026-10-06"
    # Cells 2 and 3 have envelopes crossing midnight, but their epochs are
    # before midnight. They must not be observed on 2026-10-06.
    assert {2, 3}.issubset(first["observed_cell_ids"])
    assert {2, 3}.isdisjoint(second["observed_cell_ids"])
    assert second["event_count"] == 0


def test_empty_modeled_result_is_distinct_from_unknown_access():
    draft = _begin("2026-10-05T00:00:00Z", "2026-10-05T00:05:00Z")
    _set_region(draft["draft_id"], [0.0, 0.0, 1.0, 1.0])
    proposal = _prepare(draft["draft_id"], 1, 1)
    error, text, summary = _finish(draft["draft_id"], proposal["setup_revision"])
    assert not error, text
    assert summary["summary_counts"]["event_count"] == 0
    assert summary["summary_counts"]["observed_cell_count"] == 0
    assert summary["summary_counts"]["never_observed_cell_count"] == 1
    assert summary["metrics"]["mean_event_spacing_hours"] is None
    assert summary["metrics"]["continuous_viewing_duration_available"] is False
    error, text, daily = _call("get_access_daily", {"result_id": summary["result_id"]})
    assert not error, text
    assert daily["daily"][0]["observed_cell_ids"] == []
    assert daily["daily"][0]["observed_area_km2"] == 0.0
    assert daily["daily"][0]["gap_area_km2"] == pytest.approx(
        summary["total_roi_area_km2"], abs=0.001
    )


def test_saved_snapshot_is_immutable_after_a_new_preparation():
    draft = _begin()
    _set_region(draft["draft_id"], [0.0, -65.0, 10.0, -55.0])
    first = _prepare(draft["draft_id"])
    error, text, summary = _finish(draft["draft_id"], first["setup_revision"])
    assert not error, text
    result_id = summary["result_id"]
    saved_before = _call("get_access_daily", {"result_id": result_id})[2]

    changed = copy.deepcopy(_config())
    changed["revision"] = "2026-10-27.2"
    changed["configuration_id"] = "amsr2-gcom-w1-conical-product-sector-v2"
    second = _prepare(draft["draft_id"], 1, 1, changed)
    assert second["setup_revision"] != first["setup_revision"]

    saved_after = _call("get_access_daily", {"result_id": result_id})[2]
    assert saved_after["provenance"] == saved_before["provenance"]
    assert (
        saved_after["provenance"]["setup_snapshot"]["configuration"]["configuration_id"]
        == "amsr2-gcom-w1-conical-product-sector"
    )
    assert saved_after["setup_revision"] == first["setup_revision"]



def test_conical_event_fetch_rejects_legacy_result():
    legacy_id = result_store.save_result(
        {"result_kind": "legacy_intervals", "accounting_method": "legacy_intervals_v1"}
    )
    with pytest.raises(ValueError, match="legacy interval-based"):
        conical_study.get_conical_events(legacy_id)
    with pytest.raises(ValueError, match="legacy interval-based"):
        conical_study.get_conical_event_spacing(legacy_id)
    with pytest.raises(ValueError, match="Unknown result_id"):
        conical_study.get_conical_events("missing")
