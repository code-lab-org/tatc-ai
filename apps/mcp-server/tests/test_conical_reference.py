"""Independent TAT-C reference checks for the mapped AMSR2 configuration.

The expected events and projected positions in the fixture were generated
with direct TAT-C 3.6 public calls from the literal source parameters.  The
production mapping is compared against that fixture, not against another call
to itself.  Event comparisons are software-integration checks, not mission
accuracy claims.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pyproj import Geod
from skyfield.api import wgs84
from tatc.analysis import collect_observations
from tatc.schemas import Attitude, ConicalInstrument, Point, Satellite
from tatc.utils import VelocityFrame

from src.conical_accounting import normalize_events, summarize_events
from src.conical_config import ConicalConfiguration
from src.conical_tatc import build_conical_satellite, collect_cell_events

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "amsr2_conical_reference.json"
CONFIG = (
    Path(__file__).resolve().parents[1] / "data" / "amsr2_conical_configuration.json"
)
GEOD = Geod(ellps="WGS84")


def _fixture():
    return json.loads(FIXTURE.read_text())


def _config():
    return ConicalConfiguration.model_validate(json.loads(CONFIG.read_text()))


def _parse(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _distance_m(a_lon, a_lat, b_lon, b_lat):
    return abs(GEOD.inv(a_lon, a_lat, b_lon, b_lat)[2])


def _task(case, cell, configuration):
    return {
        "configuration": configuration.portable(),
        "omm_records": [_fixture()["orbit"]["omm"]],
        "cell_id": cell["cell_id"],
        "latitude": cell["center_lat"],
        "longitude": cell["center_lon"],
        "start": _parse(case["window_start_utc"]),
        "end": _parse(case["window_end_utc_exclusive"]),
    }


def test_mapped_events_match_independent_reference_and_both_directions():
    fixture = _fixture()
    configuration = _config()
    mapped = build_conical_satellite(configuration, [fixture["orbit"]["omm"]])
    orbit = mapped.satellite.orbit
    checked_directions = {}
    for case_name in ("descending", "ascending"):
        case = fixture["cases"][case_name]
        for cell in case["cells"]:
            events = collect_cell_events(_task(case, cell, configuration))
            assert len(events) == len(cell["events"]), (
                f"{case_name} cell {cell['cell_id']} event count changed"
            )
            for actual, expected in zip(events, cell["events"]):
                difference = abs(
                    (
                        actual["epoch"].astimezone(timezone.utc)
                        - _parse(expected["epoch_utc"])
                    ).total_seconds()
                )
                assert difference <= 0.001, (
                    f"{case_name} cell {cell['cell_id']} epoch differs by "
                    f"{difference} s"
                )
                assert actual["envelope_start"] == _parse(
                    expected["envelope_start_utc"]
                ), f"{case_name} envelope start changed"
                assert actual["envelope_end"] == _parse(expected["envelope_end_utc"])
                assert actual["satellite"] == expected["satellite"]
                assert actual["instrument"] == expected["instrument"]
                assert (
                    _distance_m(
                        cell["center_lon"],
                        cell["center_lat"],
                        actual["target_longitude_deg"],
                        actual["target_latitude_deg"],
                    )
                    <= 1.0
                )
                if case_name not in checked_directions:
                    track = orbit.get_orbit_track(
                        [actual["epoch"], actual["epoch"] + timedelta(seconds=60)]
                    )
                    latitudes = track.subpoint().latitude.degrees
                    checked_directions[case_name] = latitudes[1] - latitudes[0]
    assert checked_directions["descending"] < 0
    assert checked_directions["ascending"] > 0


def test_midnight_envelope_has_epoch_on_only_one_side():
    fixture = _fixture()
    configuration = _config()
    case = fixture["cases"]["midnight"]
    raw = []
    for cell in case["cells"]:
        raw.extend(collect_cell_events(_task(case, cell, configuration)))
    start = _parse(case["window_start_utc"])
    end = _parse(case["window_end_utc_exclusive"])
    events = normalize_events(
        raw,
        start,
        end,
        applicability_start=configuration.applicability.start_utc,
        applicability_end=configuration.applicability.end_utc_exclusive,
    )
    cells = [
        {
            "id": cell["cell_id"],
            "center_lon": cell["center_lon"],
            "center_lat": cell["center_lat"],
            "bounds": [
                cell["center_lon"] - 2.5,
                cell["center_lat"] - 1.0,
                cell["center_lon"] + 2.5,
                cell["center_lat"] + 1.0,
            ],
            "area_km2": 100.0,
        }
        for cell in case["cells"]
    ]
    summary = summarize_events(cells, events, start, end)
    first_day, second_day = summary["daily"]
    # Cells 2 and 3 have envelopes crossing midnight, but their epochs are
    # before midnight; they must not appear on the following day.
    crossing_ids = {2, 3}
    assert crossing_ids.issubset(first_day["observed_cell_ids"])
    assert crossing_ids.isdisjoint(second_day["observed_cell_ids"])
    for event in events:
        if event["cell_id"] in crossing_ids:
            assert event["epoch_utc"] < "2026-10-06T00:00:00"
            assert event["envelope_end_utc"] >= "2026-10-06T00:00:00"


def test_projected_reference_positions_within_one_meter():
    fixture = _fixture()
    configuration = _config()
    mapped = build_conical_satellite(configuration, [fixture["orbit"]["omm"]])
    instrument = mapped.satellite.get_instrument(0)
    time = _parse(fixture["projected_positions"]["time_utc"])
    track = mapped.satellite.orbit.get_orbit_track([time])
    for expected in fixture["projected_positions"]["positions"]:
        azimuth = expected["scan_azimuth_deg"]
        candidate = instrument.model_copy(update={"scan_center_azimuth": azimuth}).bind_attitude(
            instrument.platform_attitude
        )
        point = candidate.compute_footprint_center(track)
        distance = _distance_m(
            expected["longitude_deg"],
            expected["latitude_deg"],
            float(__import__("numpy").asarray(point.longitude.degrees).reshape(-1)[0]),
            float(__import__("numpy").asarray(point.latitude.degrees).reshape(-1)[0]),
        )
        assert distance <= 1.0, (
            f"projected reference position at azimuth {azimuth} differs by "
            f"{distance} m"
        )


def test_independent_inside_and_outside_view_points():
    fixture = _fixture()
    configuration = _config()
    mapped = build_conical_satellite(configuration, [fixture["orbit"]["omm"]])
    instrument = mapped.satellite.get_instrument(0)
    time = _parse(fixture["inside_outside"]["time_utc"])
    track = mapped.satellite.orbit.get_orbit_track([time])
    inside = fixture["inside_outside"]["inside"]
    outside = fixture["inside_outside"]["outside_sector"]
    inside_target = wgs84.latlon(
        inside["latitude_deg"], inside["longitude_deg"]
    )
    outside_target = wgs84.latlon(
        outside["latitude_deg"], outside["longitude_deg"]
    )
    assert bool(instrument.is_in_field_of_view(track, inside_target)[0]) is (
        inside["expected_in_field_of_view"]
    )
    assert bool(instrument.is_in_field_of_view(track, outside_target)[0]) is (
        outside["expected_in_field_of_view"]
    )
    # The outside point is still on the same cone and inside the broad
    # field-of-regard envelope, but outside the accepted scan sector.
    assert outside["cone_angle_deg"] == pytest.approx(47.564, abs=0.01)
    assert outside["cone_angle_deg"] <= instrument.field_of_regard / 2
    assert outside["scan_azimuth_deg"] > (
        configuration.geometry.scan_center_azimuth.value
        + configuration.geometry.scan_half_width.value
    )


def _synthetic_full_rotation():
    data = json.loads(CONFIG.read_text())
    data["configuration_id"] = "synthetic-full-rotation-test"
    data["revision"] = "test"
    data["instrument"] = "Synthetic full rotation"
    data["geometry"]["scan_half_width"] = {"value": 180.0, "unit": "degree"}
    data["source_conflicts"] = []
    return ConicalConfiguration.model_validate(data)


def test_two_crossings_in_one_envelope_are_preserved():
    fixture = _fixture()["two_crossings"]
    configuration = _synthetic_full_rotation()
    task = {
        "configuration": configuration.portable(),
        "omm_records": [_fixture()["orbit"]["omm"]],
        "cell_id": fixture["cell_id"],
        "latitude": fixture["center_lat"],
        "longitude": fixture["center_lon"],
        "start": _parse(fixture["window_start_utc"]),
        "end": _parse(fixture["window_end_utc_exclusive"]),
    }
    events = collect_cell_events(task)
    assert len(events) == 2
    assert len(fixture["events"]) == 2
    assert events[0]["envelope_start"] == events[1]["envelope_start"]
    assert events[0]["envelope_end"] == events[1]["envelope_end"]
    assert events[0]["epoch"] != events[1]["epoch"]
    for actual, expected in zip(events, fixture["events"]):
        assert (
            abs(
                (
                    actual["epoch"].astimezone(timezone.utc)
                    - _parse(expected["epoch_utc"])
                ).total_seconds()
            )
            <= 0.001
        )
    # Direct TAT-C reference for the same synthetic point.
    direct_instrument = ConicalInstrument(
        name="Synthetic full rotation",
        cone_angle=47.564,
        scan_center_azimuth=0.498,
        scan_half_width=180.0,
        along_track_field_of_view=1.0,
        nadir_reference="geodetic",
    )
    direct_satellite = Satellite(
        name="GCOM-W1",
        orbit=build_conical_satellite(
            configuration, [_fixture()["orbit"]["omm"]]
        ).satellite.orbit,
        instruments=[direct_instrument],
        attitude=Attitude(velocity_frame=VelocityFrame.INERTIAL),
    )
    direct = collect_observations(
        Point(id=0, latitude=fixture["center_lat"], longitude=fixture["center_lon"]),
        direct_satellite,
        _parse(fixture["window_start_utc"]),
        _parse(fixture["window_end_utc_exclusive"]),
    )
    assert len(direct) == 2
    cells = [
        {
            "id": 0,
            "center_lon": fixture["center_lon"],
            "center_lat": fixture["center_lat"],
            "bounds": [4.5, -61.0, 5.5, -60.0],
            "area_km2": 100.0,
        }
    ]
    normalized = normalize_events(
        events,
        _parse(fixture["window_start_utc"]),
        _parse(fixture["window_end_utc_exclusive"]),
    )
    summary = summarize_events(
        cells,
        normalized,
        _parse(fixture["window_start_utc"]),
        _parse(fixture["window_end_utc_exclusive"]),
    )
    assert summary["metrics"]["event_count"] == 2
    assert summary["metrics"]["envelope_period_count"] == 1
    assert summary["daily"][0]["observed_cell_ids"] == [0]
    assert summary["daily"][0]["observed_area_km2"] == 100.0
    assert summary["event_spacing"][0]["event_spacing_hours"] == pytest.approx(
        [(events[1]["epoch"] - events[0]["epoch"]).total_seconds() / 3600],
        abs=1e-6,
    )


def test_mission_reference_residuals_have_justified_tolerances():
    mission = _fixture()["mission_reference"]
    residuals = mission["residuals"]
    tolerances = mission["engineering_tolerances"]
    assert tolerances["orbit_position_km"] > residuals["orbit_along_track_p95_km"]
    assert tolerances["event_time_s"] > residuals["event_time_p95_s"]
    assert tolerances["swath_width_km"] > residuals["swath_width_difference_km"]
    assert "not mission-accuracy" in mission["tolerance_justification"]
    config = _config()
    orbit_check = next(
        check for check in config.geometry_checks if check.check_id == "orbit-reference-residual"
    )
    assert orbit_check.status == "limited"
    assert orbit_check.reported_value == pytest.approx(
        residuals["orbit_along_track_p95_km"]
    )
