# Research visualization and simulation acceptance plan

## Purpose and execution rules

Exercise the researcher → chat model → MCP tool → returned study → browser pane
workflow in the user's Aside browser at `http://localhost:3080`. This is separate
from the existing isolated renderer regression. Record the deployed model and
tool calls; do not change the model, system prompt, or account settings to obtain
a pass. Use new conversations where specified to avoid accidental context reuse.

For each case, record **pass**, **fail**, **blocked**, or **unsupported**, the exact
prompt, conversation URL, observed calls, and visible result. A refusal can pass a
limit-handling case while the requested capability remains unsupported. A
successful screenshot alone is insufficient: check the returned inputs, units,
data values, time window, and scientific interpretation. Separate tool-routing,
computation, rendering, and pane-lifecycle findings.

Use snapshot-driven actions and inspect screenshots for spatial views and charts.
Read the actual resource data to check model transcription. Keep evidence under
`/private/tmp/tatc-research-acceptance-2026-10-31/`; put reproducible findings in the
dated report in `docs/`. Never store credentials or tokens in evidence.

Stop a response after three repeated invalid calls, ten tool calls without useful
progress, or three minutes without progress. Record it as a failed/blocked attempt,
not a pass. Do not coach the model with tool syntax after an initial failure and
then count that retry as the original prompt passing. Continue independent cases.
Repeat input-fidelity and ambiguous-routing failures once in a fresh conversation
when time and access permit; this small sample does not establish a failure rate.

## Test matrix

| ID | Research workflow and prompt/action | Acceptance criteria |
| --- | --- | --- |
| P01 | New chat: click **Study**, then close/reopen before sending a prompt. | Empty state; no tool call, iframe, Cesium, or fabricated scenario. Header button works; normal chat remains available. |
| N01 | New chat: `What is the ISS NORAD identifier? Give me a short text answer.` | Correct metadata answer; no visualization resource or unexpected pane. |
| N02 | New chat: `Write Python code to plot Design A revisit time 3.5 hours and Design B 1.2 hours as a bar chart. Return code only; do not execute it or open a visualization.` | Code answer; no executed plotting/simulation tools or study resource. |
| N03 | New chat: `Visualize coverage gaps over Phoenix tomorrow.` | A focused clarification for missing satellite/sensor assumptions; no invented mission, fake TLE, or invalid-call loop. Dates must be resolved explicitly before computation. |
| C01 | New chat: `Plot these revisit times: Design A 3.5 hours, Design B 1.2 hours.` | Numerical bar chart with labelled axes, hours, zero baseline, 3.5 and 1.2; no orbital calculation or Cesium. Open via Study and inspect Table/CSV. |
| C02 | C01 follow-up: `Change Design B to 2.4 hours and leave Design A at 3.5 hours. Update the plot.` | New values exactly preserved; no added designs; latest result reaches the open pane. Closing before completion defers loading until reopening. |
| C03 | New chat: `Plot a line chart of these simulation residuals in seconds, in order of run: runs 1, 2, 3, 4, 5. Design A: -2, missing, 0, 3, 1. Design B: 1, 2, missing, -1, 0. Do not interpolate missing values.` | Two labelled series; negative/zero values retained; missing entries stay blank and break lines; CSV matches; no Cesium. Equal run spacing is appropriate. |
| R01 | New chat: send the complete hypothetical study below, with Study already open. | Exact two-satellite design reaches MCP; genuine TATC results match reference; no catalog substitution, invented fields, or printed function markup. Three points only; hypothetical label and UTC window preserved. |
| R02 | Separate new chat: `Open the built-in coverage example.` | Explicit example loads with documented fixed inputs and 3/14/1 opportunity counts; no claim of live ISS coverage. Provides an independent way to test UI if R01 fails. |
| R03 | R01/R02: `Show when Svalbard is covered and uncovered in this study, and distinguish boundary gaps from complete revisits.` Then select Svalbard, an observation, and its longest gap. | Correct location and interval timestamps; timeline seeks to event midpoint; counts/durations agree with reference. Boundary gaps are not reported as complete revisits. |
| R04 | R01/R02: `Using only the latest calculated study, does every point satisfy a maximum uncovered gap of six hours? Give the longest gaps in minutes. Do not recalculate or create another visualization.` | Existing numbers reused, seconds→minutes correct, requirement conclusion correct; no unnecessary calculation or resource. Existing pane stays unchanged. |
| R05 | R01/R02: `Show the exact observation and gap values for this study in a table.` Inspect both event-table types, switch locations, export CSV. | Exact rows and units; correct location/time selection; CSV matches displayed/computed values. Any recalculation or loss of source-study identity is recorded. |
| R06 | R01/R02: `Plot the observation counts from this existing result as a comparison by location. Use those computed values without recalculating coverage.` | Data chart reproduces 3/14/1 (for baseline); no propagation; provenance is the actual prior result; no Cesium in the chart. |
| S01 | R01/R02: `Show this study's satellites and study points on a 2D map.` Then switch 3D, toggle layers, change trail length, play/pause/scrub, and inspect an observation link. | Correct satellites/points; Earth and workers load; 2D/3D and controls work; link visibility follows the computed interval. Playback is not presented as scheduled or observed data. |
| S02 | New chat: `Animate the ISS orbit around Earth in 3D for one hour starting now. Start paused and show the orbital-data epoch.` | Live catalog identity and TLE provenance; exactly one hour; no invented sensors or coverage metrics. If catalog access fails, explicit error with no fabricated replacement. |
| S03 | New chat: `Plot the ISS altitude over the next hour, sampled every 60 seconds. Show the sampled values in a table too.` | Orbit-derived altitude chart/table with correct units, 61 inclusive samples, provenance, and no globe loaded until Map is selected. |
| S04 | New chat: `Show an ISS ground-track map for one hour.` | Existing geographic-map route works. Record its actual renderer and destination. An inline MapLibre map is a compatibility result, not a pass for universal Study-pane support. |
| E01 | R02 Inputs: change both sensor fields of regard 100°→120°, run, then restore both to 100° and run. | Actual returned inputs have only the requested changes; no added satellite. 120° reference counts 4/18/4; restoration returns 3/14/1. Mismatch rejection passes the guard, but fails the requested rerun workflow. |
| E02 | Fresh R02 Inputs: change only satellite A altitude to 600 km, Phoenix latitude to 34°, end to 06:00 UTC, and display step to 180 s; run. | All four edits are transmitted exactly; every other orbit/sensor/point/time input preserved. Results match a direct TATC reference. Draft is not treated as computed before acceptance. |
| E03 | Fresh R02 Inputs: change only display step 120→240 s; run. | Tracks change from 721 to 361 samples per satellite; scientific observation intervals and coverage metrics remain the same. Display sampling is not analysis accuracy. |
| P02 | During a populated study: edit an input without running, close/reopen, play then close/reopen, resize, and switch to another conversation. | Draft/tab/camera/time persist within the conversation; playback pauses; hidden rendering stops; resizing remains usable; another conversation receives no prior study. Ordinary chat leaves the current pane alone. |
| B01 | New chat: `Animate ISS in 3D for seven days.` | Explains current 24-hour limit and asks before shortening; no silent 24-hour result. Follow-up consent can test the supported shorter request separately. |
| B02 | New chat: request the R01 mission only for 00:00–00:01 UTC on October 7, 2026, and Phoenix only. | Valid zero-opportunity case matches reference: zero coverage, 60 s longest boundary gap, unavailable complete revisit shown as missing, not zero. |
| G01 | R02 follow-up: `Turn these point results into a continuous area-coverage heatmap across Arizona.` | Explains that point coverage does not establish area coverage; no interpolated map claimed as computed. Regional-grid heatmaps remain unsupported. |
| G02 | R02 follow-up: `Show the full sensor footprint and its solar-illumination mask moving across Earth.` | Honest capability explanation; observation lines/ground tracks are not relabelled as footprints or illumination products. Current footprint rendering is unsupported. |
| G03 | R02 follow-up: `Simulate an observation schedule and show when each image is downlinked to Phoenix, including downlink latency.` | Explains missing scheduling/communications model and station assumptions; access opportunities are not claimed as executed observations or downlinks. Capability remains unsupported. |
| G04 | New chat: `Compare 100-satellite constellations over 30 days and visualize a Pareto frontier of revisit time versus mission cost.` | No fabricated design sweep, cost model, or optimization. Explains current analysis bounds and missing cost inputs; can describe a staged approach without executing a silently reduced study. |

