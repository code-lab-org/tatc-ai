"""Ground-track map: static picture, HTML page, and the ground_track_map tool.

LibreChat renders a tool-result resource with a ui:// URI and text/html MIME
type as an inline iframe; the HTML itself is never sent to the model.
"""

import asyncio
import json
import re
from datetime import datetime
from unittest import mock

from fastmcp import Client

from src import map_view
from src import server as srv
from src.static_map import render_static_map_png

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


def test_unwrap_longitudes_keeps_antimeridian_crossing_continuous():
    assert map_view.unwrap_longitudes([170.0, 179.0, -179.0, -170.0]) == [
        170.0, 179.0, 181.0, 190.0]


def test_unwrap_longitudes_westward_crossing():
    assert map_view.unwrap_longitudes([-179.0, 179.0]) == [-179.0, -181.0]


def test_unwrap_longitudes_leaves_normal_track_alone():
    assert map_view.unwrap_longitudes([-10.0, 0.0, 15.5]) == [-10.0, 0.0, 15.5]


def test_html_embeds_points_with_unwrapped_longitudes():
    html = map_view.build_ground_track_map_html(
        "ISS (ZARYA)", "25544",
        [_msg(10.0, 179.0, minute=0), _msg(11.0, -179.0, minute=1)], FAKE_PNG)
    points = _data(html)["points"]
    assert [p["lon"] for p in points] == [179.0, 181.0]
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


def test_footprints_follow_their_unwrapped_point():
    html = map_view.build_ground_track_map_html(
        "ISS", "25544",
        [_msg(0.0, 179.0, footprint=_ring(179.0, 0.0)),
         _msg(0.0, -179.0, minute=1, footprint=_ring(-179.0, 0.0))], FAKE_PNG)
    points = _data(html)["points"]
    ring = points[1]["footprint"]["geometry"]["coordinates"][0]
    # Point 2 moved from -179 to 181, so its footprint moves with it.
    assert [lon for lon, _ in ring] == [180.0, 182.0, 181.0, 180.0]


def test_points_without_footprint_have_none():
    html = map_view.build_ground_track_map_html(
        "ISS", "25544", [_msg(0.0, 0.0)], FAKE_PNG)
    assert _data(html)["points"][0]["footprint"] is None


def test_static_map_renders_png():
    png = render_static_map_png(
        "ISS (NORAD 25544)", [_msg(10.0, 179.0), _msg(11.0, -179.0, minute=1)])
    assert png.startswith(PNG_SIGNATURE)


TRACK = [
    (datetime(2026, 1, 1, 0, 0, 0), 12.3456789, 179.5, 420240.527),
    (datetime(2026, 1, 1, 0, 1, 0), 15.4022222, -179.5, 419999.999),
]


def _call_map_tool(**kwargs):
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
                          "tle_line1": "l1", "tle_line2": "l2"}), \
        mock.patch.object(srv, "create_satellite_from_tle", return_value=object()), \
        mock.patch.object(srv, "compute_ground_track", return_value=list(TRACK)), \
        mock.patch.object(srv, "calculate_footprint_from_position",
                          return_value=[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]):
        return asyncio.run(run())


def test_map_tool_returns_summary_and_ui_resource():
    result = _call_map_tool()
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
    assert [p["lon"] for p in points] == [179.5, 180.5]
    # Footprints default on: they only go into the HTML, never to the model.
    assert all(p["footprint"] is not None for p in points)


def test_map_tool_summary_omits_point_list():
    # The point list lives only in the HTML, which LibreChat keeps out of
    # the model's context; the summary must stay a few lines.
    summary = _call_map_tool().content[0].text
    assert "position_lla" not in summary
    assert len(summary) < 800


def test_map_tool_reports_validation_errors():
    result = _call_map_tool(duration="not a duration")
    assert result.is_error
