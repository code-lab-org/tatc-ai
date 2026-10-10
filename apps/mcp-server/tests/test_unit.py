"""Offline unit tests for pure logic: no network, no TAT-C propagation."""

from datetime import datetime, timedelta, timezone

import pytest
from shapely.geometry import MultiPolygon, Point, Polygon

from src import validation as v
from src import schema_formatter as fmt
from src import celestrak_client as cc
from src import server as srv
from src import tatc_integration as ti
from src.server import parse_duration, parse_time_input

ISS_OMM = {
    "OBJECT_NAME": "ISS (ZARYA)",
    "OBJECT_ID": "1998-067A",
    "EPOCH": "2026-09-18T03:25:38.782272",
    "MEAN_MOTION": 15.49160218,
    "ECCENTRICITY": 0.00048228,
    "INCLINATION": 51.6307,
    "RA_OF_ASC_NODE": 200.0361,
    "ARG_OF_PERICENTER": 152.4527,
    "MEAN_ANOMALY": 207.6718,
    "EPHEMERIS_TYPE": 0,
    "CLASSIFICATION_TYPE": "U",
    "NORAD_CAT_ID": 25544,
    "ELEMENT_SET_NO": 999,
    "REV_AT_EPOCH": 58616,
    "BSTAR": 0.00011125122,
    "MEAN_MOTION_DOT": 5.718e-5,
    "MEAN_MOTION_DDOT": 0,
}


# validation: NORAD IDs


def test_norad_accepts_int_and_numeric_string():
    assert v.validate_norad_id(25544) == 25544
    assert v.validate_norad_id("25544") == 25544
    assert v.validate_norad_id(25544.0) == 25544
    assert v.validate_norad_id(100178) == 100178


def test_norad_rejects_bool_out_of_range_and_garbage():
    for bad in (True, False, 0, 1_000_000_000, -5, "abc", None, 25544.5):
        with pytest.raises(ValueError):
            v.validate_norad_id(bad)


# validation: OMM records


def test_real_omm_record_passes_validation():
    assert v.validate_omm_records([ISS_OMM]) == [ISS_OMM]


def test_omm_validation_rejects_malformed_records():
    with pytest.raises(ValueError, match="non-empty"):
        v.validate_omm_records([])
    with pytest.raises(ValueError, match="must be an object"):
        v.validate_omm_records(["not a record"])
    incomplete = {k: val for k, val in ISS_OMM.items() if k != "MEAN_ANOMALY"}
    with pytest.raises(ValueError, match="MEAN_ANOMALY"):
        v.validate_omm_records([incomplete])
    with pytest.raises(ValueError, match="INCLINATION"):
        v.validate_omm_records([{**ISS_OMM, "INCLINATION": "steep"}])
    with pytest.raises(ValueError, match="NORAD_CAT_ID"):
        v.validate_omm_records([{**ISS_OMM, "NORAD_CAT_ID": 0}])


# validation: time range and steps


def test_time_range_accepts_valid_rejects_bad():
    start = datetime(2026, 1, 1)
    end = datetime(2026, 1, 2)
    assert v.validate_time_range(start, end) == (start, end)
    with pytest.raises(ValueError):
        v.validate_time_range(end, start)
    with pytest.raises(ValueError):
        v.validate_time_range(start, start + timedelta(days=31))
    with pytest.raises(ValueError):
        v.validate_time_range(start, end, max_duration=timedelta(hours=1))


def test_ground_track_sample_count_enforces_inclusive_limit_and_fractional_steps():
    start = datetime(2026, 1, 1)
    assert (
        v.validate_ground_track_sample_count(
            start, start + timedelta(seconds=1999), 1
        )
        == 2000
    )
    with pytest.raises(ValueError, match="2001.*2000"):
        v.validate_ground_track_sample_count(
            start, start + timedelta(seconds=2000), 1
        )

    assert (
        v.validate_ground_track_sample_count(
            start, start + timedelta(seconds=10), 2.5
        )
        == 5
    )
    assert (
        v.validate_ground_track_sample_count(
            start, start + timedelta(microseconds=10_100_000), 3
        )
        == 4
    )
    with pytest.raises(ValueError, match="positive"):
        v.validate_ground_track_sample_count(
            start, start + timedelta(seconds=1), 0.0000001
        )


def test_server_rejects_oversized_ground_track_before_external_helpers(monkeypatch):
    def unexpected_call(*args, **kwargs):
        raise AssertionError("external helper must not be called")

    monkeypatch.setattr(
        srv.celestrak_client, "get_satellite_info", unexpected_call
    )
    monkeypatch.setattr(srv, "create_orbit_from_omm", unexpected_call)
    monkeypatch.setattr(srv, "compute_ground_track", unexpected_call)

    with pytest.raises(ValueError, match="2001.*2000"):
        srv._generate_ground_track(
            "ISS",
            start_time="2026-01-01T00:00:00Z",
            duration="2000 seconds",
            step_interval="1 second",
        )


