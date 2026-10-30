"""Pointed Sentinel IW study through the staged MCP tools and TAT-C 3.6.0."""

import asyncio
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest
from fastmcp import Client
from pyproj import Geod
from tatc.analysis import collect_observations
from tatc.schemas import Instrument, Point, PointedInstrument, Satellite
from tatc.utils import NadirReference, VelocityFrame

from src import configuration_registry, coverage_core, generic_coverage, result_store
from src import server as server_module
from src.instrument_tatc import collect_cell_events
from src.pointed_config import PointedConfiguration
from src.tatc_integration import create_orbit_from_omm

REFERENCE_PATH = Path(__file__).resolve().parent / "fixtures" / "sentinel1c_iw_reference.json"
CONFIGURATION_ID = "sentinel-1c-c-sar-iw-fixed-view"
CONFIGURATION_REVISION = "2026-10-30.1"
NOW = datetime(2026, 10, 30, 1, 20, tzinfo=timezone.utc)


def _reference():
    return json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))


def _omm():
    return [_reference()["orbit"]["omm"]]


def _configuration():
    return PointedConfiguration.model_validate_json(
        (
            Path(__file__).resolve().parents[1]
            / "data"
            / "configurations"
            / f"{CONFIGURATION_ID}-{CONFIGURATION_REVISION}.json"
        ).read_text(encoding="utf-8")
    )


def _sat_info(_identifier):
    return {"norad_id": 62261, "name": "SENTINEL-1C", "omm": _omm()}


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


def _begin(start, end):
    error, text, payload = _call(
        "begin_access_run",
        {"satellite_identifier": "SENTINEL-1C", "start_time": start, "end_time": end},
    )
    assert not error, text
    return payload


def _set_region(draft_id, longitude, latitude):
    error, text, payload = _call(
        "set_access_region",
        {
            "draft_id": draft_id,
            "west_lon": longitude - 0.001,
            "south_lat": latitude - 0.001,
            "east_lon": longitude + 0.001,
            "north_lat": latitude + 0.001,
        },
    )
    assert not error, text
    return payload


def _prepare(draft_id, configuration_id=CONFIGURATION_ID, revision=CONFIGURATION_REVISION):
    return _call(
        "prepare_pointed_study",
        {
            "draft_id": draft_id,
            "grid_rows": 1,
            "grid_columns": 1,
            "configuration_id": configuration_id,
            "configuration_revision": revision,
        },
    )


