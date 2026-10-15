# tools — coarse tatc tool layer

The agent does **not** generate code. It calls these pre-built tools, which wrap
the existing `tatc` library, so a natural-language request maps to a tool plus
parameters and the library does the computing. This is the "faster, easier
access to the existing framework" core of the project.

## Tools (coarse, task-level)

- `revisit_over_point` — mean revisit period (hr) over a point, single satellite
  or Walker constellation.
- `ground_track` — sensor ground-track swath polygon (WKT) over a time window.
- `global_coverage` — mean revisit per point on a global grid at a given spacing.

Each carries a JSON schema in `registry.py` for model function-calling and for
gating by `roles.Role.tools`. `registry.call(name, **args)` dispatches;
`registry.schemas()` lists the callable tools for the model.

## Grain

Coarse by design (decision: coarse-first). One call answers one task; the model
fills plain parameters (TLE, target, window, constellation) and never touches
`tatc`'s object model. A parametric question (revisit vs. 1-6 satellites) is the
agent calling one tool several times, not a new tool. Fine-grained composable
primitives that mirror the `tatc` API are a later addition for novel questions
such as the snow-study case.

## Data

TLEs and swath widths are passed in — the pinned benchmark tier, deterministic.
Resolving a satellite's TLE by name (Celestrak) belongs in a later data-sourcing
tool, the live tier.

## Validation

The three tools reproduce the eval ground truth exactly (Q1-Q5, see `../eval`):
scalars and the vector within tolerance, ground-track IoU 1.0, coverage table
exact. They are the trusted execution layer the agent orchestrates; because the
functions are pre-written and fixed, no code sandbox is required.

## Requires

`tatc` (added to the server requirements). Importing this package imports `tatc`.
