# Research acceptance testing — November 2, 2026

Execution of [the research test plan](visualization-research-test-plan.md) is in
progress. Tests use Aside, the real local LibreChat UI, its selected **ASU Voyager /
Llama 4 Scout 17B** model, and the `tatc` MCP connection. No model/settings changes
are made for the tests. The authenticated session became available on November 2.

## Reference results

Direct HTTP MCP references were computed October 31 with TATC 3.5.1 / Skyfield
1.55. Independent event-endpoint sweeps verified interval unions, gaps, complete
revisit means, and covered/uncovered duration conservation. These are application
reference checks, not independent validation of orbital accuracy.

| Scenario | Counts: Phoenix / Svalbard / equator | Longest gaps (minutes) | Samples per satellite |
| --- | --- | --- | --- |
| Baseline, 100° sensors | 3 / 14 / 1 | 791.324 / 694.109 / 858.978 | 721 |
| Both sensors 120° | 4 / 18 / 4 | 551.208 / 528.275 / 475.659 | 721 |
| E02: altitude/location/window/step edits | 0 / 0 / 0 | 360 / 360 / 360 | 121 |
| Display sampling 240 s only | 3 / 14 / 1 | Identical to baseline | 361 |
| One-minute Phoenix window | 0 / — / — | 1 / — / — | 2 |

Baseline points fail the six-hour maximum-gap requirement. The equatorial point
has no complete revisit; that value is missing, not zero. References, their exact
requests, decoded resource payloads, and the verification script are in
`/private/tmp/tatc-research-acceptance-2026-10-31/`.

## Live outcomes

