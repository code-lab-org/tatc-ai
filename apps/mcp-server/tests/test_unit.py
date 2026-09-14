"""Offline unit tests for pure logic: no network, no TAT-C propagation."""

import math
from datetime import datetime, timedelta, timezone

import pytest

from src import validation as v
from src import schema_formatter as fmt
from src import celestrak_client as cc
from src import server as srv
from src import tatc_integration as ti
from src.server import parse_duration, parse_time_input

ISS_LINE1 = "1 25544U 98067A   26254.17569693  .00004925  00000+0  97240-4 0  9990"
ISS_LINE2 = "2 25544  51.6305 234.5133 0004993 127.7196 232.4246 15.49076359585089"


# validation: NORAD IDs


def test_norad_accepts_int_and_numeric_string():
    assert v.validate_norad_id(25544) == 25544
    assert v.validate_norad_id("25544") == 25544
    assert v.validate_norad_id(25544.0) == 25544


def test_norad_rejects_bool_out_of_range_and_garbage():
    for bad in (True, False, 0, 100000, -5, "abc", None, 25544.5):
        with pytest.raises(ValueError):
            v.validate_norad_id(bad)


# validation: TLE format


def test_real_tle_lines_pass_validation():
    assert v.validate_tle_format(ISS_LINE1, ISS_LINE2) == (ISS_LINE1, ISS_LINE2)


def test_tle_rejects_bad_checksum_short_lines_and_prefix():
    bad_checksum = ISS_LINE1[:-1] + ("1" if ISS_LINE1[-1] != "1" else "2")
    with pytest.raises(ValueError, match="checksum"):
        v.validate_tle_format(bad_checksum, ISS_LINE2)
    with pytest.raises(ValueError, match="too short"):
        v.validate_tle_format("1 short", ISS_LINE2)
    with pytest.raises(ValueError, match="must start"):
        v.validate_tle_format("0" + ISS_LINE1[1:], ISS_LINE2)
    with pytest.raises(ValueError):
        v.validate_tle_format("x" * 69, ISS_LINE2)


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
    monkeypatch.setattr(srv, "create_satellite_from_tle", unexpected_call)
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


def test_format_footprint_closes_ring_and_rejects_degenerate():
    ring = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]
    geo = fmt.format_footprint_geojson(ring)
    assert geo["type"] == "Feature"
    coords = geo["geometry"]["coordinates"][0]
    assert coords[0] == coords[-1]
    assert geo["properties"] == {}
    assert fmt.format_footprint_geojson([[0.0, 0.0], [1.0, 1.0]]) is None
    assert fmt.format_footprint_geojson([]) is None
    assert fmt.format_footprint_geojson("not a list") is None
    assert (
        fmt.format_footprint_geojson([[0.0, 0.0], None, [1.0, 0.0], [1.0, 1.0]])
        is not None
    )


def test_format_ground_track_response_skips_bad_points_quietly(capsys):
    track = [
        (datetime(2026, 1, 1), 10.0, 20.0, 400000.0),
        (datetime(2026, 1, 1, 0, 1), 200.0, 20.0, 400000.0),
    ]
    messages = fmt.format_ground_track_response("25544", track, None)
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


def test_gp_metadata_tolerates_malformed_items(monkeypatch):
    class FakeResponse:
        text = "non-empty"

        def raise_for_status(self):
            return None

        def json(self):
            return ["not a dict"]

    monkeypatch.setattr(cc.requests, "get", lambda *a, **k: FakeResponse())
    assert cc._fetch_gp_metadata(25544) is None


# tatc_integration pure geometry and failure paths


def test_circular_footprint_is_closed_sane_and_pole_safe():
    ring = ti._calculate_circular_footprint(0.0, 0.0, 408000.0, 60.0)
    assert len(ring) == ti.FOOTPRINT_POLYGON_POINTS + 1
    assert ring[0] == ring[-1]
    assert all(-180 <= lon <= 180 and -90 <= lat <= 90 for lon, lat in ring)
    first_lon, first_lat = ring[0]
    radius_deg = abs(first_lat)
    assert 1.0 < radius_deg < 5.0
    # GEO altitude previously crashed in asin; now clamps to the horizon circle.
    geo_ring = ti._calculate_circular_footprint(0.0, 0.0, 35786000.0, 60.0)
    assert geo_ring[0] == geo_ring[-1]
    expected_horizon = math.degrees(
        math.acos(ti.EARTH_RADIUS_M / (ti.EARTH_RADIUS_M + 35786000.0))
    )
    assert abs(abs(geo_ring[0][1]) - expected_horizon) < 0.5
    pole_ring = ti._calculate_circular_footprint(89.99, 0.0, 408000.0, 60.0)
    assert len(pole_ring) == ti.FOOTPRINT_POLYGON_POINTS + 1


def test_generate_ground_track_rejects_bad_step_without_calling_satellite():
    class ExplodingSatellite:
        def get_orbit_track(self, times):
            raise AssertionError("must not be called")

    for bad_step in (0, -60, float("nan"), "abc"):
        with pytest.raises(ValueError):
            ti.generate_ground_track(
                ExplodingSatellite(),
                datetime(2026, 1, 1),
                datetime(2026, 1, 2),
                bad_step,
            )


def test_integration_rejects_oversized_track_before_propagation(monkeypatch):
    monkeypatch.setattr(ti, "_require_tatc", lambda: None)

    class ExplodingSatellite:
        def get_orbit_track(self, times):
            raise AssertionError("propagation must not be called")

    start = datetime(2026, 1, 1)
    with pytest.raises(ValueError, match="2001.*2000"):
        ti.generate_ground_track(
            ExplodingSatellite(), start, start + timedelta(seconds=2000), 1
        )


def test_ensure_utc_rejects_non_datetimes():
    with pytest.raises(ValueError):
        ti._ensure_utc("now")


def test_generate_ground_track_raises_when_nothing_propagates():
    class BrokenSatellite:
        def get_orbit_track(self, times):
            raise RuntimeError("no ephemeris")

    with pytest.raises(ValueError, match="[Pp]ropagat"):
        ti.generate_ground_track(
            BrokenSatellite(), datetime(2026, 1, 1), datetime(2026, 1, 1, 0, 5), 60
        )


def test_create_satellite_rejects_garbage_tle_offline():
    with pytest.raises(ValueError):
        ti.create_satellite_from_tle("not a tle", "also not a tle")
