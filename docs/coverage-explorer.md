# Research visualization workspace

The optional research workspace adds one visual MCP entry point, `visualize`,
alongside the numerical `analyze_coverage` tool and existing satellite tools.
An always-available **Study** button in LibreChat's chat header opens a resizable
pane beside the conversation, or a full-width pane on small screens. The pane
starts closed. Before any visual result exists, it shows a simple empty state;
opening it does not call an MCP tool, invent a scenario, or load Cesium.

The conversation also contains a compact **Open study pane** link for each visual
result. Study opens the latest available result when there is no active study.
Closing and reopening preserves the active tab, draft inputs, camera, and clock
within the current page session. Playback pauses when hidden and stays paused on
return. A new visual result updates an open pane; while closed, it waits until
reopening before loading. Historical results do not open the pane automatically.
Changing conversations or reloading the page clears this local pane state. A new
chat receiving its ID after the first prompt (`/c/new` to `/c/<id>`) is the same
conversation, so a pane opened there stays open.

The MCP server returns the study data and serves the iframe's viewer assets.
LibreChat's host adapter controls the button, pane, iframe, and visibility messages;
Cesium runs in that browser iframe only when a spatial view is opened. Ordinary
chat does not open the pane. Tool selection remains the chat model's responsibility,
using the existing tool descriptions and conversation context.

The pane contains:

- **Results:** coverage comparisons by location, selectable metrics, gap-duration
  distributions, or bar/line charts of supplied numerical series. Data bar plots use labelled
  axes, numerical tick marks, a shared zero baseline, and exact-value inspection. Missing values
  remain missing, not zero.
- **Map:** CesiumJS 2D/3D views, satellite legend, orbit/ground-track/observation
  layers, and adjustable trail duration. Pointed instruments add a Footprints layer:
  the instantaneous footprint from TATC `PointedInstrument.compute_footprint` and a
  boresight line from the satellite, sampled at the display step and interpolated
  between samples. Cesium loads only when this view opens.
- **Table:** computed values and individual events, with CSV export of the current
  table. Selecting an observation or gap seeks the shared clock to its midpoint, or
  to its viewing epoch for a pointed or conical instrument.
- **Download study JSON:** one coverage study's scenario inputs, results, analysis
  ID, accounting, assumptions and software versions, for reproducing it. Orbit and
  data views have no such analysis and offer CSV only.
- **Inputs:** editable UTC analysis window, display sampling, circular-orbit
  altitude/inclination, sensor field of regard, pointed-instrument fields of view,
  roll and pitch, minimum access time, satellite/target lighting requirements, and
  study-point coordinates. Other supplied orbital elements and instrument settings
  are preserved.

Coverage views share a location selector and observation/gap timeline. Playback
starts paused. Map controls change the display immediately; mission input edits
remain drafts until a new calculation returns.

Closing the pane retains one iframe, stops Cesium's render loop, and pauses the
globe-loading deadline. Returning resumes rendering, not playback. A result that
arrives while closed is deferred; a close during asset loading waits for a visible
map before constructing the globe. No renderer substitution or automatic retry
is used.

## Tool contract and intent

| `view` | Inputs | Initial presentation |
| --- | --- | --- |
| `coverage` | Complete agreed `scenario` with satellites, sensors, points, times | `overview` |
| `orbit` | Resolved `satellite_identifiers`; optional start, duration, display step | `simulation3d` |
| `data` | `dataset` containing labelled numeric series, units and source | `overview` |
| `example` | Explicitly requested built-in hypothetical coverage study | `overview` |

`presentation` selects `overview`, `timeline`, `table`, `map2d`, or `simulation3d`.
Numeric datasets support overview/table; orbit-only results have no coverage event
timeline. Unsupported combinations are rejected, not replaced with another view.
`workspace_id` is an optional UUID copied from parameter-rerun intents so the new
result updates the same pane; a value that is not a UUID (for example a LibreChat
UI marker ID) is rejected with a message telling the model to omit it. Different
views' input arguments cannot be mixed.

LibreChat stores only a tool result's text, not its structured content. The text
of a coverage or example result therefore carries the exact study: `analysis_id`,
UTC window, display step, satellites (supplied orbit and instrument field of
regard), point coordinates, and per-point observation count, percent covered,
longest gap in minutes (flagged when at a window boundary), and mean complete
revisit in minutes or "none". A study with a pointed or conical instrument instead
gives viewing-epoch counts, first and last epochs and mean event spacing, and states
that continuous metrics are unavailable. It ends with a conditional, model-only note naming
the call that re-shows the same study if the user later asks for another view:
`visualize(view='example', presentation=<one>)` for the built-in example, or
`visualize(view='coverage', analysis_id=..., presentation=<one>)` for a custom
study. It does not list presentations, so the model does not narrate views it
never requested. The server keeps the last 100 scenarios by `analysis_id` in
memory (including those from `analyze_coverage`); after a restart the model must
resend the scenario. Orbit results state each satellite's NORAD ID and element epoch,
also shown under Methods & source, and the exact orbit call to re-show them.

