"""Pointed Sentinel IW study through the staged MCP tools and TAT-C 3.6.0."""

import asyncio
import copy
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest
from fastmcp import Client
from pyproj import Geod
from skyfield.api import load, wgs84
from skyfield.framelib import itrs
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
SKYFIELD_TIMESCALE = load.timescale(builtin=True)


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


def _direct_pointed_observations(case, roll_angle_deg=None):
    reference = _reference()
    orbit = create_orbit_from_omm([reference["orbit"]["omm"]])
    geometry = reference["independent_tatc_configuration"]
    instrument = PointedInstrument(
        name=geometry["name"],
        field_of_regard=geometry["field_of_regard_deg"],
        cross_track_field_of_view=geometry["cross_track_field_of_view_deg"],
        along_track_field_of_view=geometry["along_track_field_of_view_deg"],
        roll_angle=(
            geometry["roll_angle_deg"]
            if roll_angle_deg is None
            else roll_angle_deg
        ),
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


def _skyfield_time(value):
    timestamp = pd.Timestamp(value)
    return SKYFIELD_TIMESCALE.utc(
        timestamp.year,
        timestamp.month,
        timestamp.day,
        timestamp.hour,
        timestamp.minute,
        timestamp.second
        + timestamp.microsecond / 1_000_000
        + timestamp.nanosecond / 1_000_000_000,
    )


def _independent_orbit_geometry(case, epoch):
    """Classify a point from a propagated Skyfield ITRS frame in the test only."""
    reference = _reference()
    orbit = create_orbit_from_omm([reference["orbit"]["omm"]])
    time = _skyfield_time(epoch)
    satellite = orbit.get_orbit_track_at_time(time)
    subpoint = wgs84.subpoint(satellite)
    sub_latitude = subpoint.latitude.degrees
    sub_longitude = subpoint.longitude.degrees
    satellite_xyz = satellite.frame_xyz(itrs).km
    nadir_xyz = wgs84.latlon(sub_latitude, sub_longitude).itrs_xyz.km
    target = case["target"]
    target_xyz = wgs84.latlon(
        target["latitude_deg"], target["longitude_deg"]
    ).itrs_xyz.km
    nadir_vector = nadir_xyz - satellite_xyz
    target_vector = target_xyz - satellite_xyz
    cosine = float(
        nadir_vector.dot(target_vector)
        / (math.sqrt(float(nadir_vector.dot(nadir_vector)))
           * math.sqrt(float(target_vector.dot(target_vector))))
    )
    off_nadir_deg = math.degrees(math.acos(max(-1.0, min(1.0, cosine))))

    before = wgs84.subpoint(
        orbit.get_orbit_track_at_time(
            _skyfield_time(pd.Timestamp(epoch) - pd.Timedelta(seconds=5))
        )
    )
    after = wgs84.subpoint(
        orbit.get_orbit_track_at_time(
            _skyfield_time(pd.Timestamp(epoch) + pd.Timedelta(seconds=5))
        )
    )
    track_azimuth, _, _ = Geod(ellps="WGS84").inv(
        before.longitude.degrees,
        before.latitude.degrees,
        after.longitude.degrees,
        after.latitude.degrees,
    )
    target_bearing, _, _ = Geod(ellps="WGS84").inv(
        sub_longitude,
        sub_latitude,
        target["longitude_deg"],
        target["latitude_deg"],
    )
    relative_bearing = (target_bearing - track_azimuth + 180) % 360 - 180
    return {
        "side": "right" if relative_bearing > 0 else "left",
        "off_nadir_deg": off_nadir_deg,
        "subpoint_latitude_deg": sub_latitude,
        "subpoint_longitude_deg": sub_longitude,
    }


def _skyfield_subpoint(omm_records, epoch):
    orbit = create_orbit_from_omm(list(omm_records))
    geographic = wgs84.subpoint(orbit.get_orbit_track_at_time(_skyfield_time(epoch)))
    return geographic.latitude.degrees, geographic.longitude.degrees


@pytest.mark.parametrize(
    "case_id",
    [
        "ascending-right-looking",
        "ascending-near-range-gap",
        "ascending-left-side-envelope-only",
        "descending-right-looking",
        "descending-beyond-far-edge",
        "descending-left-side-envelope-only",
    ],
)
def test_reference_points_have_independently_projected_side_and_off_nadir(case_id):
    reference = _reference()
    case = next(case for case in reference["cases"] if case["case_id"] == case_id)
    if case["expected_event_count"]:
        epoch = case["expected_epoch_utc"]
    elif case.get("expected_left_roll_event_count"):
        direct = _direct_pointed_observations(case, roll_angle_deg=33.8)
        assert len(direct) == case["expected_left_roll_event_count"]
        epoch = next(direct.itertuples(index=False)).epoch
    else:
        epoch = case["reference_epoch_utc"]
    geometry = _independent_orbit_geometry(case, epoch)
    assert geometry["side"] == case["expected_side"]
    minimum, maximum = case["expected_off_nadir_range_deg"]
    assert minimum <= geometry["off_nadir_deg"] <= maximum


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
    reference_cases = {
        case["fixture_case_id"]: case for case in candidate["reference_cases"]
    }
    ascending = reference_cases["ascending-right-looking"]
    descending = reference_cases["descending-right-looking"]
    assert ascending["measured_epoch_residual_ms"] == 0.000798
    assert ascending["measured_position_residual_m"] > 0
    assert ascending["measured_position_residual_m"] < 1.0
    assert descending["measured_epoch_residual_ms"] == 0.000745
    assert descending["measured_position_residual_m"] > 0
    assert descending["measured_position_residual_m"] < 1.0
    assert reference_cases["ascending-near-range-gap"]["expected_event_count"] == 0
    assert reference_cases["descending-beyond-far-edge"]["expected_event_count"] == 0
    for case_id in (
        "ascending-left-side-envelope-only",
        "descending-left-side-envelope-only",
    ):
        case = reference_cases[case_id]
        assert case["expected_event_count"] == 0
        assert "left" in case["tolerance_basis"]
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
        (
            lambda data: data["geometry"].update(nadir_reference="geocentric"),
            "off_nadir_reference requires nadir_reference='geodetic'",
        ),
        (lambda data: data["geometry"].update(cross_track_field_of_view={"value": 0, "unit": "degree"}), "cross_track_field_of_view"),
        (lambda data: data["evidence"].pop(), "evidence"),
        (lambda data: data.update(operating_mode="EW"), "IW"),
        (lambda data: data["geometry"].update(off_nadir_source_precision_deg=True), "off_nadir_source_precision_deg"),
        (lambda data: data["geometry"].update(maximum_roll_steering_delta_deg=True), "maximum_roll_steering_delta_deg"),
        (lambda data: data["reference_cases"][0].update(epoch_tolerance_ms=True), "epoch_tolerance_ms"),
        (lambda data: data.update(approximation_status="supported"), "approximation_status"),
    ],
)
def test_pointed_configuration_rejects_incompatible_or_unsupported_inputs(mutation, message):
    data = _configuration().portable()
    mutation(data)
    with pytest.raises(ValueError, match=message):
        PointedConfiguration.model_validate(data)