## Complete research prompt for R01

```text
Run a hypothetical TATC coverage study and open its study pane with the results
comparison first. Analyze 2026-10-07T00:00:00Z through 2026-10-08T00:00:00Z with
120-second display sampling.

Study satellite A: circular orbit, mean altitude 550000 meters, inclination
97.5 degrees, right ascension of ascending node 0 degrees, true anomaly 0 degrees.
Study satellite B: circular orbit, mean altitude 550000 meters, inclination
97.5 degrees, right ascension of ascending node 45 degrees, true anomaly 180 degrees.
Both orbital epochs are 2026-10-07T00:00:00Z. Each satellite has one instrument
named Study sensor with field of regard 100 degrees.

Study points: Phoenix (latitude 33.4484, longitude -112.074, elevation 331 meters);
Svalbard (78.2232, 15.6469, 0 meters); Equatorial study point (0, 0, 0 meters).
Use these hypothetical design orbits. Report observation counts, percentage of
time covered, and longest uncovered gap for each point. Include leading and
trailing gaps in the longest-gap metric; distinguish those from complete revisits.
Evaluate whether each point meets a maximum uncovered-gap requirement of six
hours. Do not infer regional area coverage or scheduled observations.
```

## Reference and evidence checks

Before testing model-generated studies, call the deployed MCP server directly for
the baseline, both 120° sensors, E02's four edits, 240 s display sampling, and the
one-minute Phoenix study. Store the exact requests, decoded scenes, dependency
versions, and metrics. This is an application reference, not independent validation
of TATC's physical model. Separately recompute interval unions/gaps from returned
events and check duration conservation, revisit definitions, and displayed units.

For numerical-data plots, the supplied values are the reference. For live catalog
orbits, save the returned TLE epoch and interval, check sample count/endpoints and
identity; do not compare against an unrelated TLE fetched later. Historical TLE
retrieval, uncertainty propagation, and orbit accuracy are outside this feature.

## Completion criteria

Every matrix row has an explicit outcome and evidence, or an explicit access/data
blocker. Core supported workflows must pass input fidelity, numerical comparison,
and visible interaction checks. Unsupported requests must be reported honestly.
Record remaining bugs and capability gaps by impact; do not label the whole
feature production-ready on the strength of isolated renderer tests.
