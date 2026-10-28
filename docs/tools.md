# MCP tools

The MCP server provides satellite ground-track maps, legacy interval-based coverage estimates, and event-based conical-scanning access studies through LibreChat.

## Ground-track maps

`generate_ground_track` returns telemetry with UTC time, latitude, longitude, and altitude in meters. With `include_footprint`, it also returns a TAT-C footprint for each point. Each footprint covers the ground area inside a 60-degree cone below the satellite and is a GeoJSON Polygon or a MultiPolygon split at the antimeridian.

`ground_track_map` takes a satellite, start time, duration, and step interval. It always includes footprints and returns a short text summary plus an interactive MapLibre globe with a static Cartopy picture. LibreChat renders the page inline from a `ui://` resource, using its dark theme. The Footprint box is checked by default. Follow keeps the satellite centered while playing or scrubbing until you move the map by hand. The drawn track follows great circles between samples and splits at the antimeridian. The current orbit, one period calculated from mean altitude with Kepler's law, is bright and has hoverable points; the rest of the track is faded. Footprints stop at 85 degrees latitude, the limit of the globe's map tiles.

Ground-track requests are capped at 2,000 output points. A one-day request at one-minute intervals returns 1,441 points.

## Legacy coverage estimates

Coverage tools estimate when one satellite or a constellation of up to six satellites could view a region. TAT-C performs all coverage modeling; OSCAR helps find instruments.

A legacy run takes three calls:

1. `begin_access_run` locks one satellite, or several separated by commas, and an explicit UTC window of up to 366 days. It returns a draft ID and each orbit's mean altitude.
2. `set_access_region` sets four separate numeric bounds on the draft.
3. `finish_access_run` takes the grid size, exactly one of `field_of_regard_deg` or `swath_width_km`, a citation in `geometry_reference`, and a required `geometry_assessment` with source text and an explanation. An approximation requires a warning, which stays with saved results. The optional `daylight_only` flag filters optical imagers.

`resolve_instrument_geometry` returns instrument context and extracted numeric candidates for the assistant to assess before running coverage. `convert_swath_to_field_of_regard` converts a swath width at an altitude with the same TAT-C math used by a run.

Windows of three days or less allow 144 cells. Longer windows allow 36 cells and 72 cells times the number of satellites, and can take minutes. Legacy response fields and interval definitions are unchanged; their saved results carry `accounting_method: "legacy_intervals_v1"`.

`get_access_daily` pages through daily observed and gap areas. `get_access_cells` returns the grid as GeoJSON with per-cell access counts and revisit, filtered to all, observed, or gaps. `get_access_revisit` returns revisit numbers. Results and drafts expire after 24 hours. Coverage estimates model satellite access. They do not include actual acquisitions or cloud cover.

## Conical-scanning studies

A conical study models one sourced conical instrument on one spacecraft. It keeps every valid TAT-C viewing epoch instead of merging the broader field-of-regard period into an access interval.

A study takes four calls:

1. `begin_access_run` with exactly one satellite and an explicit UTC window.
2. `set_access_region` with four numeric bounds.
3. `prepare_conical_study` with draft ID, grid rows and columns, and one complete conical configuration. It validates and resolves the configuration but runs no calculation. It returns the region, grid, window, chosen source options, approximations, warnings, limits, and a `setup_revision`.
4. `finish_conical_study` with the draft ID, the exact `setup_revision`, and `accept_setup=true` only after the researcher accepts the proposal.

The preparation step is a separate request form from legacy geometry. Do not pass `field_of_regard_deg` or `swath_width_km` to a conical study; do not pass a conical configuration to the legacy finish tool. A stale revision, changed region, changed grid, changed orbit, changed configuration, changed source choice, changed approximation, or changed software version fails before calculation. Preparing the same draft again replaces its proposal, so an older acceptance no longer applies.

### Configuration input

The configuration is typed and rejects unknown fields. It records:

- Identity: `configuration_id`, immutable `revision`, instrument, spacecraft name and aliases, NORAD ID, orbit provider, operating mode, and channel.
- Applicability: an explicit UTC `[start_utc, end_utc_exclusive)` interval, evidence IDs, and its basis. The full requested window must lie inside it.
- Geometry: a `conical` model family; cone angle, scan center azimuth, scan half width, and along-track field of view as `{value, unit}` angles in degrees; velocity frame; nadir reference; illumination requirement; optional beam width and scan sample spacing.
- Evidence: authority, title, URL, document revision, locator, excerpt, retrieval time, interpretation, and the exact parameter keys each excerpt supports.
- Source revision: the checked revision ID, time, authority, summary, and evidence IDs.
- Source conflicts: at least two documented options, the chosen option, and a rationale. Numeric option values are checked against the geometry so a sector width cannot be paired with another option's angles or frame.
- A selected source option that gives a ground incidence angle must also give a separately sourced cone angle for the TAT-C mapping. An incidence angle alone cannot supply that mapping.
- Interpretation and approximations: the selected mode, limitations, approximation status, each approximation's affected parameters and impact, and reported geometry checks. A failed check rejects the configuration. Declared approximations cannot be labeled `supported`.

The server validates structure, ranges, units, evidence presence and references, applicability, acceptance identity, and calculation bounds. It does not authenticate arbitrary source text or independently confirm the researcher's conversation. A citation alone is not evidence that a mapping passed its checks.

### Source-backed AMSR2 example

`apps/mcp-server/data/amsr2_conical_configuration.json` is the study-specific AMSR2 configuration used by the tests and tool-calling eval. It is not a shared registry entry and acceptance does not promote it.

