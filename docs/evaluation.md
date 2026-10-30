# Tool-calling evaluation

The eval package checks how well ASU Voyager models use the MCP tools. It gives each model the server's tools and instructions, runs tool calls against the server with recorded CelesTrak and OSCAR responses, and scores the server's results.

## Run the eval

Run these commands from the repository root. By default, the eval runs the models LibreChat offers. Pass `--models` to try other models.

```bash
cd apps/mcp-server
.venv312/bin/python -m eval.run_eval --label issue51-s1c-pointed --workers 4 --repeats 1
.venv312/bin/python -m eval.run_eval --compare /path/to/baseline.jsonl /path/to/current-run.jsonl
```

The run writes a timestamped JSONL file under `eval/results/` and prints its summary. Replace the two comparison paths with existing result files; `--compare` summarizes saved runs and does not start a new model evaluation.

The eval reads the proxy key from `apps/librechat/.env` and sends prompts and tool schemas to the ASU proxy. A full run normally takes about half an hour but can take longer when model requests are slow or time out. It saves each completed conversation to the timestamped JSONL file. Use the Python 3.12 environment that matches the server Dockerfile, for example `apps/mcp-server/.venv312` if you created one.

The coverage cases include the legacy single-geometry workflow, the conical-scanning cases below, and a complete pointed Sentinel-1 IW case:

- `conical_amsr2_complete`: a study-specific sourced AMSR2 configuration with conflicting nominal/product sector choices and declared approximations. The model must prepare, accept the current revision, run, fetch viewing events, and preserve cell-center and envelope limits. It uses the study-specific fixture with its own ID and revision; the reviewed shared entry is the independent TAT-C reference target.
- `conical_amsr2_registry_selection`: the model must look up the reviewed shared configuration, leave lookup unselected, prepare with the exact configuration ID and revision the researcher chose, and retain that revision in the result.
- `conical_amsr2_failed_refresh`: the model records a reported failed source check, uses the researcher's exact prior revision, and carries source uncertainty into the result and reply.
- `conical_applicability_rejected`: a window outside the established applicability interval. The model must stop before calculation and identify the uncovered dates.
- `conical_unresolved_sector`: a configuration with no recorded choice between the nominal and product scan sectors. The model must ask for the choice and must not prepare or run.
- `conical_missing_evidence`: a configuration missing the measured cone/sector evidence. The model must not invent evidence or substitute a nominal swath.
- `pointed_s1c_iw_complete`: the model must look up and explicitly select Sentinel-1C C-SAR IW revision `2026-10-30.1`, use `prepare_pointed_study` and `finish_pointed_study`, retrieve event, daily, cell, revisit, and per-cell event-spacing views, and carry fixed-roll, source-reference, off-nadir/incidence, cell-center, and acquisition-schedule limits into its reply.

The recorded Sentinel-1C GP response uses the exact CelesTrak TLE published in the TAT-C 3.6.0 validation reference (epoch 2026-10-02) re-encoded as OMM fields. This keeps the eval's recorded clock and OMM freshness checks consistent; it is a fixed test input, not a live refresh. The pointed configuration itself cites the source record and its later 2026-10-30 CelesTrak OMM snapshot.

The eval is not a mission-accuracy test. The unit and reference integration tests cover event counts, epochs, projected positions, and independent inside/outside cases. Conical and pointed studies each accept only one configuration in this increment.

The complete conical and pointed cases check for cell-center sampling and the limits of envelope durations, and reject common claims of continuous or whole-cell viewing. These text checks are conservative heuristics. The rejection cases fail if either the legacy or conical workflow completes a calculation.

## Streaming check

The eval conversations are not streamed, but LibreChat streams model responses. Each run first sends every model a short streamed request and reports `stream: ok`, `missing role`, or `no tool call`. LibreChat drops tool calls from a stream whose first delta has no `role`. Models marked `disableStreaming` in `apps/librechat/librechat.yaml` are noted rather than failed.

Run only this check with:

```bash
cd apps/mcp-server
.venv312/bin/python -m eval.run_eval --stream-only
```