def test_pointed_daylight_basis_requires_evidence_or_unclaimed_assumption():
    data = _configuration().portable()
    data["geometry"]["illumination_requirement"] = "target_sunlit"
    data["geometry"]["illumination_requirement_basis"] = "source_evidence"
    with pytest.raises(ValueError, match="source-based target_sunlit setting requires evidence"):
        PointedConfiguration.model_validate(data)

    data["evidence"][0]["supports"].append("geometry.illumination_requirement")
    source_backed = PointedConfiguration.model_validate(data)
    assert any(
        "source-backed target-sunlit requirement" in warning
        for warning in source_backed.warnings()
    )

    data["geometry"]["illumination_requirement_basis"] = "study_assumption"
    data["geometry"]["illumination_requirement_note"] = "Study sensitivity only."
    data["evidence"][0]["supports"].remove("geometry.illumination_requirement")
    assumed = PointedConfiguration.model_validate(data)
    assert any("not a source-established scientific requirement" in warning for warning in assumed.warnings())

    data["evidence"][0]["supports"].append("geometry.illumination_requirement")
    with pytest.raises(ValueError, match="study assumption cannot be source-supported"):
        PointedConfiguration.model_validate(data)


def test_pointed_prepare_preserves_daylight_assumption_basis():
    case = _reference()["cases"][0]
    draft = _begin(case["window_start_utc"], case["window_end_utc_exclusive"])
    _set_region(
        draft["draft_id"],
        case["target"]["longitude_deg"],
        case["target"]["latitude_deg"],
    )
    configuration = _configuration().portable()
    configuration["geometry"]["illumination_requirement"] = "target_sunlit"
    configuration["geometry"]["illumination_requirement_basis"] = "source_evidence"
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
    assert "source-based target_sunlit setting requires evidence" in text

    configuration["geometry"]["illumination_requirement_basis"] = "study_assumption"
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
    assert "requires an illumination_requirement_note" in text

    configuration["geometry"]["illumination_requirement_note"] = "Study sensitivity only."
    error, text, proposal = _call(
        "prepare_pointed_study",
        {
            "draft_id": draft["draft_id"],
            "grid_rows": 1,
            "grid_columns": 1,
            "configuration": configuration,
        },
    )
    assert not error, text
    assert any("not a source-established scientific requirement" in warning for warning in proposal["warnings"])

    configuration["evidence"][0]["supports"].append("geometry.illumination_requirement")
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
    assert "study assumption cannot be source-supported" in text


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


