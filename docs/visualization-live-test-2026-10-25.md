# Visualization live test — October 25, 2026

Tested through Aside MCP in the existing logged-in LibreChat tab at
`http://localhost:3080`, using the selected **ASU Voyager / Llama 4 Scout 17B**
model and the connected `tatc` server. Prompts were entered into real chats;
parameter reruns went through the deployed model, not a simulated model response.
The model, temperature, and account settings were not changed.

**Result: rendering works, but the complete research workflow does not yet pass.**
The most important failure is an unrequested satellite appearing in a parameter
rerun. Successful schema validation alone does not guarantee preservation of the
researcher's requested scenario.

## Results

| Check | Observed result |
| --- | --- |
| Fresh two-value bar-chart request | Pass: one `visualize` call, one visible chart link, correct 3.5/1.2 values, hours, numerical axes and zero baseline. |
| Explicit built-in coverage example | Pass: baseline observation counts 3 / 14 / 1 for Phoenix / Svalbard / equatorial point. |
| Separate study pane | Pass using a direct mouse click; charts and coverage open alongside chat. |
| Cesium 3D and 2D | Pass in Aside: Earth, both satellite tracks, point markers and legend rendered. No worker-load error. |
| Playback and linked timeline | Pass: play advances time; pause stops it. Selecting Svalbard's 694.1-minute boundary gap seeks to 05:47:03 UTC, its midpoint. |
| Both sensor fields of regard 100° → 120° | Calculation pass: counts 4 / 18 / 4 and longest gaps 551.2 / 528.3 / 475.7 minutes. Pane closed during the response; fixed and retested below. |
| Both fields of regard 120° → 100° | **Fail for input fidelity:** new result included an unrequested third satellite named `TATC-Mission`. Svalbard reported 16 observations instead of the baseline 14. |
| Pane remains open during a rerun | Fixed: a subsequent live model response updated the same pane without closing it. This UI pass does not make that response's altered scenario valid. |
| CSV export | Downloaded successfully, preserved units and blank missing revisit values. Export faithfully contained the altered result, including Svalbard's 16 observations. |
| Complete custom research prompt | **Fail:** schema rejection followed by malformed function-call text in the answer. |
| Numerical follow-up requesting no recalculation | **Fail:** invoked `analyze_coverage` again despite the explicit request. Final longest-gap values were correctly converted, but the explanation mislabeled intermediate seconds as minutes. |
| Ambiguous Phoenix coverage request | **Fail:** repeated schema-invalid calls instead of requesting mission/sensor assumptions. Stopped after 12 recorded calls. |
| Explicit Python-code-only request | **Fail:** invoked visualization repeatedly, including successful chart resources, despite instructions not to run or visualize anything. Stopped with 13 tool entries, including one cancelled call. |
| Seven-day ISS animation | Pass for scope handling: reported the 24-hour limit and asked before shortening; no seven-day or silently shortened scene was returned. |

## Reproduction chats and prompts

These links refer to the local test account, not portable public artifacts.

### Chart

