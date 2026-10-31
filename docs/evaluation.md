# Tool-calling evaluation

The eval package checks how well ASU Voyager models use the MCP tools. It gives each model the server's tools and instructions, runs tool calls against the server with recorded CelesTrak and OSCAR responses, and scores the server's results.

## Run the eval

Run these commands from the repository root. By default, the eval runs the models LibreChat offers. Pass `--models` to try other models.

```bash
cd apps/mcp-server
.venv312/bin/python -m eval.run_eval --label issue52-mixed-coverage --workers 4 --repeats 1
.venv312/bin/python -m eval.run_eval --compare /path/to/baseline.jsonl /path/to/current-run.jsonl
```

The run writes a timestamped JSONL file under `eval/results/` and prints its summary. Replace the two comparison paths with existing result files; `--compare` summarizes saved runs and does not start a new model evaluation.

The eval reads the proxy key from `apps/librechat/.env` and sends prompts and tool schemas to the ASU proxy. A full run normally takes about half an hour but can take longer when model requests are slow or time out. It saves each completed conversation to the timestamped JSONL file. Use the Python 3.12 environment that matches the server Dockerfile, for example `apps/mcp-server/.venv312` if you created one.

The coverage cases include the legacy single-geometry workflow, conical and pointed studies, and two mixed-study cases:

- `conical_amsr2_complete`: a study-specific sourced AMSR2 configuration with conflicting nominal/product sector choices and declared approximations. The model must prepare, accept the current revision, run, fetch viewing events, and preserve cell-center and envelope limits. It uses the study-specific fixture with its own ID and revision; the reviewed shared entry is the independent TAT-C reference target.
- `conical_amsr2_registry_selection`: the model must look up the reviewed shared configuration, leave lookup unselected, prepare with the exact configuration ID and revision the researcher chose, and retain that revision in the result.
- `conical_amsr2_failed_refresh`: the model records a reported failed source check, uses the researcher's exact prior revision, and carries source uncertainty into the result and reply.
- `conical_applicability_rejected`: a window outside the established applicability interval. The model must stop before calculation and identify the uncovered dates.
- `conical_unresolved_sector`: a configuration with no recorded choice between the nominal and product scan sectors. The model must ask for the choice and must not prepare or run.
- `conical_missing_evidence`: a configuration missing the measured cone/sector evidence. The model must not invent evidence or substitute a nominal swath.
- `pointed_s1c_iw_complete`: the model must look up and explicitly select Sentinel-1C C-SAR IW revision `2026-10-30.1`, use `prepare_pointed_study` and `finish_pointed_study`, retrieve event, daily, cell, revisit, and per-cell event-spacing views, and carry fixed-roll, source-reference, off-nadir/incidence, cell-center, and acquisition-schedule limits into its reply.
- `pointed_registry_choices_before_acceptance`: the model must look up the shared option, present its exact spacecraft and mode plus the fixed-roll, subswath, and along-track approximations, and ask whether to proceed before any study setup or calculation.
- `mixed_amsr2_s1c_complete`: the model must look up the exact reviewed AMSR2 and Sentinel-1C revisions, stage both spacecraft, prepare and accept one mixed setup, retain each configuration's source and geometry snapshot, fetch all five combined views and all five AMSR2-scoped views, and fetch events for both exact configuration IDs. Event and spacing requests use five rows and cell requests use two features; the finish summary carries full counts.
- `mixed_shared_spacecraft_daylight`: the model must combine the reviewed AMSR2 configuration with a separate study-specific daylight sensitivity configuration on the same GCOM-W1 spacecraft. The inline configuration is passed as a JSON object inside a two-item configurations array. The model fetches combined events and daily union plus per-configuration events, preserves the distinct illumination settings, and states that daylight is a study assumption, not an AMSR2 source requirement.

The recorded Sentinel-1C GP response uses the exact CelesTrak TLE published in the TAT-C 3.6.0 validation reference (epoch 2026-10-02) re-encoded as OMM fields. This keeps the eval's recorded clock and OMM freshness checks consistent; it is a fixed test input, not a live refresh. The pointed configuration itself cites the source record and its later 2026-10-30 CelesTrak OMM snapshot.

The eval is not a mission-accuracy test. Pointed reference tests cover direct TAT-C event counts and identities, event epochs, independently propagated WGS84 subpoint separation, and test-only Skyfield side/off-nadir classifications. The mixed cases check tool use, exact selectors, saved configuration snapshots, combined and per-configuration fetches, and study-specific illumination notes. Offline integration tests separately check the event-union accounting and shared-spacecraft TAT-C path.

## Issue #51 review evaluation

The baseline and full issue #51 comparison each used one repeat per model and case. The baseline had 65 conversations across five models and 13 cases, with 63 scored rows and two infrastructure failures. The full issue run had 70 conversations across five models and 14 cases, with 67 scored rows and three infrastructure failures. Saved traces were rescored after correcting the region bounding-box order, removing a fixed limitation-phrase check, and adding the pointed choice checker. Rescoring made no model calls. Of 65 shared model/case keys, 61 were scored in both runs: 53/61 baseline passes and 55/61 issue-run passes. The overall rounded rates, 87% and 90%, are not a robust improvement claim because the runs have one repeat, one added case, and different infrastructure failures.

