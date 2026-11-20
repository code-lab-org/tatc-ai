# Mixed-instrument geometric access

Status: specification for implementation planning. The product scope follows the accepted design of 2026-10-26. The researcher approved publication as a separate issue linked to the coverage-explorer proposal and confirmed the verification approach below. The tool contract and detailed engineering criteria guide implementation and remain subject to implementation review.

## Problem statement

Researchers need to estimate geometric access from instruments with different viewing geometries in one study. The current tools apply one angle or swath and one daylight setting across the selected spacecraft. They combine observations before reporting results, which hides instrument-specific contributions and assumptions.

A conical scanner, a side-looking radar, and separated laser beams cannot be represented by the same nadir-centered swath. Source evidence, operating modes, applicable dates, and accepted approximations must remain identifiable throughout a study. Missing evidence must remain visible rather than becoming a zero-access result.

## Solution

Extend the staged access workflow to accept separately identifiable instrument configurations, calculate each with its appropriate TAT-C model, and report instrument-specific and combined results over the same region, grid, and UTC window.

Begin with AMSR2 on GCOM-W1 and Sentinel-1 C-SAR in IW mode. Shared configurations are explicitly selected, versioned registry records. Researchers may also supply sourced settings for one study. The calling assistant interprets documents and proposes choices; the server validates structured inputs, runs TAT-C, and retains the selected settings and evidence with the result.

The researcher accepts one complete proposed setup containing consequential mode choices, source conflicts, approximations, omissions, and applicability assumptions. A material change requires a new acceptance. A partial study identifies the instruments or dates it could not model.

## User stories

1. As a researcher, I want to select instruments on named spacecraft, so that each result corresponds to the payload I requested.
2. As a researcher, I want mode choices explained before running, so that the assistant does not silently select a consequential mode.
3. As a researcher, I want reusable sourced configurations, so that routine studies do not require new document research.
4. As a researcher, I want separately identifiable source revisions, so that I can choose which settings a new study uses.
5. As a researcher, I want older results to retain their settings after a source refresh, so that their interpretation remains stable.
6. As a researcher, I want to supply sourced settings for one study, so that exploratory work does not require a new shared default.
7. As a researcher, I want conflicting applicable sources and the proposed resolution presented together, so that I can make an informed choice.
8. As a researcher, I want approximations explained and recorded, so that accepting one does not erase its limitations.
9. As a researcher, I want omitted instruments identified with reasons, so that unknown access is distinguishable from zero access.
10. As a researcher, I want dates outside a configuration's applicability identified, so that unsupported extrapolation is explicit.
11. As a researcher, I want AMSR2 modeled as a conical scanner, so that a nadir strip does not replace its scan geometry.
12. As a researcher, I want Sentinel-1 IW modeled with its stated side-looking geometry, so that access appears on the correct side of its orbit.
13. As a researcher, I want illumination settings applied separately to each instrument, so that an optical restriction does not remove radar or microwave opportunities.
14. As a researcher, I want per-configuration events, daily coverage, and revisit statistics, so that I can inspect each contribution.
15. As a researcher, I want combined daily coverage to count each sampled cell once, so that overlapping contributions do not inflate area.
16. As a researcher, I want valid viewing events distinguished from broad field-of-regard periods, so that the output does not claim continuous viewing between them.
17. As a researcher, I want coverage envelopes reported separately, so that an envelope containing gaps does not become instrument-access area.
18. As a researcher, I want the output to explain cell-center sampling, so that a visited center does not imply every point in the cell was viewed.
19. As a researcher, I want geometry checks and simplifications reported separately, so that passing an engineering check does not imply scientific validation.
20. As a researcher, I want compact summaries and paged details with consistent provenance, so that I can investigate results without moving the entire dataset through chat.
21. As a researcher, I want workload limits to account for every configured instrument, so that a study remains bounded when several instruments share a spacecraft.
22. As an existing user, I want the current single-geometry tools and ground-track maps to keep working, so that the new workflow does not break established studies.
23. As a maintainer, I want shared configurations reviewed through ordinary pull requests, so that contributors can maintain them without installing agent skills.
24. As a researcher, I want to know how long settings and evidence remain available, so that I can retrieve them before temporary results expire.