def _direct_pointed_observations(case):
    reference = _reference()
    orbit = create_orbit_from_omm([reference["orbit"]["omm"]])
    geometry = reference["independent_tatc_configuration"]
    instrument = PointedInstrument(
        name=geometry["name"],
        field_of_regard=geometry["field_of_regard_deg"],
        cross_track_field_of_view=geometry["cross_track_field_of_view_deg"],
        along_track_field_of_view=geometry["along_track_field_of_view_deg"],
        roll_angle=geometry["roll_angle_deg"],
        pitch_angle=geometry["pitch_angle_deg"],
        nadir_reference=NadirReference.GEODETIC,
        velocity_frame=VelocityFrame.EARTH_FIXED,
        is_rectangular=True,
        view_geometry="frame",
    )
    satellite = Satellite(
        name="Sentinel-1C",
        orbit=orbit,
        instruments=[instrument],
    )
    start = datetime.fromisoformat(case["window_start_utc"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(case["window_end_utc_exclusive"].replace("Z", "+00:00"))
    target = Point(
        id=int(case["target"]["cell_id"]),
        latitude=case["target"]["latitude_deg"],
        longitude=case["target"]["longitude_deg"],
    )
    return collect_observations(target, satellite, start, end, omit_solar=True)


def test_pointed_registry_lookup_returns_one_explicit_s1c_iw_revision():
    error, text, result = _call(
        "lookup_instrument_configurations",
        {"instrument": "C-SAR", "spacecraft": "Sentinel-1C", "operating_mode": "IW"},
    )
    assert not error, text
    assert result["selection_required"] is True
    assert result["candidate_count"] == 1
    candidate = result["candidates"][0]
    assert candidate["configuration_id"] == CONFIGURATION_ID
    assert candidate["revision"] == CONFIGURATION_REVISION
    assert candidate["spacecraft"]["norad_id"] == 62261
    assert candidate["geometry"]["look_direction"] == "right"
    assert candidate["geometry"]["off_nadir_reference"] == "geodetic_nadir"
    assert candidate["geometry"]["incidence_reference"] == "ellipsoid_surface_normal"
    assert candidate["geometry"]["field_of_regard"]["value"] == 82.8
    assert candidate["approximation_status"] == "limited"
    assert candidate["reference_cases"]
    assert candidate["mission_reference_checks"][0]["measured_residuals"]
    reference = candidate["mission_reference_checks"][0]["measured_residuals"]
    assert reference["fixed_roll_near_edge_cross_track_p95_km"] == 14.29
    assert reference["fixed_roll_far_edge_cross_track_p95_km"] == 20.58
    assert reference["earth_fixed_epoch_abs_p95_s"] == 0.439
    assert candidate["mission_reference_checks"][0]["sample_sizes"]["iw_products"] == 402
    assert all(case["tolerance_basis"] for case in candidate["reference_cases"])
    assert all(case["epoch_tolerance_ms"] == 1.0 for case in candidate["reference_cases"])
    assert all(case["position_tolerance_m"] == 1.0 for case in candidate["reference_cases"])
    ascending, ascending_opposite, descending, descending_opposite = candidate["reference_cases"]
    assert ascending["measured_epoch_residual_ms"] == 0.000798
    assert ascending["measured_position_residual_m"] == 0.0
    assert ascending_opposite.get("measured_epoch_residual_ms") is None
    assert ascending_opposite.get("measured_position_residual_m") is None
    assert descending["measured_epoch_residual_ms"] == 0.000745
    assert descending["measured_position_residual_m"] == 0.0
    assert descending_opposite.get("measured_epoch_residual_ms") is None
    assert descending_opposite.get("measured_position_residual_m") is None
    assert "configuration" not in candidate


def test_ambiguous_spacecraft_lookup_returns_candidates_without_selecting_one(monkeypatch):
    first = _configuration()
    second_data = copy.deepcopy(first.portable())
    second_data["configuration_id"] = "sentinel-1d-c-sar-iw-fixture"
    second_data["spacecraft"]["name"] = "Sentinel-1D"
    second_data["spacecraft"]["aliases"] = ["Sentinel-1"]
    second_data["spacecraft"]["norad_id"] = 66315
    second = PointedConfiguration.model_validate(second_data)
    metadata = {
        "registry_status": "reviewed_shared",
        "source_change_checks": [],
        "source_change_notices": [],
        "refresh_uncertainty": [],
        "superseded_refresh_failures": [],
        "current_source_status": "checked",
        "base_revision": None,
    }
    monkeypatch.setattr(
        configuration_registry,
        "_all_entries_with_refreshes",
        lambda: [(first, metadata), (second, metadata)],
    )
    error, text, result = _call(
        "lookup_instrument_configurations", {"instrument": "C-SAR", "spacecraft": "Sentinel-1"}
    )
    assert not error, text
    assert result["candidate_count"] == 2
    assert result["ambiguous"] is True
    assert result["selection"] is None


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda data: data["geometry"].update(look_direction="left"), "roll"),
        (lambda data: data["geometry"].update(cross_track_field_of_view={"value": 0, "unit": "degree"}), "cross_track_field_of_view"),
        (lambda data: data["evidence"].pop(), "evidence"),
        (lambda data: data.update(operating_mode="EW"), "IW"),
    ],
)
def test_pointed_configuration_rejects_incompatible_or_unsupported_inputs(mutation, message):
    data = _configuration().portable()
    mutation(data)
    with pytest.raises(ValueError, match=message):
        PointedConfiguration.model_validate(data)


def test_pointed_study_rejects_more_than_one_configured_instrument():
    fixture = _reference()["cases"][0]
    draft = _begin(fixture["window_start_utc"], fixture["window_end_utc_exclusive"])
    _set_region(
        draft["draft_id"],
        fixture["target"]["longitude_deg"],
        fixture["target"]["latitude_deg"],
    )
    error, text, _ = _call(
        "prepare_pointed_study",
        {
            "draft_id": draft["draft_id"],
            "grid_rows": 1,
            "grid_columns": 1,
            "configuration": [_configuration().portable(), _configuration().portable()],
        },
    )
    assert error
    assert "one" in text.lower() or "multiple" in text.lower()