def test_pointed_study_rejects_conflicting_nadir_references_through_mcp():
    fixture = _reference()["cases"][0]
    draft = _begin(fixture["window_start_utc"], fixture["window_end_utc_exclusive"])
    _set_region(
        draft["draft_id"],
        fixture["target"]["longitude_deg"],
        fixture["target"]["latitude_deg"],
    )
    configuration = _configuration().portable()
    configuration["geometry"]["nadir_reference"] = "geocentric"
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
    assert "off_nadir_reference" in text
    assert "geodetic" in text


@pytest.mark.parametrize(
    "mean_motion, altitude_relation", [(14.65, "below"), (14.50, "above")]
)
def test_pointed_prepare_rejects_omm_altitude_outside_source_range(
    monkeypatch, mean_motion, altitude_relation
):
    fixture = _reference()["cases"][0]
    unsupported_omm = copy.deepcopy(_omm()[0])
    unsupported_omm["MEAN_MOTION"] = mean_motion
    monkeypatch.setattr(
        generic_coverage.celestrak_client,
        "get_satellite_info",
        lambda _identifier: {
            "norad_id": 62261,
            "name": "SENTINEL-1C",
            "omm": [unsupported_omm],
        },
    )
    draft = _begin(fixture["window_start_utc"], fixture["window_end_utc_exclusive"])
    staged_altitude_km = draft["satellites"][0]["omm_mean_altitude_km"]
    if altitude_relation == "below":
        assert staged_altitude_km < 698.0
    else:
        assert staged_altitude_km > 726.0
    _set_region(
        draft["draft_id"],
        fixture["target"]["longitude_deg"],
        fixture["target"]["latitude_deg"],
    )
    error, text, _ = _prepare(draft["draft_id"])
    assert error
    assert "mean orbit altitude" in text.lower()
    assert "698" in text and "726" in text


