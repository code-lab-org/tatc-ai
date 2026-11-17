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
