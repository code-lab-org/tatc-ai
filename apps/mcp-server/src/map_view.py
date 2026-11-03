"""Build the ground-track map page.

LibreChat renders it from a ui:// resource in a sandboxed iframe, so the page
carries its own data and loads MapLibre from a CDN. The HTML is never sent to
the model. The page has two views: an interactive 3D globe and a static
picture drawn on the server.
"""

import base64
import html
import json
import math
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import shapely
from shapely.geometry import mapping, shape

# MapLibre's globe stretches geometry beyond the Web Mercator limit (85.0511)
# to the pole, which draws a wedge toward it. Footprints are cut at 85.
MAX_LATITUDE = 85.0
# The drawn track is a great circle through this many degrees at most between
# vertices; MapLibre would otherwise draw straight Mercator lines between samples.
MAX_TRACK_SEGMENT_DEG = 1.0
EARTH_RADIUS_M = 6378137.0
EARTH_MU_M3_S2 = 3.986004418e14


def _unit_vector(lon: float, lat: float) -> List[float]:
    lon_rad, lat_rad = math.radians(lon), math.radians(lat)
    return [math.cos(lat_rad) * math.cos(lon_rad),
            math.cos(lat_rad) * math.sin(lon_rad),
            math.sin(lat_rad)]


def _turn(lon: float) -> int:
    """Which 360 degree copy of the world a longitude lies in; 0 is [-180, 180)."""
    return math.floor((lon + 180) / 360)


def great_circle_path(
    lon0: float, lat0: float, lon1: float, lat1: float,
    max_step_deg: float = MAX_TRACK_SEGMENT_DEG,
) -> List[Tuple[float, float]]:
    """Return (lon, lat) vertices from one sample to the next along the great circle.

    Both samples are kept exactly. Longitudes are continuous, so they may leave
    [-180, 180] where the path crosses the antimeridian.
    """
    a, b = _unit_vector(lon0, lat0), _unit_vector(lon1, lat1)
    angle = math.acos(max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b)))))
    pieces = math.ceil(math.degrees(angle) / max_step_deg)
    path = [(lon0, lat0)]
    for k in range(1, pieces):
        # Spherical linear interpolation along the great circle.
        t = k / pieces
        wa = math.sin((1 - t) * angle) / math.sin(angle)
        wb = math.sin(t * angle) / math.sin(angle)
        x, y, z = (wa * u + wb * v for u, v in zip(a, b))
        lon = math.degrees(math.atan2(y, x))
        path.append((lon + 360 * round((path[-1][0] - lon) / 360),
                     math.degrees(math.asin(max(-1.0, min(1.0, z))))))
    lon1 += 360 * round((path[-1][0] - lon1) / 360)
    path.append((lon1, lat1))
    return path


def split_at_antimeridian(path: List[Tuple[float, float]]) -> List[List[List[float]]]:
    """Cut a continuous path into [lon, lat] pieces that stay within [-180, 180].

    Where the path crosses the antimeridian one piece ends on the seam and the
    next starts on the opposite seam at the same latitude, so nothing is drawn
    across the map. Consecutive vertices must be less than 360 degrees apart.
    """
    (lon, lat), *rest = path
    pieces = [[[round(lon - 360 * _turn(lon), 4), round(lat, 4)]]]
    for lon1, lat1 in rest:
        turn0, turn1 = _turn(lon), _turn(lon1)
        if turn0 != turn1:
            seam = 180 + 360 * min(turn0, turn1)
            seam_lat = round(lat + (lat1 - lat) * (seam - lon) / (lon1 - lon), 4)
            edge = 180.0 if turn1 > turn0 else -180.0
            pieces[-1].append([edge, seam_lat])
            pieces.append([[-edge, seam_lat]])
        pieces[-1].append([round(lon1 - 360 * turn1, 4), round(lat1, 4)])
        lon, lat = lon1, lat1
    return pieces


def track_intervals(lons: List[float], lats: List[float]) -> List[List[List[List[float]]]]:
    """Return the track between each pair of consecutive samples.

    Interval i runs from sample i to sample i + 1 along a great circle, as
    [lon, lat] pieces cut at the antimeridian. Keeping the intervals separate
    lets the page highlight a stretch of the track with a layer filter on i.
    """
    return [
        split_at_antimeridian(great_circle_path(*start, *end))
        for start, end in zip(zip(lons, lats), zip(lons[1:], lats[1:]))
    ]


