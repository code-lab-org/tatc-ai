# Instrument configurations and study fixtures

`instrument_configuration_registry.json` lists reviewed shared configurations
and their initial source-check records. Each path names one immutable ID and
revision under `configurations/`. A changed source or geometry adds a revision
through a repository change; the MCP refresh tool keeps supplied checks and
candidates in process memory only.

`configurations/amsr2-gcom-w1-conical-product-sector-2026-10-27.1.json` is the
first reviewed shared configuration. Its source evidence and reported geometry
checks are retained in the file. The independent TAT-C reference and mission
residual record are in `../tests/fixtures/amsr2_conical_reference.json`.

`amsr2_conical_configuration.json` is one study-specific, sourced AMSR2
configuration used by the conical-study tests and documentation. It has its own
configuration ID and revision (`amsr2-gcom-w1-conical-product-sector-study` at
`2026-10-27.1-study`) so it cannot be confused with the reviewed shared entry.
It is not a shared registry entry, and accepting it in a study does not promote
it.

The configuration carries the exact normalized geometry, source evidence
locators and excerpts, source revision, source conflicts and chosen rationale,
reported geometry checks, applicability interval, approximation status, and
approximations. A geometry check may declare `geometry_parameter` to bind its
reported value to that parameter; the value must match the configured geometry
within the check tolerance. Unbound checks remain independent references.
The server validates the structure and values but does not authenticate the
source text or turn this file into a mission default.

The fixed independent TAT-C reference and mission-residual record live in
`apps/mcp-server/tests/fixtures/amsr2_conical_reference.json`. The tool-calling
eval loads the study configuration above directly, so its settings and source
evidence have one maintained copy. The independent reference stays separate.
