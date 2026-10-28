"""Staged conical study through MCP: acceptance, events, and provenance."""

import asyncio
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest
from fastmcp import Client

from src import configuration_registry, conical_study, generic_coverage, result_store
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


def _refresh_fixture_configuration():
    configuration = _config()
    configuration["revision"] = "2026-10-28-fixture-2"
    configuration["source_revision"] = {
        **configuration["source_revision"],
        "revision_id": "amsr2-refresh-fixture-2",
        "checked_at_utc": "2026-10-28T00:00:00Z",
        "summary": "Synthetic source fixture for the registry refresh test.",
    }
    configuration["geometry"]["scan_half_width"]["value"] = 72.0
    fixture_evidence = {
        "evidence_id": "fixture-geometry-refresh",
        "authority": "Test fixture",
        "title": "Synthetic AMSR2 geometry refresh",
        "url": "https://example.invalid/amsr2-geometry-refresh",
        "document_revision": "fixture-2",
        "locator": "scan sector",
        "excerpt": "The test fixture sets the product-sector half width to 72 degrees.",
        "retrieved_at_utc": "2026-10-28T00:00:00Z",
        "interpretation": "Test-only evidence for exercising changed-geometry selection.",
        "supports": ["geometry.scan_half_width"],
    }
    configuration["evidence"].append(fixture_evidence)
    configuration["source_revision"]["evidence_ids"].append(
        "fixture-geometry-refresh"
    )
    selected_sector = configuration["source_conflicts"][0]["options"][1]
    selected_sector["summary"] = "Test fixture product sector with 72 degree half width."
    selected_sector["scan_half_width_deg"] = 72.0
    selected_sector["evidence_ids"] = ["fixture-geometry-refresh"]
    configuration["geometry_checks"] = [
        check
        for check in configuration["geometry_checks"]
        if check["check_id"] != "scan-sector-consistency"
    ]
    configuration["geometry_checks"].append(
        {
            "check_id": "scan-sector-refresh-fixture",
            "description": "Test fixture scan-sector geometry check.",
            "method": "Fixed source refresh test fixture.",
            "reported_value": 72.0,
            "unit": "degree",
            "tolerance": 0.1,
            "status": "passed",
            "evidence_ids": ["fixture-geometry-refresh"],
        }
    )
    return configuration


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
    configuration_registry.clear_refresh_state()
    monkeypatch.setattr(
        generic_coverage.celestrak_client, "get_satellite_info", _sat_info
    )
    monkeypatch.setattr(coverage_core, "datetime", FixtureClock)
    yield
    result_store.clear()
    configuration_registry.clear_refresh_state()


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


def test_configuration_lookup_returns_reviewed_amsr2_without_selecting_it():
    error, text, lookup = _call(
        "lookup_instrument_configurations", {"instrument": "AMSR2"}
    )
    assert not error, text
    assert lookup["selection_required"] is True
    assert lookup["ambiguous"] is False
    assert lookup["candidate_count"] == 1

    candidate = lookup["candidates"][0]
    configuration = candidate["configuration"]
    assert candidate["registry_status"] == "reviewed_shared"
    assert configuration["configuration_id"] == "amsr2-gcom-w1-conical-product-sector"
    assert configuration["revision"] == "2026-10-27.1"
    assert configuration["geometry"]["cone_angle"] == {
        "value": 47.564,
        "unit": "degree",
    }
    assert configuration["geometry"]["scan_half_width"] == {
        "value": 75.332,
        "unit": "degree",
    }
    assert "tatc-amsr2-validation" in {
        evidence["evidence_id"] for evidence in configuration["evidence"]
    }
    assert any(
        check["check_id"] == "observation-timing"
        and check["reported_value"] == 0.27
        and check["tolerance"] == 1.0
        for check in configuration["geometry_checks"]
    )
    assert candidate["source_change_checks"][0]["kind"] == "initial_inclusion"
    assert candidate["source_change_checks"][0]["status"] == "checked"


