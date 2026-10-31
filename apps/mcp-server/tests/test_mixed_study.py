"""Mixed instrument studies through the public MCP workflow."""

import asyncio
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastmcp import Client
from pyproj import Geod

from src import (
    configuration_registry,
    conical_study,
    coverage_core,
    generic_coverage,
    result_store,
)
from src import server as server_module

REFERENCE_DIR = Path(__file__).resolve().parent / "fixtures"
AMSR2_REFERENCE = json.loads(
    (REFERENCE_DIR / "amsr2_conical_reference.json").read_text(encoding="utf-8")
)
SENTINEL_REFERENCE = json.loads(
    (REFERENCE_DIR / "sentinel1c_iw_reference.json").read_text(encoding="utf-8")
)
NOW = datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc)
START = "2026-10-04T07:07:22Z"
END = "2026-10-04T07:37:22Z"


class FixtureClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is None else NOW.astimezone(tz)


def _sat_info(identifier):
    if identifier.strip().lower() == "gcom-w1":
        omm = copy.deepcopy(AMSR2_REFERENCE["orbit"]["omm"])
        return {"norad_id": 38337, "name": "GCOM-W1", "omm": [omm]}

    omm = copy.deepcopy(SENTINEL_REFERENCE["orbit"]["omm"])
    # Align the fixed Sentinel orbit to the AMSR2 fixture window. This is a
    # software-integration orbit fixture, not a mission prediction or validation.
    omm["EPOCH"] = "2026-10-04T07:07:22.400000"
    return {"norad_id": 62261, "name": "SENTINEL-1C", "omm": [omm]}


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


def _prepared_mixed_study(
    *,
    start=START,
    end=END,
    grid_rows=1,
    grid_columns=1,
    region=(-65.0, 0.0, -55.0, 10.0),
    configurations=None,
):
    error, text, draft = _call(
        "begin_access_run",
        {
            "satellite_identifier": "GCOM-W1, SENTINEL-1C",
            "start_time": start,
            "end_time": end,
        },
    )
    assert not error, text
    error, text, _ = _call(
        "set_access_region",
        {
            "draft_id": draft["draft_id"],
            "west_lon": region[0],
            "south_lat": region[1],
            "east_lon": region[2],
            "north_lat": region[3],
        },
    )
    assert not error, text
    return _call(
        "prepare_mixed_study",
        {
            "draft_id": draft["draft_id"],
            "grid_rows": grid_rows,
            "grid_columns": grid_columns,
            "configurations": configurations or [
                {
                    "configuration_id": "amsr2-gcom-w1-conical-product-sector",
                    "configuration_revision": "2026-10-27.1",
                },
                {
                    "configuration_id": "sentinel-1c-c-sar-iw-fixed-view",
                    "configuration_revision": "2026-10-30.1",
                },
            ],
        },
    )


def test_mixed_preparation_resolves_each_configuration_without_running_events():
    error, text, proposal = _prepared_mixed_study()

    assert not error, text
    assert proposal["status"] == "awaiting_acceptance"
    assert "result_id" not in proposal
    assert proposal["grid"]["cell_count"] == 1
    assert proposal["evaluated_configuration_count"] == 2
    assert [
        item["configuration_id"] for item in proposal["configurations"]
    ] == [
        "amsr2-gcom-w1-conical-product-sector",
        "sentinel-1c-c-sar-iw-fixed-view",
    ]
    assert [item["configuration"]["schema_version"] for item in proposal["configurations"]] == [
        "conical-configuration-v1",
        "pointed-configuration-v1",
    ]
    assert [item["configuration_revision"] for item in proposal["configurations"]] == [
        "2026-10-27.1",
        "2026-10-30.1",
    ]
    assert all(item["configuration"]["evidence"] for item in proposal["configurations"])
    assert all(item["satellite"]["omm_hash"] for item in proposal["configurations"])
    assert [
        item["configuration"]["geometry"]["illumination_requirement"]
        for item in proposal["configurations"]
    ] == ["none", "none"]


