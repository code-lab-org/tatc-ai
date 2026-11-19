"""Display cuts preserve native scientific footprints at either pole."""

from shapely import union_all
from shapely.geometry import Polygon, shape

from tests.polar_fixtures import polar_cases


def test_polar_patches_preserve_native_union_holes_and_bounds():
    for case in polar_cases():
        native = shape(case["native"])
        parts = [Polygon(rings[0], rings[1:]) for rings in case["parts"]]
        assert parts, case["name"]
        assert all(part.is_valid for part in parts), case["name"]
        assert all(part.bounds[2] - part.bounds[0] <= 180 for part in parts)
        display = union_all(parts)
        assert display.symmetric_difference(native).area < 1e-8, case["name"]
        assert display.bounds == native.bounds


def test_polar_boundary_arcs_exclude_pole_closures_and_stay_finite():
    import math

    for case in polar_cases():
        if case["name"] == "antimeridian":
            continue
        assert case["boundaries"]
        for line in case["boundaries"]:
            assert len(line) >= 2
            for point in line:
                assert all(math.isfinite(value) for value in point)
                assert abs(sum(value * value for value in point) - 1) < 1e-10
                assert (
                    abs(point[2]) < 0.999999
                ), "Artificial pole-closing edges must not be outlined"


def test_dissolved_polar_parts_preserve_native_geometry():
    from src.cesium_geometry import needs_partition
    from tests.polar_fixtures import polar_dissolve_case

    case = polar_dissolve_case()
    native = shape(case["native"])
    parts = [Polygon(rings[0], rings[1:]) for rings in case["parts"]]
    assert needs_partition(native, max_width=90, max_height=90)
    assert case["boundaries"]
    assert all(part.is_valid for part in parts)
    assert union_all(parts).symmetric_difference(native).area < 1e-8


def test_narrow_pole_to_pole_polygon_is_partitioned():
    from src.cesium_geometry import cesium_parts, needs_partition

    polygon = Polygon([(10, -90), (20, -90), (20, 90), (10, 90), (10, -90)])
    assert needs_partition(polygon, max_width=90, max_height=90)
    parts = cesium_parts(polygon, max_width=90, max_height=90)
    assert all(part.bounds[3] - part.bounds[1] <= 90 for part in parts)
    assert union_all(parts).equals(polygon)
