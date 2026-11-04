"""Interval accounting, validated mission inputs, and real TATC observations."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
import json

import pytest

from src.coverage_analysis import (
    CoverageRequest,
    analyze,
    build_satellites,
    summarize_events,
    summarize_intervals,
)

START = datetime(2026, 10, 7, tzinfo=timezone.utc)
END = START + timedelta(hours=1)


def observation(start, end, satellite="A", instrument="Sensor"):
    return {
        "start": (START + timedelta(minutes=start)).isoformat(),
        "end": (START + timedelta(minutes=end)).isoformat(),
        "satellite": satellite,
        "instrument": instrument,
    }


def scenario():
    return {
        "satellites": [
            {
                "name": "Equatorial study satellite",
                "orbit": {
                    "type": "circular",
                    "mean_altitude": 550000,
                    "inclination": 0,
                    "epoch": START.isoformat(),
                },
                "instruments": [{"name": "Sensor", "field_of_regard": 120}],
            }
        ],
        "points": [{"name": "North pole", "latitude": 90, "longitude": 0}],
        "start_time": START.isoformat(),
        "end_time": END.isoformat(),
        "display_step_seconds": 120,
    }


def test_union_handles_nested_touching_and_overlapping_observations():
    result = summarize_intervals(
        [
            observation(10, 30),
            observation(12, 15, "B"),
            observation(25, 35, "C"),
            observation(35, 40, "D"),
            observation(50, 55),
        ],
        START,
        END,
    )
    assert [i["duration_seconds"] for i in result["coverage_intervals"]] == [1800, 300]
    assert len(result["coverage_intervals"][0]["contributors"]) == 4
    assert [i["duration_seconds"] for i in result["gaps"]] == [600, 600, 300]
    assert [i["boundary"] for i in result["gaps"]] == [True, False, True]
    assert result["metrics"]["mean_complete_revisit_seconds"] == 600
    assert result["metrics"]["observation_count"] == 5
    assert (
        result["metrics"]["covered_seconds"]
        + sum(g["duration_seconds"] for g in result["gaps"])
        == 3600
    )


@pytest.mark.parametrize(
    "observations, covered, longest, mean",
    [
        ([], 0, 3600, None),
        ([observation(20, 30)], 600, 1800, None),
        ([observation(-10, 70)], 3600, 0, None),
        ([observation(-20, -10), observation(70, 80)], 0, 3600, None),
        ([observation(0, 10), observation(50, 60)], 1200, 2400, 2400),
        ([observation(10, 10)], 0, 3600, None),
    ],
)
def test_boundary_and_empty_cases(observations, covered, longest, mean):
    result = summarize_intervals(observations, START, END)
    assert result["metrics"]["covered_seconds"] == covered
    assert result["metrics"]["longest_gap_seconds"] == longest
    assert result["metrics"]["mean_complete_revisit_seconds"] == mean


def test_normalizes_offsets_without_accepting_naive_times():
    data = scenario()
    data["start_time"] = "2026-10-06T17:00:00-07:00"
    assert CoverageRequest.model_validate(data).start_time == START
    data["start_time"] = "2026-10-07T00:00:00"
    with pytest.raises(ValueError, match="UTC offset"):
        CoverageRequest.model_validate(data)


@pytest.mark.parametrize(
    "change, match",
    [
        (lambda d: d.update(end_time=START.isoformat()), "24 hours"),
        (
            lambda d: d.update(end_time=(START + timedelta(days=2)).isoformat()),
            "24 hours",
        ),
        (lambda d: d.update(display_step_seconds=0), "from 30 to 300"),
        (lambda d: d["points"][0].update(latitude=float("nan")), "finite"),
        (lambda d: d["points"].append(deepcopy(d["points"][0])), "unique"),
        (lambda d: d["satellites"][0].pop("instruments"), "explicit instruments"),
        (
            lambda d: d["satellites"][0]["instruments"][0].pop("field_of_regard"),
            "field_of_regard",
        ),
        (lambda d: d["satellites"][0]["orbit"].pop("epoch"), "explicit orbit epoch"),
        (lambda d: d["satellites"][0]["orbit"].update(inclnation=50), "Unknown"),
        # Observed Scout call: the error must name the field TATC accepts.
        (
            lambda d: d["satellites"][0]["orbit"].update(raan=10),
            "Unknown CircularOrbit fields: raan. Accepted fields: .*right_ascension_ascending_node",
        ),
        (
            lambda d: d["satellites"][0]["instruments"][0].update(field_of_view=30),
            "Unknown",
        ),
        (lambda d: d["satellites"].append(deepcopy(d["satellites"][0])), "unique"),
        (
            lambda d: d["satellites"][0]["instruments"][0].update(min_access_time=-10),
            "not be negative",
        ),
        (
            lambda d: d["satellites"][0]["instruments"][0].update(
                access_time_fixed=True
            ),
            "must be positive",
        ),
    ],
)
def test_rejects_invalid_or_ambiguous_scenarios(change, match):
    data = scenario()
    change(data)
    with pytest.raises(ValueError, match=match):
        build_satellites(CoverageRequest.model_validate(data))


def test_workload_limit_is_checked_before_analysis():
    data = scenario()
    data["points"] = [{"name": str(i), "latitude": i, "longitude": 0} for i in range(8)]
    data["satellites"] = [
        dict(deepcopy(data["satellites"][0]), name=str(i)) for i in range(4)
    ]
    data["end_time"] = (START + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError, match="point-instrument-hours"):
        build_satellites(CoverageRequest.model_validate(data))


def test_supplied_tle_uses_its_own_epoch():
    data = scenario()
    data["start_time"] = "2021-06-05T00:00:00Z"
    data["end_time"] = "2021-06-05T01:00:00Z"
    data["satellites"][0]["orbit"] = {
        "type": "tle",
        "tle": [
            "1 25544U 98067A   21156.30527927  .00003432  00000-0  70541-4 0  9993",
            "2 25544  51.6455  41.4969 0003508  68.0432  78.3395 15.48957534286754",
        ],
    }
    satellites = build_satellites(CoverageRequest.model_validate(data))
    assert satellites[0].orbit.get_epoch().year == 2021


def test_real_tatc_empty_location_and_display_independence():
    request = CoverageRequest.model_validate(scenario())
    result = analyze(request, include_tracks=True)
    assert result["points"][0]["observations"] == []
    assert result["points"][0]["metrics"]["longest_gap_seconds"] == 3600
    assert len(result["tracks"][0]["samples"]) == 31
    assert result["tracks"][0]["samples"][-1][0] == "2026-10-07T01:00:00Z"
    with mock.patch(
        "src.coverage_analysis._display_track", side_effect=ValueError("sample failed")
    ):
        with pytest.raises(ValueError, match="sample failed"):
            analyze(request, include_tracks=True)
        numerical = analyze(request)
    assert numerical["points"] == result["points"]
    assert "tracks" not in numerical


def test_real_tatc_observations_match_reference_and_do_not_depend_on_display_step():
    from tatc.analysis import collect_observations
    from tatc.schemas import Point

    path = Path(__file__).resolve().parents[1] / "src" / "coverage-scenario.json"
    request = CoverageRequest.model_validate_json(path.read_text())
    result = analyze(request)
    phoenix = result["points"][0]
    assert phoenix["metrics"]["observation_count"] > 0
    satellites = build_satellites(request)
    point = Point(id=0, **request.points[0].model_dump(exclude={"name"}))
    reference = []
    for satellite in satellites:
        frame = collect_observations(
            point, satellite, request.start_time, request.end_time
        )
        reference.extend(
            (row.start.floor("us").isoformat(), row.end.floor("us").isoformat())
            for row in frame.itertuples()
        )
    actual = [
        (
            datetime.fromisoformat(o["start"]).isoformat(),
            datetime.fromisoformat(o["end"]).isoformat(),
        )
        for o in phoenix["observations"]
    ]
    assert sorted(actual) == sorted(reference)
    assert all(
        len(o[key].split(".")[-1]) <= 7  # six digits and Z, like other times
        for o in phoenix["observations"]
        for key in ("start", "end")
    )
    slower = analyze(request.model_copy(update={"display_step_seconds": 300}))
    assert slower["points"] == result["points"]
    assert "tracks" not in result
    # Recorded scenario is replayable after removing the derived epoch annotation.
    replay = dict(result["scenario"])
    replay.pop("orbit_epochs")
    assert analyze(CoverageRequest.model_validate(replay))["points"] == result["points"]
    json.dumps(result, allow_nan=False)


def tle_line(body):
    from src.coverage_analysis import _tle_checksum

    return body + str(_tle_checksum(body))


# Observed Scout fabrications: catalog 27651 with a 2020-06-26 epoch, written
# for 2022/2024 study windows. One has a bad checksum, one a 70-character line.
FABRICATED_LINE1 = tle_line(
    "1 27651U 03004A   20178.74061727  .00000023  00000-0  15761-4 0  999"
)
FABRICATED_LINE2 = tle_line(
    "2 27651  97.7362 166.4522 0001235  89.5763 270.5576 14.5710853791739"
)


@pytest.mark.parametrize(
    "tle, start, match",
    [
        (
            [FABRICATED_LINE1[:-1] + "0", FABRICATED_LINE2],
            "2024-06-01T00:00:00Z",
            "checksum",
        ),
        (
            # A valid checksum over the first 68 characters, then a stray digit.
            [FABRICATED_LINE1, FABRICATED_LINE2[:-1] + "0" + FABRICATED_LINE2[-1]],
            "2022-03-01T00:00:00Z",
            "69 characters",
        ),
        (
            [FABRICATED_LINE1, FABRICATED_LINE2],
            "2024-06-01T00:00:00Z",
            "Element epoch 2020-06-26 .* is 1435 days from the study window",
        ),
    ],
)
def test_fabricated_or_stale_tles_are_rejected_with_a_source_hint(tle, start, match):
    data = scenario()
    data["satellites"][0]["orbit"] = {"type": "tle", "tle": tle}
    data["start_time"] = start
    data["end_time"] = start.replace("T00", "T01")
    with mock.patch("tatc.analysis.collect_observations") as collect:
        with pytest.raises(ValueError, match=match) as error:
            analyze(CoverageRequest.model_validate(data))
    collect.assert_not_called()
    assert "Never write orbital elements yourself; ask the user" in str(error.value)


def test_well_formed_tle_near_its_window_is_accepted():
    data = scenario()
    data["satellites"][0]["orbit"] = {
        "type": "tle",
        "tle": [FABRICATED_LINE1, FABRICATED_LINE2],
    }
    data["start_time"] = "2020-07-20T00:00:00Z"
    data["end_time"] = "2020-07-20T01:00:00Z"
    satellites = build_satellites(CoverageRequest.model_validate(data))
    assert satellites[0].orbit.get_epoch().date().isoformat() == "2020-06-26"


# The same element set as FABRICATED_LINE1/2, as CelesTrak OMM.
OMM_RECORD = {
    "OBJECT_NAME": "SPOT 5",
    "OBJECT_ID": "2002-021A",
    "EPOCH": "2020-06-26T17:46:29.332128",
    "MEAN_MOTION": 14.57108537,
    "ECCENTRICITY": 0.0001235,
    "INCLINATION": 97.7362,
    "RA_OF_ASC_NODE": 166.4522,
    "ARG_OF_PERICENTER": 89.5763,
    "MEAN_ANOMALY": 270.5576,
    "EPHEMERIS_TYPE": 0,
    "CLASSIFICATION_TYPE": "U",
    "NORAD_CAT_ID": 27651,
    "ELEMENT_SET_NO": 999,
    "REV_AT_EPOCH": 91739,
    "BSTAR": 1.5761e-5,
    "MEAN_MOTION_DOT": 2.3e-7,
    "MEAN_MOTION_DDOT": 0,
}


def test_omm_orbits_from_the_satellite_info_tool_are_tatc_gp_orbits():
    data = scenario()
    data["satellites"][0]["orbit"] = {"type": "omm", "elements": [OMM_RECORD]}
    data["start_time"] = "2020-07-20T00:00:00Z"
    data["end_time"] = "2020-07-20T01:00:00Z"
    satellites = build_satellites(CoverageRequest.model_validate(data))
    assert satellites[0].orbit.type == "gp"
    assert satellites[0].orbit.get_epoch().date().isoformat() == "2020-06-26"
    data["start_time"] = "2024-06-01T00:00:00Z"
    data["end_time"] = "2024-06-01T01:00:00Z"
    with pytest.raises(ValueError, match="Element epoch 2020-06-26 .* 1435 days"):
        build_satellites(CoverageRequest.model_validate(data))


POINTED = {
    "name": "Pointed",
    "field_of_regard": 100,
    "cross_track_field_of_view": 100,
    "along_track_field_of_view": 2,
}
CONICAL = {
    "name": "Conical",
    "field_of_regard": 120,
    "cone_angle": 47.5,
    "scan_half_width": 70,
    "along_track_field_of_view": 1,
}
BASIC = {"name": "Basic", "field_of_regard": 100}


def sensor_request(*instruments):
    """A pass over 45N 100W, seen by each of these instruments in the window."""
    return CoverageRequest.model_validate(
        {
            "satellites": [
                {
                    "name": "S",
                    "orbit": {
                        "type": "circular",
                        "mean_altitude": 550000,
                        "inclination": 97.5,
                        "right_ascension_ascending_node": 0,
                        "true_anomaly": 0,
                        "epoch": START.isoformat(),
                    },
                    "instruments": list(instruments),
                }
            ],
            "points": [{"name": "P", "latitude": 45, "longitude": -100}],
            "start_time": START.isoformat(),
            "end_time": (START + timedelta(hours=12)).isoformat(),
        }
    )


def in_view(request, time):
    from skyfield.api import wgs84

    satellite = build_satellites(request)[0]
    moment = datetime.fromisoformat(time)
    track = satellite.orbit.to_gp_orbit().get_orbit_track([moment, moment])
    target = wgs84.latlon([45, 45], [-100, -100])
    return bool(satellite.instruments[0].is_in_field_of_view(track, target)[0])


@pytest.mark.parametrize(
    "instrument, method",
    [(POINTED, "pointed_events_v1"), (CONICAL, "conical_events_v1")],
)
def test_sensor_counts_valid_epochs_and_never_its_field_of_regard_envelope(
    instrument, method
):
    # Review reproduction: TATC's period was reported as continuous coverage,
    # including times when TATC says this sensor cannot see the target.
    request = sensor_request(instrument)
    result = analyze(request)
    point = result["points"][0]
    event = point["observations"][0]
    assert in_view(request, event["epoch"])
    assert not in_view(request, event["envelope_start"])
    assert not in_view(request, event["envelope_end"])
    assert event["envelope_duration_seconds"] > 100
    assert event["accounting_method"] == method
    assert not {"start", "end", "duration_seconds"} & event.keys()

    metrics = point["metrics"]
    assert metrics["observation_count"] == metrics["distinct_event_count"] == 1
    assert metrics["first_event_utc"] == metrics["last_event_utc"] == event["epoch"]
    for unavailable in (
        "coverage_period_count",
        "covered_seconds",
        "coverage_fraction",
        "longest_gap_seconds",
        "mean_complete_revisit_seconds",
    ):
        assert metrics[unavailable] is None
    assert metrics["continuous_viewing_duration_available"] is False
    assert metrics["continuous_uncovered_duration_available"] is False
    assert point["coverage_intervals"] == [] and point["gaps"] == []
    assert result["accounting"]["event_based"] is True
    assert result["accounting"]["method"] == method
    assert "envelope" in " ".join(result["assumptions"])


def test_basic_instrument_keeps_interval_accounting():
    result = analyze(sensor_request(BASIC))
    point = result["points"][0]
    assert result["accounting"]["event_based"] is False
    assert point["metrics"]["covered_seconds"] > 0
    assert point["metrics"]["coverage_fraction"] > 0
    assert point["metrics"]["continuous_viewing_duration_available"] is True
    assert point["coverage_intervals"] and point["gaps"]
    assert {"start", "end", "duration_seconds"} <= point["observations"][0].keys()


def test_study_mixing_basic_and_event_instruments_reports_no_continuous_metrics():
    result = analyze(sensor_request(BASIC, POINTED))
    point = result["points"][0]
    assert result["accounting"]["event_based"] is True
    assert point["metrics"]["covered_seconds"] is None
    assert point["metrics"]["observation_count"] == 2
    # Only the pointed row is a viewing event.
    assert point["metrics"]["distinct_event_count"] == 1
    assert {"start", "end"} <= next(
        o for o in point["observations"] if o["instrument"] == "Basic"
    ).keys()


def test_event_study_without_events_is_unavailable_not_zero():
    request = sensor_request(POINTED)
    request.end_time = START + timedelta(hours=1)  # Before the first pass.
    metrics = analyze(request)["points"][0]["metrics"]
    assert metrics["observation_count"] == 0
    assert metrics["first_event_utc"] is None
    assert metrics["mean_event_spacing_hours"] is None
    assert metrics["coverage_fraction"] is None


def test_event_spacing_uses_distinct_epochs_and_ignores_basic_intervals():
    def event(hour):
        return {
            "epoch": (START + timedelta(hours=hour)).isoformat(),
            "envelope_start": "ignored",
        }

    basic = {"epoch": (START + timedelta(hours=9)).isoformat()}
    metrics = summarize_events(
        [event(1), event(1), event(2), event(5), basic], "pointed_events_v1"
    )["metrics"]
    assert metrics["observation_count"] == 5
    assert metrics["distinct_event_count"] == 3
    assert metrics["event_spacing_sample_count"] == 2
    assert metrics["min_event_spacing_hours"] == 1
    assert metrics["max_event_spacing_hours"] == 3
    assert metrics["mean_event_spacing_hours"] == 2
    assert metrics["last_event_utc"].startswith("2026-10-07T05:00:00")


def test_study_records_the_software_needed_to_reproduce_it():
    assert {"tatc", "skyfield", "sgp4", "python"} == analyze(sensor_request(BASIC))[
        "software"
    ].keys()
