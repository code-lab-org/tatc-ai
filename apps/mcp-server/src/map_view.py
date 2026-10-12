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
from typing import Any, Dict, List, Optional

import shapely
from shapely.geometry import mapping, shape

# MapLibre's globe stretches geometry beyond the Web Mercator limit (85.0511)
# to the pole, which draws a wedge toward it. Footprints are cut at 85.
MAX_LATITUDE = 85.0
# The drawn track is a great circle through this many degrees at most between
# vertices; MapLibre would otherwise draw straight Mercator lines between samples.
MAX_TRACK_SEGMENT_DEG = 1.0


def unwrap_longitudes(lons: List[float]) -> List[float]:
    """Shift longitudes by 360 so a track crossing the antimeridian stays continuous."""
    unwrapped = []
    for lon in lons:
        if unwrapped:
            while lon - unwrapped[-1] > 180:
                lon -= 360
            while lon - unwrapped[-1] < -180:
                lon += 360
        unwrapped.append(lon)
    return unwrapped


def _unit_vector(lon: float, lat: float) -> List[float]:
    lon_rad, lat_rad = math.radians(lon), math.radians(lat)
    return [math.cos(lat_rad) * math.cos(lon_rad),
            math.cos(lat_rad) * math.sin(lon_rad),
            math.sin(lat_rad)]


def densify_track(
    lons: List[float], lats: List[float], max_step_deg: float = MAX_TRACK_SEGMENT_DEG
) -> List[List[float]]:
    """Return [lon, lat] vertices with great-circle points between samples.

    The samples themselves are kept exactly. Input longitudes must already be
    unwrapped; the result stays continuous across the antimeridian.
    """
    coords = [(lons[0], lats[0])]
    for lon, lat, prev_lon, prev_lat in zip(lons[1:], lats[1:], lons, lats):
        a, b = _unit_vector(prev_lon, prev_lat), _unit_vector(lon, lat)
        angle = math.acos(max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b)))))
        pieces = math.ceil(math.degrees(angle) / max_step_deg)
        for k in range(1, pieces):
            # Spherical linear interpolation along the great circle.
            t = k / pieces
            wa = math.sin((1 - t) * angle) / math.sin(angle)
            wb = math.sin(t * angle) / math.sin(angle)
            x, y, z = (wa * u + wb * v for u, v in zip(a, b))
            coords.append((math.degrees(math.atan2(y, x)),
                           math.degrees(math.asin(max(-1.0, min(1.0, z))))))
        coords.append((lon, lat))
    unwrapped = unwrap_longitudes([lon for lon, _ in coords])
    return [[round(lon, 4), round(lat, 4)] for lon, (_, lat) in zip(unwrapped, coords)]


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
    lons = unwrap_longitudes([m["position_lla"]["lon_deg"] for m in messages])
    points = [
        {
            "time": m["time"],
            "lat": lat,
            "lon": lon,
            "alt_km": round(m["position_lla"]["alt_m"] / 1000, 1),
            "footprint": clip_footprint(m.get("footprint_geojson")),
        }
        for m, lat, lon in zip(messages, lats, lons)
    ]
    data = {"points": points, "track": densify_track(lons, lats)}
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
const wrapLon = (lon) => (((lon + 540) % 360) - 180).toFixed(4);
const describe = (p) => `${p.time} · ${p.lat}, ${wrapLon(p.lon)} · ${p.alt_km} km`;

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

function show(i) {
  const p = pts[i];
  slider.value = i;
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
  // Track (densified along great circles); clicking a point moves the slider there, hovering shows its details.
  map.addSource("track", { type: "geojson", data: {
    type: "Feature", properties: {},
    geometry: { type: "LineString", coordinates: DATA.track },
  } });
  map.addLayer({ id: "track", type: "line", source: "track",
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
