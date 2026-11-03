"""Ground-track map: static picture, HTML page, and the ground_track_map tool.

LibreChat renders a tool-result resource with a ui:// URI and text/html MIME
type as an inline iframe; the HTML itself is never sent to the model.
"""

import asyncio
import io
import json
import math
import re
from datetime import datetime, timedelta
from unittest import mock

import cartopy.crs as ccrs
import pytest
from fastmcp import Client
from PIL import Image
from shapely.geometry import Polygon, mapping

from src import map_view
from src import server as srv
from src.static_map import render_static_map_png

# Import pyplot after static_map selects the headless backend.
import matplotlib.pyplot as plt

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
FAKE_PNG = PNG_SIGNATURE + b"fake"


def _ring(lon, lat):
    return {"type": "Feature", "properties": {},
            "geometry": {"type": "Polygon", "coordinates": [[
                [lon - 1, lat], [lon + 1, lat], [lon, lat + 1], [lon - 1, lat]]]}}


def _msg(lat, lon, alt=420000.0, minute=0, footprint=None):
    m = {
        "id": "25544",
        "time": f"2026-01-01T00:{minute:02d}:00Z",
        "position_lla": {"lat_deg": lat, "lon_deg": lon, "alt_m": alt},
    }
    if footprint is not None:
        m["footprint_geojson"] = footprint
    return m


def _data(html):
    """Return the data object embedded in the page."""
    match = re.search(r"const DATA = (.*?);\n", html, re.S)
    assert match, "map data block not found"
    return json.loads(match.group(1).replace("<\\/", "</"))


def test_html_embeds_points_in_canonical_longitudes():
    html = map_view.build_ground_track_map_html(
        "ISS (ZARYA)", "25544",
        [_msg(10.0, 179.0, minute=0), _msg(11.0, -179.0, minute=1)], FAKE_PNG)
    points = _data(html)["points"]
    assert [p["lon"] for p in points] == [179.0, -179.0]
    assert points[0]["time"] == "2026-01-01T00:00:00Z"
    assert points[0]["alt_km"] == 420.0
    assert "maplibre" in html.lower()
    assert '"globe"' in html
    assert "ISS (ZARYA)" in html


def test_html_has_static_picture_and_view_toggle():
    html = map_view.build_ground_track_map_html(
        "ISS", "25544", [_msg(0.0, 0.0)], FAKE_PNG)
    assert "data:image/png;base64,iVBORw0KGg" in html  # base64 PNG signature
    assert ">Interactive<" in html and ">Static<" in html


def test_html_escapes_script_breaking_names():
    html = map_view.build_ground_track_map_html(
        "</script><script>alert(1)</script>", "1", [_msg(0.0, 0.0)], FAKE_PNG)
    assert "</script><script>alert(1)" not in html


def test_footprints_stay_in_canonical_coordinates():
    footprint = _ring(-179.0, 0.0)
    html = map_view.build_ground_track_map_html(
        "ISS", "25544",
        [_msg(0.0, 179.0, footprint=_ring(179.0, 0.0)),
         _msg(0.0, -179.0, minute=1, footprint=footprint)], FAKE_PNG)
    points = _data(html)["points"]
    assert points[1]["lon"] == -179.0
    assert points[1]["footprint"] == footprint


def test_points_without_footprint_have_none():
    html = map_view.build_ground_track_map_html(
        "ISS", "25544", [_msg(0.0, 0.0)], FAKE_PNG)
    assert _data(html)["points"][0]["footprint"] is None


def test_html_preserves_dateline_multipolygon_footprints():
    footprint = {
        "type": "Feature", "properties": {},
        "geometry": {"type": "MultiPolygon", "coordinates": [
            [[[179, -1], [180, -1], [180, 1], [179, 1], [179, -1]]],
            [[[-180, -1], [-179, -1], [-179, 1], [-180, 1], [-180, -1]]],
        ]},
    }
    html = map_view.build_ground_track_map_html(
        "ISS", "25544",
        [_msg(0, 179), _msg(0, -179, minute=1, footprint=footprint)], FAKE_PNG,
    )
    point = _data(html)["points"][1]
    assert point["lon"] == -179
    assert point["footprint"] == footprint


def _cap(pole, edge):
    """A footprint covering a pole, as TAT-C returns it: ring along +-180."""
    return {
        "type": "Feature", "properties": {},
        "geometry": {"type": "Polygon", "coordinates": [[
            [-180, pole], [-180, edge], [0, edge], [180, edge],
            [180, pole], [-180, pole],
        ]]},
    }


def test_clip_footprint_leaves_footprint_inside_the_band_alone():
    footprint = _ring(10.0, 84.0)
    assert map_view.clip_footprint(footprint) is footprint
    assert map_view.clip_footprint(None) is None


