# TATC capabilities not yet visualized in TATC-AI

This lists TATC 3.5.1 functions and schemas that could drive Cesium
visualizations in the study pane but are not exposed by the TATC-AI MCP layer.
It was compiled from the installed `tatc` package (public modules under
`tatc.analysis`, `tatc.generation`, `tatc.schemas` and `tatc.utils`) on
November 2, 2026.

The server now pins TATC 3.6.0. It still exports every analysis function listed
here from `tatc.analysis`, under reorganized submodules (for example
`point_sampling` instead of `coverage`), and adds APIs such as radar tracks,
region observations, limb and coincidence analysis that this list has not yet
reviewed.

## What the MCP layer uses today

| TATC API | Where TATC-AI uses it | Visualization |
| --- | --- | --- |
| `analysis.coverage.collect_observations` | `analyze_coverage` and `visualize(view="coverage")` | Study pane: results, observation/gap timeline, tables, CSV and study JSON downloads, Cesium 2D/3D orbits and observation links |
| `analysis.track.compute_ground_track` | `ground_track_map` | Inline MapLibre map with a circular footprint from an assumed 60° field of view; not in the Cesium pane |
| `schemas.Satellite` with any TATC orbit type | Coverage scenarios | Orbit tracks in Cesium |
| `schemas.Instrument`, `schemas.PointedInstrument` | Coverage scenarios | Access results; all settings in the summary and Inputs |
| `PointedInstrument.compute_footprint`, `compute_footprint_center` | Coverage scenes with pointed instruments | Footprints layer: instantaneous footprint and boresight line |
| `schemas.Point` | Coverage study points | Point markers and per-point timelines |

Orbit views (`visualize(view="orbit")`) use CelesTrak GP (OMM) elements and TATC propagation
for display sampling only.

## Accepted by the MCP layer but not carried through end to end

These inputs are validated and computed, and are preserved for pane reruns, but
the result does not show them.

| Input | Computed | Model-visible summary | Editable in Inputs | Shown in Cesium |
| --- | --- | --- | --- | --- |
| `PointedInstrument` (`roll_angle`, `pitch_angle`, cross/along-track field of view, `is_rectangular`) | Yes; TATC keeps a viewing epoch only if the footprint contains the point when its view sweeps over it; the study reports epochs, with continuous metrics unavailable | Yes | Yes, except `is_rectangular` and pixel fields | Yes; footprint and boresight |
| Instrument constraints (`req_self_sunlit`, `req_target_sunlit`, `min_access_time`, `access_time_fixed`) | Yes | Yes | Yes, except `access_time_fixed` | No day/night context |
| Non-circular orbits (`sso`, `keplerian`, `molniya`, `tundra`, `geosynchronous`) | Yes; orbit JSON is listed | Yes | No; "Orbital elements are preserved" | Yes, as tracks |
| Circular RAAN and true anomaly | Yes | Yes | No; only altitude and inclination | Yes |
| `compute_ground_track` swaths | Only in `ground_track_map` | One-line summary | n/a | No; MapLibre only, assumed field of view |

Tool descriptions mention only circular, OMM and TLE orbits.
An invalid orbit returns TATC's full multi-model validation error (16 entries
for an incomplete Molniya orbit), which is hard for a chat model to act on.

## TATC functions missing from the MCP layer

Each could become a selectable study type or map layer in the pane: chosen in
Inputs, computed by TATC on the server, drawn by Cesium, and summarized in text
for chat follow-ups.

### Instrument geometry

| TATC API | Computes | Cesium visualization | Pane control |
| --- | --- | --- | --- |
| `utils.projection.compute_projected_ray_position` | Ground intersection of any ray | Satellite-to-corner frustum lines | Part of the Footprints layer |
| `analysis.track.collect_ground_track` | One footprint polygon per time step | Swath accumulating behind the satellite | Swath layer, trail length |
| `analysis.track.compute_ground_track` | Footprints dissolved per orbit or over the window | Total observed area per satellite or instrument | Observed-area layer (replaces the assumed 60° circle) |
| `analysis.track.collect_ground_pixels` | Ground position of each detector pixel (rectangular `PointedInstrument`) | Pixel grid showing sampling and overlap | Pixel counts, oversampling |
| `utils.projection.compute_limb` | Outline of the Earth disk visible from the satellite | Horizon circle following the satellite | Visible-Earth layer |