## Implementation decisions

### Configuration identity and evidence

Use a validated configuration record with the following information. Store normalized values alongside their source meaning; normalization must preserve the distinction between incidence, off-nadir, cone, scan, and view angles.

| Information | Required behavior |
| --- | --- |
| Identity | Record a stable configuration ID and revision, instrument and spacecraft identities, aliases, mode, and band or channel when it affects geometry. Resolve the spacecraft to its orbit-provider identity. |
| Applicability | Record established UTC validity intervals and their evidence. Distinguish configuration applicability from orbit availability and from confirmed acquisition schedules. An unknown boundary remains unknown. |
| Geometry | Select a supported typed model and include its required parameters, units, angle references, velocity frame, nadir reference, and illumination requirement. Reject unknown fields, non-finite values, invalid ranges, and incompatible parameter combinations. |
| Provenance | Retain the source authority, URL, document title, revision or date, page/table/field locator, relevant evidence excerpt, retrieval time, and interpretation for each geometry value or documented group of values. Record derived parameters and the TAT-C conversion used. |
| Assessment | Keep the assistant's interpretation, declared approximations, unresolved conflicts, and geometry-check results distinct. A source citation or assistant assessment alone does not establish that a model mapping passed its checks. |
| Origin | Distinguish a shared registry revision from study-specific supplied settings. Study acceptance does not promote a configuration into the registry. |

Registry IDs and revisions are immutable. A revision with changed geometry creates a new record. A saved study contains a resolved snapshot rather than a mutable pointer to the latest registry entry. Separate registry data from generic tool logic; tool code has no mission-specific geometry defaults.

Check for source-document changes when preparing a shared configuration for first inclusion and when explicitly refreshing it. The assistant or maintainer supplies the checked revision and change finding. Preserve the prior revision, disclose a changed source, and obtain the researcher's explicit revision choice when the geometry changes. An existing configuration may be selected without a fresh search, subject to its stated applicability and known change notices. A failed explicit refresh records uncertainty rather than claiming the source is unchanged.

The assistant retrieves and interprets general documents using its available tools. When it cannot obtain sufficient evidence, it asks the researcher. Server-side enforcement covers schemas, units, ranges, evidence presence, applicability, accepted setup identity, and calculation bounds. The server does not claim to authenticate arbitrary source text or independently confirm the researcher's conversation.

### Staged tool contract

Preserve the existing begin, region, finish, and result-fetch workflow. Add the mixed-study path without making existing callers provide mixed-study arguments.

- Add a configuration lookup tool that returns matching registry revisions, their applicability, and limitations. Ambiguous instruments or modes return candidates requiring selection. Looking up a record does not select it for a study.
- Add a study-preparation tool operating on an existing draft. It accepts the grid and a bounded list of explicitly selected registry revisions or sourced study-specific configurations, plus proposed omissions and applicability assumptions. It returns the normalized proposed setup, warnings, unresolved requirements, and a setup revision bound to all consequential settings.
- Extend the finish tool with an accepted setup revision for mixed studies. It executes only a complete, accepted proposal. Mixing that form with legacy geometry arguments is an error with corrective guidance. Changed region, grid, configuration, source revision, omission, or assumption invalidates the previous acceptance.
- Return a result ID, completeness status, modeled and omitted configurations, applicable and unresolved dates, and compact per-configuration and combined summaries. Offer a paged event-fetch tool and selectors on daily, cell, and revisit retrieval for either one configuration or the combined result.
- Return the same setup snapshot, provenance, warnings, and accounting definitions through summary and fetch responses. Keep results usable without a new browser interface.

Acceptance records the researcher's choice as relayed by the calling assistant. The assistant presents required choices together and obtains acceptance before sending the finish request. Ordinary studies have no maintainer review queue.

### TAT-C dependency and integration

