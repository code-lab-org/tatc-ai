# Tool-calling evaluation

The eval package checks how well ASU Voyager models use the MCP tools. It gives each model the server's tools and instructions, runs tool calls against the server with recorded CelesTrak and OSCAR responses, and scores the server's results.

## Verification tiers

There are three tiers:

- **Test**: `python -m pytest apps/mcp-server/tests`, plus the snapshot test that compares the model-facing surface with `eval/fixtures/model_surface.json`. The `test` job reports on every pull request; its steps run when the pull request changes the MCP server, the LibreChat config, or the test workflow. Deterministic, no model in the loop.
- **Scoped eval**: the CI gate maps each changed tool to its eval case through `eval/scoped_cases.py` and runs at most three cases with the recorded HTTP cache. The `scoped-eval` job reports on every pull request; it runs only when the snapshot changes and only for same-repository pull requests.
- **Full eval**: all 25 cases. It runs on demand with the commands below and never on pull requests. When the project starts cutting releases, the full eval becomes the release gate.

The `test` and `scoped-eval` jobs are the checks to require on `main`. Branch protection is not enabled yet; an administrator must apply the settings in [issue #106](https://github.com/code-lab-org/tatc-ai/issues/106). Until then, a red check does not block a merge.

A checker failure, including a checker that raises, blocks. A provider or harness error retries once while the job budget allows; if less than 60 seconds remain or the retry also fails, the run is recorded as unmeasured and does not block. A timeout that stops before any row is written is retried. A run that exits without writing result rows is a harness failure and blocks, because the harness or its setup is broken. When every touched tool has no covering case, the gate reports the gaps and exits green; a reviewer decides whether to add a case. A fork pull request gets a green `scoped-eval` check without running the eval, and a maintainer runs the scoped eval before merge. The [Scoped eval in CI](#scoped-eval-in-ci) section has the commands and the full verdict list.

## Run the eval

Run these commands from the repository root. The routine model is `deepseek-v4-1-flash` only. It is checked against the LibreChat ASU Voyager catalog before any request; a missing catalog entry fails loudly instead of falling back to another model. Pass `--models` with explicit model IDs to send them as given, or `--all-models` to run the catalog set. Only the routine default is catalog-validated.

```bash
cd apps/mcp-server
timeout -k 10 900 .venv312/bin/python -m eval.run_eval --label routine
timeout -k 10 1800 .venv312/bin/python -m eval.run_eval --label affected --repeats 3 --cases conical_amsr2_complete pointed_s1c_iw_complete point_study_explicit
timeout -k 10 900 .venv312/bin/python -m eval.run_eval --label second-model --models glm-5-3 --cases conical_amsr2_complete pointed_s1c_iw_complete
timeout -k 10 1800 .venv312/bin/python -m eval.run_eval --label issue52-mixed-coverage --workers 4 --repeats 1
OPENCODE_GO_API_KEY=... timeout -k 10 900 .venv312/bin/python -m eval.run_eval --provider opencode-go --models deepseek-v4.1-flash --reasoning-effort max --label opencode-go
.venv312/bin/python -m eval.run_eval --compare /path/to/baseline.jsonl /path/to/current-run.jsonl
```

Repeats default to 1 for a whole suite. Use repeat 3 for the cases affected by a change when the budget allows. Occasional second-model checks and an occasional `--all-models` run are manual, not routine. Every batch command keeps the `timeout -k` wrapper as its hard external deadline; keep a local job log with the owner, PID, start time, deadline, and checkpoint file.

The eval reads the proxy key and base URL from the process environment first (`VOYAGER_API_KEY`, `VOYAGER_BASE_URL`), then from `apps/librechat/.env`, and sends prompts and tool schemas to the ASU proxy. `VOYAGER_API_KEY` is the same key the scoped-eval workflow uses, but a local run uses your own key. To reproduce the CI selection and failure policy, use `python -m eval.ci gate` as shown under Scoped eval in CI. One request is limited to 180 seconds and one complete conversation to 600 seconds. Override these with `--request-timeout` and `--conversation-timeout`. The harness retries at most once for a connection failure, 429, 502, or 503. It does not retry a read timeout, context overflow, or another client error. It bounds trickle reads and the wait for a tool call; a tool already running in the server may finish after the caller stops waiting. The external `timeout -k` covers the whole batch. A provider failure is recorded as unmeasured infrastructure failure, not a score; do not tune prompts or checkers to force a pass.

The run writes a timestamped JSONL file under `eval/results/` and prints its summary. The default file keeps scores, counts, and small partial failure progress, not prompts, replies, or full tool text. A companion `-requests.jsonl` file records model, case, turn, attempt, request and response bytes, duration, token usage, request ID, and error category. Pass `--save-traces` only when full traces are needed for diagnosis. `--rescore` needs saved traces, so use `--save-traces` on runs you expect to rescore. Results and request records are ignored local artifacts. Replace the two comparison paths with existing result files; `--compare` summarizes saved runs and does not start a new model evaluation.

The routine provider is ASU Voyager. Use `--provider opencode-go` to send the same OpenAI-compatible requests to OpenCode Go instead. Put `OPENCODE_GO_API_KEY` in the process environment or in the private `apps/mcp-server/.env` file; copy `apps/mcp-server/.env.example` and do not commit the key. The runner also checks `apps/librechat/.env` as a fallback. The OpenCode Go model ID is `deepseek-v4.1-flash`. Pass `--reasoning-effort max` for the Max setting. The runner sends the effort in the request body, plus `x-opencode-session` and the OpenCode user agent. A provider failure is recorded as infrastructure failure, not a model score.

Use the Python 3.12 environment that matches the server Dockerfile, for example `apps/mcp-server/.venv312` if you created one. Token usage plus total duration is all the record keeps; it cannot separate queue time, prefill, and decode time.

The coverage cases include the legacy single-geometry workflow, the conical-scanning cases below, a complete pointed Sentinel-1 IW case, and two mixed-study cases. `short_given` accepts either the legacy regional result or an equivalent named-point result, because the explorer now handles a 24-hour single-city request. `point_study_explicit` requires `analyze_coverage` with two catalog satellites and three named points. The full suite is 25 cases.

- `conical_amsr2_complete`: a study-specific sourced AMSR2 configuration with conflicting nominal/product sector choices and declared approximations. The model must prepare, accept the current revision, run, fetch viewing events, and preserve cell-center and envelope limits. It uses the study-specific fixture with its own ID and revision; the reviewed shared entry is the independent TAT-C reference target.
- `conical_amsr2_claims`: a compact `conical-configuration-v2` study configuration that cites one synthetic fixture document (`eval/fixtures/source_library/eval-amsr2-claims-source.json`) as `source_claims` instead of inline evidence. The model must send the configuration exactly as given, and the checker validates the sent arguments against the advertised `prepare_conical_study` input schema, requires the sent configuration to equal the prompted one, and requires the proposal to carry the library-resolved evidence. The fixture reuses the registry record's AMSR2 geometry values so preparation runs the same TAT-C path, but the configuration is an evaluation fixture, not a reviewed registry entry; the runner points the source library at the fixture for the run and the production `data/sources` library stays empty. The scoped gate maps `prepare_conical_study` to this case; see `docs/scoped-eval-backtest.md` for its backtest.
- `conical_amsr2_registry_selection`: the model must look up the reviewed shared configuration, leave lookup unselected, prepare with the exact configuration ID and revision the researcher chose, and retain that revision in the result.
- `conical_amsr2_failed_refresh`: the model records a reported failed source check, uses the researcher's exact prior revision, and carries source uncertainty into the result and reply.
- `conical_applicability_rejected`: a window entirely outside the established applicability interval. The model must stop before calculation and identify that no part is covered.
- `conical_applicability_partial`: a window partly outside the established interval. The model must prepare a temporal partial proposal, name the modeled and unresolved intervals, accept it only after the researcher's acceptance, run it, fetch daily rows, and report `partial` without calling unresolved time zero access.
- `conical_unresolved_sector`: a configuration with no recorded choice between the nominal and product scan sectors. The model must ask for the choice and must not prepare or run.
- `conical_missing_evidence`: a configuration missing the measured cone/sector evidence. The model must not invent evidence or substitute a nominal swath.
- `pointed_s1c_iw_complete`: the model must look up and explicitly select Sentinel-1C C-SAR IW revision `2026-10-30.1`, use `prepare_pointed_study` and `finish_pointed_study`, retrieve event, daily, cell, revisit, and per-cell event-spacing views, and carry fixed-roll, source-reference, off-nadir/incidence, cell-center, and acquisition-schedule limits into its reply.
- `pointed_registry_choices_before_acceptance`: the model must look up the shared option, present its exact spacecraft and mode plus the fixed-roll, subswath, and along-track approximations, and ask whether to proceed before any study setup or calculation.
- `mixed_amsr2_s1c_complete`: the model must look up the exact reviewed AMSR2 and Sentinel-1C revisions, stage both spacecraft, prepare and accept one mixed setup, retain each configuration's source and geometry snapshot, fetch all five combined views and all five AMSR2-scoped views, and fetch events for both exact configuration IDs. Event and spacing requests use five rows and cell requests use two features; the finish summary carries full counts.
- `mixed_shared_spacecraft_daylight`: the model must combine the reviewed AMSR2 configuration with a separate study-specific daylight sensitivity configuration on the same GCOM-W1 spacecraft. The inline configuration is passed as a JSON object inside a two-item configurations array. The model fetches combined events and daily union plus per-configuration events, preserves the distinct illumination settings, and states that daylight is a study assumption, not an AMSR2 source requirement.
- `mixed_follow_up_grid`: the model runs the mixed study once with a 2x2 grid, then changes only the grid to 3x3 and runs it again. The check requires two accepted finishes, two distinct setup revisions, and two result IDs.
- `mixed_partial_amsr2_unsupported`: the model must stage only GCOM-W1, record SeaWiFS on OrbView-2 as an explicit unsupported-instrument omission with unknown access, and prepare the reviewed AMSR2 configuration as a partial proposal. It must accept the exact omission ID, fetch combined events, daily area, and cells plus AMSR2 events, and report partial completeness, the omission reason, subset-only totals, and the usual cell-center and envelope limits.
- `mixed_partial_amsr2_self_built`: the same partial study, but the prompt does not provide the omission JSON. The model must build the omission from the `prepare_mixed_study` schema, then accept the exact scope and report partial results.
- `mixed_partial_s1c_amsr2_omitted`: stages both GCOM-W1 and Sentinel-1C with their recorded CelesTrak display names, models the reviewed Sentinel-1C revision, and builds an AMSR2 omission on the staged GCOM-W1 spacecraft. This exercises the staged-omission identity path.

The recorded Sentinel-1C GP response uses the exact CelesTrak TLE published in the TAT-C 3.6.0 validation reference (epoch 2026-10-02) re-encoded as OMM fields. This keeps the eval's recorded clock and OMM freshness checks consistent; it is a fixed test input, not a live refresh. The pointed configuration itself cites the source record and its later 2026-10-30 CelesTrak OMM snapshot.

The eval is not a mission-accuracy test. Pointed reference tests cover direct TAT-C event counts and identities, event epochs, independently propagated WGS84 subpoint separation, and test-only Skyfield side/off-nadir classifications. The mixed cases check tool use, exact selectors, saved configuration snapshots, combined and per-configuration fetches, and study-specific illumination notes. Offline integration tests separately check the event-union accounting and shared-spacecraft TAT-C path.

## Scoped eval in CI

The `Scoped eval` workflow reports on every pull request. It compares the model-facing snapshot on the pull request base commit with the checkout's `apps/mcp-server/eval/fixtures/model_surface.json`, maps the changed tools through the table in `eval/scoped_cases.py`, and runs at most three cases with the recorded HTTP cache. A pull request whose snapshot does not change runs the job with its eval steps skipped, and that green check satisfies branch protection once it is enabled.

The job needs the repository secret `VOYAGER_API_KEY` (an ASU Research Computing proxy key, the same secret the model check workflow uses) and sends requests to `https://openai.rc.asu.edu/v1`. Fork pull requests receive no repository secrets, so the job skips them with a warning and a maintainer runs the scoped eval before merge.

The gate (`python -m eval.ci gate`) classifies the result rows. A scored case that failed its checks is a checker failure and blocks the merge. A provider or harness error is an infrastructure failure, is retried once while the budget allows, and a second one or a skipped retry is recorded as unmeasured without blocking. A timeout that stops before a row is written is an infrastructure failure and gets the retry. A checker that raises is not infrastructure; the runner records it as `checker_error` and the gate blocks. A run that exits without producing any result rows is a harness failure and blocks, because the harness or its setup is broken. A changed tool with no entry in the mapping table fails the selection, and a change that maps to no case reports its coverage gaps without running anything. The gate bounds the eval to 480 seconds, reserving 180 seconds for the retry, so two attempts fit the 12-minute job with setup headroom.

Reproduce the CI gate locally from the base commit's snapshot:

```bash
git show "$BASE_SHA:apps/mcp-server/eval/fixtures/model_surface.json" > /tmp/model-surface-before.json
cd apps/mcp-server
VOYAGER_API_KEY=... .venv312/bin/python -m eval.ci gate \
  --before /tmp/model-surface-before.json --after eval/fixtures/model_surface.json
```

`python -m eval.ci select --before ... --after ...` prints only the selected cases and coverage gaps as JSON, and `python -m eval.ci classify --results ... --cases ...` classifies a saved result file. Pass `--result-out` to `eval.run_eval` to write the result file to a fixed path.

## Issue #51 review evaluation

The baseline and full issue #51 comparison each used one repeat per model and case. The baseline had 65 conversations across five models and 13 cases, with 63 scored rows and two infrastructure failures. The full issue run had 70 conversations across five models and 14 cases, with 67 scored rows and three infrastructure failures. Saved traces were rescored after correcting the region bounding-box order, removing a fixed limitation-phrase check, and adding the pointed choice checker. Rescoring made no model calls. Of 65 shared model/case keys, 61 were scored in both runs: 53/61 baseline passes and 55/61 issue-run passes. The overall rounded rates, 87% and 90%, are not a robust improvement claim because the runs have one repeat, one added case, and different infrastructure failures.

Two cases regressed by one response each: `needs_clarification` went from 4/5 to 3/5, and `long_given` went from 5/5 to 4/5. The baseline had one model timeout and one result-parsing infrastructure error. The full issue run had three timeouts: `glm-5-3-flash` on the complete conical case, and `mimo-v2-6-flash` on the failed-refresh conical and complete pointed cases.

The scoped review run requested ten conversations, five models across the two pointed cases. After rescoring, all five choice-before-acceptance cases passed; four complete-study cases passed, and Mimo timed out on the remaining complete study after 883.7 seconds. The scoped run repeated neither the baseline nor the 70-conversation issue set. Three of the five choice replies also made Sentinel-1A or Sentinel-1D status claims absent from the lookup results; the checker does not score those extra claims.

## Issue #52 comparison and mixed-study evaluation

The full issue #51 comparison baseline is `eval/results/20261030T163644Z-issue51-s1c-pointed-combined.jsonl` (70 conversations, 14 cases, five models; 67 scored and 60 passed). The full issue #52 run is `eval/results/20261030T231506Z-issue52-mixed-final.jsonl`; it requested 85 conversations across 17 cases and reached its 90-minute deadline after 83 rows. Its initial score was 76 scored, 67 passed, nine checker failures, and seven infrastructure errors. It completed four scored mixed rows, all passing; four more mixed rows were infrastructure errors and the last two Qwen mixed rows were not recorded.

The 70 shared model/case/repeat keys yielded 65 rows scored in both runs: 58 passed in the issue #51 baseline and 56 in the issue #52 run. `boundary_given` changed from 5/5 to 3/5 (GLM-5-3-Flash and Mimo failed in issue #52); `short_given` changed from 5/5 to 4/5 (GLM-5-3-Flash failed). These paired case results do not show an overall improvement. The baseline used the earlier 14-turn cap. The full issue #52 run's cap was not retained reliably: the local audit and initial report disagree on whether it was 14 or 20. The longest recorded conversations used 8 and 9 turns, respectively, below either cap. The current runner uses a 20-turn cap.

The review rerun `eval/results/20261031T014232Z-issue52-review-boundary-short-r2.jsonl` used two repeats per case across five models with the current 20-turn cap. All 20 conversations passed, including 10/10 for each case. This is a separate repeat-2 run and does not change the paired result above.

The saved scoped mixed run, `eval/results/20261031T004523Z-issue52-mixed-scoped-final.jsonl`, has been rescored with the final reporting and illumination checks. Rescoring changed two of the six scored passes to failures: both daylight replies claim source support for a `study_assumption`. Those traces predate the fixture correction that removes the claim while retaining the source excerpt as background. Four other conversations are infrastructure errors: one context-limit error and three provider timeouts. The tightened reporting check initially flagged `continuous rectangular view` in a geometry limitation; it now targets continuous viewing or coverage claims, and its regression tests pass.

The updated real mixed cases use the 24-hour window `2026-10-04T00:00:00Z` to the exclusive end `2026-10-05T00:00:00Z`, region `(-65, 0, -55, 10)`, and a 2x2 grid. The in-process TAT-C regression on that window produces five base AMSR2 events, one daylight-filtered event, one Sentinel-1C event, and six exact-epoch combined events; it also verifies a simultaneous contributor merge and nonzero radar contribution.

The changed-window review run, `eval/results/20261031T015307Z-issue52-review-mixed-24h-r1.jsonl`, completed all ten conversations across five models. After rescoring with the final checkers, all four scored replies passed and six conversations were infrastructure errors. The AMSR2/Sentinel-1C case had three scored passes and two infrastructure errors, one GLM-5-3 context-limit error and one Mimo timeout. The shared-spacecraft daylight case had one scored pass and four provider timeouts. All five stream probes passed, and no scored row failed a checker. The longest scored conversation used eight turns under the 20-turn cap.

This conversation run started before the final fetch-provenance compaction. The final tool header keeps the selected source-choice IDs and the accepted finish snapshot keeps each full rationale. Public MCP regression tests cover that final response shape; no provider rerun was made for this payload-only change. No new full 85-conversation issue #52 run is planned. The raw JSONL traces and `/tmp` logs are local-only artifacts excluded from the PR.

The complete conical, pointed, and mixed cases require cell-center and envelope limits, reject claims of confirmed acquisitions, continuous viewing, and whole-cell coverage, and accept clear negated disclaimers. The checks recognize `modeled`/`modelled` and `center`/`centre`. They are text heuristics. The rejection cases fail if either the legacy or conical workflow completes a calculation.

## Streaming check

The eval conversations are not streamed, but LibreChat streams model responses. Each run first sends every model a short streamed request and reports `stream: ok`, `missing role`, or `no tool call`. LibreChat drops tool calls from a stream whose first delta has no `role`. Models marked `disableStreaming` in `apps/librechat/librechat.yaml` are noted rather than failed.

Run only this check with:

```bash
cd apps/mcp-server
.venv312/bin/python -m eval.run_eval --stream-only
```

## Issue #54 applicability evaluation

The temporal-partial path was checked against OpenCode Go with `deepseek-v4.1-flash`, one repeat per case. The model prepared the partial proposal, named the modeled and unresolved intervals, accepted the exact setup revision, ran the study, fetched daily rows, and reported `partial` without calling unresolved time zero access. `conical_applicability_partial` passed in `eval/results/20261120T082405Z-go-partial5.jsonl`; `conical_applicability_rejected` passed in the combined `20261120T082537Z-issue54-opencode-go` run. The combined run's `mixed_amsr2_s1c_complete` row is an infrastructure failure because the recorded cache clock and the live CelesTrak OMM epoch are outside the 14-day window; no mixed conclusion should be drawn from that row. The focused unit and integration suite covers mixed partial dates separately.

The OpenCode Go key was not committed. The runner reads `OPENCODE_GO_API_KEY`, sends `x-opencode-session` and an `opencode/` user agent, and uses `https://opencode.ai/inference/go/v1`. A missing key fails before any model request. Mixed cases must use the recorded cache clock. Passing `--now` with a mixed case now fails before any request, because the recorded OMM epoch and the reviewed Sentinel-1C applicability window would otherwise fall outside the guardrails. Other cases still follow `--now` or the cache's recorded time.