Required keys, size and range limits, timestamps and `workspace_id` are checked
by the server rather than the JSON schema (the `visualize` and `analyze_coverage`
schemas have no formats, ranges, list sizes or patterns), because LibreChat
reports schema failures to the model only as an opaque mismatch. A scenario or
dataset sent as a JSON-encoded string is parsed. Relative dates such as
"tomorrow", missing satellites, and points or times nested inside a satellite
get errors that say what to ask or which top-level keys to use. Unknown orbit or
instrument fields are rejected with the list of accepted field names; a circular
orbit accepts `type`, `mean_altitude` (or `altitude`), `inclination`,
`right_ascension_ascending_node`, `true_anomaly` and `epoch`.

Choose output from the question and the conversation, not from keywords. “Visualize”
is not a synonym for orbital simulation, and “code to visualize” is usually a code
request. The server instructions and tool descriptions give this guidance to the
chat model; they are not a deterministic prompt classifier. Model routing needs
separate evaluation with the model used by your deployment.

| Request in context | Expected output |
| --- | --- |
| What is the ISS NORAD ID? / Show its orbital elements | Metadata or GP (OMM) elements |
| What is the longest gap in this scenario? | Numerical coverage analysis |
| Compare the longest gaps at these study points | Coverage overview if a visual comparison is wanted |
| Show when Phoenix has no access in this study | Coverage timeline |
| Give me the individual observation values | Numerical output or table, according to context |
| Plot these previously calculated revisit values | `data` chart; no propagation or Cesium |
| Write Python code to plot these values | Code; do not open a scene merely because “plot” appears |
| Show these study locations and orbits on a map | `map2d` |
| Animate ISS around Earth for an hour | Orbit `simulation3d` |
| Map the ISS ground track | Existing ground-track map tool |
| Visualize coverage over Phoenix tomorrow, with no mission context | Ask which mission and sensor assumptions to use |

Do not invent TLEs, instruments, measurements, or scope. Clarify only missing facts
or competing interpretations that materially affect the result. Do not ask users
to memorize tool names or JSON syntax. An orbit does not imply sensor coverage.
Requests exceeding limits must not silently become shorter or smaller requests.

Numeric charts require `kind` (`line` or `bar`; there is no default) and
`provenance`: `{"kind": "user"}` claims the user typed every value, which the
server cannot verify; `{"kind": "study", "analysis_id": ...}` charts a stored
study's point metrics, and the server checks that labels are its point names and
each series copies one metric (observations, percent covered, longest gap
minutes, or mean complete revisit minutes) within rounding. They accept up to 8
uniquely named series and 2,000 labels, at most 6,000 values total. Series lengths must match labels; use `null` for missing values.
All series share one unit. Source attribution is optional and shown when supplied; plotting values from a
prompt does not require a citation. Line
charts show values in supplied label order with equal label spacing; they do not
infer irregular time intervals. Exact values are available in Table and CSV.
For example, a `data` input can be:

```json
{
  "title": "Revisit comparison",
  "source": "Values supplied by the researcher",
  "labels": ["Design A", "Design B"],
  "series": [{"name": "Mean revisit", "values": [3.5, 1.2]}],
  "unit": "hours",
  "kind": "bar",
  "provenance": {"kind": "user"}
}
```

## Parameter reruns

**Run updated study in chat** sends the draft as an MCP UI intent through the
original LibreChat resource renderer. LibreChat asks its model to call `visualize`
with the exact parameters and workspace ID. The server validates and computes a
fresh result. The host compares its calculated request with the submitted inputs
before replacing the pane. Added satellites, changed parameters, and missing
request records are rejected with a visible notice; the draft and previous
calculated values remain available. Property ordering does not affect comparison.
The check applies to that workspace for the current conversation page session;
it is not persisted across a page reload and does not validate historical studies.
This does not expose an
unauthenticated calculation endpoint or claim that browser edits recalculate TATC.
If the chat model cannot carry out the request, its error remains in chat and the
old study remains available. A successful rerun still depends on the host model
honoring the intent; rejection does not automatically correct or retry its call.
the integration test verifies the actual renderer callback and the exact subsequent
MCP call without substituting a mock calculation.

## Host integration and delivery

