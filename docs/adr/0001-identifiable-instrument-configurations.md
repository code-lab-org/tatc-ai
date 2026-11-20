---
status: accepted
date: 2026-10-26
---

# Preserve instrument configurations and study scope

Mixed-instrument studies need separately identifiable configurations for different operating modes and source revisions. Each study references the configuration it used, including its applicable period, geometry assumptions, and source revision. Check for source-document changes when first adding a configuration and when explicitly refreshing it. Record detected documentation changes explicitly while preserving the distinction between revisions. If updated documentation changes the geometry, explain the change and its applicability, and obtain the researcher's explicit choice of revision for a new study. Earlier studies retain their original settings and source revision; a documentation refresh never silently rewrites them.

A researcher may use a study-specific configuration absent from the registry. Retain its evidence and limitations, and require explicit acceptance of approximations. Acceptance for one study does not automatically make the configuration a registry default. This permits exploratory work without treating each accepted proposal as a shared default.

The calling chat assistant finds and interprets source documents using its available tools, or asks the researcher for the relevant evidence when it cannot obtain it. The MCP server uses saved registry configurations or accepts sourced settings and evidence supplied by that assistant, checks the inputs, and runs the calculation. General document search and retrieval are outside the MCP server's first-version scope. The starting configurations can be used without new document research, subject to their stated applicability and any known source-change notices.

For the first version, study-specific configurations do not enter a separate review queue. A maintainer reviews a configuration through the normal repository pull request process only when deliberately adding it as a shared default. Routine studies do not require maintainer review, and registry promotion is optional.

Keep evidence obtained during study setup with the instrument settings in the existing temporary result record. That record expires 24 hours after saving and can disappear sooner on server restart or capacity eviction. Keep source evidence for shared defaults in the repository alongside their registry entries. This is temporary provenance retention, not a durable source archive. Durable study storage remains a later phase; a document downloader, archive, refresh service, and source-outage fallback mechanism are outside the first version. The source-change checks described above happen during configuration preparation or explicit refresh without requiring a dedicated monitoring service.

If an instrument lacks enough evidence to model its access, withhold any result described as covering the full requested set. Offer a clearly labeled partial study that names the omitted instrument and reason. Never count an unmodeled instrument as having zero access.

If applicable mission sources still conflict after accounting for operating mode and date, the assistant presents both sources and its proposed choice. Obtain the researcher's agreement before running, and retain the disagreement and choice rationale with the configuration.

An envelope containing gaps between viewing footprints may be useful for exploration, but it stays separate from totals described as instrument access. Accepting an approximation does not make envelope area interchangeable with instrument access.

Present required researcher choices together in one proposed study setup, including operating mode, source conflicts, approximations, and omitted instruments where relevant. After the researcher accepts the setup, proceed without repeating confirmation unless something material changes.

If a researcher names an instrument without specifying an operating mode and the choice affects the calculation, the assistant proposes a mode and explains its implications in that study-setup confirmation. Do not silently select a consequential mode.

Each instrument's result states which checks were performed on its modeled geometry, which simplifications and limitations remain, and whether access could be calculated. Report geometry checks and approximations separately because a simplified model can pass its implementation checks. Scientific validation is a separate claim.

Apply settings only within their established applicability period. If a requested study extends beyond that period, calculate access for the established period and identify the remaining interval as unresolved. State the affected instrument and unresolved dates clearly in the researcher-facing response. Extending the settings across that interval requires an explicit assumption accepted in the study setup.

Start with AMSR2 on GCOM-W1 as a conical-scan case and Sentinel-1 C-SAR in IW mode as a side-looking radar case. This exercises two different viewing geometries before expanding to NISAR and ICESat-2 ATLAS. TAT-C 3.6.0 supplies ConicalInstrument and PointedInstrument in its [published source archive](https://files.pythonhosted.org/packages/ee/18/c1aa9662e3e2fda2ba9187abd7ddd15c214250db5ca3b5eee6e7cf6fc619/tatc-3.6.0.tar.gz), making these candidates feasible without relying on unreleased APIs. Upstream [AMSR2](https://tatc.readthedocs.io/en/latest/validation/ValidateConicalAMSR2.html) and [Sentinel-1](https://tatc.readthedocs.io/en/latest/validation/ValidateSARSentinel1.html) examples establish feasibility, not correctness of the MCP integration or scientific validation. The runtime dependency must include those APIs; the dependency change and regression checks remain implementation work.
