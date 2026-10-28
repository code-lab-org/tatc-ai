# Study fixtures

`amsr2_conical_configuration.json` is one study-specific, sourced AMSR2
configuration used by the conical-study tests and documentation. It is not a
shared registry entry, and accepting it in a study does not promote it.

The configuration carries the exact normalized geometry, source evidence
locators and excerpts, source revision, source conflicts and chosen rationale,
reported geometry checks, applicability interval, approximation status, and
approximations. The server validates the structure and values but does not
authenticate the source text or turn this file into a mission default.

The fixed independent TAT-C reference and mission-residual record live in
`apps/mcp-server/tests/fixtures/amsr2_conical_reference.json`. The compact
configuration used in the tool-calling eval lives in
`apps/mcp-server/eval/fixtures/amsr2_conical_prompt_config.json`.