TATC calculations run in the existing backend. CesiumJS runs in the browser;
Cesium ion is not used. The Docker image packages pinned CesiumJS 1.146, workers,
and Natural Earth imagery. No CDN or ion token is needed. Charts/tables do not need
WebGL; maps do. Both require a modern browser with `DecompressionStream`.

LibreChat's installed MCP resource renderer embeds resources inline; it does not
provide a side-pane API for them. The small adapter in `apps/librechat/assets/`
adds that pane without forking LibreChat or modifying its bundled JavaScript.
Compose mounts the adapter, and an idempotent startup script adds its script/style
tags to LibreChat's index. Content hashes in those URLs prevent stale host assets
after updates. The adapter accepts messages only from the configured
viewer origin and verified resource frames, binds reruns to their originating
resource, and closes when the conversation changes. Message refreshes may remove
and recreate resource frames; the pane stays open and rebinds to the same result.
Remounting an older result cannot replace a newer calculation in the open pane.

The MCP result is a `ui://` resource with MIME type `text/uri-list` pointing to a
small launcher. The workspace iframe has its own HTTP origin and a sandbox allowing
scripts, same-origin assets and CSV downloads. This avoids LibreChat's opaque
`text/html` srcdoc origin, which broke Cesium workers. Viewer assets use a restrictive
CSP; Cesium's embedded workers require blob scripts, and its expression evaluator
and WebAssembly require `unsafe-eval`. No worker monkey patch is used.

Compressed study JSON travels in the URL fragment, which is not sent in HTTP
requests. Public routes serve generic code/assets, not a stored mission dataset.
The URL itself contains the study and should be handled like the MCP result.
Labels are written as text, never executed as HTML.

The one added compatibility path is an explicit link to open the same workspace
in a separate tab when the client lacks the pane adapter. It uses the same data
and renderer; parameter reruns require the connected LibreChat pane. A map failure
shows an error while independent numerical results remain usable. No alternate
renderer, synthetic orbit, partial trajectory or demo is substituted. Existing
catalog metadata and static ground-track behavior is unchanged.

## Run and try it

```sh
docker compose -f docker-compose.dev.yml up -d --build mcp-server librechat
```

Refresh LibreChat and start a new conversation or reconnect the MCP server so its
updated schema/instructions are loaded. Old inline HTML messages retain old code.
Ask **“Open the built-in coverage example.”** Then open the study pane. Expected
observation counts are Phoenix 3, Svalbard 14, equatorial point 1. The fixed
October 7–8, 2026 scenario is hypothetical, not live data.

Try Results metric selection, Table → gaps, and Map → 2D/3D. In Inputs, change the
first sensor's field of regard from 100 to 80 degrees and run the updated study.
The original results must remain until a new MCP result is returned. Use
[coverage-scenario.json](../apps/mcp-server/src/coverage-scenario.json) as a complete
custom scenario when testing the coverage tool directly.

For a Python-only server, install assets with `npm ci --ignore-scripts` in
`apps/mcp-server/viewer-assets`. `TATC_CESIUM_ASSET_DIR` can select an explicit
Cesium `Build/Cesium` directory.

The browser-facing MCP URL is `TATC_VIEWER_BASE_URL`, or `MCP_BASE_URL` when unset;
dev defaults to `http://localhost:8000`. The LibreChat adapter's
`TATC_VIEWER_ORIGIN` must match it. Deployment compose sets both to
`https://mcp.<DOMAIN>`. Browsers must reach that hostname, not a Docker service name.
Reverse proxies must serve `/visualization/` alongside `/mcp`. The adapter expects
the default `/visualization/v1/` path; a custom path prefix requires updating its
path configuration. MCP authentication remains unchanged.

The orbit view uses current CelesTrak GP (OMM) elements and reports their epochs. It is not a
historical ephemeris service. Defaults are now, one hour and 60-second samples;
resolved values are returned. CelesTrak must be reachable for catalog orbit requests.

## Inputs and limits

Each satellite uses the native TATC `Satellite` JSON schema. Explicit instrument
`name` and `field_of_regard` are required; other instrument defaults are preserved
in the output. A `PointedInstrument` (with `cross_track_field_of_view` and
`along_track_field_of_view`) keeps a viewing epoch only if its footprint contains
the point when its view sweeps over it, so roll and pitch change results. A
`ConicalInstrument` keeps each valid crossing of its scanned cone. The model-visible
summary lists every supplied instrument setting. Circular and other Keplerian-family designs must specify an epoch
with a timezone. Catalog satellites use `{"type": "omm", "elements": [records]}`
in the orbit field, with the OMM records returned by the satellite-info tool;
TATC stores them as a `gp` orbit. Supplied TLEs use
`{"type": "tle", "tle": [line1, line2]}`. Either needs the satellite name and
explicit instruments; the explorer does not fetch or substitute new orbital data
during a study. Supplied TLEs must be two 69-character lines with valid checksums
and the same catalog number. Element sets must have an epoch within 30 days of
the study window (SGP4 error grows by roughly
1–3 km/day in LEO). This also rejects element sets a chat model writes from
memory; the error tells it to use the satellite-info tool or ask the user.
A satellite search with no match returns a message telling the model to ask
which satellite is meant instead of an empty list, which LibreChat shows as
"(No response)".

