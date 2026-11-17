"""Display partitions of native TATC polygons for Cesium's local triangulator."""

from shapely import set_precision, union_all
from shapely.geometry import box


def cesium_parts(geometry):
    """Partition wide polygons without changing their union or coverage meaning.

    TATC splits at poles/antimeridian but can return a cap or annulus spanning
    all longitudes. Cesium cannot triangulate that ring in one local plane.
    TATC has no Cesium display-partition API: intersect only these wide polygons
    with longitude patches. Keep native scientific/export geometry unchanged.
    """
    parts = []
    for polygon in getattr(geometry, "geoms", [geometry]):
        if polygon.is_empty:
            continue
        west, _, east, _ = polygon.bounds
        if east - west < 180:
            parts.append(polygon)
            continue
        for left in (-180, -90, 0, 90):
            clipped = polygon.intersection(box(left, -90, left + 90, 90))
            parts.extend(
                part
                for part in getattr(clipped, "geoms", [clipped])
                if part.geom_type == "Polygon" and not part.is_empty
            )
    return parts


def part_rings(parts):
    """WGS84 [longitude, latitude] rings, retaining native coordinate precision."""
    return [
        [
            [[float(x), float(y)] for x, y, *_ in ring.coords]
            for ring in [part.exterior, *part.interiors]
        ]
        for part in parts
    ]


def needs_partition(geometry):
    return any(
        part.bounds[2] - part.bounds[0] >= 180
        for part in getattr(geometry, "geoms", [geometry])
        if not part.is_empty
    )


def boundary_lines(geometry):
    """Native TATC boundary arcs, omitting polar degeneracies and split seams.

    Unit geocentric directions are placed on WGS84 by Cesium. This pinned
    TATC helper already distinguishes real boundaries from artificial seams.
    """
    from tatc.utils.geometry import _get_boundary_arcs

    # Native shared-seam longitudes can differ by ~1e-13 degrees. Snap only
    # outline geometry (<0.02 mm at the equator) before removing internal seams.
    outline = union_all(
        [set_precision(part, 1e-10) for part in getattr(geometry, "geoms", [geometry])]
    )
    starts, ends = _get_boundary_arcs(outline)
    lines = []
    for start, end in zip(starts.T.tolist(), ends.T.tolist()):
        if lines and lines[-1][-1] == start:
            lines[-1].append(end)
        else:
            lines.append([start, end])
    return lines