[Fresh chart conversation](http://localhost:3080/c/f67144cc-9ba7-4740-ba92-bde5150f50a1)

> Plot these revisit times: Design A 3.5 hours, Design B 1.2 hours.

The earlier duplicated-card screenshot was not reproduced by this fresh request.

### Custom research request

[Custom study conversation](http://localhost:3080/c/d6f19f01-ca0e-4f69-9d40-6e675fde7cc1)

```text
Run a hypothetical TATC coverage study and open its Cesium 3D study view, initially paused. Analyze 2026-10-07T00:00:00Z through 2026-10-08T00:00:00Z with 120-second display sampling. Satellite A: circular orbit, mean altitude 550000 m, inclination 97.5 degrees, RAAN 0 degrees, true anomaly 0 degrees. Satellite B: same altitude and inclination, RAAN 45 degrees, true anomaly 180 degrees. Both epochs are 2026-10-07T00:00:00Z. Each carries an instrument named Study sensor with field of regard 100 degrees. Study points: Phoenix (latitude 33.4484, longitude -112.074, elevation 331 m); Svalbard (78.2232, 15.6469, 0 m); Equatorial study point (0, 0, 0 m). Use these design orbits, not catalog satellites. Report observation counts, time-covered percentages and longest uncovered gaps including window boundaries for each point. Does each point meet a maximum-gap requirement of six hours? Keep complete revisit gaps distinct from boundary gaps. Do not infer regional area coverage or scheduled observations.
```

The rejected call included unsupported `paused: true`. The textual continuation
used `RAAN` rather than the native `right_ascension_ascending_node`, malformed
JSON, and a placeholder `workspace_id`. These were printed function-like strings,
not successful tool executions.

### Coverage pane and parameter reruns

[Coverage and rerun conversation](http://localhost:3080/c/fa00f1b8-b899-4597-863c-50100af34ca3)

1. Send `Open the built-in coverage example.`
2. Open the pane, inspect Map → 3D/2D, select Svalbard, and select its longest gap.
3. In Inputs, change both sensor fields of regard from 100 to 120 and run the study.
4. Change both fields back to 100 and run again.
5. Inspect the actual returned scenario, not only the visible longest-gap bars.

Recorded results, decoded from the actual rendered MCP resources:

| Analysis ID | Returned scenario | Counts: Phoenix / Svalbard / equator |
| --- | --- | --- |
| `c43f1858a65739d0b3b6` | Original two satellites, 100° sensors | 3 / 14 / 1 |
| `7bf2588ec6bb0b32d5d0` | Original two satellites, 120° sensors | 4 / 18 / 4 |
| `ace47dd83febbda43ba7` | Original satellites plus unrequested `TATC-Mission` | 3 / 16 / 1 |

The third result must not be treated as a reproduction of the baseline. Its
longest gaps happen to match the baseline, which is why checking only that chart
would miss the changed scenario. The downloaded `tatc-study.csv` confirmed the
different count and mean complete revisit at Svalbard.

The follow-up prompt was:

> Using only the latest calculated study, tell me whether all three study points satisfy a maximum uncovered gap of six hours. Give each longest gap in minutes. Do not create a new visualization or rerun the study.

### Ambiguity, code and duration

[Ambiguous request](http://localhost:3080/c/894216f1-ee89-4437-8534-95ea1938bad5):

> Visualize coverage gaps over Phoenix tomorrow.

Calls contained empty satellite lists, unresolved dates such as `tomorrow`, and
later arbitrary September 2024 dates without timezones. Validation rejected them;
the model did not ask for the missing mission and sensor.

[Code-only request](http://localhost:3080/c/fc3c79d9-7e12-4001-910f-5aaee1d0edd5):

> Write Python code to plot Design A revisit time 3.5 hours and Design B 1.2 hours as a bar chart. Return code only; do not run it or open a visualization.

[Duration-limit request](http://localhost:3080/c/64f155dd-70b7-43b3-a91a-95d6178f2602):

> Animate ISS in 3D for seven days.

## Fix made during testing

The host adapter closed the study whenever its originating iframe disappeared.
LibreChat also removes/recreates those iframes during message refreshes, including
the arrival of a new tool response. Closing now follows conversation-path changes.
The adapter rebinds a returning launcher without resetting study state and ignores
already-seen historical results when deciding whether to update the open pane.
If the authenticated originating renderer is unavailable, submitting a rerun now
reports a submission error instead of waiting silently.

The local LibreChat container was restarted to load the updated adapter in its
served HTML. Verified asset version: `21a85ef673fd`.

Regression coverage was added for removal of the originating iframe during a
rerun, remounting old results, duplicate current resources preserving the selected
view, and conversation navigation closing the pane. The full browser suite passed
against the actual installed LibreChat resource renderer and the running MCP
server in about 67 seconds. An earlier run timed out waiting for the headless map;
the repeated run with added diagnostics passed. The actual Aside map also passed.

Aside's locator shortcut for links navigated directly to the launcher's URL.
Repeating the interaction with a physical mouse click opened the pane correctly.
The resulting separate-tab behavior was an automation artifact, not an application
failure. No new rendering fallback was added.

## UI refinement — October 26

The research pane now matches LibreChat's neutral dark surface, with smaller
headings, consistent spacing, plain underlined tabs, and lighter dividers.
Secondary gap-distribution details are collapsed initially. Tables align numeric
columns, the event timeline includes UTC tick labels, and inputs are grouped by
analysis window, satellite, and study point. Map controls and CSV export remain
available. No rendering fallback was added.

Verified in the existing Aside conversation at 650px and 410px pane widths:

- Results, Table, Inputs, and the rendered Cesium map remain usable; the narrow
  form stacks dates and sensor fields without clipping them.
- Changed display sampling from 120 to 60 seconds, the first satellite altitude
  from 550 to 600 km, and Phoenix latitude from 33.4484 to 34 degrees. Each edit
  remained a draft and the table retained baseline counts 3 / 14 / 1. Restored
  these values and reopened the original study afterwards.
- These new draft edits were **not submitted to the chat model**. The earlier
  full field-of-regard reruns and their input-fidelity failure remain as recorded
  above.

All 14 UI tests passed. The full browser regression passed against the installed
LibreChat resource renderer in 64 seconds, including desktop/mobile charts and
maps, CSV, exact input delivery to its callback, pane lifecycle, and a visible
error when workers are blocked. The callback test invokes the requested MCP call
directly; it does not prove the model preserves that call.

Earlier regression attempts missed clicks immediately after resizing the parent
pane or browser viewport. Diagnostics showed the Results tab still selected and
Cesium never requested. The test now waits for the cross-origin iframe viewport
to match its layout width before clicking; it also waits for rendered table rows
before counting them. The map's loading timeout was not extended or bypassed.

## Workspace layout — October 29

Reworked the pane around the supplied visualization references. It now has one
study title, a compact view toolbar, a map that fills the remaining viewport, and
a persistent bottom timeline and playback strip. Results use a shared labelled
numerical axis and a compact summary; tables and inputs scroll within their own
view. The main workspace no longer requires scrolling past the map to reach time
controls. Standalone viewers retain their title. No new rendering fallback or
model-routing rule was added.

All 14 UI tests passed, including the coverage axis and unchanged draft results.
The full browser regression passed in 79 seconds against the installed LibreChat
renderer. It checks desktop and mobile geometry, the timeline staying inside the
pane, playback, 2D/3D switching, CSV, input delivery, and worker-error reporting.
Responsive geometry is tested by resizing; mobile interactions are tested after
navigation at the mobile viewport. In the earlier mixed sequence, Chrome sent
the play click to the parent iframe rather than the child button after resizing.
That sequence is not evidence that post-resize pointer handling is reliable.

Final Aside review used the original saved coverage example. The inputs retained
two satellites with 100° field of regard, and the table showed 3, 14, and 1
observations for Phoenix, Svalbard, and the equatorial point. The map loaded,
cleared its loading indicator, switched between 2D and 3D, and played and paused
with the study clock advancing. The header, map, and timeline fit the pane.
An earlier Aside attempt hit the existing 30-second globe-loading timeout; its
cause was not established. This successful repeat does not establish that the
intermittent load failure is resolved. No timeout extension or retry was added.

## Rerun checks and loading deadline — October 30

The host now compares the request carried by a returned TATC result with the
pane's submitted inputs before applying it. The comparison preserves all input
values and array order while ignoring object property order. It rejects altered
satellites, sensors, study points, times, presentation, and missing request records.
Rejected results retain the prior pane and draft, with a notice in the pane and
an unavailable launcher labelled "Study inputs did not match". An exact response
can still be accepted afterward. Closing the pane does not clear the check; a new
submission supersedes the previous one, and changing conversations clears it.
This state is local to the current page session, not persisted across reloads.
It does not prevent the model from making an incorrect call or describing that
call incorrectly in chat, and it does not repair historical results.

A focused test reproduced a false loading timeout when the user switched away
from Map while tiles were pending. The loading deadline now counts 30 seconds
with both Map and the browser document visible. Hidden time pauses the remaining
budget; returning does not reset it. Completion removes the listeners and timer.
The existing stalled-map error remains, with no retries, substituted renderer, or
new fallback. This addresses a reproducible case, not every possible cause of the
earlier intermittent timeout.

All 19 UI tests passed. The browser regression exercised real MCP calculations
and the installed LibreChat resource renderer, rejecting an unrequested third
satellite and accepting the exact submitted request afterward. It also covered
CSV, charts, desktop/mobile layout, playback, 2D/3D switching, and worker failure.
Aside currently presents the login screen, so a new live chat-model rerun was not
performed in this session. The previous model-routing findings still apply.

## Always-available Study pane — October 31

Added a Study button to the deployed LibreChat chat header. It opens an empty
workspace before any result exists, without invoking a tool or creating an iframe.
Trusted visual resources populate an open pane; closed panes defer new results
until reopened. Existing resource links still open their specific studies.
Closing retains the iframe, active tab, draft inputs, camera and clock, pauses
playback and disables Cesium's render loop. Reopening resumes rendering while
leaving playback paused. Conversation changes dispose the iframe and local state.

All 23 UI tests passed. The suite covers those lifecycle cases, late/remounted resources, exact rerun
checks, and closing while Cesium assets are loading. The browser regression uses
real HTTP MCP calculations and the installed LibreChat UIResourceRenderer in a
test host. It verifies empty-state behavior without Cesium, retained table and
edited sensor inputs, preserved camera/time after closing during playback,
desktop/mobile geometry, CSV, 2D/3D switching, and the worker-error case. Desktop
and mobile screenshots were inspected. The final browser run passed in 72 seconds
against the rebuilt local services. This verifies the renderer and adapter,
not a new live natural-language conversation.

Aside was at LibreChat's login screen, so the authenticated chat header and live
model routing could not be exercised in that session. The integration uses the
installed Header's `main .absolute.top-0 > .hide-scrollbar` structure; the browser
regression checks that the deployed bundle still contains that contract. An
upstream Header change requires updating this adapter, not adding selector
fallbacks. No new fallback or routing rule was added. Existing release blockers
below remain.

## Outstanding release blockers

- Make pane-submitted reruns execute exact parameters without model transcription.
  The October 30 display check rejects mismatches within the active page session;
  it does not ensure the model produces an acceptable result or preserve checks
  across page reloads. Repeat live model tests and verify that session boundary.
- Make the custom-scenario schema and its validation errors usable by the deployed
  chat model, then repeat natural-language tests without coaching its tool syntax.
- Verify that missing mission facts trigger clarification and code/text requests
  can finish without tools. Bound repeated invalid-call loops in the host.
- Verify that numerical summaries reach the model in the format LibreChat actually
  consumes; investigate the unnecessary recalculation instead of assuming the
  structured MCP output is available to the model.

These observations characterize this deployment and these test runs. They do not
establish a failure rate for other models or prove the remaining cases reliable.