def current_orbit_half_window(times: List[str], alts_m: List[float]) -> int:
    """Return how many samples the current orbit reaches back and ahead.

    The period comes from Kepler's third law on the mean altitude, a circular
    orbit being close enough to highlight about one lap. A track shorter than
    one period is all current orbit.
    """
    semi_major_axis = EARTH_RADIUS_M + sum(alts_m) / len(alts_m)
    period_s = 2 * math.pi * math.sqrt(semi_major_axis ** 3 / EARTH_MU_M3_S2)
    start, end = (datetime.fromisoformat(t) for t in (times[0], times[-1]))
    duration_s = (end - start).total_seconds()
    if duration_s <= period_s:
        return len(times)
    return math.ceil(period_s / 2 / (duration_s / (len(times) - 1)))


def clip_footprint(footprint: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Cut a footprint Feature to |lat| <= MAX_LATITUDE for display.

    Footprints already come split at the antimeridian in [-180, 180], which the
    globe draws in place, so only the polar part needs trimming.
    """
    if footprint is None:
        return None
    geometry = shape(footprint["geometry"])
    if -MAX_LATITUDE <= geometry.bounds[1] and geometry.bounds[3] <= MAX_LATITUDE:
        return footprint
    clipped = shapely.set_precision(
        shapely.clip_by_rect(geometry, -180, -MAX_LATITUDE, 180, MAX_LATITUDE), 1e-4)
    if clipped.is_empty or clipped.geom_type not in ("Polygon", "MultiPolygon"):
        return None
    return {**footprint, "geometry": mapping(clipped)}


def build_ground_track_map_html(
    name: str, norad_id: str, messages: List[Dict[str, Any]], png: bytes
) -> str:
    """Return the map page for formatted ground-track telemetry messages."""
    lats = [m["position_lla"]["lat_deg"] for m in messages]
    lons = [m["position_lla"]["lon_deg"] for m in messages]
    alts_m = [m["position_lla"]["alt_m"] for m in messages]
    points = [
        {
            "time": m["time"],
            "lat": lat,
            "lon": lon,
            "alt_km": round(alt_m / 1000, 1),
            "footprint": clip_footprint(m.get("footprint_geojson")),
        }
        for m, lat, lon, alt_m in zip(messages, lats, lons, alts_m)
    ]
    data = {
        "points": points,
        "track": track_intervals(lons, lats),
        "half_window": current_orbit_half_window([m["time"] for m in messages], alts_m),
    }
    title = f"{name} (NORAD {norad_id})"
    # Escape "</" so a value cannot close the <script> block early.
    data_json = json.dumps(data).replace("</", "<\\/")
    return (
        _TEMPLATE
        .replace("__TITLE__", html.escape(title))
        .replace("__PNG__", base64.b64encode(png).decode("ascii"))
        .replace("__DATA__", data_json)
    )


_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>__TITLE__</title>
<link rel="stylesheet" href="https://unpkg.com/maplibre-gl@5.24.0/dist/maplibre-gl.css"
  integrity="sha384-uTttxo/aOKbdE5RlD/SPzSDoDmNvGlUYPjONi2MN/b7c9HPSvW07OIuyP7uL6jxK" crossorigin="">
<script src="https://unpkg.com/maplibre-gl@5.24.0/dist/maplibre-gl.js"
  integrity="sha384-5+cfbwT0iiub6VsQAdn6yz16nr6sDiQoHx6tm4O8OVYXHYOxcffFmCJBL0dgdvGp" crossorigin=""></script>
<style>
  /* LibreChat's dark theme colors. */
  :root {
    color-scheme: dark;
    accent-color: #10b981;
    --bg: #212121; --surface: #2f2f2f; --hover: #424242; --border: #595959;
    --text: #ececec; --muted: #cdcdcd; --active: #047857;
  }
  body { margin: 0; font: 13px system-ui, sans-serif; background: var(--bg); color: var(--text); }
  header, .controls { display: flex; align-items: center; gap: 8px; margin: 8px 12px; }
  h1 { font-size: 14px; margin: 0 auto 0 0; }
  button, select {
    font: inherit; color: var(--text); background: var(--surface);
    border: 1px solid var(--border); border-radius: 6px; padding: 3px 10px;
  }
  button:hover { background: var(--hover); }
  button[aria-pressed="true"] { background: var(--active); color: #fff; border-color: var(--active); }
  /* A lighter framed panel, so the globe stands out from the page. */
  #map {
    height: 400px; margin: 8px 12px; overflow: hidden;
    border: 1px solid var(--border); border-radius: 8px;
    background: radial-gradient(circle, var(--hover), var(--surface));
  }
  #slider { flex: 1; }
  #readout { margin: 0 12px 8px; color: var(--muted); }
  #static img { display: block; width: 100%; }
  .now { background: var(--active); color: #fff; padding: 1px 6px; border-radius: 4px; }
  /* MapLibre controls, popup and attribution on the dark surface. */
  .maplibregl-ctrl-group { background: var(--surface); }
  .maplibregl-ctrl-group button + button { border-top: 1px solid var(--border); }
  .maplibregl-ctrl-group button, .maplibregl-ctrl-attrib-button { background-color: transparent; border-radius: 0; }
  .maplibregl-ctrl-group button:hover { background: var(--hover); }
  .maplibregl-ctrl-icon, .maplibregl-ctrl-attrib-button { filter: invert(1); }
  .maplibregl-ctrl-attrib, .maplibregl-ctrl-attrib.maplibregl-compact {
    background: rgba(33, 33, 33, 0.85); color: var(--text);
  }
  .maplibregl-ctrl-attrib a { color: var(--text); }
  .maplibregl-popup-content { background: var(--surface); color: var(--text); }
  .maplibregl-popup-tip { display: none; }
</style>
</head>
<body>
<header>
  <h1>__TITLE__</h1>
  <button id="show-interactive">Interactive</button>
  <button id="show-static">Static</button>
</header>
<div id="interactive">
  <div id="map"></div>
  <div class="controls">
    <button id="play">Play</button>
    <input id="slider" type="range" min="0" value="0">
    <button id="follow" aria-pressed="false">Follow</button>
    <label><input id="footprint" type="checkbox" checked> Footprint</label>
    <select id="basemap">
      <option value="streets">Streets</option>
      <option value="imagery">Imagery</option>
    </select>
  </div>
  <div id="readout"></div>
</div>
<div id="static" hidden>
  <img src="data:image/png;base64,__PNG__" alt="Static ground track map">
</div>
<script>
const DATA = __DATA__;
</script>
<script>
const pts = DATA.points;
const color = "#d6336c";
const describe = (p) => `${p.time} · ${p.lat}, ${p.lon} · ${p.alt_km} km`;

const PLAY_INTERVAL_MS = 200;
const empty = { type: "FeatureCollection", features: [] };

// 3D globe with two Esri basemaps; both work without an API key.
const esri = "https://server.arcgisonline.com/ArcGIS/rest/services/";
const basemap = (name) => ({
  type: "raster", tileSize: 256, attribution: "Tiles &copy; Esri",
  tiles: [esri + name + "/MapServer/tile/{z}/{y}/{x}"],
});
const map = new maplibregl.Map({
  container: "map",
  center: [pts[0].lon, pts[0].lat],
  zoom: 1.5,
  style: {
    version: 8,
    projection: { type: "globe" },
    sources: { streets: basemap("World_Street_Map"), imagery: basemap("World_Imagery") },
    layers: [
      { id: "streets", type: "raster", source: "streets" },
      { id: "imagery", type: "raster", source: "imagery", layout: { visibility: "none" } },
    ],
  },
});
map.addControl(new maplibregl.NavigationControl());

// "Now" marker, only when the current time falls inside the track.
const times = pts.map((p) => Date.parse(p.time));
const now = Date.now();
if (now >= times[0] && now <= times[times.length - 1]) {
  const i = times.reduce((best, t, j) =>
    Math.abs(t - now) < Math.abs(times[best] - now) ? j : best, 0);
  const label = document.createElement("div");
  label.className = "now";
  label.textContent = "Now";
  new maplibregl.Marker({ element: label, opacityWhenCovered: 0 })
    .setLngLat([pts[i].lon, pts[i].lat])
    .addTo(map);
}

// Satellite marker and footprint follow the slider. Markers on the far side
// of the globe are hidden instead of showing through it.
const sat = new maplibregl.Marker({ color: "#1c7ed6", opacityWhenCovered: 0 })
  .setLngLat([pts[0].lon, pts[0].lat])
  .addTo(map);
const slider = document.getElementById("slider");
const footprintBox = document.getElementById("footprint");
slider.max = pts.length - 1;

// The current orbit is the stretch of track within half_window samples of the
// satellite. Moving it is a filter change on static sources: no data is re-sent.
function highlightOrbit(i) {
  const lo = Math.max(0, i - DATA.half_window);
  const hi = Math.min(pts.length - 1, i + DATA.half_window);
  const inWindow = (upper) => ["all", [">=", ["get", "i"], lo], [upper, ["get", "i"], hi]];
  map.setFilter("orbit", inWindow("<"));
  map.setFilter("points", inWindow("<="));
}

function show(i) {
  const p = pts[i];
  slider.value = i;
  highlightOrbit(i);
  sat.setLngLat([p.lon, p.lat]);
  document.getElementById("readout").textContent = describe(p);
  map.getSource("footprint").setData(footprintBox.checked && p.footprint ? p.footprint : empty);
  if (following && !pointerDown) panToSatellite();
}

// Follow keeps the satellite centered by easing the globe linearly over one
// play step. Moving the map by hand turns it off; zooming does not.
let following = false;
const followButton = document.getElementById("follow");
const panToSatellite = () =>
  map.easeTo({ center: sat.getLngLat(), duration: PLAY_INTERVAL_MS, easing: (t) => t });
function setFollowing(on) {
  following = on;
  followButton.setAttribute("aria-pressed", on);
  followButton.textContent = on ? "Following" : "Follow";
  if (on) panToSatellite();
}
followButton.onclick = () => setFollowing(!following);

// Each follow step restarts a camera animation, and that cancels MapLibre's
// own drag gestures. So steps pause while a pointer is down, and a pan, rotate,
// or tilt gesture (fired for user input only, not for easeTo) ends following.
let pointerDown = false;
// Release is watched on the window, so letting go outside the map still counts.
map.getCanvasContainer().addEventListener("pointerdown", () => { pointerDown = true; });
for (const type of ["pointerup", "pointercancel"]) window.addEventListener(type, () => { pointerDown = false; });
for (const type of ["dragstart", "rotatestart", "pitchstart"]) map.on(type, () => setFollowing(false));

map.on("load", () => {
  // The whole track is faded, the current orbit bright. Both draw the same
  // source, one feature per sample interval, along great circles. Points on
  // the current orbit can be clicked to move the slider there, and hovered for details.
  map.addSource("track", { type: "geojson", data: {
    type: "FeatureCollection",
    features: DATA.track.map((pieces, i) => ({ type: "Feature", properties: { i },
      geometry: { type: "MultiLineString", coordinates: pieces } })),
  } });
  map.addLayer({ id: "track", type: "line", source: "track",
    paint: { "line-color": color, "line-width": 1.5, "line-opacity": 0.35 } });
  map.addLayer({ id: "orbit", type: "line", source: "track",
    paint: { "line-color": color, "line-width": 3 } });
  map.addSource("points", { type: "geojson", data: {
    type: "FeatureCollection",
    features: pts.map((p, i) => ({ type: "Feature", properties: { i },
      geometry: { type: "Point", coordinates: [p.lon, p.lat] } })),
  } });
  map.addLayer({ id: "points", type: "circle", source: "points",
    paint: { "circle-radius": 4, "circle-color": color } });
  map.addSource("footprint", { type: "geojson", data: empty });
  map.addLayer({ id: "footprint", type: "fill", source: "footprint",
    paint: { "fill-color": "#1c7ed6", "fill-opacity": 0.2 } });

  const popup = new maplibregl.Popup({ closeButton: false });
  map.on("mousemove", "points", (e) => {
    popup.setLngLat(e.lngLat).setText(describe(pts[e.features[0].properties.i])).addTo(map);
  });
  map.on("mouseleave", "points", () => popup.remove());
  map.on("click", "points", (e) => show(e.features[0].properties.i));

  slider.oninput = () => show(Number(slider.value));
  footprintBox.onchange = () => show(Number(slider.value));
  show(0);
  resize();
});

// The basemap layers exist once the style loads; ignore earlier changes.
document.getElementById("basemap").onchange = (e) => {
  if (!map.isStyleLoaded()) return;
  map.setLayoutProperty("streets", "visibility", e.target.value === "streets" ? "visible" : "none");
  map.setLayoutProperty("imagery", "visibility", e.target.value === "imagery" ? "visible" : "none");
};

let timer = null;
const play = document.getElementById("play");
play.onclick = () => {
  if (timer) {
    clearInterval(timer);
    timer = null;
    play.textContent = "Play";
    return;
  }
  play.textContent = "Pause";
  timer = setInterval(() => show((Number(slider.value) + 1) % pts.length), PLAY_INTERVAL_MS);
};

// LibreChat sizes the iframe from this message; otherwise it stays 150px tall.
function resize() {
  parent.postMessage({ type: "ui-size-change",
    payload: { height: document.documentElement.scrollHeight } }, "*");
}

function setView(view) {
  document.getElementById("interactive").hidden = view !== "interactive";
  document.getElementById("static").hidden = view !== "static";
  map.resize();
  resize();
}
document.getElementById("show-interactive").onclick = () => setView("interactive");
document.getElementById("show-static").onclick = () => setView("static");

resize();
</script>
</body>
</html>
"""