def test_mixed_finish_and_fetch_support_combined_and_configuration_scopes():
    error, text, proposal = _prepared_mixed_study()
    assert not error, text

    error, text, summary = _call(
        "finish_mixed_study",
        {
            "draft_id": proposal["draft_id"],
            "accepted_setup_revision": proposal["setup_revision"],
            "accept_setup": True,
        },
    )
    assert not error, text
    assert summary["result_kind"] == "mixed_study"
    assert summary["accounting_method"] == "mixed_events_union_v1"
    assert summary["setup_revision"] == proposal["setup_revision"]
    assert summary["completeness_status"] == "complete"
    assert summary["accepted_setup_revision"] == proposal["setup_revision"]
    assert [item["configuration_id"] for item in summary["modeled_configurations"]] == [
        "amsr2-gcom-w1-conical-product-sector",
        "sentinel-1c-c-sar-iw-fixed-view",
    ]
    snapshots = summary["provenance"]["setup_snapshot"]["configurations"]
    assert len(snapshots) == 2
    assert [item["configuration_revision"] for item in snapshots] == [
        "2026-10-27.1",
        "2026-10-30.1",
    ]
    assert all(item["configuration"]["evidence"] for item in snapshots)
    assert all(item["satellite"]["omm_hash"] for item in snapshots)
    assert all(
        all("excerpt" in evidence for evidence in item["configuration"]["evidence"])
        for item in snapshots
    )

    fetches = {
        "get_mixed_events": "events",
        "get_mixed_daily": "daily",
        "get_mixed_cells": "cells",
        "get_mixed_revisit": "revisit",
        "get_mixed_event_spacing": "spacing",
    }
    for configuration_id in (
        None,
        "amsr2-gcom-w1-conical-product-sector",
        "sentinel-1c-c-sar-iw-fixed-view",
    ):
        selector = "combined" if configuration_id is None else "configuration"
        for tool_name, result_key in fetches.items():
            arguments = {"result_id": summary["result_id"]}
            if configuration_id is not None:
                arguments["configuration_id"] = configuration_id
            error, text, fetched = _call(tool_name, arguments)
            assert not error, text
            assert fetched["scope"] == selector
            assert fetched["configuration_id"] == configuration_id
            assert fetched["accounting_method"] in {
                "mixed_events_union_v1",
                "mixed_configuration_events_v1",
            }
            assert fetched["provenance"]["setup_revision"] == proposal["setup_revision"]
            if configuration_id is None:
                assert fetched["warnings"] == summary["warnings"]
            else:
                assert fetched["warnings"]
                assert all(
                    warning.startswith(f"{configuration_id}: ")
                    for warning in fetched["warnings"]
                )
            provenance_bytes = json.dumps(
                fetched["provenance"], separators=(",", ":")
            )
            response_bytes = json.dumps(fetched, separators=(",", ":"))
            assert len(provenance_bytes) < 10_000, (
                f"repeated provenance header is {len(provenance_bytes)} characters"
            )
            assert len(response_bytes) < 25_000, (
                f"sample fetch response is {len(response_bytes)} characters"
            )
            compact_snapshots = fetched["provenance"]["setup_snapshot"]["configurations"]
            assert len(compact_snapshots) == len(snapshots)
            for compact, full in zip(compact_snapshots, snapshots):
                configuration = full["configuration"]
                canonical = json.dumps(
                    configuration, sort_keys=True, separators=(",", ":"), default=str
                ).encode("utf-8")
                assert compact["configuration_sha256"] == hashlib.sha256(canonical).hexdigest()
                assert compact["configuration_id"] == full["configuration_id"]
                assert compact["configuration_revision"] == full["configuration_revision"]
                assert compact["instrument"] == configuration["instrument"]
                assert compact["source_revision_id"] == configuration["source_revision"]["revision_id"]
                assert compact["source_choices"] == full["source_choices"]
                assert compact["geometry"]["model_family"] == configuration["geometry"]["model_family"]
                assert compact["geometry"]["illumination_requirement"] == configuration["geometry"]["illumination_requirement"]
                assert compact["geometry"]["tatc_mapping"] == configuration["tatc_mapping"]
                assert compact["applicability"] == full["applicability"]
                assert compact["spacecraft"] == full["satellite"]
                assert [
                    (item["evidence_id"], item["url"])
                    for item in compact["source_evidence"]
                ] == [
                    (item["evidence_id"], item["url"])
                    for item in configuration["evidence"]
                ]
            assert "excerpt" not in json.dumps(fetched["provenance"])
            assert fetched["warnings"]
            assert fetched["definitions"]["event"]
            if result_key == "events":
                assert isinstance(fetched["events"], list)
                assert fetched["limit"] == 5
            elif result_key == "daily":
                assert isinstance(fetched["daily"], list)
            elif result_key == "cells":
                assert fetched["geojson"]["type"] == "FeatureCollection"
                assert fetched["geojson"]["metadata"]["setup_revision"] == proposal[
                    "setup_revision"
                ]
                assert "provenance" not in fetched["geojson"]["metadata"]
                assert fetched["limit"] == 2
            elif result_key == "revisit":
                assert "event_spacing_sample_count" in fetched["metrics"]
            else:
                assert isinstance(fetched["event_spacing"], list)
                assert fetched["limit"] == 5

    oversized_pages = {
        "get_mixed_events": 20,
        "get_mixed_cells": 10,
        "get_mixed_event_spacing": 20,
    }
    for tool_name, max_limit in oversized_pages.items():
        error, text, fetched = _call(
            tool_name, {"result_id": summary["result_id"], "limit": 100}
        )
        assert not error, text
        assert fetched["limit"] == max_limit


