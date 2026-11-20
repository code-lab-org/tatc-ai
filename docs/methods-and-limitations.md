# Methods and limitations

This page describes what a TAT-C AI study calculates and what it does not. The tool guide has the input details and result fields.

## What a study calculates

A study estimates geometric access at grid-cell centers. TAT-C 3.6 propagates the orbit, builds the configured instrument, and returns valid viewing epochs. The server counts a cell as visited on a UTC day when at least one event epoch falls in that half-open day. The server does not replace TAT-C orbit, instrument, or viewing-event math.

## Sensors

Two reviewed configurations are available: AMSR2 on GCOM-W1 and Sentinel-1C C-SAR in IW mode. Each has source evidence, applicability, geometry checks, and declared approximations. A sensor outside those configurations can be recorded as an omission. Its access is unknown, not zero. A complete study-specific configuration can be supplied for the supported conical and pointed schemas, but it does not enter the shared registry.

## Pass direction and applicability

Ascending passes move north and descending passes move south. The pass-direction filter keeps one direction per configuration and records the choice. Events with no latitude change are unclassified and excluded when a filter is set. The legacy interval coverage workflow does not support the filter.

Windows are half-open UTC intervals. A configuration is modeled only inside its established applicability and any accepted study assumption. Unresolved dates are unknown, not zero access. A result is complete only when the requested window and every requested configuration are modeled. An applicability assumption is marked as assumption-modeled, not source-established.

## Orbit inputs

CelesTrak OMM elements supply the orbit. The server rejects element sets outside its freshness guardrail and does not fetch a new orbit silently during a study. Orbit error grows with distance from the element epoch, so long windows report aggregate statistics only.

Each result keeps the exact OMM records. The export includes them so the orbit can be audited. Replaying the setup does not reuse the exported orbit records: `begin_access_run` fetches current elements, so compare the new orbit hash with the recorded one before treating a replay as the same run.

## Envelopes and daily area

A TAT-C row includes a broad field-of-regard envelope and a valid viewing epoch. Only the epoch is an access event. The envelope is metadata. Its duration is not continuous viewing time or uncovered time.

Daily area is the sum of WGS84 cell areas whose centers had at least one event that day. It is sampled-cell area, not complete cell or pixel coverage. A day with zero events over a modeled interval is a modeled zero, not an omission.

## Storage and export

Results and drafts live in server memory for 24 hours. A restart or capacity eviction can remove them earlier. There is no durable server archive.

`export_study` returns a signed download link for a JSON file. The link is valid for 15 minutes. The file contains the exact orbit records, configuration snapshots, source evidence, software versions, assumptions, results, and a replay block. Save the file if you need it later.

## Verification

The test suite compares mapped conical and pointed configurations with independent direct TAT-C 3.6 calls. It checks event counts, target identities, epochs within 1 ms, and projected positions within 1 m. Those are software-integration tolerances, not mission accuracy. The AMSR2 mission reference covers one analysis day and does not establish accuracy for other dates or channels. Scientific review remains a separate milestone.

## Not modeled

- Actual acquisitions, tasking, or instrument schedules.
- Clouds, terrain, atmosphere, or retrieval quality.
- Continuous viewing time from field-of-regard envelopes.
- Downlink, latency, cost, or design optimization.
- Sensors without a reviewed configuration or a complete supported study-specific configuration.
