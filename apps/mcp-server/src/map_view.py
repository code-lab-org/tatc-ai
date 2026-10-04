"""Build the ground-track map page.

LibreChat renders it from a ui:// resource in a sandboxed iframe, so the page
carries its own data and loads MapLibre from a CDN. The HTML is never sent to
the model. The page has two views: an interactive 3D globe and a static
picture drawn on the server.
"""

import base64
import html
import json
from typing import Any, Dict, List


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


def _footprint_near(footprint: Dict[str, Any], lon: float) -> Dict[str, Any]:
    """Move a footprint polygon next to its point's (unwrapped) longitude."""
    ring = footprint["geometry"]["coordinates"][0]
    lons = unwrap_longitudes([lon] + [c[0] for c in ring])[1:]
    shifted = [[x, c[1]] for x, c in zip(lons, ring)]
    return {**footprint, "geometry": {"type": "Polygon", "coordinates": [shifted]}}


def build_ground_track_map_html(
    name: str, norad_id: str, messages: List[Dict[str, Any]], png: bytes
) -> str:
    """Return the map page for formatted ground-track telemetry messages."""
    lons = unwrap_longitudes([m["position_lla"]["lon_deg"] for m in messages])
    points = [
        {
            "time": m["time"],
            "lat": m["position_lla"]["lat_deg"],
            "lon": lon,
            "alt_km": round(m["position_lla"]["alt_m"] / 1000, 1),
            "footprint": (
                _footprint_near(m["footprint_geojson"], lon)
                if "footprint_geojson" in m else None
            ),
        }
        for m, lon in zip(messages, lons)
    ]
    title = f"{name} (NORAD {norad_id})"
    # Escape "</" so a value cannot close the <script> block early.
    data_json = json.dumps({"points": points}).replace("</", "<\\/")
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
  body { margin: 0; font: 13px system-ui, sans-serif; background: #fff; }
  header, .controls { display: flex; align-items: center; gap: 8px; margin: 8px 12px; }
  h1 { font-size: 14px; margin: 0 auto 0 0; }
  #map { height: 400px; background: #000; }
  #slider { flex: 1; }
  #readout { margin: 0 12px 8px; color: #555; }
  #static img { display: block; width: 100%; }
  .now { background: #2b8a3e; color: #fff; padding: 1px 6px; border-radius: 4px; }
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
    <button id="center">Center</button>
    <label><input id="footprint" type="checkbox"> Footprint</label>
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
}

map.on("load", () => {
  // Track; clicking a point moves the slider there, hovering shows its details.
  map.addSource("track", { type: "geojson", data: {
    type: "Feature", properties: {},
    geometry: { type: "LineString", coordinates: pts.map((p) => [p.lon, p.lat]) },
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

document.getElementById("center").onclick = () => map.flyTo({ center: sat.getLngLat() });
document.getElementById("basemap").onchange = (e) => {
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
  timer = setInterval(() => show((Number(slider.value) + 1) % pts.length), 200);
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
