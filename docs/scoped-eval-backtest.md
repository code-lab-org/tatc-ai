# Scoped eval backtest: the advertised claim-form schema

Issue #99, parent #95. A new gate check ships only if it fails on the
commit before a real fix and passes on the fix. This is that record for
the advertised evidence schema regression fixed in b9e1437 ("Fix
advertised schema, mixed dispatch, and library errors"), whose parent
5537670 ("Accept source claims at the MCP boundary and harden the
library") introduced the `conical-configuration-v2` source-claims form.

Verdict: the regression is real and deterministic, and the gate's
required coverage for it is the offline contract test
`test_advertised_schema_accepts_both_forms` (in
`tests/test_source_library.py`, added by b9e1437), which validates both
configuration forms against the advertised model schema and fails on
5537670. The model-level case `conical_amsr2_claims` added here is a
manual, full-eval backtest artifact, not part of the scoped eval
mapping: one conversation took 252 to 307 seconds, over the scoped
gate's 300-second first-attempt budget, so mapping it into the gate
would turn every selection into an unmeasured timeout. It runs in the
full eval and in this recorded backtest.

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
advertised schema is what models see, and it is what b9e1437 fixed. The
schema contract test b9e1437 added
(`test_advertised_schema_accepts_both_forms`, 2 parametrized cases for
the conical and pointed models) is the check that fails on 5537670 and
passes on b9e1437; it is offline, deterministic, and runs in the normal
`pytest` suite, which is why the scoped gate can rely on it instead of a
model conversation.

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

- `advertised_schema_ok`: the sent arguments validate against the tool's
  advertised input schema, read from the checkout's own server through
  the in-process FastMCP client - the same surface the model sees. At
  5537670 the claim form always violates the required `evidence`
  property, so this check fails whatever the model does.
- `configuration_unchanged`: the sent configuration equals the prompted
  claim-form configuration exactly, so a model cannot pass by
  converting the claims to inline evidence (the form the before commit
  advertised) or by changing a geometry value.
- `claims_resolved`: the proposal carries the fixture-resolved evidence
  records (the prompt never states the authorities or locators, so a
  proposal that echoes unresolved claims cannot pass);
- `schema_normalized`: the proposal is normalized back to v1;
- `prepared` and `reported`: a prepare happened and the reply names the
  configuration.

The case does not accept or run the study, so it needs no TAT-C
calculation and no access results. The full suite is now 25 cases; the
existing 24 are unchanged.

## Why the case can fail before the fix

The runtime accepted the claim form at 5537670, so a checker that only
reads server responses cannot discriminate. The check
`advertised_schema_ok` scores the advertised surface: it validates the
arguments the model actually sent against the input schema the checkout
under test advertises. At 5537670 a claim-form configuration is always
missing the required `evidence` property, so the check fails whatever
the model does; at b9e1437 it passes.

This also means the check is version-relative by design: rescoring a
saved before-commit trace on the current checkout revalidates against
the current schema and passes. The discrimination is only observed when
the case runs against the commit under test, which is how the scoped
gate would run it (at the pull-request checkout). The unit test
`test_claims_case_requires_the_claim_form_and_resolved_evidence` pins
the schema-dependent behavior offline by patching the advertised schema
to the before-commit form.

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
  timeout -k 60 2400 /home/satyal/tatc-ai/apps/mcp-server/.venv312/bin/python \
  -m eval.run_eval --cases conical_amsr2_claims --offline --workers 1 \
  --save-traces --request-timeout 300 --conversation-timeout 900 \
  --label backtest-before-final-5537670

cd /tmp/opencode/bt-after/apps/mcp-server
EVAL_ENV_FILE=/home/satyal/tatc-ai/apps/librechat/.env \
  timeout -k 60 3000 /home/satyal/tatc-ai/apps/mcp-server/.venv312/bin/python \
  -m eval.run_eval --cases conical_amsr2_claims --offline --workers 1 \
  --save-traces --request-timeout 600 --conversation-timeout 1800 \
  --label backtest-after-r3-b9e1437
```

`EVAL_ENV_FILE` pointed the runner at the local `apps/librechat/.env`
for the Voyager key; the key was never printed or committed. Both runs
used the default routine model `deepseek-v4-1-flash`, one repeat, and
the recorded CelesTrak/OSCAR cache (`--offline`).

## Results

Deterministic probe (no model):

- 5537670: advertised schema rejects the claim-form arguments
  (`'evidence' is a required property`); runtime model and tool
  boundary accept them. Unit suite: 18 passed.
- b9e1437: advertised schema accepts the claim-form arguments; runtime
  model and tool boundary accept them. Unit suite: 24 passed.

Model runs, case `conical_amsr2_claims`, model `deepseek-v4-1-flash`,
one repeat, scored rows:

- Before (5537670), run
  `eval/results/20261121T131413Z-backtest-before-final-5537670.jsonl`,
  307 seconds wall clock, 1 conversation, 4 turns: `status: ok`,
  `passed: false`. Failed check: `advertised_schema_ok`. The model sent
  the exact v2 claim form (11 `source_claims`, no inline `evidence`,
  arguments otherwise unchanged), the server accepted it and resolved
  the fixture citations, and the conversation completed - the failure is
  precisely the advertised schema, not model behavior.
- After (b9e1437), run
  `eval/results/20261121T140325Z-backtest-after-r3-b9e1437.jsonl`,
  266 seconds wall clock, 1 conversation, 4 turns: `status: ok`,
  `passed: true`, all checks green, same claim form sent and resolved.

Both scored traces were rescored with the final checker (the
`advertised_schema_ok` check now validates the full sent arguments
against the advertised schema and `configuration_unchanged` requires
equality with the prompted configuration); both pass at the current
checkout, for the version-relativity reason above. The before run's
recorded failure was produced with the case running against the before
commit's server, which is how a scoped gate at a pull-request checkout
would encounter it.

Four attempts hit provider read timeouts on the turn that emits the
roughly 15 KB configuration argument: one before attempt at the default
180-second request budget, and two after attempts at 300 seconds. The
scored runs completed at 300 seconds (before) and 600 seconds (after).
The conversation needs a request budget above roughly 300 seconds; the
scoped gate's 120-second request and 240-second conversation limits
would not have completed either scored run.

Raw traces stay in the local worktrees' `eval/results/` directories and
copies in `apps/mcp-server/eval/results/` (git-ignored); the JSONL files
are ~25 KB each with prompts.

## Gate decision

The scoped gate's coverage for this regression is the offline contract
test `test_advertised_schema_accepts_both_forms`, which fails on
5537670 and passes on b9e1437 and runs in the normal `pytest` job. The
scoped eval mapping is unchanged: `prepare_conical_study` selects
`conical_amsr2_complete` only, because the claims case is too slow for
the gate's 300-second first-attempt budget (one scored conversation took
252 to 307 seconds). A schema change to `prepare_conical_study` still
selects `conical_amsr2_complete`, which exercises the inline-evidence
form end to end; the claim form is covered by the contract test and by
this manual, full-eval backtest. Two residual risks:

- The scoped gate does not catch a model-facing regression that only
  affects the claim-form path; the contract test covers the advertised
  schema deterministically, but a model-behavior regression on the
  claim form would only surface in a full eval run.
- The model case needs a request budget above the scoped gate's limits.
  Three of five attempts timed out at 180 seconds on the configuration
  turn; a green run is one measured pass, not a proof the model always
  complies.

## What this change does not do

- It does not enable the CI gate; issue #98's workflow owns that.
- It does not change the scoped-eval mapping, the production
  `data/sources` library, the registry configurations, or any server
  code.
- It does not add pointed or mixed claim-form cases.