def test_pointed_study_rejects_window_outside_established_applicability():
    fixture = _reference()["cases"][0]
    draft = _begin(fixture["window_start_utc"], fixture["window_end_utc_exclusive"])
    _set_region(
        draft["draft_id"],
        fixture["target"]["longitude_deg"],
        fixture["target"]["latitude_deg"],
    )
    configuration = _configuration().portable()
    configuration["applicability"]["end_utc_exclusive"] = "2026-10-29T11:59:00Z"
    error, text, _ = _call(
        "prepare_pointed_study",
        {
            "draft_id": draft["draft_id"],
            "grid_rows": 1,
            "grid_columns": 1,
            "configuration": configuration,
        },
    )
    assert error
    assert "applicability" in text.lower()


@pytest.mark.parametrize("case_index", [0, 2], ids=["ascending", "descending"])
def test_complete_pointed_mcp_study_matches_independent_tatc_reference(case_index):
    case = _reference()["cases"][case_index]
    draft = _begin(case["window_start_utc"], case["window_end_utc_exclusive"])
    _set_region(
        draft["draft_id"],
        case["target"]["longitude_deg"],
        case["target"]["latitude_deg"],
    )
    error, text, proposal = _prepare(draft["draft_id"])
    assert not error, text
    assert proposal["approximation_status"] == "limited"
    assert any("fixed" in warning.lower() and "roll" in warning.lower() for warning in proposal["warnings"])
    assert any("acquisition" in warning.lower() for warning in proposal["warnings"])

    error, text, summary = _call(
        "finish_pointed_study",
        {
            "draft_id": draft["draft_id"],
            "accepted_setup_revision": proposal["setup_revision"],
            "accept_setup": True,
        },
    )
    assert not error, text
    assert summary["result_kind"] == "pointed_study"
    assert summary["accounting_method"] == "pointed_events_v1"
    assert summary["summary_counts"]["event_count"] == case["expected_event_count"]
    assert summary["configuration_id"] == CONFIGURATION_ID
    assert summary["configuration_revision"] == CONFIGURATION_REVISION
    assert summary["approximation_status"] == "limited"

    error, text, fetched = _call(
        "get_pointed_events", {"result_id": summary["result_id"], "offset": 0, "limit": 10}
    )
    assert not error, text
    assert fetched["total_events"] == case["expected_event_count"]
    assert fetched["warnings"] == summary["warnings"]
    if case["expected_event_count"]:
        actual = fetched["events"][0]
        direct = _direct_pointed_observations(case)
        assert len(direct) == 1
        reference_row = next(direct.itertuples(index=False))
        expected_epoch = datetime.fromisoformat(
            case["expected_epoch_utc"].replace("Z", "+00:00")
        )
        reference_epoch = reference_row.epoch.to_pydatetime(warn=False)
        assert abs((reference_epoch - expected_epoch).total_seconds()) <= 0.001
        # The independently constructed Point uses the fixture's reference ID;
        # the one-cell MCP grid assigns its sole center the stable grid ID 0.
        assert actual["cell_id"] == 0
        assert actual["configuration_id"] == CONFIGURATION_ID
        assert actual["instrument"] == "C-SAR"
        assert actual["satellite"] == str(reference_row.satellite)
        assert actual["instrument"] == str(reference_row.instrument)
        actual_epoch = datetime.fromisoformat(actual["epoch_utc"].replace("Z", "+00:00"))
        assert abs((actual_epoch - reference_epoch).total_seconds()) <= 0.001
        measured = next(
            reference_case
            for reference_case in _configuration().reference_cases
            if reference_case.fixture_case_id == case["case_id"]
        )
        residual_ms = abs(
            reference_row.epoch.value - pd.Timestamp(actual_epoch).value
        ) / 1e6
        assert residual_ms == measured.measured_epoch_residual_ms
        _, _, distance_m = Geod(ellps="WGS84").inv(
            actual["target_longitude_deg"],
            actual["target_latitude_deg"],
            float(reference_row.geometry.x),
            float(reference_row.geometry.y),
        )
        assert distance_m <= 1.0
        assert distance_m == measured.measured_position_residual_m
        assert actual["envelope_start_utc"] < actual["epoch_utc"] < actual["envelope_end_utc"]

    for tool, args, key in (
        ("get_pointed_daily", {"result_id": summary["result_id"], "offset": 0, "limit": 2}, "daily"),
        ("get_pointed_cells", {"result_id": summary["result_id"], "include": "all", "offset": 0, "limit": 2}, "geojson"),
        ("get_pointed_revisit", {"result_id": summary["result_id"]}, "event_spacing"),
        (
            "get_pointed_event_spacing",
            {"result_id": summary["result_id"], "offset": 0, "limit": 2},
            "event_spacing",
        ),
    ):
        error, text, payload = _call(tool, args)
        assert not error, text
        assert payload["warnings"] == summary["warnings"]
        assert payload["configuration_id"] == CONFIGURATION_ID
        assert key in payload