| Item | Value | Source |
| --- | --- | --- |
| Spacecraft | GCOM-W1, NORAD 38337, CelesTrak | JAXA GCOM-W page |
| Mode / channel | 40 rpm conical scan; S1 10.65 GHz group | JAXA AMSR2 specifications; TAT-C validation |
| Cone angle | 47.564 deg from geodetic nadir | TAT-C 3.6.0 AMSR2 validation, Test 2 |
| Scan center / half width | 0.498 deg / 75.332 deg | TAT-C 3.6.0 validation, Test 2 |
| Velocity frame | inertial | TAT-C 3.6.0 validation, Test 2 |
| Along-track field of view | 1 deg, explicitly approximate | TAT-C 3.6.0 validation, Test 3 |
| Beam width / sample spacing | 1.2 deg / 0.622 deg | JAXA 10.65 GHz channel; TAT-C validation |
| Applicability | 2012-08-10 through 2026-10-27 exclusive | JAXA regular-observation notice and in-operation status |

The product scan sector is deliberately different from several nearby source facts:

- JAXA gives a nominal performance-guarantee sector of plus or minus 61 degrees (1,450 km) and a wider product sector of plus or minus 75 degrees (1,620 km). The TAT-C validation measured a 75.332 degree half width from 10.65 GHz footprints. This configuration uses the product sector. The nominal sector is a different configuration; it is not combined with the measured product angles.
- JAXA's nominal 55 degree incidence angle is measured at the ground. TAT-C's cone angle is 47.564 degrees from nadir. They are not interchangeable. The configuration records the 55 degree value as context only.
- The 1 degree along-track field of view stands in for integration-time motion and footprint width. It is not a measured integration time. TAT-C's validation also tests time-step-tailored widths; the fixed 1 degree value is the event-matching approximation.

The applicability end is the source retrieval cutoff, not a documented mission end date. Only the requested window inside that interval is modeled. This increment rejects uncovered dates instead of returning partial-study rows. Configuration applicability is distinct from orbit availability, which the existing OMM freshness checks handle separately, and from a confirmed acquisition schedule, which is not modeled.

### Events and accounting

TAT-C `collect_observations` returns a row for each valid conical viewing epoch. A row's `start`/`end` is the broader field-of-regard period. The save retains that period as envelope metadata on the event. A full-rotation or wider-sector instrument can produce two valid cone crossings in one period; both rows are kept. The AMSR2 product sector is forward-looking, so its fixtures normally produce one valid crossing per period.

The result uses `accounting_method: "conical_events_v1"`:

- An event belongs to the half-open study window and applicability interval containing its epoch.
- A cell is visited on a UTC day when at least one valid event epoch falls in that half-open day. The cell's WGS84 geodesic area is counted once that day, no matter how many events occur.
- Daily area is sampled-cell area at cell centers, not complete cell or pixel coverage.
- Event spacing is the difference between consecutive distinct viewing epochs at the same cell, in hours. Fewer than two distinct events gives null spacing. Exact simultaneous duplicates count once and merge their contributors; nearby epochs are not merged with an undocumented tolerance.
- Field-of-regard envelope durations are not continuous viewing time, access duration, or uncovered duration. The result reports continuous viewing and uncovered durations as unavailable.
- A period crossing midnight does not create access on the next day unless a valid epoch occurs there.
- A saved result with zero events is a modeled zero over the accepted window. An unresolved or rejected setup produces no result and must not be described as zero access.
- The compact result states `completeness_status: complete` only because this increment accepts exactly one modeled configuration and requires its applicability interval to cover the whole window. `modeled_configurations`, `omitted_configurations`, `applicable_dates`, and `unresolved_dates` make that scope explicit.

### Result fetches

All conical fetches return the same accepted setup snapshot, provenance, warnings, limits, and accounting definitions, plus `accounting_method`. Results and drafts expire 24 hours after saving; an earlier restart clears them and capacity eviction can remove older entries. There is no durable archive.

Cell replies carry provenance, warnings, and definitions once at the response level. Their GeoJSON metadata keeps the configuration and setup identity without repeating those records.

- `get_access_events` pages events, default limit 100, maximum 366.
- `get_access_daily` pages half-open UTC daily rows with event counts and visited-cell areas.
- `get_access_cells` returns paged GeoJSON grid cells with event count, observed days, and per-cell spacing. Offset and limit apply to matching cells.
- `get_access_revisit` returns event-spacing metrics and explicitly lists continuous metrics as unavailable for conical results.
- `get_access_event_spacing` pages per-cell spacing values and summary statistics.

### Verification limits

The integration test compares the mapped instrument with an independently constructed direct TAT-C 3.6 public-API reference using the same fixed orbit and points. It requires identical event counts and target identities, corresponding epochs within 1 millisecond, and projected reference positions within 1 meter. The fixture also includes independent inside/outside-view points and both orbit directions.

Those are software-integration tolerances, not mission-accuracy claims. The recorded mission reference is the TAT-C 3.6.0 AMSR2 validation for one analysis day: about 1.662 km along-track p95 orbit residual, 0.399 km cross-track p95, 0.555 km radial p95, 0.27 s p95 event-time residual, and 2.83 km swath-width difference. The per-case engineering tolerances are 2 km, 1 s, and 5 km, each a rounded margin above the recorded residual. They describe that one reference day and do not establish general mission accuracy for other dates or channels.

This workflow does not include a shared registry, registry lookup, additional instrument families, partial or disjoint applicability studies, acquisition schedules, clouds, terrain, retrieval quality, or a browser coverage explorer. Study-specific settings are not promoted into a shared registry.