Two cases regressed by one response each: `needs_clarification` went from 4/5 to 3/5, and `long_given` went from 5/5 to 4/5. The baseline had one model timeout and one result-parsing infrastructure error. The full issue run had three timeouts: `glm-5-3-flash` on the complete conical case, and `mimo-v2-6-flash` on the failed-refresh conical and complete pointed cases.

The scoped review run requested ten conversations, five models across the two pointed cases. After rescoring, all five choice-before-acceptance cases passed; four complete-study cases passed, and Mimo timed out on the remaining complete study after 883.7 seconds. The scoped run repeated neither the baseline nor the 70-conversation issue set. Three of the five choice replies also made Sentinel-1A or Sentinel-1D status claims absent from the lookup results; the checker does not score those extra claims.

## Issue #52 comparison and mixed-study evaluation

The full issue #51 comparison baseline is `eval/results/20261030T163644Z-issue51-s1c-pointed-combined.jsonl` (70 conversations, 14 cases, five models; 67 scored and 60 passed). The full issue #52 run is `eval/results/20261030T231506Z-issue52-mixed-final.jsonl`; it requested 85 conversations across 17 cases and reached its 90-minute deadline after 83 rows. Its initial score was 76 scored, 67 passed, nine checker failures, and seven infrastructure errors. It completed four scored mixed rows, all passing; four more mixed rows were infrastructure errors and the last two Qwen mixed rows were not recorded.

The 70 shared model/case/repeat keys yielded 65 rows scored in both runs: 58 passed in the issue #51 baseline and 56 in the issue #52 run. `boundary_given` changed from 5/5 to 3/5 (GLM-5-3-Flash and Mimo failed in issue #52); `short_given` changed from 5/5 to 4/5 (GLM-5-3-Flash failed). These paired case results do not show an overall improvement. The baseline used the earlier 14-turn cap and issue #52 used the current 20-turn cap; the longest recorded conversations used 8 and 9 turns, respectively, so neither saved run approached its cap.

The review rerun `eval/results/20261031T014232Z-issue52-review-boundary-short-r2.jsonl` used two repeats per case across five models with the current 20-turn cap. All 20 conversations passed, including 10/10 for each case. This is a separate repeat-2 run and does not change the paired result above.

The saved scoped mixed run, `eval/results/20261031T004523Z-issue52-mixed-scoped-final.jsonl`, has been rescored with the final reporting and illumination checks. Rescoring changed two of the six scored passes to failures: both daylight replies claim source support for a `study_assumption`. Those traces predate the fixture correction that removes the claim while retaining the source excerpt as background. Four other conversations are infrastructure errors: one context-limit error and three provider timeouts. The tightened reporting check initially flagged `continuous rectangular view` in a geometry limitation; it now targets continuous viewing or coverage claims, and its regression tests pass.

The updated real mixed cases use the 24-hour window `2026-10-04T00:00:00Z` to the exclusive end `2026-10-05T00:00:00Z`, region `(-65, 0, -55, 10)`, and a 2x2 grid. The in-process TAT-C regression on that window produces five base AMSR2 events, one daylight-filtered event, one Sentinel-1C event, and six exact-epoch combined events; it also verifies a simultaneous contributor merge and nonzero radar contribution.

The changed-window review run, `eval/results/20261031T015307Z-issue52-review-mixed-24h-r1.jsonl`, completed all ten conversations across five models. After rescoring with the final checkers, all four scored replies passed and six conversations were infrastructure errors. The AMSR2/Sentinel-1C case had three scored passes and two infrastructure errors, one GLM-5-3 context-limit error and one Mimo timeout. The shared-spacecraft daylight case had one scored pass and four provider timeouts. All five stream probes passed, and no scored row failed a checker. The longest scored conversation used eight turns under the 20-turn cap.

This conversation run started before the final fetch-provenance compaction. The final tool header keeps the selected source-choice IDs and the accepted finish snapshot keeps each full rationale. Public MCP regression tests cover that final response shape; no provider rerun was made for this payload-only change. No new full 85-conversation issue #52 run is planned. The raw JSONL traces and `/tmp` logs are local-only artifacts excluded from the PR.

The complete conical and mixed cases require cell-center and envelope limits, reject claims of confirmed acquisitions, continuous viewing, and whole-cell coverage, and accept clear negated disclaimers. The checks recognize `modeled`/`modelled` and `center`/`centre`. They are text heuristics. The rejection cases fail if either the legacy or conical workflow completes a calculation.

## Streaming check

The eval conversations are not streamed, but LibreChat streams model responses. Each run first sends every model a short streamed request and reports `stream: ok`, `missing role`, or `no tool call`. LibreChat drops tool calls from a stream whose first delta has no `role`. Models marked `disableStreaming` in `apps/librechat/librechat.yaml` are noted rather than failed.

Run only this check with:

```bash
cd apps/mcp-server
.venv312/bin/python -m eval.run_eval --stream-only
```