def test_known_events_use_exact_epoch_union_and_independent_cell_area(monkeypatch):
    fixture = json.loads(
        (REFERENCE_DIR / "mixed_known_events.json").read_text(encoding="utf-8")
    )
    conical = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "data"
            / "configurations"
            / "amsr2-gcom-w1-conical-product-sector-2026-10-27.1.json"
        ).read_text(encoding="utf-8")
    )
    conical["configuration_id"] = "amsr2-gcom-w1-conical-accounting-copy"
    conical["revision"] = "fixture-1"
    selections = [
        {
            "configuration_id": "amsr2-gcom-w1-conical-product-sector",
            "configuration_revision": "2026-10-27.1",
        },
        {"configuration": conical},
        {
            "configuration_id": "sentinel-1c-c-sar-iw-fixed-view",
            "configuration_revision": "2026-10-30.1",
        },
    ]
    error, text, proposal = _prepared_mixed_study(
        start=fixture["window_start_utc"],
        end=fixture["window_end_utc_exclusive"],
        grid_rows=2,
        grid_columns=2,
        configurations=selections,
    )
    assert not error, text
    event_rows = [
        {
            **event,
            "epoch": datetime.fromisoformat(event["epoch"].replace("Z", "+00:00")),
        }
        for event in fixture["events"]
    ]
    monkeypatch.setattr(
        conical_study,
        "_collect_tasks",
        lambda _tasks, _workers, _work: event_rows,
    )
    error, text, summary = _call(
        "finish_mixed_study",
        {
            "draft_id": proposal["draft_id"],
            "accepted_setup_revision": proposal["setup_revision"],
            "accept_setup": True,
        },
    )
    assert not error, text
    assert summary["summary_counts"]["event_count"] == 5
    assert summary["configuration_summaries"][2]["summary_counts"]["event_count"] == 0

    error, text, combined_events = _call(
        "get_mixed_events", {"result_id": summary["result_id"]}
    )
    assert not error, text
    assert combined_events["total_events"] == 5
    simultaneous = next(
        event
        for event in combined_events["events"]
        if event["cell_id"] == 0 and event["epoch_utc"] == "2026-10-04T23:10:00Z"
    )
    assert simultaneous["contributors"] == [
        "amsr2-gcom-w1-conical-accounting-copy",
        "amsr2-gcom-w1-conical-product-sector",
    ]
    assert len(simultaneous["contributor_configurations"]) == 2
    assert len(
        [event for event in combined_events["events"] if event["cell_id"] == 0]
    ) == 2

    error, text, daily = _call("get_mixed_daily", {"result_id": summary["result_id"]})
    assert not error, text
    assert [row["utc_date"] for row in daily["daily"]] == ["2026-10-04", "2026-10-05"]
    assert daily["daily"][0]["observed_cell_ids"] == [0, 1]
    assert daily["daily"][1]["observed_cell_ids"] == [1, 2]
    geod = Geod(ellps="WGS84")

    def area(west, south, east, north):
        area_m2, _ = geod.polygon_area_perimeter(
            [west, east, east, west], [south, south, north, north]
        )
        return abs(area_m2) / 1_000_000

    expected_first_day = round(
        area(-65.0, 0.0, -60.0, 5.0) + area(-60.0, 0.0, -55.0, 5.0), 3
    )
    expected_second_day = round(
        area(-60.0, 0.0, -55.0, 5.0) + area(-65.0, 5.0, -60.0, 10.0), 3
    )
    assert abs(daily["daily"][0]["observed_area_km2"] - expected_first_day) <= 0.001
    assert abs(daily["daily"][1]["observed_area_km2"] - expected_second_day) <= 0.001
    error, text, one_configuration_daily = _call(
        "get_mixed_daily",
        {
            "result_id": summary["result_id"],
            "configuration_id": "amsr2-gcom-w1-conical-product-sector",
        },
    )
    assert not error, text
    assert [row["observed_area_km2"] for row in one_configuration_daily["daily"]] == [
        row["observed_area_km2"] for row in daily["daily"]
    ]

    error, text, cells = _call(
        "get_mixed_cells", {"result_id": summary["result_id"], "limit": 4}
    )
    assert not error, text
    by_id = {
        item["properties"]["id"]: item["properties"]
        for item in cells["geojson"]["features"]
    }
    assert by_id[0]["event_count"] == 2
    assert by_id[0]["mean_event_spacing_hours"] == pytest.approx(0.000139)
    assert by_id[1]["event_count"] == 2
    assert by_id[1]["mean_event_spacing_hours"] == pytest.approx(0.000278)
    assert by_id[3]["event_count"] == 0
    assert by_id[3]["mean_event_spacing_hours"] is None
    assert by_id[3]["status"] == "gap"
    assert [row["event_count"] for row in daily["daily"]] == [3, 2]
    error, text, spacing = _call(
        "get_mixed_event_spacing", {"result_id": summary["result_id"]}
    )
    assert not error, text
    spacing_by_id = {row["cell_id"]: row for row in spacing["event_spacing"]}
    assert spacing_by_id[0]["event_spacing_hours"] == pytest.approx([0.000139])
    assert spacing_by_id[3]["spacing_status"] == "null_fewer_than_two_distinct_events"

    error, text, zero_events = _call(
        "get_mixed_events",
        {
            "result_id": summary["result_id"],
            "configuration_id": "sentinel-1c-c-sar-iw-fixed-view",
        },
    )
    assert not error, text
    assert zero_events["total_events"] == 0
    assert zero_events["metrics"]["mean_event_spacing_hours"] is None