def test_pointed_warnings_follow_configuration_evidence_and_approximations():
    data = _configuration().portable()
    data["instrument"] = "Synthetic instrument"
    data["spacecraft"]["name"] = "Synthetic spacecraft"
    for index, approximation in enumerate(data["approximations"]):
        approximation["description"] = f"Synthetic approximation detail {index}."
        approximation["impact"] = f"Synthetic impact {index}."
    data["mission_reference_checks"][0]["limitation"] = (
        "The comparison covers a separate product sample."
    )
    configuration = PointedConfiguration.model_validate(data)
    warnings = "\n".join(configuration.warnings())
    assert "Synthetic instrument" in warnings
    assert "Synthetic spacecraft" in warnings
    assert "Synthetic approximation detail 0." in warnings
    assert "The comparison covers a separate product sample." in warnings
    assert "Synthetic impact 2." in warnings
    assert "The fixed rectangular view approximates altitude-dependent roll" not in warnings
    assert "published Synthetic spacecraft TAT-C validation residuals" not in warnings


@pytest.mark.parametrize(
    "case_id", ["ascending-right-looking", "descending-right-looking"]
)
def test_complete_pointed_mcp_study_matches_independent_tatc_reference(case_id):
    case = next(
        item for item in _reference()["cases"] if item["case_id"] == case_id
    )
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
        adapter_subpoint = _skyfield_subpoint(_omm(), actual_epoch)
        direct_subpoint = _skyfield_subpoint(_omm(), reference_row.epoch)
        _, _, distance_m = Geod(ellps="WGS84").inv(
            adapter_subpoint[1],
            adapter_subpoint[0],
            direct_subpoint[1],
            direct_subpoint[0],
        )
        assert distance_m <= 1.0
        assert round(distance_m, 3) == measured.measured_position_residual_m
        assert actual["target_longitude_deg"] == case["target"]["longitude_deg"]
        assert actual["target_latitude_deg"] == case["target"]["latitude_deg"]
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
            assert abs((adapted[0]["epoch"] - direct_epoch).total_seconds()) <= 0.001

    cases_by_id = {case["case_id"]: case for case in reference["cases"]}
    for envelope_case in reference["broad_envelope_nonviews"]:
        case = cases_by_id[envelope_case["case_id"]]
        start = datetime.fromisoformat(case["window_start_utc"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(case["window_end_utc_exclusive"].replace("Z", "+00:00"))
        target_data = case["target"]
        target = Point(
            id=target_data["cell_id"],
            latitude=target_data["latitude_deg"],
            longitude=target_data["longitude_deg"],
        )
        broad_satellite = Satellite(name="SENTINEL-1C", orbit=orbit, instruments=[broad_instrument])
        pointed_satellite = Satellite(name="SENTINEL-1C", orbit=orbit, instruments=[pointed])
        broad = collect_observations(target, broad_satellite, start, end, omit_solar=True)
        view = collect_observations(target, pointed_satellite, start, end, omit_solar=True)
        assert len(broad) == envelope_case["expected_envelope_event_count"]
        assert len(view) == envelope_case["expected_view_event_count"]
        if case.get("expected_left_roll_event_count"):
            left_roll = _direct_pointed_observations(case, roll_angle_deg=33.8)
            assert len(left_roll) == case["expected_left_roll_event_count"]
            left_row = next(left_roll.itertuples(index=False))
            geometry = _independent_orbit_geometry(case, left_row.epoch)
            assert geometry["side"] == "left"
            assert 33.0 <= geometry["off_nadir_deg"] <= 35.0
        adapted = collect_cell_events(
            {
                "configuration": configuration,
                "omm_records": _omm(),
                "cell_id": target_data["cell_id"],
                "latitude": target_data["latitude_deg"],
                "longitude": target_data["longitude_deg"],
                "start": start,
                "end": end,
            }
        )
        assert len(adapted) == envelope_case["expected_view_event_count"]
