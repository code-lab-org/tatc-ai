"""Display partitions of native TATC polygons for Cesium's local triangulator."""

from shapely import set_precision, union_all
from shapely.geometry import box


def cesium_parts(geometry, *, max_width=180, max_height=None):
    """Partition wide polygons without changing their union or coverage meaning.

    TATC splits at poles/antimeridian but can return a cap or annulus spanning
    all longitudes. Cesium cannot triangulate that ring in one local plane.
    TATC has no Cesium display-partition API. Wide footprints use longitude
    patches; dissolved areas also request latitude patches to bound long strips.
    Keep scientific/export geometry unchanged.
    """
    parts = []
    for polygon in getattr(geometry, "geoms", [geometry]):
        if polygon.is_empty:
            continue
        if not needs_partition(polygon, max_width=max_width, max_height=max_height):
            parts.append(polygon)
            continue
        for left in (-180, -90, 0, 90):
            for bottom, top in ((-90, 0), (0, 90)) if max_height is not None else ((-90, 90),):
                clipped = polygon.intersection(box(left, bottom, left + 90, top))
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


def needs_partition(geometry, *, max_width=180, max_height=None):
    return any(
        part.bounds[2] - part.bounds[0] >= max_width
        or (max_height is not None and part.bounds[3] - part.bounds[1] > max_height)
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
