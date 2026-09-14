"""Compact ground-track output: rounded coords, footprints opt-in.

Guards the failure where a 61-point track returned ~15k tokens (86%
footprint polygons) and the model fabricated the tail of its answer.
"""

import json
from datetime import datetime
from unittest import mock

from src import schema_formatter as fmt
from src import server as srv


def test_position_rounds_to_4_4_1():
    assert fmt.format_position_lla(12.3456789, -105.1234567, 420240.527) == {
        "lat_deg": 12.3457,
        "lon_deg": -105.1235,
        "alt_m": 420240.5,
    }


def test_position_rounding_keeps_exact_values():
    assert fmt.format_position_lla(51.5, -0.12, 408000.0) == {
        "lat_deg": 51.5,
        "lon_deg": -0.12,
        "alt_m": 408000.0,
    }


def test_footprint_coords_rounded_to_4():
    ring = [[10.884655043035224 + i * 0.5, 45.201679462178106 - i * 0.3] for i in range(5)]
    geo = fmt.format_footprint_geojson(ring)
    assert geo is not None
    for lon, lat in geo["geometry"]["coordinates"][0]:
        assert lon == round(lon, 4) and lat == round(lat, 4)
    coords = geo["geometry"]["coordinates"][0]
    assert coords[0] == coords[-1]


TRACK = [
    (datetime(2026, 1, 1, 0, 0, 0), 12.3456789, -105.1234567, 420240.527),
    (datetime(2026, 1, 1, 0, 1, 0), 15.4022222, -102.8088888, 419999.999),
]


def _mocked_track(**kwargs):
    with mock.patch.object(
        srv.celestrak_client, "get_satellite_info",
        return_value={"norad_id": 25544, "name": "ISS",
                      "tle_line1": "l1", "tle_line2": "l2"}), \
        mock.patch.object(srv, "create_satellite_from_tle", return_value=object()), \
        mock.patch.object(srv, "compute_ground_track", return_value=list(TRACK)), \
        mock.patch.object(srv, "calculate_footprint_from_position",
                          return_value=[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]) as fp:
        out = srv._generate_ground_track("ISS", "2026-01-01T00:00:00Z",
                                         "2 minutes", "1 minute", **kwargs)
    return out, fp


def test_default_omits_footprint_and_skips_compute():
    out, fp = _mocked_track()
    assert len(out) == 2
    assert all("footprint_geojson" not in m for m in out)
    fp.assert_not_called()
    assert out[0]["position_lla"] == {
        "lat_deg": 12.3457, "lon_deg": -105.1235, "alt_m": 420240.5}


def test_opt_in_includes_footprint():
    out, _ = _mocked_track(include_footprint=True)
    assert all("footprint_geojson" in m for m in out)


def test_default_payload_is_a_fraction_of_full():
    slim, _ = _mocked_track()
    full, _ = _mocked_track(include_footprint=True)
    assert len(json.dumps(slim)) < len(json.dumps(full)) / 2
