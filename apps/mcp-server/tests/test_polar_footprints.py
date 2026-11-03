"""Verify TAT-C footprint shape at the pole and the dateline, not just bounds."""

from datetime import datetime, timedelta, timezone

import pytest
from shapely.geometry import Point, shape
from tatc.schemas import CircularOrbit

from src import schema_formatter as fmt
from src import tatc_integration as ti

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _polar_orbit(raan_deg):
    return CircularOrbit(
        mean_altitude=500000, inclination=90, epoch=START,
        right_ascension_ascending_node=raan_deg,
    )


def _footprint(orbit, offset_seconds=0, field_of_regard=None):
    """Return (lat, lon, GeoJSON feature) for one TAT-C footprint sample."""
    time = START + timedelta(seconds=offset_seconds)
    options = {} if field_of_regard is None else {"footprint_field_of_regard": field_of_regard}
    _, lat, lon, _, footprint = ti.generate_ground_track(
        orbit, time, time, include_footprint=True, **options
    )[0]
    return lat, lon, fmt.format_footprint_geojson(footprint)


# This polar orbit passes within 0.2 degrees of the north pole at 1420 s.
@pytest.mark.parametrize("field_of_regard", [None, 120])
def test_polar_footprint_covers_pole_not_equator(field_of_regard):
    lat, lon, feature = _footprint(_polar_orbit(0), 1420, field_of_regard)
    geometry = shape(feature["geometry"])
    assert lat > 89
    assert geometry.is_valid
    assert geometry.covers(Point(lon, lat))
    for longitude in [-179.9, -90, 0, 90, 179.9]:
        assert geometry.covers(Point(longitude, 89.999))
        assert not geometry.covers(Point(longitude, 0))


def test_dateline_footprint_splits_into_valid_closed_parts():
    # The ground point starts on the dateline, so the footprint straddles it.
    lat, lon, feature = _footprint(_polar_orbit(280.66))
    assert feature["geometry"]["type"] == "MultiPolygon"
    geometry = shape(feature["geometry"])
    assert geometry.is_valid
    assert geometry.covers(Point(lon, lat))
    assert geometry.covers(Point(-lon, lat))
    assert not geometry.covers(Point(0, lat))
    for polygon in geometry.geoms:
        coords = list(polygon.exterior.coords)
        assert coords[0] == coords[-1]
        assert polygon.exterior.is_ccw
        assert all(abs(a[0] - b[0]) <= 180 for a, b in zip(coords, coords[1:]))


def test_ordinary_footprint_stays_a_polygon():
    lat, lon, feature = _footprint(_polar_orbit(0))
    geometry = shape(feature["geometry"])
    assert feature["geometry"]["type"] == "Polygon"
    assert geometry.is_valid and geometry.contains(Point(lon, lat))
    assert geometry.exterior.is_ccw


def test_dateline_footprint_survives_telemetry_formatting():
    track = ti.generate_ground_track(
        _polar_orbit(280.66), START, START, include_footprint=True
    )
    messages = fmt.format_ground_track_response("25544", track)
    assert messages[0]["footprint_geojson"]["geometry"]["type"] == "MultiPolygon"