def test_clip_footprint_cuts_at_the_mercator_limit():
    # A circle like the NOAA 20 one at lat -80.8 reaching -85.3.
    circle = Polygon(
        [(-70 + 4.5 * math.cos(a / 10), -80.8 + 4.5 * math.sin(a / 10))
         for a in range(63)])
    footprint = {"type": "Feature", "properties": {}, "geometry": mapping(circle)}
    clipped = map_view.clip_footprint(footprint)
    ring = clipped["geometry"]["coordinates"][0]
    assert clipped["geometry"]["type"] == "Polygon"
    assert min(lat for _, lat in ring) == -map_view.MAX_LATITUDE
    assert max(lat for _, lat in ring) == pytest.approx(-76.3, abs=0.01)


@pytest.mark.parametrize("pole", [-90, 90])
def test_clip_footprint_turns_polar_cap_into_band(pole):
    clipped = map_view.clip_footprint(_cap(pole, pole * 0.9))  # edge at 81
    lats = [lat for ring in clipped["geometry"]["coordinates"] for _, lat in ring]
    assert max(abs(lat) for lat in lats) == map_view.MAX_LATITUDE
    assert min(abs(lat) for lat in lats) == 81
    assert Polygon(clipped["geometry"]["coordinates"][0]).is_valid


@pytest.mark.parametrize("pole", [-90, 90])
def test_clip_footprint_drops_footprint_wholly_beyond_the_limit(pole):
    assert map_view.clip_footprint(_cap(pole, pole * 0.98)) is None


def test_clip_footprint_keeps_both_pieces_of_a_clipped_dateline_footprint():
    pieces = [
        [[[179, 80], [180, 80], [180, 88], [179, 88], [179, 80]]],
        [[[-180, 80], [-179, 80], [-179, 88], [-180, 88], [-180, 80]]],
    ]
    footprint = {"type": "Feature", "properties": {},
                 "geometry": {"type": "MultiPolygon", "coordinates": pieces}}
    clipped = map_view.clip_footprint(footprint)["geometry"]
    assert clipped["type"] == "MultiPolygon" and len(clipped["coordinates"]) == 2
    assert all(lat <= map_view.MAX_LATITUDE
               for polygon in clipped["coordinates"] for _, lat in polygon[0])


def test_embedded_footprints_are_clipped():
    html = map_view.build_ground_track_map_html(
        "Polar", "1", [_msg(-80.8, 0, footprint=_cap(-90, -81))], FAKE_PNG)
    ring = _data(html)["points"][0]["footprint"]["geometry"]["coordinates"][0]
    assert min(lat for _, lat in ring) == -map_view.MAX_LATITUDE


def _angle_deg(a, b):
    """Great-circle angle between two [lon, lat] vertices."""
    va, vb = map_view._unit_vector(*a), map_view._unit_vector(*b)
    return math.degrees(math.acos(min(1.0, sum(x * y for x, y in zip(va, vb)))))