def test_daylight_assumption_is_applied_per_configuration_on_shared_spacecraft():
    daylight = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "data"
            / "configurations"
            / "amsr2-gcom-w1-conical-product-sector-2026-10-27.1.json"
        ).read_text(encoding="utf-8")
    )
    daylight["configuration_id"] = "amsr2-gcom-w1-daylight-study-assumption"
    daylight["revision"] = "fixture-daylight-1"
    daylight["geometry"]["illumination_requirement"] = "target_sunlit"
    daylight["geometry"]["illumination_requirement_basis"] = "study_assumption"
    daylight_note = (
        "A daylight sensitivity filter selected for this study only; the source "
        "does not establish a daylight-only AMSR2 requirement."
    )
    daylight["geometry"]["illumination_requirement_note"] = daylight_note
    daylight["interpretation"]["limitations"].append(daylight_note)
    selections = [
        {
            "configuration_id": "amsr2-gcom-w1-conical-product-sector",
            "configuration_revision": "2026-10-27.1",
        },
        {"configuration": daylight},
        {
            "configuration_id": "sentinel-1c-c-sar-iw-fixed-view",
            "configuration_revision": "2026-10-30.1",
        },
    ]
    error, text, proposal = _prepared_mixed_study(
        start="2026-10-05T00:00:00Z",
        end="2026-10-05T01:00:00Z",
        grid_rows=2,
        grid_columns=2,
        region=(0.0, -65.0, 10.0, -55.0),
        configurations=selections,
    )
    assert not error, text
    base, filtered = proposal["configurations"][:2]
    assert base["spacecraft"]["norad_id"] == filtered["spacecraft"]["norad_id"]
    assert base["configuration"]["geometry"]["illumination_requirement"] == "none"
    assert filtered["configuration"]["geometry"]["illumination_requirement"] == "target_sunlit"
    assert filtered["configuration"]["geometry"]["illumination_requirement_basis"] == "study_assumption"
    assert filtered["configuration"]["tatc_mapping"]["req_target_sunlit"] is True
    assert any("not a source-established scientific requirement" in warning for warning in filtered["warnings"])

    error, text, summary = _call(
        "finish_mixed_study",
        {
            "draft_id": proposal["draft_id"],
            "accepted_setup_revision": proposal["setup_revision"],
            "accept_setup": True,
        },
    )
    assert not error, text
    base_events = summary["configuration_summaries"][0]["summary_counts"]["event_count"]
    daylight_events = summary["configuration_summaries"][1]["summary_counts"]["event_count"]
    assert base_events == 4
    assert daylight_events == 0
    assert any("not a source-established scientific requirement" in warning for warning in summary["warnings"])


