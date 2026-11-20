# Methods and limitations

This page describes what a TAT-C AI study calculates and what it does not. Read it before treating a result as a mission or science claim.

## What a study calculates

A study estimates geometric access at grid-cell centers. TAT-C 3.6 propagates the orbit, builds the configured instrument, and returns valid viewing epochs. The server counts a cell as visited on a UTC day when at least one event epoch falls in that half-open day.

The server does not replace TAT-C orbit, instrument, or viewing-event math. It validates inputs, stages the study, preserves identity and evidence, and translates TAT-C output into daily rows, cells, events, and spacing.

## Sensors

Two reviewed configurations are available:

- AMSR2 on GCOM-W1, conical scan, product sector.
- Sentinel-1C C-SAR in IW mode, fixed right-looking pointed model.

Each configuration is a reviewed revision with source evidence, applicability, geometry checks, and declared approximations. A sensor outside those configurations can be recorded as an omission. Its access is unknown, not zero. A study-specific configuration can be supplied for the supported conical and pointed schemas, but it is not added to the shared registry.

## Pass direction

An ascending pass moves north. A descending pass moves south. Set `orbit_direction` per configuration to keep one direction. The server derives direction from the sign of the propagated geodetic latitude change around each event epoch. TAT-C does not provide the field.

An event at a turning point can be unclassified. When a filter is set, unclassified events are excluded. The setup and every result page record the filter, excluded count, and unclassified count.

The filter is available for event-based conical, pointed, and mixed studies. The legacy interval coverage workflow does not support it.

## Time and applicability

Windows are half-open UTC intervals `[start, end)`. A configuration is modeled only inside its established applicability intervals and any accepted study assumption. Dates outside those intervals are unresolved, not zero access. A result is complete only when the requested window and every requested configuration are modeled. A partial result names omitted configurations and unresolved dates.

An explicit applicability assumption can extend a configuration for one study. The result marks that time as assumption-modeled, not source-established.

## Orbit inputs and freshness

CelesTrak OMM elements supply the orbit. The server rejects element sets outside its freshness guardrail and does not fetch a new orbit silently during a study. Orbit propagation error grows with distance from the element epoch, so a long window reports only aggregate statistics.

Every result now keeps the exact OMM records. The export file includes them, so the study can be audited or replayed without a network fetch.

## Pass direction, envelopes, and daily area

A TAT-C row includes a broad field-of-regard envelope and a valid viewing epoch. Only the epoch is an access event. The envelope is metadata. Its duration is not continuous viewing time or uncovered time.

Daily area is the sum of WGS84 cell areas whose centers had at least one event in that day. It is sampled-cell area, not complete cell or pixel coverage. A day with zero events over a modeled interval is a modeled zero, not an omission.

## Storage and export

Results and drafts live in server memory for 24 hours. A restart or capacity eviction can remove them earlier. There is no durable server archive.

`export_study` returns a short-lived signed download link for a JSON file. The file contains the exact orbit records, configuration snapshots, source evidence, software versions, assumptions, combined and per-configuration results, and a replay block. The link expires with the result. Save the file if you need it later.

The export is a record, not an import tool. Re-running means feeding the replay block back through the normal begin, region, and prepare calls.

## Verification

The test suite compares mapped conical and pointed configurations with independent direct TAT-C 3.6 calls. It checks event counts, target identities, epochs within 1 ms, and projected positions within 1 m. Those are software-integration tolerances, not mission accuracy.

The AMSR2 mission reference is one analysis day. Recorded residuals and per-case engineering tolerances are in the tool guide. They do not establish accuracy for other dates or channels. Scientific review remains a separate milestone.

## Not modeled

- Actual acquisitions, tasking, or instrument schedules.
- Clouds, terrain, atmosphere, or retrieval quality.
- Continuous viewing time from field-of-regard envelopes.
- Downlink, latency, cost, or design optimization.
- Sensors without a reviewed configuration or a complete supported study-specific configuration.