Pin the first implementation to TAT-C 3.6.0 and verify its dependency set under Python 3.12. The published package includes both required instrument classes. Its public analysis exports preserve the relevant function names, but its internal module layout and point-result identity differ from the current integration. Migrate to public APIs and translate TAT-C target identities back to stable study-cell IDs. A dependency bump alone is insufficient. [Published TAT-C 3.6.0 source](https://files.pythonhosted.org/packages/ee/18/c1aa9662e3e2fda2ba9187abd7ddd15c214250db5ca3b5eee6e7cf6fc619/tatc-3.6.0.tar.gz).

Use TAT-C for orbit propagation, instrument geometry, viewing-event calculation, illumination, and supported aggregation. Preserve instrument and configuration identity before any combined accounting. Apply illumination requirements to each instrument, including each instrument on a shared spacecraft. Use bounded batches and at most four workers; do not start nested process pools.

The package's point-observation API has no display-sampling interval argument. Ground-track footprint sampling and a modeled instrument's along-track field of view serve different purposes. Keep any integration-time or view-width approximation explicit in the configuration; changing animation sampling must not change access results. [TAT-C AMSR2 example](https://tatc.readthedocs.io/en/latest/validation/ValidateConicalAMSR2.html), [TAT-C Sentinel-1 example](https://tatc.readthedocs.io/en/latest/validation/ValidateSARSentinel1.html).

### First configurations and release gates

The v1 registry includes the agreed AMSR2 conical-scan and Sentinel-1 IW side-looking cases. Each entry requires exact source-backed geometry parameters and an established applicability interval before it can run as a shared selection. A mission-family name alone cannot select a particular Sentinel spacecraft or prove applicability to every spacecraft in that family.

For AMSR2, retain cone angle, scan-center azimuth, scan half-width, velocity frame, along-track view definition, and any channel-dependent beam parameters used by the chosen model. JAXA distinguishes the nominal performance scan sector from the wider product sector. Keep these as different configurations when both are offered; do not combine a width from one sector with angles from the other. Ground incidence is distinct from cone angle. The upstream example provides a modeling reference, not blanket approval to copy its measured settings into all dates or channels. [JAXA AMSR2 specifications](https://www.eorc.jaxa.jp/AMSR/satellite/gcom-w_en.html).

For Sentinel-1 IW, retain spacecraft, mode, look direction, near/far viewing-angle interpretation, cross-track and along-track view, reference frame, and the roll/attitude simplification. Preserve the distinction between subswath limits at different altitudes and ground incidence. A fixed rectangular view must disclose its approximation to steering and subswath behavior. Mode capability is distinct from an actual acquisition schedule. [Copernicus Sentinel-1 mission and IW specifications](https://sentiwiki.copernicus.eu/web/s1-mission).

The first configuration PR must contain the exact normalized parameters, source evidence, applicable dates, reference cases, and measured residuals. Those values are implementation deliverables subject to the checks below; this specification does not invent them from a nominal swath. A configuration without them remains unavailable for shared use. User-accepted simplified configurations retain their approximation status after passing engineering checks.

### Event and coverage accounting

For pointed and conical instruments, TAT-C reports a period inside the field of regard together with a valid viewing epoch. Preserve the epoch as the mixed-study access event and retain the broader period only as separately labeled envelope metadata. Two valid cone crossings in one broad period remain two events. Broad-period durations cannot establish continuous instrument-viewing time. [Published point-observation implementation](https://files.pythonhosted.org/packages/ee/18/c1aa9662e3e2fda2ba9187abd7ddd15c214250db5ca3b5eee6e7cf6fc619/tatc-3.6.0.tar.gz).

Mixed-study outputs use these definitions:

- An event belongs to the half-open UTC study and applicability interval containing its epoch. A field-of-regard period crossing midnight does not create access on both days unless a valid event occurs on both days.
- A configuration visits a cell on a UTC day when at least one valid event occurs at that cell's center in the modeled portion of that day. Observed area is the sum of those cells' geodesic areas. It is sampled-cell area, with no claim of complete cell or pixel coverage.
- Combined daily area is the set union of visited cell IDs from included configurations, counted once per day. Retain every contributor. Adding identical visits cannot increase area.
- Per-configuration event spacing and combined event spacing use consecutive viewing epochs at the same cell. Name and define these metrics explicitly. Simultaneous combined events retain their contributor set and count as one opportunity for spacing; do not merge merely nearby times using an undocumented tolerance. Fewer than two distinct events yield a null spacing statistic.
- Mixed results do not populate continuous viewing-duration or continuous uncovered-duration metrics from field-of-regard periods. Such quantities remain unavailable with an explanation. If a later mode supplies defensible viewing intervals, union those intervals separately with their stated semantics.
- Keep legacy single-geometry interval definitions and response fields unchanged. An explicit accounting-method/version field distinguishes mixed event-based statistics from legacy interval-based statistics.

This prevents current interval aggregation from erasing pointed/conical viewing epochs or turning envelope windows into access totals. Small application-level event/day summaries are permitted where TAT-C lacks the required accounting; their documented definitions and fixture tests are mandatory. Orbit and geometry formulas remain TAT-C's responsibility.

### Completeness, applicability, and failures

Represent every requested configuration as modeled, omitted with a reason, or awaiting clarification. An unmodeled instrument has unknown access. A modeled configuration with no valid events has zero modeled events over its established interval; these states must stay distinct.

Clip calculations to established applicability intervals. Report each unresolved date interval. An explicit assumption may extend applicability only when the proposed setup records it and the researcher accepts it. Do not infer operating dates from the existence of orbit elements. Rows for partially modeled days state their modeled interval and unresolved remainder; missing periods cannot become full-day gaps.

Withhold a result described as complete whenever a requested configuration or interval remains unresolved. Offer a partial proposal naming the omissions and reasons. A partial proposal with no calculable configuration cannot produce a zero-coverage study. Calculation failures surface with the affected configuration; newly dropping a configuration requires an updated accepted setup rather than silently changing the study's scope.

Keep result-wide assumptions and per-configuration limitations in all relevant fetch responses. Source conflicts remaining after mode/date reconciliation require the researcher's recorded source choice and rationale.

### Bounds, storage, and compatibility

Keep the existing maximum study window, orbit-freshness rules, region/grid validation, and long-horizon caveats. Allow at most six spacecraft and six evaluated configurations in a mixed study initially. Count multiple configurations on one spacecraft separately for work limits. For windows longer than three days, retain the maximum of 36 cells and apply the existing 72-cell calculation bound to cells multiplied by evaluated configurations. Short runs retain the maximum of 144 cells. Disclose the resulting limits before execution.

Store resolved settings, source evidence, accepted choices, per-configuration events and summaries, and combined accounting together in the existing temporary result record. Retention remains 24 hours from saving, subject to earlier restart loss and capacity eviction. Record software version and orbit epoch with the result. Shared registry evidence lives with the repository's configuration data. This creates no durable study archive.

Maintain the current legacy geometry-assessment and unit-evidence checks, satellite search, OSCAR discovery, maps, and existing access calls. Existing clients can continue their current workflow. Mixed-study support does not silently reinterpret an existing study or switch a requested configuration to the newest revision.

## Testing decisions

Agreed primary verification boundary: invoke the MCP tools from draft creation through preparation, execution, and paged result retrieval. Use fixed source/orbit fixtures and real TAT-C calculation for the integration cases. Replace network access at the upstream boundary; do not replace the geometry calculation in tests that claim geometry integration. Focused source-mapping comparisons and tool-calling evals supplement these tests. This increment does not require browser verification in LibreChat.

Existing staged-access, fixed-orbit, daylight, UTC-day, overlap, and temporary-store tests supply the patterns to extend. Test externally observable behavior rather than reproducing internal construction code.

Required acceptance cases:

1. A bounded mixed run preserves configuration identity, source revision, accepted choices, and distinct instrument settings across the MCP summary and all fetch tools. Multiple instruments on one spacecraft are evaluated and reported separately.
2. Each starting geometry produces valid events and explicit non-events at points chosen to distinguish a conical arc or right-looking view from a nadir strip. Include both orbit directions and points inside the broad envelope but outside the actual view.
3. Compare mapped configurations with independently constructed direct calls to the pinned TAT-C public API using identical fixtures. Require identical configuration/target identities and event counts, corresponding epochs within 1 millisecond, and projected reference positions within 1 meter. These are proposed software-integration tolerances, not mission-accuracy claims. Reference fixtures must include exact source parameters and independent expected inclusion/exclusion cases; calling the same mapping helper twice is insufficient evidence.
4. For mission-data comparisons, record the exact fixture, reference document revision, parameter interpretation, measured residuals, and a justified per-case engineering tolerance before accepting the comparison. Upstream notebook plots or matching a nominal swath alone cannot pass this gate. A failed comparison leaves the configuration unavailable or explicitly limited with researcher-accepted approximation status, as appropriate.
5. Preserve both conical crossings and epoch-based UTC-day membership. Include an envelope period crossing midnight with its valid epoch on only one side. Report no continuous access duration from that envelope.
6. Use small known-event fixtures to prove per-configuration daily counts, combined cell unions, duplicate-area invariance, contributor preservation, simultaneous-event handling, null spacing with fewer than two events, and half-open end boundaries. Compare rounded area to the independently expected sum within 0.001 square kilometers, allowing one output rounding unit.
7. Test per-instrument daylight requirements together with an unfiltered radar/microwave configuration. A requirement on one instrument must not remove the other's events.
8. Test source changes, conflicting sources, changed setup revisions, immutable saved snapshots, study-only settings, unsupported modes, missing evidence, invalid numeric/unit inputs, and legacy/mixed argument conflicts. Requests with unresolved required choices must fail before calculation.
9. Test accepted partial studies, disjoint applicability intervals, partial UTC days, explicit applicability assumptions, unknown access versus zero events, all-unresolved requests, and calculation failure after setup. Do not fill unresolved intervals with zero-access rows.
10. Test the total evaluated-configuration limit and long-window cell/configuration limit, including several instruments sharing one spacecraft. Confirm bounds apply before expensive calculation.
11. Run the existing server suite under Python 3.12 with the pinned dependency, including the ground-track/map references and legacy access behavior. Verify migrated result identities preserve stable cell IDs and correct GeoJSON longitude/latitude order.
12. Update and run the real tool-calling eval when tool names, schemas, descriptions, or server instructions change, and compare with the latest baseline. Include complete, partial, approximate, conflicting-source, mode-selection, and applicability scenarios. Record the proxy model set, external-call requirement, results, and any unavailable checks. Unit tests alone do not establish that the chat models use the new workflow correctly.

## Out of scope

- NISAR, ICESat-2 ATLAS, and an exhaustive instrument registry in the first release.
- A document-search engine, downloader, persistent source archive, refresh monitor, or source-outage service inside the MCP server.
- A separate approval queue for study-specific settings or automatic promotion into shared defaults.
- Durable study storage, historical orbit reconstruction, acquisition catalog ingestion, tasking schedules, clouds, terrain occlusion, or scientific retrieval/quality validation.
- A new coverage explorer, timeline, Cesium interface, or other browser UI.
- Treating access opportunities from different measurement types as interchangeable scientific observations.
- Repository skill setup, agent-specific tracker configuration, or new triage labels.

## Further notes

This specification applies to the repository state at commit `8b734f47eb3ebf88889af5406b4a804b21788df6`. The accepted design interview is complete; implementation questions should reopen it only when a concrete conflict arises.

[Issue #20](https://github.com/code-lab-org/tatc-ai/issues/20) proposes a broader linked coverage explorer and overlaps in instrument assumptions and contributor tracking. This specification has a separate calculation and configuration-provenance scope; the researcher approved tracking it in a separate linked issue. It does not take over the explorer's browser interface or timeline work. [Closed issue #33](https://github.com/code-lab-org/tatc-ai/issues/33) records the original staged coverage implementation.

The older source-research notes remain useful for discovery but predate the merged parser fixes and the published TAT-C 3.6.0 evidence. This specification requires new integration checks; no dependency installation, numerical test, tool eval, or mission-validation run has occurred as part of drafting it.

Split the work into small, independently verifiable implementation issues and work them sequentially. Each issue should link this specification and declare any blocking issue. Contributors use ordinary GitHub issues, tests, and pull requests; using agent skills is optional.