def test_step_interval_rejects_nonpositive_nonfinite_and_huge():
    assert v.validate_step_interval(60) == 60.0
    assert v.validate_step_interval("30") == 30.0
    for bad in (0, -1, 3601, float("nan"), float("inf"), "abc", True):
        with pytest.raises(ValueError):
            v.validate_step_interval(bad)


# validation: coordinates and altitude


def test_coordinates_validate_wrap_and_reject():
    assert v.validate_coordinates(45.0, 190.0) == (45.0, -170.0)
    with pytest.raises(ValueError):
        v.validate_coordinates(91.0, 0.0)
    with pytest.raises(ValueError):
        v.validate_coordinates(float("nan"), 0.0)
    with pytest.raises(ValueError):
        v.validate_coordinates(0.0, float("inf"))
    with pytest.raises(ValueError):
        v.validate_coordinates(True, 0.0)


def test_altitude_validates_bounds_and_rejects_nan():
    assert v.validate_altitude(408000.0) == 408000.0
    with pytest.raises(ValueError):
        v.validate_altitude(100.0, min_alt=1000.0)
    with pytest.raises(ValueError):
        v.validate_altitude(float("nan"))
    with pytest.raises(ValueError):
        v.validate_altitude(False)


# server: time and duration parsing


def test_parse_time_input_now_iso_and_relative():
    now = parse_time_input("now")
    assert now.tzinfo is None
    assert (
        abs((datetime.now(timezone.utc).replace(tzinfo=None) - now).total_seconds())
        < 60
    )
    assert parse_time_input("2026-01-01T00:00:00Z") == datetime(2026, 1, 1)
    assert parse_time_input("2026-01-01 00:00:00") == datetime(2026, 1, 1)
    delta = parse_time_input("in 2 hours") - now
    assert timedelta(hours=1, minutes=55) < delta < timedelta(hours=2, minutes=5)
    delta_words = parse_time_input("in one hour") - now
    assert timedelta(minutes=55) < delta_words < timedelta(hours=1, minutes=5)
    with pytest.raises(ValueError):
        parse_time_input("not a time at all xyz")
    with pytest.raises(ValueError):
        parse_time_input(None)


def test_parse_duration_words_numbers_and_bare_default():
    assert parse_duration("1 hour") == timedelta(hours=1)
    assert parse_duration("90 seconds") == timedelta(seconds=90)
    assert parse_duration("one hour") == timedelta(hours=1)
    assert parse_duration("twenty-five minutes") == timedelta(minutes=25)
    assert parse_duration("5") == timedelta(minutes=5)
    assert parse_duration("1.5") == timedelta(minutes=1.5)
    with pytest.raises(ValueError):
        parse_duration("ten lightyears")
    with pytest.raises(ValueError):
        parse_duration("hour")
    with pytest.raises(ValueError):
        parse_duration(None)


# schema_formatter


def test_format_timestamp_strips_microseconds_and_normalizes():
    assert (
        fmt.format_timestamp(datetime(2026, 1, 1, 12, 30, 45, 123456))
        == "2026-01-01T12:30:45Z"
    )
    aware = datetime(2026, 1, 1, 14, 30, tzinfo=timezone(timedelta(hours=2)))
    assert fmt.format_timestamp(aware) == "2026-01-01T12:30:00Z"
    with pytest.raises(ValueError):
        fmt.format_timestamp("2026-01-01")


def test_format_position_wraps_longitude():
    assert fmt.format_position_lla(10.0, 190.0, 400000.0) == {
        "lat_deg": 10.0,
        "lon_deg": -170.0,
        "alt_m": 400000.0,
    }


