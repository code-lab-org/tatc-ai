# Scoped eval backtest: the advertised claim-form schema

Issue #99, parent #95. A new gate check ships only if a case the scoped
gate selects fails on the commit before a real fix and passes on the
fix. This is that record for the advertised evidence schema regression
fixed in b9e1437 ("Fix advertised schema, mixed dispatch, and library
errors"), whose parent 5537670 ("Accept source claims at the MCP
boundary and harden the library") introduced the
`conical-configuration-v2` source-claims form.

Verdict: the gate can be enabled for this regression. The scoped
mapping sends `prepare_conical_study` changes to `conical_amsr2_claims`,
and that case fails on 5537670 and passes on b9e1437 with the routine
model, inside the gate's 120-second request, 240-second conversation,
and 300-second first-attempt limits.

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
# From apps/mcp-server at the commit under test; the configuration is
# the claim-form case configuration from eval/cases.py, with the source
# library resolved from eval/fixtures/source_library/.
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

Results at both commits, with the compact case configuration:

| probe | 5537670 (before) | b9e1437 (after) |
| --- | --- | --- |
| advertised schema accepts the v2 claim-form arguments | rejected: `configuration: 'evidence' is a required property` | accepted |
| `required` list of the advertised configuration schema | includes `evidence` | excludes `evidence` |
| runtime `ConicalConfiguration.model_validate` of the claim form | accepted | accepted |
| `prepare_conical_study` tool call with the claim form | passes validation; fails only on the deliberately missing draft ID | same |

The before commit's unit suite passes (18 source-library tests) because
those tests validate the Python model, not the advertised surface. The
advertised schema is what models see, and it is what b9e1437 fixed. The
schema contract test b9e1437 added
(`test_advertised_schema_accepts_both_forms`, two parametrized cases for
the conical and pointed models) is the offline check that fails on
5537670 and passes on b9e1437; the scoped gate's coverage for this
regression rests on that test plus the mapped model case below.

## The case

`conical_amsr2_claims` (in `eval/cases.py`) asks the model to prepare a
conical AMSR2 study from a compact `conical-configuration-v2`
configuration that cites one synthetic fixture document
(`eval/fixtures/source_library/eval-amsr2-claims-source.json`) as
`source_claims` instead of inline evidence. The first version of this
case derived a claim form from the full 15 KB registry record; its
prompt was 15.2 KB, the configuration turn was the largest request in
the conversation, and scored runs needed 252 to 307 seconds - over the
scoped gate's budget. The compact configuration reuses the registry
record's AMSR2 geometry values with one excerpt, no conflicts, and no
approximations, which brings the prompt to 3.2 KB and the conversation
inside the gate limits while still driving the same
`prepare_conical_study` claim-resolution path. It is an evaluation
fixture, not a reviewed registry entry; the runner points
`src.source_library.SOURCES_DIR` at the fixture directory in
`Harness.__init__`, the same place it pins the eval clock, and the
production `data/sources` library stays empty.

The checker requires one successful `prepare_conical_study` where:

- `advertised_schema_ok`: the sent arguments validate against the tool's
  advertised input schema, read from the checkout's own server through
  the in-process FastMCP client - the same surface the model sees. At
  5537670 the claim form always violates the required `evidence`
  property, so this check fails whatever the model does.
- `configuration_unchanged`: the sent configuration equals the prompted
  claim-form configuration exactly, so a model cannot pass by rounding
  a geometry value or by converting the claims to inline evidence (the
  form the before commit advertised).
- `claims_resolved`: the proposal carries the fixture-resolved evidence
  record (the prompt never states the authority, locator, or excerpt,
  so a proposal that echoes unresolved claims cannot pass);
- `schema_normalized`: the proposal is normalized back to v1;
- `prepared` and `reported`: a prepare happened and the reply names the
  configuration.

The case does not accept or run the study, so it needs no TAT-C
event calculation and no access results. The full suite is now 25
cases; the existing 24 are unchanged.

This check is version-relative by design: rescoring a saved before-commit
trace on the current checkout revalidates against the current schema and
passes. The discrimination is only observed when the case runs against
the commit under test, which is how the scoped gate runs it (at the
pull-request checkout).

## Scoped-gate selection

`prepare_conical_study` maps to `conical_amsr2_claims`;
`finish_conical_study` stays on `conical_amsr2_complete`. Diffing the
model-facing snapshot of 5537670 against this branch selects
`conical_amsr2_failed_refresh`, `conical_amsr2_claims`, and
`pointed_s1c_iw_complete` - b9e1437 also changed the pointed tools'
schema and the refresh tool's error text, so those cases light up too.
The recorded backtest runs `conical_amsr2_claims` alone with `--cases`
to isolate the regression and stay inside one gate attempt.

## Commands

Both commits were checked out in temporary worktrees. Each worktree got
the current `eval/cases.py`, `eval/fixtures/source_library/`, and the
one-line harness pin in `eval/run_eval.py` (pointing
`src.source_library.SOURCES_DIR` at the fixture), so the only difference
between the two runs was the commit under test:

```bash
cd /home/satyal/tatc-ai
git worktree add /tmp/opencode/bt-before 5537670
git worktree add /tmp/opencode/bt-after  b9e1437
for wt in /tmp/opencode/bt-before /tmp/opencode/bt-after; do
  cp apps/mcp-server/eval/cases.py $wt/apps/mcp-server/eval/cases.py
  cp -r apps/mcp-server/eval/fixtures/source_library \
        $wt/apps/mcp-server/eval/fixtures/source_library
  # apply the Harness.__init__ pin from this PR to
  # $wt/apps/mcp-server/eval/run_eval.py
done

cd /tmp/opencode/bt-before/apps/mcp-server
EVAL_ENV_FILE=/home/satyal/tatc-ai/apps/librechat/.env \
  timeout -k 60 300 /home/satyal/tatc-ai/apps/mcp-server/.venv312/bin/python \
  -m eval.run_eval --cases conical_amsr2_claims --offline --workers 1 \
  --save-traces --request-timeout 120 --conversation-timeout 240 \
  --label backtest-before-gatelimits-5537670

cd /tmp/opencode/bt-after/apps/mcp-server
EVAL_ENV_FILE=/home/satyal/tatc-ai/apps/librechat/.env \
  timeout -k 60 300 /home/satyal/tatc-ai/apps/mcp-server/.venv312/bin/python \
  -m eval.run_eval --cases conical_amsr2_claims --offline --workers 1 \
  --save-traces --request-timeout 120 --conversation-timeout 240 \
  --label backtest-after-gatelimits-b9e1437
```

These are the scoped gate's limits: a 120-second request budget, a
240-second conversation budget, and a hard 300-second first-attempt
wall clock (`eval/ci.py`). `EVAL_ENV_FILE` pointed the runner at the
local `apps/librechat/.env` for the Voyager key; the key was never
printed or committed. Both runs used the default routine model
`deepseek-v4-1-flash`, one repeat, and the recorded CelesTrak/OSCAR
cache (`--offline`).

## Results

Deterministic probe (no model), with the compact case configuration:

- 5537670: advertised schema rejects the claim-form arguments
  (`'evidence' is a required property`); runtime model and tool
  boundary accept them.
- b9e1437: advertised schema accepts the claim-form arguments; runtime
  model and tool boundary accept them.

Model runs with the compact case at the gate's limits, model
`deepseek-v4-1-flash`, one repeat:

- Before (5537670),
  `eval/results/20261121T201652Z-backtest-before-gatelimits-5537670.jsonl`:
  173.8 seconds wall clock, 3 tool calls, `status: ok`, `passed: false`.
  Failed check: `advertised_schema_ok`. The model sent the exact v2
  claim form (one `source_claims` entry, no inline `evidence`), the
  before-commit server accepted it and resolved the fixture citation,
  and the conversation completed - the failure is precisely the
  advertised schema, not model behavior. `eval.ci classify` on this file
  returns `checker_failure` (exit 1), the blocking verdict.
- After (b9e1437),
  `eval/results/20261121T202130Z-backtest-after-gatelimits-b9e1437.jsonl`:
  109.0 seconds wall clock, 3 tool calls, `status: ok`, `passed: true`,
  all checks green, same claim form sent and resolved.
  `eval.ci classify` returns `passed` (exit 0).

Both runs fit the gate budget with room for the retry (173.8 s and
109.0 s against a 300-second first attempt and a 480-second gate budget
at `WORKERS=3`, where the three selected cases run concurrently).

An earlier, wider configuration also backtested successfully but did not
fit the gate limits. Those runs used a 15.2 KB prompt derived from the
registry record. The saved rows report 175.4, 291.5, and 252.3 seconds
when they completed; the 175.4-second row used the earlier checker. They
timed out three times in six attempts: once at the default 180-second
request budget on the before commit, and twice on the after commit (once
at the default budget, once at 300 seconds). One before attempt with the
earlier checker (which lacked `advertised_schema_ok` and
`configuration_unchanged`) scored a pass that the current checker would
not score the same way; that is why the checker now scores the
advertised schema and the exact sent configuration, and why the gate-limit
runs above supersede those attempts. The earlier traces were recorded at
their commit with the checker as it stood; they are not rescored here.

## Gate decision

The scoped gate can be enabled for this regression. The mapping sends
`prepare_conical_study` changes to `conical_amsr2_claims`, the case
discriminates the two commits, and one scored conversation fits the
gate's 300-second first-attempt budget with 173.8 s (before) and 109.0 s
(after). The offline contract test
`test_advertised_schema_accepts_both_forms` is the deterministic backstop
that fails on 5537670 without a model call. Residual risks:

- The gate runs at most three cases; on this real diff it also selects
  `conical_amsr2_failed_refresh` and `pointed_s1c_iw_complete` for the
  pointed-tool and refresh-text changes in b9e1437. The recorded
  backtest isolates `conical_amsr2_claims`; the full three-case
  selection runs concurrently under the gate budget.
- A green scoped run is one measured pass with one routine model, not a
  proof the model always complies. Provider read timeouts stay possible
  and are classified as unmeasured infrastructure, not failures.

## What this change does not do

- It does not enable the CI gate; issue #98's workflow owns that.
- It does not change the production `data/sources` library, the registry
  configurations, or any server code.
- It does not add pointed or mixed claim-form cases; a pointed-only
  regression of this kind would need its own case.