def test_explicit_geometry_refresh_adds_a_revision_and_leaves_both_candidates():
    updated = _refresh_fixture_configuration()
    error, text, refresh = _call(
        "record_instrument_configuration_refresh",
        {
            "configuration_id": _config()["configuration_id"],
            "base_revision": _config()["revision"],
            "status": "geometry_changed",
            "checked_at_utc": "2026-10-28T00:00:00Z",
            "checked_source_revision_id": updated["source_revision"]["revision_id"],
            "reported_by": "assistant",
            "summary": "Fixture refresh found a changed product-sector half width.",
            "evidence_ids": ["fixture-geometry-refresh"],
            "source_evidence": [updated["evidence"][-1]],
            "updated_configuration": updated,
        },
    )
    assert not error, text
    assert refresh["status"] == "geometry_changed"
    assert refresh["geometry_changes"] == [
        {
            "parameter": "geometry.scan_half_width",
            "previous_value": {"value": 75.332, "unit": "degree"},
            "new_value": {"value": 72.0, "unit": "degree"},
        }
    ]
    assert refresh["new_revision_candidate"]["revision"] == "2026-10-28-fixture-2"

    error, text, lookup = _call(
        "lookup_instrument_configurations",
        {"instrument": "AMSR2", "operating_mode": "40_rpm"},
    )
    assert not error, text
    assert lookup["candidate_count"] == 2
    assert lookup["ambiguous"] is True
    assert lookup["selection_required"] is True
    assert lookup["selection"] is None
    old = next(
        item
        for item in lookup["candidates"]
        if item["revision"] == _config()["revision"]
    )
    new = next(
        item
        for item in lookup["candidates"]
        if item["revision"] == "2026-10-28-fixture-2"
    )
    assert old["source_change_notices"][0]["new_revision"] == "2026-10-28-fixture-2"
    assert new["registry_status"] == "source_refresh_candidate"
    assert new["configuration"]["geometry"]["scan_half_width"]["value"] == 72.0


def _finish(draft_id, revision, accept=True):
    return _call(
        "finish_conical_study",
        {
            "draft_id": draft_id,
            "accepted_setup_revision": revision,
            "accept_setup": accept,
        },
    )


def _prepare_registry(draft_id, configuration_id, configuration_revision=None):
    arguments = {
        "draft_id": draft_id,
        "grid_rows": 2,
        "grid_columns": 2,
        "configuration_id": configuration_id,
    }
    if configuration_revision is not None:
        arguments["configuration_revision"] = configuration_revision
    return _call("prepare_conical_study", arguments)