def test_mixed_preparation_rejects_duplicate_and_unknown_registry_selections():
    duplicate = {
        "configuration_id": "amsr2-gcom-w1-conical-product-sector",
        "configuration_revision": "2026-10-27.1",
    }
    error, text, _ = _prepared_mixed_study(configurations=[duplicate, duplicate])
    assert error
    assert "selected more than once" in text

    error, text, _ = _prepared_mixed_study(
        configurations=[
            {"configuration_id": "not-a-reviewed-configuration", "configuration_revision": "1"}
        ]
    )
    assert error
    assert "no configuration revision" in text.lower()
    assert "not-a-reviewed-configuration" in text

    amsr2 = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "data"
            / "configurations"
            / "amsr2-gcom-w1-conical-product-sector-2026-10-27.1.json"
        ).read_text(encoding="utf-8")
    )
    changed_revision = copy.deepcopy(amsr2)
    changed_revision["revision"] = "fixture-other-revision"
    error, text, _ = _prepared_mixed_study(
        configurations=[
            {"configuration": amsr2},
            {"configuration": changed_revision},
            {
                "configuration_id": "sentinel-1c-c-sar-iw-fixed-view",
                "configuration_revision": "2026-10-30.1",
            },
        ]
    )
    assert error
    assert "inconsistent revisions or instrument identity" in text

    error, text, _ = _prepared_mixed_study(
        configurations=[{"configuration": {"schema_version": "unsupported-v9"}}]
    )
    assert error
    assert "unsupported mixed configuration schema_version" in text.lower()


def test_mixed_preparation_rejects_spacecraft_identity_mismatch():
    amsr2 = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "data"
            / "configurations"
            / "amsr2-gcom-w1-conical-product-sector-2026-10-27.1.json"
        ).read_text(encoding="utf-8")
    )
    amsr2["configuration_id"] = "amsr2-wrong-spacecraft-fixture"
    amsr2["revision"] = "fixture-identity-1"
    amsr2["spacecraft"]["norad_id"] = 62261
    error, text, _ = _prepared_mixed_study(
        configurations=[
            {"configuration": amsr2},
            {
                "configuration_id": "amsr2-gcom-w1-conical-product-sector",
                "configuration_revision": "2026-10-27.1",
            },
            {
                "configuration_id": "sentinel-1c-c-sar-iw-fixed-view",
                "configuration_revision": "2026-10-30.1",
            },
        ]
    )
    assert error
    assert "but staged norad 62261 resolves" in text.lower()


def test_mixed_preparation_rejects_a_preexisting_single_configuration_proposal():
    error, text, draft = _call(
        "begin_access_run",
        {
            "satellite_identifier": "GCOM-W1",
            "start_time": START,
            "end_time": END,
        },
    )
    assert not error, text
    error, text, _ = _call(
        "set_access_region",
        {
            "draft_id": draft["draft_id"],
            "west_lon": -65.0,
            "south_lat": 0.0,
            "east_lon": -55.0,
            "north_lat": 10.0,
        },
    )
    assert not error, text
    error, text, _ = _call(
        "prepare_conical_study",
        {
            "draft_id": draft["draft_id"],
            "grid_rows": 1,
            "grid_columns": 1,
            "configuration_id": "amsr2-gcom-w1-conical-product-sector",
            "configuration_revision": "2026-10-27.1",
        },
    )
    assert not error, text
    error, text, _ = _call(
        "prepare_mixed_study",
        {
            "draft_id": draft["draft_id"],
            "grid_rows": 1,
            "grid_columns": 1,
            "configurations": [
                {
                    "configuration_id": "amsr2-gcom-w1-conical-product-sector",
                    "configuration_revision": "2026-10-27.1",
                }
            ],
        },
    )
    assert error
    assert "single-configuration conical proposal" in text.lower()