def test_reference_fixture_has_both_sides_and_broad_envelope_nonviews():
    reference = _reference()
    orbit = create_orbit_from_omm([reference["orbit"]["omm"]])
    configuration = _configuration().portable()
    broad_instrument = Instrument(name="IW envelope fixture", field_of_regard=82.8)
    pointed = PointedInstrument(
        name="C-SAR",
        field_of_regard=82.8,
        cross_track_field_of_view=13.3,
        along_track_field_of_view=0.23,
        roll_angle=-33.8,
        pitch_angle=0.0,
        nadir_reference=NadirReference.GEODETIC,
        velocity_frame=VelocityFrame.EARTH_FIXED,
        is_rectangular=True,
        view_geometry="frame",
    )
    for case in reference["cases"]:
        start = datetime.fromisoformat(case["window_start_utc"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(case["window_end_utc_exclusive"].replace("Z", "+00:00"))
        target = Point(
            id=case["target"]["cell_id"],
            latitude=case["target"]["latitude_deg"],
            longitude=case["target"]["longitude_deg"],
        )
        satellite = Satellite(
            name="Sentinel-1C", orbit=orbit, instruments=[pointed]
        )
        direct = collect_observations(target, satellite, start, end, omit_solar=True)
        assert len(direct) == case["expected_event_count"]
        adapted = collect_cell_events(
            {
                "configuration": configuration,
                "omm_records": _omm(),
                "cell_id": case["target"]["cell_id"],
                "latitude": case["target"]["latitude_deg"],
                "longitude": case["target"]["longitude_deg"],
                "start": start,
                "end": end,
            }
        )
        assert len(adapted) == len(direct)
        assert all(event["cell_id"] == case["target"]["cell_id"] for event in adapted)
        if case["expected_event_count"]:
            row = next(direct.itertuples(index=False))
            assert adapted[0]["satellite"] == str(row.satellite)
            assert adapted[0]["instrument"] == str(row.instrument)
            assert adapted[0]["configuration_id"] == CONFIGURATION_ID
            assert adapted[0]["configuration_revision"] == CONFIGURATION_REVISION
            expected_epoch = datetime.fromisoformat(
                case["expected_epoch_utc"].replace("Z", "+00:00")
            )
            direct_epoch = row.epoch.to_pydatetime(warn=False)
            assert abs((direct_epoch - expected_epoch).total_seconds()) <= 0.001
            _, _, distance_m = Geod(ellps="WGS84").inv(
                case["target"]["longitude_deg"],
                case["target"]["latitude_deg"],
                float(row.geometry.x),
                float(row.geometry.y),
            )
            assert distance_m <= 1.0
            assert abs((adapted[0]["epoch"] - direct_epoch).total_seconds()) <= 0.001
            _, _, adapted_distance_m = Geod(ellps="WGS84").inv(
                adapted[0]["target_longitude_deg"],
                adapted[0]["target_latitude_deg"],
                float(row.geometry.x),
                float(row.geometry.y),
            )
            assert adapted_distance_m <= 1.0

    for case in reference["broad_envelope_nonviews"]:
        start = datetime.fromisoformat(case["window_start_utc"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(case["window_end_utc_exclusive"].replace("Z", "+00:00"))
        target = Point(id=case["cell_id"], latitude=case["latitude_deg"], longitude=case["longitude_deg"])
        broad_satellite = Satellite(name="SENTINEL-1C", orbit=orbit, instruments=[broad_instrument])
        pointed_satellite = Satellite(name="SENTINEL-1C", orbit=orbit, instruments=[pointed])
        broad = collect_observations(target, broad_satellite, start, end, omit_solar=True)
        view = collect_observations(target, pointed_satellite, start, end, omit_solar=True)
        assert len(broad) == case["expected_envelope_event_count"]
        assert len(view) == case["expected_view_event_count"]
        adapted = collect_cell_events(
            {
                "configuration": configuration,
                "omm_records": _omm(),
                "cell_id": case["cell_id"],
                "latitude": case["latitude_deg"],
                "longitude": case["longitude_deg"],
                "start": start,
                "end": end,
            }
        )
        assert len(adapted) == case["expected_view_event_count"]