def test_explicit_registry_revisions_run_with_tatc_and_keep_saved_snapshots():
    base = _config()
    base_id = base["configuration_id"]
    base_revision = base["revision"]

    old_draft = _begin()
    _set_region(old_draft["draft_id"], [0.0, -65.0, 10.0, -55.0])
    error, text, old_proposal = _prepare_registry(
        old_draft["draft_id"], base_id, base_revision
    )
    assert not error, text
    assert old_proposal["configuration_revision"] == base_revision
    assert old_proposal["registry_selection"]["selection"] == "explicit_exact_revision"
    assert old_proposal["registry_selection"]["source"] == "reviewed_shared"

    error, text, old_summary = _finish(
        old_draft["draft_id"], old_proposal["setup_revision"]
    )
    assert not error, text
    old_result_id = old_summary["result_id"]

    refreshed = _refresh_fixture_configuration()
    error, text, _ = _call(
        "record_instrument_configuration_refresh",
        {
            "configuration_id": base_id,
            "base_revision": base_revision,
            "status": "geometry_changed",
            "checked_at_utc": "2026-10-28T00:00:00Z",
            "checked_source_revision_id": refreshed["source_revision"]["revision_id"],
            "reported_by": "assistant",
            "summary": "Fixture refresh found a changed product-sector half width.",
            "evidence_ids": ["fixture-geometry-refresh"],
            "source_evidence": [refreshed["evidence"][-1]],
            "updated_configuration": refreshed,
        },
    )
    assert not error, text

    error, text, old_events = _call(
        "get_access_events", {"result_id": old_result_id, "offset": 0, "limit": 1}
    )
    assert not error, text
    old_selection = old_events["provenance"]["setup_snapshot"]["registry_selection"]
    assert old_events["configuration_revision"] == base_revision
    assert old_selection["source_change_notices"] == []
    assert (
        old_events["provenance"]["setup_snapshot"]["configuration"]["geometry"]
        ["scan_half_width"]["value"]
        == 75.332
    )

    error, text, ambiguous = _call(
        "lookup_instrument_configurations",
        {"instrument": "AMSR2", "operating_mode": "40_rpm"},
    )
    assert not error, text
    assert ambiguous["ambiguous"] is True
    assert ambiguous["selection"] is None

    new_draft = _begin()
    _set_region(new_draft["draft_id"], [0.0, -65.0, 10.0, -55.0])
    error, text, prior_proposal = _prepare_registry(
        new_draft["draft_id"], base_id, base_revision
    )
    assert not error, text
    assert prior_proposal["registry_selection"]["source_change_notices"][0][
        "new_revision"
    ] == refreshed["revision"]
    assert any("geometry.scan_half_width" in warning for warning in prior_proposal["warnings"])

    error, text, _ = _prepare_registry(new_draft["draft_id"], base_id)
    assert error
    assert "configuration_revision is required" in text

    new_revision = refreshed["revision"]
    error, text, new_proposal = _prepare_registry(
        new_draft["draft_id"], base_id, new_revision
    )
    assert not error, text
    assert new_proposal["registry_selection"]["source"] == "source_refresh_candidate"
    assert new_proposal["configuration"]["geometry"]["scan_half_width"]["value"] == 72.0
    assert any("geometry.scan_half_width" in warning for warning in new_proposal["warnings"])

    error, text, new_summary = _finish(
        new_draft["draft_id"], new_proposal["setup_revision"]
    )
    assert not error, text
    assert new_summary["configuration_revision"] == new_revision
    error, text, new_events = _call(
        "get_access_events", {"result_id": new_summary["result_id"], "limit": 1}
    )
    assert not error, text
    assert new_events["registry_selection"]["revision"] == new_revision
    assert (
        new_events["provenance"]["setup_snapshot"]["configuration"]["geometry"]
        ["scan_half_width"]["value"]
        == 72.0
    )
    for tool_name, arguments in (
        ("get_access_daily", {"result_id": new_summary["result_id"]}),
        ("get_access_cells", {"result_id": new_summary["result_id"]}),
        ("get_access_revisit", {"result_id": new_summary["result_id"]}),
        (
            "get_access_event_spacing",
            {"result_id": new_summary["result_id"], "offset": 0, "limit": 1},
        ),
    ):
        error, text, page = _call(tool_name, arguments)
        assert not error, text
        assert page["registry_selection"]["revision"] == new_revision
        assert (
            page["provenance"]["setup_snapshot"]["configuration"]["revision"]
            == new_revision
        )


def test_source_refresh_cannot_reuse_an_immutable_registry_revision():
    configuration = _config()
    updated = _refresh_fixture_configuration()
    updated["revision"] = configuration["revision"]
    error, text, _ = _call(
        "record_instrument_configuration_refresh",
        {
            "configuration_id": configuration["configuration_id"],
            "base_revision": configuration["revision"],
            "status": "geometry_changed",
            "checked_at_utc": "2026-10-28T00:00:00Z",
            "checked_source_revision_id": updated["source_revision"]["revision_id"],
            "reported_by": "assistant",
            "summary": "Fixture refresh attempts to reuse an existing revision.",
            "source_evidence": [updated["evidence"][-1]],
            "updated_configuration": updated,
        },
    )
    assert error
    assert "new immutable revision" in text
    error, text, lookup = _call(
        "lookup_instrument_configurations",
        {"configuration_id": configuration["configuration_id"]},
    )
    assert not error, text
    assert lookup["candidate_count"] == 1


