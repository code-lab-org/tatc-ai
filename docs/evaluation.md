# Tool-calling evaluation

The eval package checks how well ASU Voyager models use the MCP tools. It gives each model the server's tools and instructions, runs tool calls against the server with recorded CelesTrak and OSCAR responses, and scores the server's results.

## Run the eval

Run these commands from the repository root. By default, the eval runs the models LibreChat offers. Pass `--models` to try other models.

```bash
cd apps/mcp-server
.venv/bin/python -m eval.run_eval --label mychange
.venv/bin/python -m eval.run_eval --compare eval/results/*.jsonl
```

The eval reads the proxy key from `apps/librechat/.env` and sends prompts and tool schemas to the ASU proxy. A full run takes about half an hour. Use the Python 3.12 environment that matches the server Dockerfile, for example `apps/mcp-server/.venv312` if you created one.

The coverage cases include the legacy single-geometry workflow and three conical-scanning cases:

- `conical_amsr2_complete`: a sourced AMSR2 configuration with conflicting nominal/product sector choices and declared approximations. The model must prepare, accept the current revision, run, fetch viewing events, and preserve cell-center and envelope limits.
- `conical_applicability_rejected`: a window outside the established applicability interval. The model must stop before calculation and identify the uncovered dates.
- `conical_unresolved_sector`: a configuration with no recorded choice between the nominal and product scan sectors. The model must ask for the choice and must not prepare or run.
- `conical_missing_evidence`: a configuration missing the measured cone/sector evidence. The model must not invent evidence or substitute a nominal swath.

The eval is not a mission-accuracy test. The unit and reference integration tests cover event counts, epochs, projected positions, and independent inside/outside cases. A partial or multi-configuration conical proposal is intentionally absent because this increment does not support it.

The complete conical case checks for cell-center sampling and the limits of envelope durations, and rejects common claims of continuous or whole-cell viewing. These text checks are conservative heuristics. The rejection cases fail if either the legacy or conical workflow completes a calculation.

## Streaming check

The eval conversations are not streamed, but LibreChat streams model responses. Each run first sends every model a short streamed request and reports `stream: ok`, `missing role`, or `no tool call`. LibreChat drops tool calls from a stream whose first delta has no `role`. Models marked `disableStreaming` in `apps/librechat/librechat.yaml` are noted rather than failed.

Run only this check with:

```bash
cd apps/mcp-server
.venv/bin/python -m eval.run_eval --stream-only
```