def test_format_footprint_keeps_polygons_and_antimeridian_splits():
    square = Polygon([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
    geo = fmt.format_footprint_geojson(square)
    assert geo["type"] == "Feature" and geo["properties"] == {}
    coords = geo["geometry"]["coordinates"][0]
    assert coords[0] == coords[-1] and all(len(c) == 2 for c in coords)
    split = MultiPolygon([
        Polygon([(179, 0), (180, 0), (180, 1), (179, 1)]),
        Polygon([(-180, 0), (-179, 0), (-179, 1), (-180, 1)]),
    ])
    assert fmt.format_footprint_geojson(split)["geometry"]["type"] == "MultiPolygon"
    assert fmt.format_footprint_geojson(None) is None
    assert fmt.format_footprint_geojson(Polygon()) is None
    assert fmt.format_footprint_geojson(Point(0, 0)) is None


def test_format_ground_track_response_skips_bad_points_quietly(capsys):
    track = [
        (datetime(2026, 1, 1), 10.0, 20.0, 400000.0, None),
        (datetime(2026, 1, 1, 0, 1), 200.0, 20.0, 400000.0, None),
    ]
    messages = fmt.format_ground_track_response("25544", track)
    assert len(messages) == 1
    assert messages[0]["id"] == "25544"
    assert capsys.readouterr().out == ""


# celestrak_client pure helpers


def test_normalize_and_score_rank_exact_first():
    assert cc._normalize_name("NOAA-19") == "NOAA 19"
    exact = cc._score_search_result("NOAA-19", "NOAA 19")
    partial = cc._score_search_result("NOAA", "NOAA 19")
    assert exact > partial > 0


def test_query_variants_expand_dashes():
    assert cc._satcat_query_variants("NOAA-19") == ["NOAA-19", "NOAA 19"]
    assert cc._satcat_query_variants("ISS") == ["ISS"]


def test_format_satcat_record_filters_decayed_and_idless():
    assert (
        cc._format_satcat_record(
            {"OBJECT_NAME": "X", "NORAD_CAT_ID": 1, "DECAY_DATE": "2020-01-01"}
        )
        is None
    )
    assert cc._format_satcat_record({"OBJECT_NAME": "X", "DECAY_DATE": ""}) is None
    assert cc._format_satcat_record("not a dict") is None
    assert (
        cc._format_satcat_record(
            {"OBJECT_NAME": 123, "NORAD_CAT_ID": 1, "DECAY_DATE": ""}
        )["name"]
        == "123"
    )
    good = cc._format_satcat_record(
        {
            "OBJECT_NAME": "X",
            "NORAD_CAT_ID": 1,
            "OBJECT_TYPE": "PAY",
            "OWNER": "US",
            "LAUNCH_DATE": "2020-01-01",
            "DECAY_DATE": "",
        }
    )
    assert good["norad_id"] == 1


def test_aliases_resolve_without_network():
    assert cc.get_norad_id("25544") == 25544
    assert cc.get_norad_id(25544) == 25544
    assert cc.get_norad_id("ISS") == 25544
    assert cc.get_norad_id("hubble") == 20580
    assert cc.get_norad_id("Tiangong") == 48274
    assert cc.get_norad_id(None) is None
    assert cc._resolve_alias("NOAA-19") is None


def test_decayed_hint_names_the_dead_objects(monkeypatch):
    class FakeResponse:
        text = "non-empty"

        def raise_for_status(self):
            return None

        def json(self):
            return [
                {
                    "OBJECT_NAME": "TIANGONG-1",
                    "NORAD_CAT_ID": 37820,
                    "DECAY_DATE": "2018-04-02",
                },
            ]

    monkeypatch.setattr(cc.requests, "get", lambda *a, **k: FakeResponse())
    hint = cc._decayed_match_hint("TIANGONG-1")
    assert "TIANGONG-1" in hint and "37820" in hint and "2018-04-02" in hint


# tatc_integration pure geometry and failure paths


ISS_OMM = {
    "OBJECT_NAME": "ISS (ZARYA)", "OBJECT_ID": "1998-067A",
    "EPOCH": "2026-10-08T23:34:55.716384", "MEAN_MOTION": 15.48782794,
    "ECCENTRICITY": 0.00068216, "INCLINATION": 51.6313,
    "RA_OF_ASC_NODE": 96.9458, "ARG_OF_PERICENTER": 239.5445,
    "MEAN_ANOMALY": 120.487, "EPHEMERIS_TYPE": 0, "CLASSIFICATION_TYPE": "U",
    "NORAD_CAT_ID": 25544, "ELEMENT_SET_NO": 999, "REV_AT_EPOCH": 58939,
    "BSTAR": 0.00011684905, "MEAN_MOTION_DOT": 5.946e-5, "MEAN_MOTION_DDOT": 0,
}


def test_ground_track_and_footprints_come_from_tatc():
    orbit = ti.create_orbit_from_omm([ISS_OMM])
    start = datetime(2026, 10, 10)
    track = ti.generate_ground_track(orbit, start, start + timedelta(minutes=90), 60,
                                     include_footprint=True)
    assert len(track) == 91
    time, lat, lon, alt, footprint = track[0]
    assert time == start and time.tzinfo is None
    assert -51.7 < lat < 51.7 and 380_000 < alt < 460_000
    assert footprint.geom_type in ("Polygon", "MultiPolygon")
    assert footprint.contains(Point(lon, lat))
    # A 90-minute ISS track crosses the antimeridian; TAT-C splits it there.
    assert any(f.geom_type == "MultiPolygon" for *_, f in track)
    assert all(f is None for *_, f in ti.generate_ground_track(orbit, start, start, 60))


def test_ground_track_rejects_bad_step_and_oversized_tracks_before_tatc():
    class ExplodingOrbit:
        def __getattr__(self, name):
            raise AssertionError("TAT-C must not be called")

    start = datetime(2026, 1, 1)
    for bad_step in (0, -60, float("nan"), "abc"):
        with pytest.raises(ValueError):
            ti.generate_ground_track(ExplodingOrbit(), start, start + timedelta(days=1), bad_step)
    with pytest.raises(ValueError, match="2001.*2000"):
        ti.generate_ground_track(ExplodingOrbit(), start, start + timedelta(seconds=2000), 1)


def test_ensure_utc_rejects_non_datetimes():
    with pytest.raises(ValueError):
        ti._ensure_utc("now")


def test_create_orbit_rejects_garbage_omm_offline():
    with pytest.raises(ValueError, match="OMM"):
        ti.create_orbit_from_omm([{"OBJECT_NAME": "X"}])