def test_failed_source_refresh_is_disclosed_and_stales_an_older_proposal():
    configuration = _config()
    draft = _begin()
    _set_region(draft["draft_id"], [0.0, -65.0, 10.0, -55.0])
    error, text, proposal = _prepare_registry(
        draft["draft_id"], configuration["configuration_id"], configuration["revision"]
    )
    assert not error, text

    error, text, refresh = _call(
        "record_instrument_configuration_refresh",
        {
            "configuration_id": configuration["configuration_id"],
            "base_revision": configuration["revision"],
            "status": "failed",
            "checked_at_utc": "2026-10-28T00:00:00Z",
            "checked_source_revision_id": "failed-source-check-2026-10-28",
            "reported_by": "assistant",
            "summary": "The requested source refresh could not be completed.",
            "failure_reason": "The source page was unavailable during this check.",
        },
    )
    assert not error, text
    assert refresh["status"] == "failed"

    error, text, _ = _finish(draft["draft_id"], proposal["setup_revision"])
    assert error
    assert "new source-change information" in text
    assert "No calculation was run" in text

    error, text, lookup = _call(
        "lookup_instrument_configurations",
        {"configuration_id": configuration["configuration_id"]},
    )
    assert not error, text
    candidate = lookup["candidates"][0]
    assert candidate["refresh_uncertainty"][0]["failure_reason"] == (
        "The source page was unavailable during this check."
    )
    assert candidate["source_change_checks"][-1]["status"] == "failed"

    error, text, proposal = _prepare_registry(
        draft["draft_id"], configuration["configuration_id"], configuration["revision"]
    )
    assert not error, text
    assert any("current source state is unknown" in warning for warning in proposal["warnings"])
    error, text, summary = _finish(draft["draft_id"], proposal["setup_revision"])
    assert not error, text
    assert summary["registry_selection"]["refresh_uncertainty"][0]["failure_reason"]
    assert any("current source state is unknown" in warning for warning in summary["warnings"])


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
    assert any(
        "product-75deg" in warning for warning in proposal["warnings"]
    )
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

    error, text, cell_page = _call(
        "get_access_cells",
        {"result_id": result_id, "offset": 0, "limit": 2},
    )
    assert not error, text
    assert cell_page["cell_count"] == 2
    assert cell_page["matched_cells"] == 4
    assert cell_page["total_cells"] == 4
    error, text, cell_page_two = _call(
        "get_access_cells",
        {"result_id": result_id, "offset": 2, "limit": 2},
    )
    assert not error, text
    assert cell_page_two["cell_count"] == 2
    assert {f["properties"]["id"] for f in cell_page["geojson"]["features"]}.isdisjoint(
        {f["properties"]["id"] for f in cell_page_two["geojson"]["features"]}
    )

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


def test_finish_freezes_accepted_stage_before_concurrent_edit(monkeypatch):
    draft = _begin()
    accepted_bbox = [0.0, -65.0, 10.0, -55.0]
    _set_region(draft["draft_id"], accepted_bbox)
    proposal = _prepare(draft["draft_id"])
    real_collect = conical_study._collect_tasks

    def edit_draft_then_collect(tasks, max_workers, work):
        result_store.set_draft_region(draft["draft_id"], [20.0, -10.0, 22.0, -8.0])
        return real_collect(tasks, max_workers, work)

    monkeypatch.setattr(conical_study, "_collect_tasks", edit_draft_then_collect)
    error, text, summary = _finish(draft["draft_id"], proposal["setup_revision"])
    assert not error, text
    assert summary["roi_bbox"] == accepted_bbox
    assert summary["provenance"]["setup_snapshot"]["region"] == accepted_bbox
    assert summary["summary_counts"]["observed_cell_count"] == 4
    assert result_store.get_draft(draft["draft_id"])["roi_bbox"] == [
        20.0,
        -10.0,
        22.0,
        -8.0,
    ]



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