| Case | Outcome | Evidence |
| --- | --- | --- |
| P01 | Pass | In authenticated `/c/new`, Study opens/closes/reopens an empty pane. There is no study iframe, Cesium request, or tool call. |
| N01 | Pass | [Chat](http://localhost:3080/c/bd2abab9-67c6-45d7-ba64-395fe01bec1d): one `get_satellite_info` call; short answer 25544; no visual resource. |
| N02 | Fail | [Chat](http://localhost:3080/c/005948ea-43ea-472e-8ba8-9835d834fff6): code-only prompt produced 16 `visualize` calls and multiple chart resources, then “The provided function definitions do not align with the task requirements. Please revise and update them.” No requested code. The response finished before the stop action could take effect. |
| N03 | Fail | [Chat](http://localhost:3080/c/b7781f52-3e3c-4f2d-9bec-83521448378f): repeated `visualize`/`analyze_coverage` calls, with schema rejection visible, then attempted satellite search instead of asking which mission/sensor to use. Stopped the generation after the ten-call threshold; additional calls had already streamed by the stop action. |
| C01 | Pass | [Chat](http://localhost:3080/c/a471aa49-8abe-4b90-8a1f-c93968294980): one `visualize` call; decoded data contains only Design A/B, values 3.5/1.2, unit hours. Study opens the labelled zero-baseline bar plot. Table and downloaded CSV preserve both exact values and units; no orbital scene. |
| C02 | Pass | Same chart chat: one further `visualize` call with 3.5/2.4 hours. Closed pane stayed closed and retained the old 3.5/1.2 data until reopening, then loaded the exact updated values. Switching to a new conversation removed the old study iframe. |
| C03 | Fail: chart selection; data pass | [Chat](http://localhost:3080/c/912c28ad-e136-4040-a12e-3def2bcea0bc): one `visualize` call returned `kind: bar` despite the explicit line-chart request. The answer incorrectly called it a line chart. Both series, negative/zero values, missing entries, units and run order were correct in decoded data and downloaded CSV. |
| R01 | Fail | [Chat](http://localhost:3080/c/dcda8c5c-6d70-4c96-b65f-8f59874e3d53): the complete mission prompt produced two `visualize` failures, both `Unknown CircularOrbit fields: raan`, then stalled. Stopped the attempt around the three-minute bound without a study result. |
| R02 | Pass | [Chat](http://localhost:3080/c/bcd5979f-8c32-46d9-a20b-de3c4cf42611): one `visualize` call; actual analysis ID `c43f1858a65739d0b3b6`, exact baseline inputs, 3/14/1 counts, 721 samples per satellite, and matching longest gaps. The Results view and timeline rendered. |

| R03 | Fail: model; UI pass | [Chat](http://localhost:3080/c/19038fda-9e08-417c-ad2e-125784f967c2): instead of reusing the example, the model invented four TLE satellites (30° sensors, 2022 window, Svalbard at 78.2/16.4) and opened a new study after one schema rejection. In the genuine example pane, Svalbard shows 14 observations and 15 gaps identical to the reference to the microsecond; the 694.1 min leading gap and 31.9 min trailing gap are labelled as touching the window boundary. Selecting intervals seeks to their exact midpoints (e.g. 20823.3 s for the leading gap). |
| R04 | Fail | [Chat](http://localhost:3080/c/bcd5979f-8c32-46d9-a20b-de3c4cf42611): three `analyze_coverage` calls with an invented San Francisco/Los Angeles/New York scenario, a 2024 window and a fake demo TLE; reported 5.86 min gaps and concluded all points pass. Reference: all three fail the six-hour requirement. |
| R05 | Fail: model | [Chat](http://localhost:3080/c/e5eaf5b2-b026-4d24-bbe1-de18fc22475a): 25 identical `visualize(view=coverage, presentation=table, workspace_id="91144a6b50")` calls rejected by schema; no final answer. |
| R06 | Blocked | Same cause as R03–R05: the model never receives the computed counts. Not run to avoid another invalid-call loop. |
| S01 | Fail: model; UI pass | [Chat](http://localhost:3080/c/5220b18e-a059-4ef0-a579-21191abe2cc2): 25 identical `visualize(view=coverage, presentation=map2d, workspace_id="0ec194da93")` schema rejections. In the example pane, the globe loads in 3D and 2D, Layers (orbits, ground tracks, observations, 30 min trail) work, the satellite→Phoenix link appears during the 06:30 observation and is absent at the following gap midpoint, and Play advances at about 300× and Pause holds the time. The 2D camera stays framed on Phoenix when another location is selected. |
| S02 | Fail: provenance | [Chat](http://localhost:3080/c/69edb86f-146d-46cb-bd32-fe12943aa819): one `visualize(view=orbit, ISS, 1 hour, simulation3d)`; window exactly one hour, starts paused. The TLE epoch is computed server-side but neither shown in the viewer nor given to the model, so the requested epoch is not displayed. |
| S03 | Pass with provenance gap | [Chat](http://localhost:3080/c/23c31c6a-1cf0-4a5b-ab31-84cbf6a69dea): one `visualize(view=orbit, presentation=table)`. Table has 61 inclusive 60 s samples (10:17:48–11:17:48 UTC), altitude in km (415–428 km); the Results chart is labelled as not coverage. Cesium is not loaded for chart/table. TLE epoch missing as in S02. |
| S04 | Compatibility | [Chat](http://localhost:3080/c/b3c3b7e7-acc6-479f-bfb8-1b5c7b4e4e96): routed to the existing `ground_track_map` tool (61 points, one hour), rendered inline rather than in the Study pane. |
| B01 | Pass | [Chat](http://localhost:3080/c/ceb9dac8-47d9-4a81-b18a-821b4431ccca): one rejected 7-day call; the reply explains the 24-hour limit and asks before shortening. |
| B02 | Fail | [Chat](http://localhost:3080/c/ea3d4e72-4f4b-4a7f-8743-2f8e8922af1e): two `Unknown CircularOrbit fields: raan` rejections; the model then dropped RAAN/true anomaly and finally ran invented satellites with 30° sensors. The reported 0 observations / 60 s gap / null revisit coincide with the reference but come from wrong inputs. |
| G01 | Pass with defects | [Chat](http://localhost:3080/c/839dab8f-fa28-4d28-b7f7-a71986c2d88f): no tool call; states that point results do not support area coverage and draws no map. The reply also prints raw `<function=visualize_mcp_tatc>` markup and suggests external heatmap libraries. |
| G02 | Fail | [Chat](http://localhost:3080/c/0a25de12-8fa5-495e-ac8e-732064c32463): invented a TLE satellite with a 30° sensor and a 2022 window, got a rejection, then printed raw function markup. No explanation that footprints and illumination masks are not rendered. |
| G03 | Fail | [Chat](http://localhost:3080/c/0170f127-44ef-408e-8446-c66669af6770): treated Phoenix as a satellite, invented TLE and San Francisco/Los Angeles scenarios, assumed a store-and-forward system and finally opened a visualization. Access opportunities were not distinguished from schedules or downlinks. |
| G04 | Fail | [Chat](http://localhost:3080/c/426183c7-fd14-4ae5-9bfa-7849033af67c): the model invented a constellation (500 km, 45°, 30° sensors, 2022 epoch) for `visualize`, got an opaque schema rejection and stalled beyond three minutes; stopped. No explanation of missing cost model or limits. |

Remaining cases are pending execution; the renderer regression from the earlier
implementation is not counted as live-model acceptance evidence.

## Retest after fixes (same prompts, fresh chats)

Deployed: model-visible study summaries with `analysis_id`, server-side limit
and ID validation with actionable errors, TLE checksum/epoch guards, grounding
rules in server instructions, TLE epoch provenance, and the `/c/new` pane fix.

| Case | Outcome | Evidence |
| --- | --- | --- |
| R03 | Pass with defect | [Chat](http://localhost:3080/c/90674a67-ab13-49f5-8d58-ec54848232a6): one rejected UI-marker `workspace_id`, self-corrected to `visualize(view=example, presentation=timeline)`; Svalbard values exact (694.109 min boundary gap, 51.797 min mean complete revisit). Reply then claims further presentations "have been shown" without calls. |
| R04 | Pass | [Chat](http://localhost:3080/c/c4db504a-0b35-4a4a-a52f-3106ff9c4c4c): no tool call; exact longest gaps, boundary flags, and correct conclusion that no point meets six hours. |
| R05 | Pass | [Chat](http://localhost:3080/c/0bc7088c-dd98-42f6-9f5e-b7e8b2466258): one `visualize(view=example, presentation=table)`; reply table matches reference. |
| R06 | Pass | [Chat](http://localhost:3080/c/64bbe0a8-a188-4090-b696-00d29ccead56): one data call with 3/14/1 taken from the prior result; no coverage calculation. |
| S01 | Pass with defect | [Chat](http://localhost:3080/c/555943b9-46cc-4595-a89a-75096ec88652): one `visualize(view=example, presentation=map2d)`; same phantom "has been shown" claims as R03. |
| S02 | Pass | [Chat](http://localhost:3080/c/80ffd11c-e502-4985-b328-06f2ac5a441e): TLE epoch 2026-11-01T18:56:25Z reported; the reply also echoes call syntax to the user. |
| B02 | Pass | [Chat](http://localhost:3080/c/665d8096-6dec-4ff4-b38e-5c798625f162): one `analyze_coverage` with exact inputs using `right_ascension_ascending_node`; 0 observations, 60 s gap, null revisit. |
| R01 | Fail | [Chat](http://localhost:3080/c/eb35ba5e-340a-4844-805c-7d9c2f8d41b4): printed `<function=…>` text with the points nested inside the satellite list instead of calling a tool. |
| G01 | Fail | [Chat](http://localhost:3080/c/c8e0d79d-2e72-4d44-954c-e37fbcacc4df): rebuilt a Phoenix-only scenario, schema rejection, then printed function markup. |
| G02 | Fail | [Chat](http://localhost:3080/c/e57a5468-b9e0-4a84-a256-7e451871c78b): invented a "sensor_footprint" satellite (500 km, 30°, 2022); rejected, then offered a reduced study with "Earth" as a point. |
| G03 | Fail | [Chat](http://localhost:3080/c/d1e96bd2-bb8b-477e-978b-35d6533bbece): printed function markup with the study inputs; no capability explanation. |
| G04 | Fail (worse) | [Chat](http://localhost:3080/c/6ee763ee-afbc-402c-8685-56fb988d8d5a): scenario sent as a JSON string (schema rejection), then a data chart of invented "revisit/cost" values presented as a Pareto frontier. |
| N03 | Fail | [Chat](http://localhost:3080/c/1aaab1ad-afd6-4bd1-adfe-5b87db6288d7): five schema rejections with `satellites: []` and times such as "tomorrow"; no clarification. |
| N02 | Fail | [Chat](http://localhost:3080/c/19acf95c-9b63-41c6-bcaf-e13c1a2d1b80): two data-chart calls before returning the requested code. |
| C03 | Fail | [Chat](http://localhost:3080/c/e096a25b-eff7-4648-b778-1492627b2358): `kind` omitted, so a bar chart again; reply calls it a line chart. |

## Retest after round 3

Deployed: conditional, model-only re-show note; required chart `kind` and
`provenance` with server verification of study-derived values; JSON-string
scenarios; schema constraints moved to actionable server errors.

| Case | Outcome | Evidence |
| --- | --- | --- |
| R05 | Pass | [Chat](http://localhost:3080/c/7cd81066-bb99-4876-9142-1163f29075d7): `visualize(view=coverage, analysis_id=c43f…, presentation=table)`; exact table, no phantom claims. |
| R06 | Pass | [Chat](http://localhost:3080/c/bbd03435-2834-4fbd-9ba8-13940864a215): first call labelled "Equatorial" and was rejected by the provenance check; corrected to the study point names with 3/14/1. |
| S01 | Pass | [Chat](http://localhost:3080/c/14a8a5d2-2720-4864-aa2e-1e4dbc6ff496): one `visualize(view=example, presentation=map2d)`; no phantom claims. |
| C03 | Pass | [Chat](http://localhost:3080/c/8f87d782-3005-4067-ae80-add8aff6d562): `kind: line`, both series with nulls preserved, after one schema rejection for a missing `view`. |
| R03 | Fail: reply | [Chat](http://localhost:3080/c/1ac134f8-a936-4c51-9f95-77507e8332ae): correct `analysis_id` timeline call, but the reply is model-directed text ("if the user asks…, you can reply…") rather than an answer. |
| R01 | Fail: input fidelity | [Chat](http://localhost:3080/c/3bf2cf58-f21d-4609-a55c-91796366562e): call succeeded, but the model added satellites C and D (RAAN 90°/135°) that the user did not supply; reported 5/29/3 observations as the study result without mentioning them. |
| G01 | Fail | [Chat](http://localhost:3080/c/62780405-4fc0-4adc-8a80-f4550595b0da): ran a Phoenix-only study and claimed an Arizona area-coverage heatmap "has been generated". |
| G02 | Fail | [Chat](http://localhost:3080/c/3b4ee8d8-5741-4b40-bff5-9f8dca005929): invented a satellite and a "sun" satellite; after rejections ran an invented study and claimed footprint and illumination mask are visible. |
| G03 | Fail | [Chat](http://localhost:3080/c/5a5a030a-6193-40cb-b541-88c218e4e21c): invented satellites, rejection, then printed function markup. |
| G04 | Fail | [Chat](http://localhost:3080/c/108bf38e-d597-43b8-b898-b80db602335c): printed function markup with an invented 30-day constellation; no chart produced. |
| N03 | Fail | [Chat](http://localhost:3080/c/60a63b53-a5a7-4dad-94e7-44d684e4d011): invented "Satellite1" and resolved "tomorrow" to 2024-09-17; reported 0% coverage as the Phoenix result. |
| N02 | Fail | [Chat](http://localhost:3080/c/c2222cc0-f1b5-4860-8205-01a45614b407): two data-chart calls before returning the code. |

Server-enforced behaviour now holds: provenance-checked study charts, required
chart kind, actionable errors, and re-show by `analysis_id`. Remaining failures
are the selected Llama 4 Scout model inventing inputs, ignoring the
unsupported-capability rules, printing tool markup, or claiming outputs that
were not produced, despite those rules being in the injected server
instructions and tool descriptions. The server cannot see the conversation, so
it cannot verify that supplied satellites, orbits, sensors or dates came from
the user.

## Model comparison on the remaining failures

Same deployment and prompts as round 3; models selected through the LibreChat
model picker and confirmed from each stored response's `model` field. A first
attempt that opened chats with `?spec=<model>` changed the header label but
every response still came from `llama4-scout-17b`; those 16 runs are discarded
from the comparison (in that sample Scout passed R01 twice with reference
values, so Scout outcomes vary between runs).

| Case | Llama 4 Scout 17B (round 3) | GPT-OSS 120B | Qwen 3 235B Instruct |
| --- | --- | --- | --- |
| R01 | Fail: added satellites C/D | [Pass](http://localhost:3080/c/94fb20ba-7b9d-4bd4-99c3-afda431ed827): exact inputs after one self-corrected orbit error; 3/14/1, 791/694/859 min, none meet six hours | [Partial](http://localhost:3080/c/8997f5ab-747f-439f-b52e-17c16f774dc0): exact inputs and reference results, but the reply contains only the UI marker, no metrics |
| R03 | Fail: model-directed reply | [Pass with defect](http://localhost:3080/c/0031b910-8b71-4e03-81ea-527858e2f885): `analysis_id` reuse, correct boundary/complete-revisit explanation; claims boundary gaps are drawn in a lighter shade, which the UI does not do | [Pass](http://localhost:3080/c/6e26ba08-204f-41ea-8bfe-aac45b019329): `view=example` timeline, exact values and correct distinction |
| G01 | Fail | [Pass](http://localhost:3080/c/7e6f8346-71bb-4016-869a-6f131481efce): no call; not supported, offers point grid / CSV | [Pass](http://localhost:3080/c/7971c73f-0f58-49c0-99d8-8d1f1d7c3d6b): no call; not supported |
| G02 | Fail | [Pass](http://localhost:3080/c/8b62024d-70d4-4b6f-af04-91a8e08f19cc): no call; lists supported alternatives | [Pass](http://localhost:3080/c/f562e9fb-1f69-45f5-9b05-9509fa6b6809): no call; not supported |
| G03 | Fail | [Pass](http://localhost:3080/c/3033d375-f0cc-4ca0-acc5-17df6c6e4ea7): no call; access opportunities distinguished from downlinks | [Pass](http://localhost:3080/c/bfd0a05b-6e3d-45ea-beaa-0d61bcf4cbd6): no call; same distinction |
| G04 | Fail | [Pass](http://localhost:3080/c/2afbc953-5b6f-435d-bff3-c5998d6d9579): no call; states 4-satellite, 24 h, point-only limits and missing cost model | [Pass](http://localhost:3080/c/d5897ff4-27d6-4494-8636-322b14d15a47): no call; asks for inputs for a supported study |
| N02 | Fail | [Pass](http://localhost:3080/c/1b28bfc0-4304-4c71-a5b8-0d435b1d3aeb): code only, no tool call | [Pass](http://localhost:3080/c/fda3cd4f-049c-46e7-ba56-554901ee924e): code only, no tool call |
| N03 | Fail | [Pass](http://localhost:3080/c/80cdd9ea-a93a-4902-8025-bf38c37d295a): asks for satellites/sensors and exact UTC window | [Pass](http://localhost:3080/c/7c3dc64b-4617-48d3-a656-80c0af7a9226): asks for satellites/sensors and window |

With the same server instructions and tool descriptions, both larger models
decline unsupported requests and ask for missing inputs; the remaining
failures are specific to Llama 4 Scout.

After adding a scope statement to every coverage result ("Nothing here is an
area heatmap, sensor footprint…; if the user asked for one, say that it is not
supported") and asking the reply to name each satellite, Scout was rerun on
G01–G04. G01 ([chat](http://localhost:3080/c/0116e814-cc55-4636-bad0-6ae5ed9cc904))
made no call but replied with instruction text rather than an answer; G02
([chat](http://localhost:3080/c/84d3d3ec-97ed-46a5-8cff-dc67b1fb76cf)) stated
the capabilities are unsupported, then attempted a scenario-less call that the
server rejected; neither claimed a heatmap or footprint. G03
([chat](http://localhost:3080/c/39800279-29c7-4f71-86f5-3e2103b38dc3)) still
invented a third satellite and a 10-minute downlink latency and presented a
downlink table; G04 ([chat](http://localhost:3080/c/d6fa507f-d8a1-43cf-98b0-bd28dd424ec8))
still printed function markup with an invented constellation. The wording
reduces false output claims but does not make Scout decline reliably.

The `?spec=` mismatch between the
displayed and the used model is a separate product defect.

## Study pane, Cesium and Inputs reruns (GPT-OSS 120B)

Run after all fixes were deployed, in
[a coverage chat](http://localhost:3080/c/0b378168-9a34-4fdc-9945-f06b7d9e2464)
and [an orbit chat](http://localhost:3080/c/43566e2c-d4bd-437d-9a54-be3a15229e1b),
with the model confirmed as `gpt-oss-120b`. Rerun results were decoded from the
pane's loaded resource.

| Case | Outcome | Evidence |
| --- | --- | --- |
| `/c/new` pane | Pass | Study opened empty in `/c/new`; after the first prompt the URL became `/c/0b378168…`, the pane stayed open, and the new example loaded into it. |
| Map re-centring | Pass | Location select moves the 3D camera from Phoenix to a polar view of Svalbard; in 2D it frames Svalbard and then the equatorial point. |
| Layers and trail | Pass | At the equatorial observation (14:19:44 UTC): Orbits off removes the orbit line only, Ground tracks off removes the track, Observations off removes the green link; trail 5 min and 90 min visibly shorten and lengthen the track. |
| R05 UI / CSV | Pass | Location summary, 14 Svalbard observation events and 15 gaps (boundary flags `Yes` on first and last) match the reference exactly in full precision; missing revisit shows "—" and an empty CSV cell, not 0. |
| E03 | Pass | Only `display_step_seconds` 120→240 sent, same `workspace_id`, pane updated in place; 361 samples per satellite (was 721) and all metrics identical. "Submitted to chat. These results remain unchanged until the new TATC calculation returns." shown meanwhile. |
| E01 | Pass | Both sensors 100→120°: 4/18/4 observations, longest gaps 551.208/528.275/475.659 min (reference). Restoring 100° and 120 s returned 3/14/1, 721 samples and the original `analysis_id` c43f1858a65739d0b3b6. |
| E02 | Pass | Satellite A 600 km, Phoenix 34°, end 06:00 UTC, step 180 s transmitted exactly with all other inputs preserved; 0/0/0 observations, 360 min boundary gaps, 121 samples (reference). |
| P02 | Pass | Unrun draft (step 240) and the Inputs tab survive close/reopen; playback paused on close (time held at 52809.3 s while hidden); keyboard resize clamps at window − 360 px (650→670 max, →470); a new conversation has no study frame and opens to the empty state. |
| S02 pane | Pass | One-hour ISS window, Cesium 3D globe, starts paused, Methods & source lists "ISS (ZARYA) (NORAD 25544): TLE epoch 2026-11-02T10:25:21.580320Z"; the reply states the epoch. |

Layout findings: with the LibreChat sidebar open in a ~1030 px window, the
650 px pane covers the entire chat column, because the pane width limit
(`innerWidth − 360`) ignores the sidebar. Cesium satellite labels render with a
doubled, offset outline ("ISS (ZARYA) (25544)", "Study satellite B").

Fixed and verified live at the same 1030 px window (DPR 1.6): the pane width
now also subtracts the sidebar and is re-evaluated when the sidebar is toggled,
keeping the user's chosen width for when it closes (360 px sidebar → 380 px
pane, 290 px readable chat column). The viewer renders at device pixel ratio
(606 px canvas for 379 CSS px), and satellite labels use white text on a dark
callout instead of a coloured fill with a heavy outline; labels are sharp over
ocean and land in 3D and 2D. Remaining cosmetic notes: at this width LibreChat
clips one of its own header buttons beside the model name, and the zoomed-out
2D map shows dark space above the map's northern edge.

## Model-context finding

LibreChat stores only a tool result's text content and a `\ui{id}` marker for
the model; MCP `structuredContent` is dropped. Coverage results therefore reach
the model without the scenario, workspace UUID, or per-point metrics, and the
only identifier it sees is the LibreChat UI resource ID. This causes the R03–R06
and S01 failures: the model fabricates studies or passes the resource ID as
`workspace_id`, which LibreChat rejects with an uninformative schema error.

A browser-automation note: clicking a launcher link through Aside's cross-origin
frame locator followed the fallback `href` into a new tab; a DOM click inside the
launcher frame opens the pane correctly. This is a harness artifact, not a
product defect.

## UI lifecycle finding

Opening the empty Study pane in `/c/new` and then sending the first prompt closes
the pane when LibreChat assigns the conversation its persistent ID. Observed in
R01: `/c/new` → `/c/dcda8c5c-6d70-4c96-b65f-8f59874e3d53`; the pane element was
removed. The adapter treats this initial save as a conversation switch. This is
a failure of the intended already-open-pane workflow, separate from tool routing.

## Pointed-instrument chat test (2026-11-03)

End-to-end run through LibreChat (Aside browser) after adding pointed-instrument
summaries, Inputs fields and the Footprints layer.

| Step | Model | Result |
| --- | --- | --- |
| "Show a coverage study … pointed imager, roll 25°" | GPT-OSS 120B | Called `analyze_coverage`, not `visualize` ("show" was read as numerical). 0 observations, correct. Exposed a server bug: the echoed scenario and `analysis_id` omitted pointing. **Fixed** (see below). |
| "Visualize … cross-track 40°, along-track 5°, roll 10° … I want to see the instrument footprint" | GPT-OSS 120B | Pass. First call put orbit fields on the satellite; recovered from the server error. `visualize` returned 1 observation (17:03–17:10 UTC, 0.52%), matching a direct TATC run. In the pane at 17:06 UTC the 40° × 5° footprint lies over Phoenix with the boresight line and the observation link. |
| Inputs: roll 10° → 25°, Run | GPT-OSS 120B | Pass. Exact edited params and `workspace_id` resent; new `analysis_id`; 0 observations (matches direct run); pane updated, Inputs shows 25°. |
| Same "Visualize …" prompt | Llama 4 Scout (default) | Fail. Nested Phoenix inside `satellites` and used `24:00Z`; after the server's specific error it wrote the corrected call as `<function=…>` text instead of calling the tool. Same Scout failure mode as earlier rounds. |

Fixes from this run:

- **Pointing dropped from the scenario and `analysis_id`.** `PointedInstrument`
  subclasses `Instrument`, the first member of TATC's instrument union, so
  Pydantic serialized pointed instruments as basic ones. Studies differing only
  in pointing shared an `analysis_id`, so an `analysis_id` re-show could return
  the other study. `analyze` now dumps satellites with `serialize_as_any=True`;
  regression test `test_pointing_is_part_of_the_scenario_and_its_analysis_id`.
- **Footprint wording.** GPT-OSS twice said footprints appear only at access
  opportunities. The summary now states the footprint is drawn throughout the
  window, whether or not a study point is observed.

Footprint samples are limited to 5,768 per scene (8 pointed instruments over
24 hours at the 120 s default step); this limit is intended.
