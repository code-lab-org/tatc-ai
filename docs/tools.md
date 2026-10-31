# MCP tools

The MCP server provides satellite ground-track maps, legacy interval-based coverage estimates, shared instrument-configuration lookup, and event-based conical, pointed, and mixed-instrument access studies through LibreChat.

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

## Shared instrument configuration lookup

`lookup_instrument_configurations` searches reviewed revisions by instrument, spacecraft name or alias, operating mode, configuration ID, and exact revision. Revision filters use equality. Each candidate includes identity, channel, applicability, normalized geometry, source evidence, geometry checks, source revision, interpretation, approximation status, approximations, source conflicts, and recorded source checks. Pointed candidates also include direct TAT-C reference cases and mission-product comparison metrics. A lookup never selects a configuration. Pass the exact `configuration_id` and `configuration_revision` to the matching conical, pointed, or mixed preparation tool; preparation rejects a missing revision instead of choosing the newest one.

The reviewed registry contains an AMSR2 conical-scan revision for GCOM-W1 and a Sentinel-1C C-SAR IW pointed revision. The AMSR2 revision is `2026-10-27.1`; the pointed revision is `2026-10-30.1`. Each record has its own registry manifest, exact source evidence, and applicability interval. Neither record claims an unrecorded refresh.

`record_instrument_configuration_refresh` records a source check supplied by the assistant or a maintainer. The server does not retrieve source documents. Replaying an identical check is idempotent, including when its timestamp uses another offset for the same UTC instant. An unchanged check records no change. A documentation-only change adds a review notice without a new revision. A changed-geometry finding must include a complete configuration with a new revision and exact copies of the supplied evidence for each changed parameter. A changed-applicability finding must include a complete new revision whose interval retains evidence supporting `applicability.start` or `applicability.end` when those dates change. A geometry check may bind its reported value to a geometry parameter with `geometry_parameter`; a bound value must match the configured geometry within the check tolerance, and an unbound check remains an independent reference. A revised assessment cannot carry a bound geometry check that still reports the prior value of a changed parameter, must include a check that cites the refresh evidence, and cannot silently drop or narrow a declared approximation; an intentional removal needs supplied evidence supporting `approximation.<id>.removed`. The lookup then returns the prior revision with a change notice and the new revision as a source-refresh candidate. It reports the latest source status separately from superseded failed checks, so a successful later check resolves current uncertainty while the history remains in `source_change_checks`. An unchanged check recorded after preparation does not invalidate the accepted setup revision; a new material notice or current failure does. The candidate remains in memory for the current server process; a durable shared revision requires a reviewed repository change. The server rejects a refresh before changing state when it reaches 256 records, 32 candidate revisions, or 1 MiB of serialized refresh data. It never removes an earlier notice to make room. A failed check records why the source state remains unknown. The previous revision remains available within its recorded applicability and carries that uncertainty into a new proposal and saved result. A refresh never changes an earlier result snapshot.

Researchers can still supply a complete study-specific configuration directly. It does not enter the shared registry or require a maintainer queue.

## Pointed Sentinel-1 IW study

A pointed study models one side-looking instrument on one spacecraft. It uses the same staged MCP seam as conical studies: begin with one spacecraft and a UTC window, set the region, call `prepare_pointed_study`, show the sourced geometry and warnings, call `finish_pointed_study` only after the researcher accepts the returned setup revision, then fetch the saved rows with the pointed fetch tools. The single-study tool accepts one configuration; use the mixed workflow below to evaluate multiple configurations together.

The shared Sentinel-1C record is [sentinel-1c-c-sar-iw-fixed-view, revision 2026-10-30.1](../apps/mcp-server/data/configurations/sentinel-1c-c-sar-iw-fixed-view-2026-10-30.1.json). It identifies C-SAR on Sentinel-1C (NORAD 62261), mode IW, right-looking, with a geodetic-nadir off-nadir reference, ellipsoid-normal incidence reference, Earth-fixed zero-Doppler frame, explicit zero-pitch assumption, and fixed negative TAT-C roll. Preparation rejects a resolved TAT-C mean orbit altitude outside the sourced range of 698 km to 726 km. The application interval is 2026-10-02 through 2026-10-31 exclusive; it is bounded by the product-reference day and evidence-check cutoff, not a mission-lifetime claim.

The exact TAT-C reference summary is approximately 27.1–40.4 degrees off geodetic nadir and 30.4–46.0 degrees incidence at the ground. Those are distinct angles. The fixed mapping uses a 33.8-degree right-looking roll magnitude and 13.3-degree cross-track field of view, producing a 27.15–40.45-degree view range within the source's published one-decimal precision. TAT-C's 82.8-degree field of regard adds the validation notebook's 1-degree margin at each side of the far-look angle before doubling it. The 0.23-degree physical azimuth antenna beam width is used as the along-track angular view; it does not model SAR integration, bursts, or image formation.