- At most 4 satellites, 2 instruments per satellite, and 8 unique named points.
- Positive time window of at most 24 hours, with explicit UTC offsets or `Z`.
- At most 384 point-instrument-hours and 4,000 returned observation opportunities.
- Display sampling from 30 to 300 seconds, with at most 6,000 total samples.
- At most 5,768 footprint samples per scene (8 pointed instruments over 24 hours
  at 120 seconds); otherwise the error asks for a larger display step.
- Satellite/instrument names must be unique within their relevant scope.
- Unknown fields, missing required assumptions, and non-finite numbers are rejected.

The first increment evaluates discrete points. It does not interpolate area
coverage, construct regional grids, automatically run design sweeps, or calculate downlink latency.
Tool descriptions list these, plus swath rendering, footprints of basic
field-of-regard instruments,
solar-illumination masks, observation scheduling and cost models, as unsupported
studies the model should explain rather than approximate with invented inputs.

## Interpretation

TATC `collect_observations` calculates event-based opportunities for each point,
satellite, and instrument. For basic field-of-regard instruments this feature clips
observations to the requested window and unions overlapping or touching intervals
before computing uncovered time. Individual observations retain their satellite and
instrument identities.

- **Time covered:** union duration divided by the requested window duration.
- **Longest gap:** largest uncovered interval inside the window, including leading
  and trailing gaps. A point with no observations has a gap equal to the window.
- **Mean complete revisit:** arithmetic mean of uncovered intervals bounded by
  coverage on both sides. Boundary gaps are excluded; the value is `null` when
  there are no complete gaps. This is an explicitly defined point-level metric,
  not TATC's gridded revisit aggregation.

A study with any pointed or conical instrument uses the event-based accounting of
the regional studies instead. TATC validates such an instrument's view only at each
epoch; the wider field-of-regard period around it is kept as `envelope_start` and
`envelope_end` metadata and is not viewing time.

- **Viewing events:** valid TATC epochs in the half-open window `[start, end)`.
  The timeline marks each epoch. The map's observation link is held 30 seconds
  either side so playback can show it; that hold is not a viewing duration.
- **Mean event spacing:** mean time between distinct epochs at a point; `null` with
  fewer than two. It is not an uncovered interval. Median, minimum and maximum are
  in the result too.
- **Unavailable:** time covered, covered duration, gaps and revisit are `null`,
  flagged by `continuous_viewing_duration_available` and
  `continuous_uncovered_duration_available`. The charts and table do not offer them.
- **Mixed studies:** a discrete event cannot be unioned with an interval, so one
  pointed or conical instrument makes these metrics unavailable for the whole study.
  Basic instruments keep their own intervals in the observation rows, outside event
  spacing.

TATC checks some instrument/illumination constraints at pass midpoints; reported
intervals inherit those approximations. Opportunities are not scheduled or executed
observations. Short windows can also truncate access durations used by the model.
Display samples are independent of these calculations and use Earth-fixed WGS84
positions with linear Cartesian interpolation; a smooth replay is not a precision
ephemeris. No observational accuracy or uncertainty is inferred from the display.

The server pins TATC 3.6.0, which the explorer's observation window handling and
schema use and its reference tests check. Requirements do not pin all numerical
dependencies; record and reproduce the reported versions for comparative studies.

## Local verification

```sh
python -m pytest apps/mcp-server/tests -q
cd apps/mcp-server/tests/ui
npm ci --ignore-scripts
TATC_PYTHON=/absolute/path/to/python npm test
```

The Python tests check scientific calculations, limits, tool schemas and delivery.
DOM tests use real computed TATC results and Cesium entity/clock APIs with only the
WebGL renderer stubbed. They also check chart/table values, lazy map loading, draft
submission and map failure isolation.
The map's loading deadline counts 30 seconds while the Map tab, study pane, and browser
document are visible. Closing the pane, switching views, or backgrounding the document pauses the
deadline; returning resumes the remaining budget. It does not restart the map or
substitute a different renderer.