def _orbit_msgs(count, step_minutes=1, alt=420000.0):
    """An inclined, eastward track that circles the Earth every 90 minutes."""
    start = datetime(2026, 1, 1)
    msgs = []
    for i in range(count):
        m = _msg(51.6 * math.sin(2 * math.pi * i * step_minutes / 90),
                 ((-100 + 4 * i * step_minutes + 180) % 360) - 180, alt)
        m["time"] = (start + timedelta(minutes=i * step_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
        msgs.append(m)
    return msgs


def test_great_circle_path_keeps_samples_and_limits_segment_length():
    path = map_view.great_circle_path(0.0, 0.0, 30.0, 10.0)
    assert path[0] == (0.0, 0.0) and path[-1] == (30.0, 10.0)
    assert len(path) > 30
    assert all(_angle_deg(a, b) <= 1.0001 for a, b in zip(path, path[1:]))


def test_great_circle_path_follows_the_great_circle_not_the_straight_line():
    # Along 60N a great circle between two points bulges poleward of the
    # parallel, where a Mercator straight line would stay on it.
    path = map_view.great_circle_path(0.0, 60.0, 60.0, 60.0)
    assert max(lat for _, lat in path) > 63


@pytest.mark.parametrize(("lon0", "lon1"), [(170.0, -170.0), (-170.0, 170.0)])
def test_great_circle_path_is_continuous_across_the_antimeridian(lon0, lon1):
    lons = [lon for lon, _ in map_view.great_circle_path(lon0, 0.0, lon1, 0.0)]
    assert all(abs(b - a) <= 1.0001 for a, b in zip(lons, lons[1:]))
    assert abs(lons[-1] - lons[0]) == 20.0


def test_great_circle_path_handles_repeated_sample():
    assert map_view.great_circle_path(5.0, 6.0, 5.0, 6.0) == [(5.0, 6.0), (5.0, 6.0)]


def test_split_at_antimeridian_leaves_other_paths_whole():
    assert map_view.split_at_antimeridian([(-10.0, 0.0), (0.0, 5.0)]) == [
        [[-10.0, 0.0], [0.0, 5.0]]]


@pytest.mark.parametrize(("path", "seam"), [
    ([(179.0, 0.0), (181.0, 10.0)], 180.0),
    ([(-179.0, 0.0), (-181.0, 10.0)], -180.0),
])
def test_split_at_antimeridian_ends_and_starts_pieces_at_the_seam(path, seam):
    west, east = map_view.split_at_antimeridian(path)
    assert west[-1] == [seam, 5.0]
    assert east[0] == [-seam, 5.0]
    assert west[0] == [path[0][0], 0.0]
    assert east[-1] == [path[1][0] - math.copysign(360, path[1][0]), 10.0]


def _vertices(pieces):
    return [v for piece in pieces for v in piece]


def test_track_intervals_stay_within_the_world_over_many_orbits():
    msgs = _orbit_msgs(300)  # 3.3 laps
    lons = [m["position_lla"]["lon_deg"] for m in msgs]
    lats = [m["position_lla"]["lat_deg"] for m in msgs]
    intervals = map_view.track_intervals(lons, lats)

    assert len(intervals) == 299
    assert all(-180.0001 <= lon <= 180.0001
               for pieces in intervals for lon, _ in _vertices(pieces))
    # Each interval runs from its sample to the next one.
    for i, pieces in enumerate(intervals):
        assert _vertices(pieces)[0] == [lons[i], pytest.approx(lats[i], abs=1e-4)]
        assert _vertices(pieces)[-1] == [lons[i + 1], pytest.approx(lats[i + 1], abs=1e-4)]
    # Every dateline crossing splits an interval, with no gap in latitude.
    split = [pieces for pieces in intervals if len(pieces) == 2]
    assert len(split) == 3
    for west, east in split:
        assert abs(west[-1][0]) == 180.0 and east[0][0] == -west[-1][0]
        assert west[-1][1] == east[0][1]


def test_html_embeds_track_intervals_but_points_stay_samples():
    html = map_view.build_ground_track_map_html(
        "AQUA", "27424", [_msg(0, 0), _msg(0, 30, minute=5)], FAKE_PNG)
    data = _data(html)
    assert len(data["points"]) == 2
    assert len(data["track"]) == 1  # one interval between the two samples
    track = _vertices(data["track"][0])
    assert len(track) > 30
    assert track[0] == [0, 0] and track[-1] == [30, 0]


def test_current_orbit_half_window_is_half_a_kepler_period_in_samples():
    # 420 km altitude: period 92.9 minutes, so 46.4 minutes each way.
    msgs = _orbit_msgs(300)
    times = [m["time"] for m in msgs]
    assert map_view.current_orbit_half_window(times, [420000.0] * 300) == 47
    # Higher orbits are slower, so the window grows with altitude.
    assert map_view.current_orbit_half_window(times, [800000.0] * 300) == 51


def test_current_orbit_half_window_covers_a_track_shorter_than_one_orbit():
    times = [m["time"] for m in _orbit_msgs(91)]  # 90 minutes < 92.9
    assert map_view.current_orbit_half_window(times, [420000.0] * 91) == 91
    assert map_view.current_orbit_half_window(times[:1], [420000.0]) == 1


def test_page_filters_the_current_orbit_on_static_sources():
    html = map_view.build_ground_track_map_html(
        "ISS", "25544", _orbit_msgs(300), FAKE_PNG)
    assert _data(html)["half_window"] == 47
    assert 'map.setFilter("orbit"' in html and 'map.setFilter("points"' in html


def test_page_has_follow_toggle_and_footprints_on_by_default():
    html = map_view.build_ground_track_map_html(
        "ISS", "25544", [_msg(0.0, 0.0)], FAKE_PNG)
    assert '<button id="follow" aria-pressed="false">Follow</button>' in html
    assert 'id="center"' not in html
    assert re.search(r'<input id="footprint" type="checkbox" checked>', html)


def test_page_uses_librechat_dark_surface():
    html = map_view.build_ground_track_map_html(
        "ISS", "25544", [_msg(0.0, 0.0)], FAKE_PNG)
    assert "color-scheme: dark" in html
    assert "--bg: #212121" in html and "--text: #ececec" in html


@pytest.mark.parametrize(
    ("messages", "expected_longitudes", "expected_latitudes"),
    [
        pytest.param(
            [_msg(40, -74), _msg(41, -73, minute=1)], [-74, -73], [40, 41],
            id="ordinary-track",
        ),
        pytest.param(
            [_msg(10, 179), _msg(11, -179, minute=1)], [179, -179], [10, 11],
            id="dateline-crossing-track",
        ),
    ],
)
def test_static_map_renders_png_and_plots_track(
    monkeypatch, messages, expected_longitudes, expected_latitudes
):
    plot_calls = []
    real_plot = plt.Axes.plot

    def capture_plot(axes, *args, **kwargs):
        artists = real_plot(axes, *args, **kwargs)
        plot_calls.append((kwargs, artists))
        return artists

    monkeypatch.setattr(plt.Axes, "plot", capture_plot)
    png = render_static_map_png("ISS (NORAD 25544)", messages)

    with Image.open(io.BytesIO(png)) as image:
        image.load()
        assert image.format == "PNG"

    track_artists = next(
        artists for kwargs, artists in plot_calls
        if isinstance(kwargs.get("transform"), ccrs.Geodetic)
    )
    assert list(track_artists[0].get_xdata()) == expected_longitudes
    assert list(track_artists[0].get_ydata()) == expected_latitudes

    for index, prefix in ((0, "Start"), (-1, "End")):
        label = f"{prefix} {messages[index]['time']}"
        artist = next(
            artists[0] for options, artists in plot_calls
            if options.get("label") == label
        )
        assert list(artist.get_xdata()) == [expected_longitudes[index]]
        assert list(artist.get_ydata()) == [expected_latitudes[index]]
        assert artist.get_label() == label


def test_static_map_uses_dark_background():
    png = render_static_map_png("ISS (NORAD 25544)", [_msg(10, 20), _msg(11, 21, minute=1)])
    with Image.open(io.BytesIO(png)) as image:
        assert image.convert("RGB").getpixel((2, 2)) == (0x21, 0x21, 0x21)


FOOTPRINT = Polygon([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)])
TRACK = [
    (datetime(2026, 1, 1, 0, 0, 0), 12.3456789, 179.5, 420240.527, FOOTPRINT),
    (datetime(2026, 1, 1, 0, 1, 0), 15.4022222, -179.5, 419999.999, FOOTPRINT),
]


def _run_map_tool(**kwargs):
    """Return the tool result and the mocked TAT-C track call."""
    async def run():
        async with Client(srv.mcp) as client:
            return await client.call_tool(
                "ground_track_map",
                {"satellite_identifier": "ISS",
                 "start_time": "2026-01-01T00:00:00Z",
                 "duration": "2 minutes", "step_interval": "1 minute",
                 **kwargs},
                raise_on_error=False)

    with mock.patch.object(
            srv.celestrak_client, "get_satellite_info",
            return_value={"norad_id": 25544, "name": "ISS (ZARYA)",
                          "omm": [{"OBJECT_NAME": "ISS (ZARYA)", "NORAD_CAT_ID": 25544}]}), \
        mock.patch.object(srv, "create_orbit_from_omm", return_value=object()), \
        mock.patch.object(srv, "compute_ground_track", return_value=list(TRACK)) as compute:
        return asyncio.run(run()), compute


def _call_map_tool(**kwargs):
    return _run_map_tool(**kwargs)[0]


def test_map_tool_returns_summary_and_ui_resource():
    result, compute = _run_map_tool()
    assert not result.is_error
    kinds = [c.type for c in result.content]
    assert kinds == ["text", "resource"]

    summary = result.content[0].text
    assert "25544" in summary and "ISS (ZARYA)" in summary
    assert "2 points" in summary

    resource = result.content[1].resource
    assert str(resource.uri).startswith("ui://tatc/ground-track/25544/")
    assert resource.mimeType == "text/html"
    assert "data:image/png;base64," in resource.text
    points = _data(resource.text)["points"]
    assert [p["lon"] for p in points] == [179.5, -179.5]
    # Footprints are always computed: they only go into the HTML, never to the model.
    assert compute.call_args.kwargs["include_footprint"] is True
    assert all(p["footprint"] is not None for p in points)


def test_map_tool_has_no_include_footprint_parameter():
    async def schema():
        async with Client(srv.mcp) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
        return tools["ground_track_map"].inputSchema

    properties = asyncio.run(schema())["properties"]
    assert "include_footprint" not in properties
    assert set(properties) == {
        "satellite_identifier", "start_time", "duration", "step_interval"}


def test_map_tool_summary_omits_point_list():
    # The point list lives only in the HTML, which LibreChat keeps out of
    # the model's context; the summary must stay a few lines.
    summary = _call_map_tool().content[0].text
    assert "position_lla" not in summary
    assert len(summary) < 800


def test_map_tool_reports_validation_errors():
    result = _call_map_tool(duration="not a duration")
    assert result.is_error