Pointing is a fixed roll and pitch per instrument; TATC has no yaw or
time-varying attitude. Conical scanners (for example TMI, GMI, MWRI-RM) can only
be approximated as a pitched rectangle, not the scanned arc.

### Orbits and constellations

| TATC API | Computes | Cesium visualization | Pane control |
| --- | --- | --- | --- |
| `schemas.WalkerConstellation` (delta/star) | Satellites from planes, count and relative spacing | All members and orbital planes, coloured by plane | Constellation builder in Inputs |
| `schemas.TrainConstellation` | Satellites in sequence at an interval, optional repeat ground track | Train members along one track | Interval, count |
| `schemas.SOCConstellation` | Streets-of-coverage arrangement from swath width and packing distance | Planes and adjacent swaths | Swath width, packing |
| `schemas.MOGConstellation` | Mutual orbiting group around a reference orbit | Relative motion of the group | Axes, direction, count |
| `analysis.track.collect_orbit_track` (`WGS84`, `ECEF`, `ECI`; position or position/velocity) | Satellite state vectors | Earth-fixed or inertial orbit display; velocity vectors | Reference-frame toggle |

Constellations are currently rejected (`type: Input should be 'satellite'`)
because scenarios accept only `Satellite`.

### Area coverage

| TATC API | Computes | Cesium visualization | Pane control |
| --- | --- | --- | --- |
| `generation.points.generate_points_fibonacci_lattice`, `generate_points_uniform_spacing`, `generate_points_uniform_angular_distance` (optional mask) | Study points over a region | Generated point sets over a polygon or the globe | Region, spacing, method |
| `generation.cells.generate_cells_uniform_spacing`, `generate_cells_uniform_angular_spacing` | Grid cells over a region | Cell outlines | Grid size |
| `analysis.coverage.collect_multi_observations`, `aggregate_observations`, `reduce_observations` | Constellation observations, interleaved and reduced per point | Per-point access count and revisit | Metric |
| `analysis.coverage.grid_observations` | Per-cell samples, mean access and harmonic-mean revisit | Area coverage and revisit heatmaps | Metric, colour scale |

### Ground stations and latency

| TATC API | Computes | Cesium visualization | Pane control |
| --- | --- | --- | --- |
| `schemas.GroundStation` (`min_elevation_angle`, `min_access_time`) | Station definition | Station markers and visibility cones | Add stations, minimum elevation |
| `analysis.latency.collect_downlinks` | Contact windows between satellites and stations | Satellite–station links; contacts on the timeline | Station filter |
| `analysis.latency.compute_latencies`, `reduce_latencies` | Delay from observation to first downlink, per point | Latency timeline and table | Metric |
| `analysis.latency.grid_latencies` | Per-cell latency statistics | Latency heatmap | Grid, metric |

### Navigation and radio occultation

| TATC API | Computes | Cesium visualization | Pane control |
| --- | --- | --- | --- |
| `analysis.dop.compute_dop` (`GDOP`, `PDOP`, `HDOP`, `VDOP`, `TDOP`) | Dilution of precision over time at a point | Lines to visible satellites; DOP time series | Method, minimum elevation |
| `analysis.ro_coverage.collect_ro_observations` | Radio-occultation events and tangent-point tracks | Tangent-point profiles in 3D | Receiver and transmitter sets, elevation range |

### Illumination

| TATC API | Computes | Cesium visualization | Pane control |
| --- | --- | --- | --- |
| Solar columns from `collect_observations(..., omit_solar=False)` (`sat_sunlit`, solar altitude/azimuth) | Sunlit state and solar angles per observation | Observations marked day or night; Cesium sun lighting and terminator | Lighting toggle; illumination constraint |

TATC does not produce a solar-illumination mask as a separate product.

## Unsupported list in the MCP instructions

The server currently tells the model that the study pane does not draw swaths,
footprints of basic field-of-regard instruments or illumination masks, and that
scheduling, downlink/latency, cost models and design-space optimization are
unsupported. Regional coverage goes to the begin, region and finish workflow,
whose cells are not yet drawn in the pane. Swaths, area heatmaps and downlink
latency are TATC capabilities missing from the study pane, not TATC limitations; that
list should change as each is exposed. Observation scheduling, cost models and
design-space optimization are not TATC functions.