The configuration retains Product Definition v2.13's separate IW1/IW2/IW3 off-nadir and incidence ranges at its approximately 698 km and 726 km reference altitudes. These per-subswath ranges do not imply one continuously steered swath. Sentinel-1 IW uses three TOPSAR subswaths and orbit-dependent roll steering. The one rectangular fixed-roll view is therefore marked `limited` and carries warnings through the proposal, result, and every fetch.

The [TAT-C 3.6.0 Sentinel-1C validation](https://tatc.readthedocs.io/en/latest/validation/ValidateSARSentinel1.html) compares product annotations from 2026-10-02, including 402 IW products (169 ascending and 233 descending). Its fixed-roll test reports 2,990 edge samples with near/far cross-track p95 errors of 14.29/20.58 km and maxima of 23.83/37.04 km. A roll profile lowers the p95 errors to 6.32/7.02 km. Fixed-roll IW footprint IoU has median 0.925 and fifth percentile 0.865; 750 direct observation checks have no misses, p95 epoch residual 0.439 s, and maximum 0.495 s. These source results are reference context only. The notebook uses an along-track field tailored to approximately five-second footprint sampling, unlike this configuration's physical antenna beam-width mapping, and does not validate the repository's exact adapter or predict acquisition schedules. The [product definition](https://sentiwiki.copernicus.eu/__attachments/a_343020cd67157ee306363c81c9963d6e0ed60b436ea1d5e9bace1595d8ab40b9/S1-RS-MDA-52-7440_Sentinel-1-Product-Definition-v2.13.pdf?cb=9d5ad6f78060502a54f924a898f08f45) is the source for frequency, beam width, mode, steering, altitude, and subswath values.

The local reference fixture independently constructs TAT-C `PointedInstrument`, `Satellite`, and `Point` objects and calls `collect_observations`; it does not call the server's pointed mapping helper. It covers right-side events in ascending and descending passes, a right-side near-range gap, a right-side target beyond the far edge, and a true left-side target in each orbit direction. The left-side targets are independently classified from a Skyfield-propagated ITRS orbit frame; each is inside the broad field-of-regard envelope, excluded by the configured negative right-looking roll, and included by direct TAT-C with the roll sign reversed. The projected orbit-frame math is test-only; production event geometry remains TAT-C.

The MCP integration checks require identical event counts and satellite, instrument, and configuration identities, with corresponding event epochs within 1 ms. Position residuals compare independently propagated WGS84 satellite subpoints at the serialized MCP event epoch and the direct TAT-C event epoch; they do not measure the target point echoed by the event row. Both measured position residuals are 0.005 m, below the 1 m software-integration limit. The separate 1 ms epoch and 1 m position limits bound timestamp conversion and propagated-position comparison rounding; neither is a mission-accuracy validation.

Pointed results use `accounting_method: "pointed_events_v1"`. A day counts a cell only when a valid event epoch falls in that half-open UTC day. Envelope start/end values remain event metadata; they are not continuous viewing time or confirmed acquisition. `get_pointed_events`, `get_pointed_daily`, `get_pointed_cells`, `get_pointed_revisit`, and `get_pointed_event_spacing` page through the saved result and keep the same limitations attached.

## Conical-scanning studies

A conical study models one sourced conical instrument on one spacecraft. It keeps every valid TAT-C viewing epoch instead of merging the broader field-of-regard period into an access interval.

A study takes four calls:

1. `begin_access_run` with exactly one satellite and an explicit UTC window.
2. `set_access_region` with four numeric bounds.
3. `prepare_conical_study` with draft ID and grid rows and columns, plus either one complete conical configuration or an exact registry `configuration_id` and `configuration_revision`. It validates and resolves the configuration but runs no calculation. It returns the region, grid, window, chosen source options, approximations, warnings, limits, and a `setup_revision`.
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

The conical tests and tool-calling eval derive a study-specific AMSR2 example from the shared configuration by overriding its ID and revision to `amsr2-gcom-w1-conical-product-sector-study` at `2026-10-27.1-study` and removing the shared check bindings. This keeps the legacy unbound input path covered without a duplicate data file. Researchers can still supply a complete study-specific configuration directly. The shared AMSR2 entry remains the independent reference target, and a study-specific configuration is not promoted when accepted.

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

All conical fetches return the same accepted setup snapshot, selected registry ID and revision when applicable, source-change records, provenance, warnings, limits, and accounting definitions, plus `accounting_method`. Results and drafts expire 24 hours after saving; an earlier restart clears them and capacity eviction can remove older entries. There is no durable archive.

Cell replies carry provenance, warnings, and definitions once at the response level. Their GeoJSON metadata keeps the configuration and setup identity without repeating those records.

- `get_access_events` pages events, default limit 100, maximum 366.
- `get_access_daily` pages half-open UTC daily rows with event counts and visited-cell areas.
- `get_access_cells` returns paged GeoJSON grid cells with event count, observed days, and per-cell spacing. Offset and limit apply to matching cells.
- `get_access_revisit` returns event-spacing metrics and explicitly lists continuous metrics as unavailable for conical results.
- `get_access_event_spacing` pages per-cell spacing values and summary statistics.

### Verification limits

The conical integration test compares the mapped instrument with an independently constructed direct TAT-C 3.6 public-API reference using the same fixed orbit and points. It requires identical event counts and target identities, corresponding epochs within 1 millisecond, and projected reference positions within 1 meter. The fixture also includes independent inside/outside-view points and both orbit directions. The pointed reference method and its distinct WGS84 subpoint residual are described above.

Those are software-integration tolerances, not mission-accuracy claims. The recorded mission reference is the TAT-C 3.6.0 AMSR2 validation for one analysis day: about 1.662 km along-track p95 orbit residual, 0.399 km cross-track p95, 0.555 km radial p95, 0.27 s p95 event-time residual, and 2.83 km swath-width difference. The per-case engineering tolerances are 2 km, 1 s, and 5 km, each a rounded margin above the recorded residual. They describe that one reference day and do not establish general mission accuracy for other dates or channels.

## Mixed instrument studies

Use the mixed workflow to evaluate one through six conical or pointed configurations together, using exact reviewed revisions or complete study-specific configurations. A study can include up to six staged spacecraft, and multiple configurations may share a spacecraft. Each staged spacecraft must have a matching selected configuration. Select registry entries by exact ID and revision, or provide a complete study-specific configuration inside a `configuration` field. Pass `configurations` as a JSON list of objects; for an inline configuration, use `{"configuration": {...}}`, not a quoted JSON string. Duplicate configuration IDs, inconsistent identities, unsupported schemas, spacecraft mismatches, and legacy proposal conflicts are rejected.

The workflow uses the same draft and acceptance boundary as the single-instrument studies:

1. `begin_access_run` stages the exact spacecraft set and a UTC window; `set_access_region` records the study region.
2. `prepare_mixed_study` takes a list of exact registry selectors or complete study-specific configurations. It checks every full-window applicability interval, the spacecraft identities, and workload limits, then returns a proposal with each configuration's evidence, revision, orbit, geometry, illumination setting, warnings, and setup revision. Preparation runs no viewing-event calculation.
3. Present every configuration and its limits. Run `finish_mixed_study` only after the researcher accepts that exact setup revision. A changed orbit, region, window, grid, source notice, configuration, or setting requires a new proposal and acceptance.
4. Fetch `get_mixed_events`, `get_mixed_daily`, `get_mixed_cells`, `get_mixed_revisit`, or `get_mixed_event_spacing`. Omit `configuration_id` for the combined result. Pass one exact ID from the result to retrieve that configuration's contribution.

Every selected configuration must apply to the complete half-open UTC study window. The mixed workflow does not return partial rows for unsupported dates. Studies up to three days allow at most 144 cells. Longer studies allow at most 36 cells and 72 cell-configuration calculations. Execution uses one worker pool with at most four workers and no nested pools.

TAT-C supplies each instrument's viewing epochs using that configuration's geometry and illumination setting. Per-configuration results retain the selected ID, revision, evidence, source choices, spacecraft orbit, and warnings. The combined event result merges only equal cell IDs at the exact same UTC epoch. It retains every simultaneous contributor; a nearby but distinct epoch remains a separate event. Combined daily area sums the WGS84 areas for the union of visited cell IDs once per day. Combined spacing uses consecutive distinct epochs at each cell. Envelope periods remain metadata, never continuous viewing time.

Illumination is configured per instrument. A `target_sunlit` requirement must identify its basis as source evidence or as a study assumption. A study assumption needs an explanatory note and stays labeled as a sensitivity choice, not as a source-established scientific requirement. This does not add illumination science to TAT-C or the configuration.

All mixed fetch responses include the scope, accounting method, modeled configuration identities, scope-specific warnings, limits, applicability, definitions, and compact provenance. Combined fetches return warnings for every configuration; a configuration-scoped fetch returns that configuration's warnings. Event and spacing pages default to five rows and cap at 20; cell pages default to two features and cap at 10. Use `offset` to retrieve later pages. The repeated provenance header contains the accepted setup revision, each configuration's SHA-256 snapshot digest, source revision and evidence IDs/URLs, applicability interval, TAT-C geometry mapping and illumination setting, and spacecraft OMM identity/hash. Cell GeoJSON metadata repeats only result, scope, setup, and window identity. The accepted finish summary retains the complete configuration and evidence snapshots, including full geometry, evidence excerpts, and interpretations. `mixed_events_union_v1` identifies the combined exact-epoch union; `mixed_configuration_events_v1` identifies one configuration's accounting. A zero-event result is a modeled zero for the accepted window. An unaccepted or rejected study has no result. Existing legacy and single-instrument workflows remain available on drafts without a mixed proposal.

The reviewed registry currently contains the AMSR2 conical and Sentinel-1C IW pointed configurations; it does not include other instrument families, partial or disjoint applicability studies, acquisition schedules, clouds, terrain, retrieval quality, or a browser coverage explorer. Source-document search, downloading, and monitoring remain outside the MCP server. Study-specific settings are not promoted into the shared registry.
