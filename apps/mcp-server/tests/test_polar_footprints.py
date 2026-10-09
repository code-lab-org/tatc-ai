"""Verify footprint shape, not just coordinate bounds, at poles and the dateline."""

import math
from datetime import datetime

import pytest
from shapely.geometry import Point, shape

from src import schema_formatter as fmt
from src import tatc_integration as ti


def _angular_distance(lat1, lon1, lat2, lon2):
    lat1, lat2 = math.radians(lat1), math.radians(lat2)
    delta_lon = math.radians(lon2 - lon1)
    # Independent haversine check of each vertex's distance from the center.
    haversine = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return 2 * math.asin(math.sqrt(max(0.0, min(1.0, haversine))))


# Sample an off-axis meridian sparsely to catch longitude-dependent clipping.
VERTEX_CASES = [
    pytest.param(latitude, longitude, altitude,
                 id=f"lat{latitude}_lon{longitude}_alt{altitude}")
    for latitude in [-90, -89.99, -89, -87, -80, 0, 80, 87, 89, 89.99, 90]
    for longitude in [-179.9, 0, 179.9]
    for altitude in [408000.0, 35786000.0]
] + [
    pytest.param(0, 123, 408000.0, id="equator_leo_off_axis"),
    pytest.param(89.99, 123, 35786000.0, id="north_pole_geo_off_axis"),
]

POLAR_CAP_CASES = [
    pytest.param(latitude, longitude, id=f"lat{latitude}_lon{longitude}")
    for latitude in [-90, -89.99, -89, 89, 89.99, 90]
    for longitude in [-179.9, 0, 179.9]
] + [
    pytest.param(-89.99, 123, id="south_pole_off_axis"),
    pytest.param(89.99, 123, id="north_pole_off_axis"),
]


@pytest.mark.parametrize(("latitude", "longitude", "altitude"), VERTEX_CASES)
def test_vertices_keep_constant_surface_radius(latitude, longitude, altitude):
    ring = ti.calculate_footprint_from_position(latitude, longitude, altitude)
    assert ring is not None
    assert ring[0] == ring[-1]
    assert len(set(map(tuple, ring[:-1]))) == ti.FOOTPRINT_POLYGON_POINTS
    # The equatorial north vertex gives the expected radius without duplicating
    # the spherical destination calculation under test.
    reference = ti.calculate_footprint_from_position(0, 0, altitude)[0][1]
    for lon, lat in ring[:-1]:
        assert -180 <= lon <= 180 and -90 <= lat <= 90
        assert _angular_distance(latitude, longitude, lat, lon) == pytest.approx(
            math.radians(reference), abs=1e-12
        )
    geometry = shape(fmt.format_footprint_geojson(ring)["geometry"])
    assert geometry.is_valid
    assert geometry.covers(Point(longitude, latitude))


@pytest.mark.parametrize(("latitude", "longitude"), POLAR_CAP_CASES)
def test_polar_geojson_covers_cap_without_degenerate_or_global_polygon(latitude, longitude):
    ring = ti.calculate_footprint_from_position(latitude, longitude, 408000.0)
    feature = fmt.format_footprint_geojson(ring)
    assert feature is not None
    geometry = shape(feature["geometry"])
    assert geometry.is_valid and geometry.area > 0
    pole_sign = 1 if latitude > 0 else -1
    # Every longitude just below the pole lies inside these polar footprints.
    for lon in [-179.9, -90, 0, 90, 179.9]:
        assert geometry.covers(Point(lon, pole_sign * 89.999))
        assert not geometry.covers(Point(lon, 0))
    assert geometry.covers(Point(longitude, latitude))
    polygons = [geometry] if geometry.geom_type == "Polygon" else geometry.geoms
    for polygon in polygons:
        assert polygon.exterior.is_ccw
        assert all(-180 <= lon <= 180 and -90 <= lat <= 90 for lon, lat in polygon.exterior.coords)


@pytest.mark.parametrize("latitude", [-80, 0, 80])
@pytest.mark.parametrize("longitude", [-179.9, 179.9])
def test_dateline_footprint_splits_into_valid_local_parts(latitude, longitude):
    ring = ti.calculate_footprint_from_position(latitude, longitude, 408000.0)
    feature = fmt.format_footprint_geojson(ring)
    assert feature["geometry"]["type"] == "MultiPolygon"
    geometry = shape(feature["geometry"])
    assert geometry.is_valid
    assert geometry.covers(Point(longitude, latitude))
    assert geometry.covers(Point(-longitude, latitude))
    assert not geometry.covers(Point(0, latitude))
    for polygon in geometry.geoms:
        assert polygon.exterior.is_ccw
        coords = list(polygon.exterior.coords)
        assert coords[0] == coords[-1]
        assert all(abs(a[0] - b[0]) <= 180 for a, b in zip(coords, coords[1:]))


def test_ordinary_footprint_stays_a_polygon():
    feature = fmt.format_footprint_geojson(ti.calculate_footprint_from_position(30, 20, 408000))
    assert feature["geometry"]["type"] == "Polygon"
    geometry = shape(feature["geometry"])
    assert geometry.is_valid and geometry.exterior.is_ccw
    assert geometry.contains(Point(20, 30))


def test_dateline_footprint_survives_telemetry_formatting():
    ring = ti.calculate_footprint_from_position(0, 179.9, 408000)
    messages = fmt.format_ground_track_response(
        "25544", [(datetime(2026, 1, 1), 0, 179.9, 408000)], [ring]
    )
    assert messages[0]["footprint_geojson"]["geometry"]["type"] == "MultiPolygon"
