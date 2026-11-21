# Scoped eval backtest: the advertised claim-form schema

Issue #99, parent #95. The scoped eval gate ships only if it fails on the
commit before a real fix and passes on the fix. This is that record for
the advertised evidence schema regression fixed in b9e1437 ("Fix
advertised schema, mixed dispatch, and library errors"), whose parent
5537670 ("Accept source claims at the MCP boundary and harden the
library") introduced the `conical-configuration-v2` source-claims form.

Verdict: the gate can be enabled. The case fails on 5537670 and passes
on b9e1437 with the routine model.

## The regression

`prepare_conical_study` (and the pointed and mixed preparation tools)
accept a study-specific configuration in two forms: the v1 inline form,
where every evidence record is quoted in full, and the v2 claim form,
where the configuration cites excerpts of the shared source library by
`document_id@revision#excerpt_id` and the server resolves them.

At 5537670 the runtime accepted the claim form but the advertised tool
schema still required inline evidence:

- `ConicalConfiguration.evidence` was `Field(min_length=1)` with no
  default, so the JSON schema embedded in `prepare_conical_study`'s
  advertised input listed `evidence` under `required`.
- `source_claims` was advertised as an optional property, so the surface
  contradicted itself: it offered the claim form while demanding inline
  evidence. A client that follows the advertised schema cannot send a
  valid claim-form configuration.
- b9e1437 made `evidence` optional (`default_factory=list`) with a model
  validator requiring exactly one of the two forms, and the advertised
  schema stopped requiring inline evidence.

A deterministic probe (validate the tool-call arguments for a v2
claim-form configuration against the advertised
`prepare_conical_study` schema with `jsonschema`, then call the tool
in-process) records the exact failure at both commits:

```python
# From apps/mcp-server at the commit under test; the configuration is the
# claim form of data/configurations/amsr2-gcom-w1-conical-product-sector-
# 2026-10-27.1.json with the source library resolved from a fixture.
import jsonschema, asyncio
from fastmcp import Client
import src.server as server
from src import source_library
from src.conical_config import ConicalConfiguration

source_library.SOURCES_DIR = <fixture directory>
arguments = {"draft_id": "probe", "grid_rows": 2, "grid_columns": 2,
             "configuration": claim_form_configuration}
schema = next(t.inputSchema for t in asyncio.run(_list(server))
              if t.name == "prepare_conical_study")
jsonschema.validate(arguments, schema)      # before: ValidationError
asyncio.run(_call(server, "prepare_conical_study", arguments))
# before and after: is_error with "Unknown draft_id 'probe'", which means
# the configuration itself validated and only the draft lookup failed
```

Results at both commits:

| probe | 5537670 (before) | b9e1437 (after) |
| --- | --- | --- |
| advertised schema accepts the v2 claim-form arguments | rejected: `configuration: 'evidence' is a required property` | accepted |
| `required` list of the advertised configuration schema | includes `evidence` | excludes `evidence` |
| runtime `ConicalConfiguration.model_validate` of the claim form | accepted | accepted |
| `prepare_conical_study` tool call with the claim form | passes validation; fails only on the deliberately missing draft ID | same |

The before commit's unit suite passes (18 source-library tests) because
those tests validate the Python model, not the advertised surface. The
advertised schema is what models see, and it is what b9e1437 fixed.

## The case

`conical_amsr2_claims` (new, `eval/cases.py`) asks the model to prepare
a conical AMSR2 study from a `conical-configuration-v2` configuration
that cites the committed fixture library
`eval/fixtures/source_library/` instead of carrying inline evidence.
The five fixture documents are derived from the reviewed registry
configuration `amsr2-gcom-w1-conical-product-sector-2026-10-27.1.json`:
evidence items that share one source identity (authority, title, URL,
document revision, retrieval date) become one document with ordered
excerpts, so resolving the claims yields the same evidence records the
inline form carries and nothing else about the configuration differs
from `conical_amsr2_complete`. `eval/cases.py` derives the claims from
the registry record at build time and fails loudly if the fixture no
longer matches. The runner points `src.source_library.SOURCES_DIR` at
the fixture directory in `Harness.__init__`, the same place it pins the
eval clock; the production `data/sources` library stays empty and
untouched, and nothing under `data/` changes in this PR.

The checker requires one successful `prepare_conical_study` where:

- `claim_form_sent`: the sent configuration kept
  `schema_version: conical-configuration-v2` with `source_claims` and no
  inline `evidence`;
- `advertised_schema_ok`: the sent configuration omits nothing the
  advertised schema requires. The required set is read from the
  checkout's own model, the same schema FastMCP embeds in the tool
  input, so the check follows the code under test without a second
  schema copy;
- `claims_resolved`: the proposal carries the fixture-resolved evidence
  records (the prompt never states the authorities or locators, so a
  proposal that echoes unresolved claims cannot pass);
- `schema_normalized`: the proposal is normalized back to v1;
- `prepared` and `reported`: a prepare happened and the reply names the
  configuration.

The case does not accept or run the study, so it needs no TAT-C
calculation and no access results. The existing 24 cases are unchanged.

## Why the case can fail before the fix

The runtime accepted the claim form at 5537670, so a checker that only
reads server responses cannot discriminate: the first before-run
(deepseek-v4-1-flash, 20261121T115716Z-backtest-before-r2-5537670)
passed every response-level check because the routine model sent the
claim form without consulting the advertised `required` list. The
regression is real but lives in the advertised surface. The check
`advertised_schema_ok` scores exactly that surface: it validates the
arguments the model actually sent against the required properties the
checkout advertises. At 5537670 a claim-form configuration is always
missing the required `evidence` property, so the check fails whatever
the model does; at b9e1437 it passes.

## Mapping

`TOOL_CASES` in `eval/scoped_cases.py` now maps a tool name to one case
ID, a tuple of case IDs, or a `NoCase` gap. `prepare_conical_study`
maps to `("conical_amsr2_claims", "conical_amsr2_complete")`: a change
to that tool's advertised schema or description selects the claim-form
case alongside the complete-flow case. `select_cases` de-duplicates and
keeps the cap of three.

Diffing the model-facing snapshot of 5537670 against the current one
selects `conical_amsr2_failed_refresh`, `conical_amsr2_claims`, and
`conical_amsr2_complete` (b9e1437 also changed the pointed tools' schema
and the refresh tool's error text, so the mixed and pointed cases also
light up on that real diff; the cap holds the run at three).

## Commands

Both commits were checked out in temporary worktrees, given the same
eval cases and fixture plus the one-line harness pin, and run with the
repository's Python 3.12 environment and the recorded HTTP cache:

```bash
git worktree add /tmp/opencode/bt-before 5537670
git worktree add /tmp/opencode/bt-after  b9e1437
# copy eval/cases.py, eval/fixtures/source_library/ into both worktrees
# and point src.source_library.SOURCES_DIR at the fixture in each
# Harness.__init__ (the pin the PR adds to eval/run_eval.py)

cd /tmp/opencode/bt-before/apps/mcp-server
EVAL_ENV_FILE=../../apps/librechat/.env timeout -k 60 3000 \
  .venv312/bin/python -m eval.run_eval \
  --cases conical_amsr2_claims --offline --workers 1 --save-traces \
  --request-timeout 600 --conversation-timeout 1800 \
  --label backtest-before-5537670

# repeat from /tmp/opencode/bt-after/apps/mcp-server with
# --label backtest-after-b9e1437
```

`EVAL_ENV_FILE` pointed the runner at the local `apps/librechat/.env`
for the Voyager key; the key was never printed or committed. Both runs
used the default routine model `deepseek-v4-1-flash`, one repeat, and
the recorded CelesTrak/OSCAR cache (`--offline`). The case conversation
peaks at a roughly 15 KB tool-call argument, and three attempts hit
provider read timeouts on that turn at the default 180-second request
budget before the completed runs above; the gate's one retry reserve
absorbs such failures as unmeasured rather than failed.

## Results

Deterministic probe (no model):

- 5537670: advertised schema rejects the claim-form arguments
  (`'evidence' is a required property`); runtime model and tool
  boundary accept them. Unit suite: 18 passed.
- b9e1437: advertised schema accepts the claim-form arguments; runtime
  model and tool boundary accept them. Unit suite: 24 passed.

Scoped eval, case `conical_amsr2_claims`, model `deepseek-v4-1-flash`:

- Before (5537670), run `20261121T131413Z-backtest-before-final-5537670`,
  307 seconds wall clock, 1 conversation, 4 turns: `status: ok`,
  `passed: false`. Failed check: `advertised_schema_ok`. The model sent
  the exact v2 claim form (11 `source_claims`, no inline `evidence`),
  the server accepted it and resolved the fixture citations, and the
  conversation completed - the failure is precisely the advertised
  schema, not model behavior. A first attempt
  (`20261121T114644Z-backtest-before-5537670`, default timeouts) hit a
  provider read timeout on turn 3 and is recorded as an infrastructure
  retry, not a result.
- After (b9e1437), run `20261121T140325Z-backtest-after-r3-b9e1437`,
  266 seconds wall clock, 1 conversation, 4 turns: `status: ok`,
  `passed: true`, all six checks green, same claim form sent and
  resolved. Two earlier after attempts
  (`20261121T132555Z` and `20261121T134843Z`) hit the same provider
  read timeout on turn 3 and were retried; they are infrastructure
  outcomes, not checker results.

Raw traces stay local in the worktrees' `eval/results/` directories
(git-ignored); the JSONL files are ~25 KB each with prompts.

## Gate decision

The selected case fails on the commit before the fix and passes on the
fix, so the scoped eval gate may be enabled for the surface it covers.
Two caveats:

- The gate catches the advertised-schema regression only when a changed
  tool maps to the new case. `prepare_pointed_study` maps to
  `pointed_s1c_iw_complete`, which still uses inline evidence; a
  pointed-configuration-only schema regression of the same kind would
  need its own claim-form case. The mixed tool has the same exposure.
- The eval runs one repeat with one routine model. Three of five runs
  timed out at the default 180-second request budget on the turn that
  emits the large configuration; a green scoped run is one measured
  pass, not a proof the model always complies.

## What this change does not do

- It does not enable the CI gate; issue #98's workflow owns that.
- It does not change the production `data/sources` library, the registry
  configurations, or any server code.
- It does not add pointed or mixed claim-form cases; the mapping keeps
  their existing entries.
