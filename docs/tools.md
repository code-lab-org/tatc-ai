# MCP tools

The MCP server provides satellite ground-track maps and modeled coverage estimates through LibreChat.

## Ground-track maps

`generate_ground_track` returns telemetry with UTC time, latitude, longitude, and altitude in meters. With `include_footprint`, it also returns a TAT-C footprint for each point. Each footprint covers the ground area inside a 60-degree cone below the satellite and is a GeoJSON Polygon or a MultiPolygon split at the antimeridian.

`ground_track_map` takes a satellite, start time, duration, and step interval. It always includes footprints and returns a short text summary plus an interactive MapLibre globe with a static Cartopy picture. LibreChat renders the page inline from a `ui://` resource, using its dark theme. The Footprint box is checked by default. Follow keeps the satellite centered while playing or scrubbing until you move the map by hand. The drawn track follows great circles between samples and splits at the antimeridian. The current orbit, one period calculated from mean altitude with Kepler's law, is bright and has hoverable points; the rest of the track is faded. Footprints stop at 85 degrees latitude, the limit of the globe's map tiles.

Ground-track requests are capped at 2,000 output points. A one-day request at one-minute intervals returns 1,441 points.

## Coverage estimates

Coverage tools estimate when one satellite or a constellation of up to six satellites could view a region. TAT-C performs all coverage modeling; OSCAR helps find instruments.

A run takes three calls:

1. `begin_access_run` locks one satellite, or several separated by commas, and an explicit UTC window of up to 366 days. It returns a draft ID and each orbit's mean altitude.
2. `set_access_region` sets four separate numeric bounds on the draft.
3. `finish_access_run` takes the grid size, exactly one of `field_of_regard_deg` or `swath_width_km`, a citation in `geometry_reference`, and a required `geometry_assessment` with source text and an explanation. An approximation requires a warning, which stays with saved results. The optional `daylight_only` flag filters optical imagers.

`resolve_instrument_geometry` returns instrument context and extracted numeric candidates for the assistant to assess before running coverage. `convert_swath_to_field_of_regard` converts a swath width at an altitude with the same TAT-C math used by a run.

Windows of three days or less allow 144 cells. Longer windows allow 36 cells and 72 cells times the number of satellites, and can take minutes.

`get_access_daily` pages through daily observed and gap areas. `get_access_cells` returns the grid as GeoJSON with per-cell access counts and revisit, filtered to all, observed, or gaps. `get_access_revisit` returns revisit numbers. Results and drafts expire after 24 hours. Coverage estimates model satellite access. They do not include actual acquisitions or cloud cover.