def test_mixed_workload_limits_are_rejected_before_tatc_resolution(monkeypatch):
    def unexpected_tatc_resolution(*args, **kwargs):
        raise AssertionError("TAT-C configuration resolution must not run")

    monkeypatch.setattr(
        conical_study, "resolved_snapshot", unexpected_tatc_resolution
    )
    error, text, _ = _prepared_mixed_study(grid_rows=13, grid_columns=12)
    assert error
    assert "between 1 and 12" in text

    amsr2 = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "data"
            / "configurations"
            / "amsr2-gcom-w1-conical-product-sector-2026-10-27.1.json"
        ).read_text(encoding="utf-8")
    )
    amsr2["configuration_id"] = "amsr2-gcom-w1-conical-product-sector-study-copy"
    amsr2["revision"] = "fixture-study-copy-1"
    error, text, _ = _prepared_mixed_study(
        start="2026-10-04T00:00:00Z",
        end="2026-10-08T00:00:00Z",
        grid_rows=5,
        grid_columns=5,
        configurations=[
            {
                "configuration_id": "amsr2-gcom-w1-conical-product-sector",
                "configuration_revision": "2026-10-27.1",
            },
            {"configuration": amsr2},
            {
                "configuration_id": "sentinel-1c-c-sar-iw-fixed-view",
                "configuration_revision": "2026-10-30.1",
            },
        ],
    )
    assert error
    assert "25 cells x 3 configurations = 75" in text

    error, text, _ = _prepared_mixed_study(
        start="2026-10-04T00:00:00Z",
        end="2026-10-08T00:00:00Z",
        grid_rows=7,
        grid_columns=6,
    )
    assert error
    assert "at most 36 cells" in text

    selection = {
        "configuration_id": "amsr2-gcom-w1-conical-product-sector",
        "configuration_revision": "2026-10-27.1",
    }
    error, text, _ = _prepared_mixed_study(configurations=[selection] * 7)
    assert error
    assert "at most 6 instrument configurations" in text.lower()


def test_mixed_preparation_accepts_the_short_window_144_cell_limit():
    error, text, proposal = _prepared_mixed_study(grid_rows=12, grid_columns=12)
    assert not error, text
    assert proposal["grid"]["cell_count"] == 144
    assert proposal["status"] == "awaiting_acceptance"


def test_mixed_preparation_requires_full_window_applicability():
    amsr2 = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "data"
            / "configurations"
            / "amsr2-gcom-w1-conical-product-sector-2026-10-27.1.json"
        ).read_text(encoding="utf-8")
    )
    amsr2["configuration_id"] = "amsr2-gcom-w1-limited-applicability"
    amsr2["revision"] = "fixture-applicability-1"
    amsr2["applicability"]["end_utc_exclusive"] = "2026-10-04T07:20:00Z"
    error, text, _ = _prepared_mixed_study(
        configurations=[
            {"configuration": amsr2},
            {
                "configuration_id": "sentinel-1c-c-sar-iw-fixed-view",
                "configuration_revision": "2026-10-30.1",
            },
        ],
    )
    assert error
    assert "not fully covered" in text.lower()


def test_mixed_and_single_configuration_workflows_cannot_share_a_draft():
    error, text, proposal = _prepared_mixed_study()
    assert not error, text

    error, text, _ = _call(
        "prepare_conical_study",
        {
            "draft_id": proposal["draft_id"],
            "grid_rows": 1,
            "grid_columns": 1,
            "configuration_id": "amsr2-gcom-w1-conical-product-sector",
            "configuration_revision": "2026-10-27.1",
        },
    )
    assert error
    assert "mixed proposal" in text.lower()

    error, text, _ = _call(
        "finish_conical_study",
        {
            "draft_id": proposal["draft_id"],
            "accepted_setup_revision": proposal["setup_revision"],
            "accept_setup": True,
        },
    )
    assert error
    assert "mixed proposal" in text.lower()

    error, text, _ = _call(
        "finish_access_run",
        {
            "draft_id": proposal["draft_id"],
            "grid_rows": 1,
            "grid_columns": 1,
            "field_of_regard_deg": 1,
            "geometry_reference": "test only",
            "geometry_assessment": {
                "status": "supported",
                "source_text": "test only",
                "explanation": "test only",
            },
        },
    )
    assert error
    assert "mixed finish tool" in text.lower()
