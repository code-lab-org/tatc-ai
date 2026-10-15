# TAT-C AI — Project Documentation

Living record of the project's vision, approved decisions, components, and open
questions. Companion to `docs/ARCHITECTURE.md` (design reasoning and per-file
I/O maps) and the per-package `README.md` files. Update this whenever a decision
is made or a component's status changes.

Last updated: 2026-10-15.

## 1. Vision & motive

Make the existing `tatc` mission-analysis library **fast and easy to use in
natural language**, for technical and non-technical users alike. A user states a
request in plain language and gets the answer — number, table, or figure — with
memory, model choice, and token spend handled by the pipeline, not the user.

Founding principle: **no code generation on the runtime/user path.** When a user
makes a request, the agent does not write `tatc` code — it calls pre-built tools
that wrap `tatc` operations, the model selects a tool and fills its parameters,
and the library does the computing. This is deterministic, needs no
arbitrary-code sandbox, and narrows failure to wrong-tool / wrong-parameter.

Code generation and execution exist in exactly one place: the **authoring track**
(§4). A developer generates new code — including new visualizations — in a
sandbox; it is reviewed, its eval/tests must pass, and an admin pushes it; only
then does it become a fixed tool users can call. Visualizations are therefore
*authored* into fixed chart tools, never generated at runtime.

## 2. Approved decisions

| Date | Decision |
| --- | --- |
| 2026-09-22 | Evaluation scoring is **execution output**: run the agent's tool call and compare its result to ground truth. |
| 2026-09-22 | **Two eval tiers** — `pinned` (deterministic regression) and `live` (agentic stretch, e.g. snow-study). |
| 2026-09-22 | Ground truth is **generated** from reference code, not transcribed. |
| 2026-09-25 | **No code generation on the runtime path.** The agent accesses `tatc` via tool-calling. |
| 2026-09-25 | **Coarse task-level tools first**; fine-grained composable primitives later. |
| 2026-09-25 | **Roles are configuration** of one agent (model, tools, budget, prompt) — not multi-agent orchestration. |
| 2026-09-25 | **Orchestration is a bounded multi-call tool loop.** |
| 2026-09-25 | **Memory is two subsystems**: `retrieval/` (corpus) and `conversation/` (session). |
| 2026-10-15 | **Visualizations are authored** into fixed chart tools via the developer→admin pipeline; never generated at runtime. |
| 2026-10-15 | **Public LLM APIs first** for every case; replace with open models where needed once a use case works. |
| 2026-10-15 | **Code changes are gated**: developer proposes (workflow, prompts, code), admin verifies (eval/tests green) and pushes before users reach the new functions. |
| 2026-10-15 | **Secrets live in a git-ignored `.env`**; `.env.example` is the template; loaded by docker compose and locally by `src/config.py`. |
| 2026-10-15 | **Role access matrix defined**: analysis tools for all roles; `author`/`run_eval`/`promote`/`manage_users` capabilities gated in code per role. |
| 2026-10-15 | **Standalone demo app** (FastAPI + single-page UI) to test the role system; seeded users `codelab_{role}_{id}@lab.com` / `{role}_{id}`, pbkdf2-hashed. |
| standing | Google Gemini is the public LLM API (default `gemini-2.5-flash-lite`). |

## 3. Architecture at a glance

LibreChat (chat UI) → FastMCP server → the agent pipeline. A request flows
through the packages under `apps/mcp-server/src/`:

- `retrieval/` — corpus memory: find relevant example scaffolds.
- `conversation/` — session memory: assemble prompt context, cache-aware.
- `models/` — route to a provider and generate / converse (tool-calling).
- `tools/` — the coarse `tatc` tool layer the agent calls.
- `orchestrator.py` — the bounded multi-call loop that drives the tools.
- `tokens/` — meter every call to `usage.jsonl`.
- `rendering/` — shape the result (table today; chart tools arrive via the authoring track).
- `roles/` — per-role configuration applied to a run.

Design reasoning and the per-file I/O map live in `docs/ARCHITECTURE.md`.

**Secrets & config.** API keys and deploy secrets live in a git-ignored `.env`
(`.env.example` is the committed template). Docker compose loads the root `.env`;
local runs load it automatically via `src/config.py` (python-dotenv). No key is
ever committed.

## 4. Roles & permissions

Roles configure one agent; they are not separate collaborating agents. Each role
binds a model/API, a tool scope, capabilities, a token budget, and a system
prompt. The four roles are now defined (`src/roles`): all get the analysis tools,
and capabilities separate them — `user` {query}; `contributor` {query, author};
`developer` {query, author, run_eval}; `admin` {query, author, run_eval, promote,
manage_users}. Models are Gemini for all for now; token budgets are still default.
The `apps/demo/` app enforces this matrix end to end.

Guardrail principle: `role.tools` is an **authorization boundary enforced in
code**. The orchestrator refuses any tool call outside a role's allow-list; the
model is never trusted to respect the boundary through prompting alone.

Authoring track (approved, not built): a developer / contributor role as a
**code-editing agent** that can add, update, and modify the codebase — and author
new tools, including chart tools — via agents, behind real guardrails. The flow is
separation of duties: the developer proposes the workflow, system prompts, and new
code; the **eval harness is the verification gate** (the new function's eval/tests
must pass); an admin verifies and pushes; only then can users reach the new
functions. The final push is human-gated. This is a distinct mode with its own
security surface, separate from the runtime analysis path.

## 5. Memory model

Two subsystems, deliberately separate because they solve different problems:

- `retrieval/` — corpus memory. Retrieves example (natural-language → tool-call)
  scaffolds; scales with the case library. Baseline `KeywordStore`; roadmap is
  embeddings / hybrid retrieval with reranking. Precision risk: a wrong retrieved
  example is worse than none, so it needs a "no good match → no example" path.
- `conversation/` — session memory. Keeps a long dialogue in the context window;
  scales with turns. Baseline `NullCompactor`; roadmap is a summarizing compactor
  and cache-aware compaction. Cache principle: stable content (system prompt,
  tools, retrieved examples) at the front, volatile summary at the back, compact
  on a chosen cadence — every compaction is a deliberate cache cost.

## 6. Model routing (open vs public)

Roles are the home for the open-vs-public split: a critical / admin role → a
powerful public LLM; a display or formatting role → a cheap or local open model;
a privacy-sensitive role → on-prem only. `router.client_for(model)` selects the
client (Gemini today); open-source / local clients are added behind the same
interface. Cost and privacy are treated as **separate** justifications for
routing.

## 7. Tool layer

Coarse, task-level tools that wrap `tatc`, each with a JSON schema for
function-calling and gating; `registry.call(name, **args)` dispatches.

| Tool | Answers |
| --- | --- |
| `revisit_over_point` | mean revisit period (hr) over a point, single satellite or Walker constellation |
| `ground_track` | sensor ground-track swath polygon (WKT) over a window |
| `global_coverage` | mean revisit per point on a global grid at a given spacing |

Parameters are passed in (TLE, swath, target, window, constellation, spacing) —
the deterministic pinned path. Resolving a TLE by satellite name (Celestrak) is a
later data-sourcing tool (the live tier). The three tools reproduce the eval
ground truth exactly.

## 8. Orchestration

`orchestrator.run` is a **bounded multi-call loop**: the model receives the tool
schemas, issues one or more tool calls, sees each result fed back, and iterates
until it returns a final answer or hits `max_steps`. A parametric question (e.g.
revisit vs. 1–6 satellites) is the model calling one tool several times. Role
tool-gating is enforced in the loop, in code.

## 9. Evaluation

Harness at `apps/mcp-server/eval/` (see `EVAL.md`). Scoring compares executed
output to ground truth with type-aware comparators: relative tolerance for
scalars and vectors, intersection-over-union for the ground-track polygon, and
per-point tolerance for the coverage table.

Answer key (tatc 3.5.1):

| Case | Question | Ground truth |
| --- | --- | --- |
| Q1 | Revisit over Tempe, VIIRS/NOAA-20 single sat | 9.1977 hr |
| Q2 | Revisit, Walker delta 3 sats / 3 planes | 2.9444 hr |
| Q3 | Revisit vs 1–6 sats/plane | [2.9444, 1.4185, 0.9127, 0.6595, 0.5076, 0.4077] hr |
| Q4 | Ground track, 30 min | swath polygon (WKT), IoU ≥ 0.98 |
| Q5 | Global coverage @5000 km | 32 points, 0.9020 → 3.2951 hr |

## 10. Component status

| Component | Status |
| --- | --- |
| Eval harness + ground truth | Built; self-validated (reference replay 5/5, wrong answers rejected) |
| Coarse tool layer (`tools/`) | Built; reproduces the full answer key exactly |
| Orchestrator loop (`orchestrator.py`) | Built; loop logic tested with a fake client (single, multi, cap, gating) |
| Gemini function-calling (`GeminiClient.converse`) | Written; **not yet verified against the live API** |
| Roles (`roles/`) | Defined: analysis tools for all; `author`/`run_eval`/`promote`/`manage_users` capabilities per the matrix |
| Demo app (`apps/demo/`) | Built + tested (9/9): login, per-role capability gating, real `/ask` through the orchestrator |
| Retrieval (`retrieval/`) | Baseline `KeywordStore` only; no embeddings/corpus yet |
| Conversation (`conversation/`) | Baseline `NullCompactor` only; not wired into the loop yet |
| Rendering (`rendering/`) | JSON-array → table only; chart tools come via the authoring track (not started) |
| Config / secrets (`.env`, `src/config.py`) | `.env` git-ignored; `.env.example` template; auto-loaded locally and in compose |

## 11. Open questions / not yet decided

- Per-role model and token-budget policy (tool + capability access is now defined; models are Gemini-only for now).
- Live data sourcing (TLE-by-name lookup).
- Eval adapter that scores the orchestrator end-to-end.
- Fine-grained `tatc` primitives for novel questions (snow-study).
- Which chart tools to author first, and how they render in LibreChat.
- Whether/when true multi-agent orchestration is needed.

## 12. Roadmap

1. Verify `GeminiClient.converse` against the live API (one real round-trip).
2. Eval adapter: score the orchestrator end-to-end on the pinned cases.
3. Define role policies (models, tools, budgets).
4. Retrieval: embeddings + a scaffold corpus. Conversation: a summarizing compactor wired into the loop.
5. Open-source / local model client + role-based routing.
6. Live data-sourcing tool (TLE by name).
7. Fine-grained `tatc` primitives for the long tail.

## 13. Future tracks (not started)

- Developer-role **code-editing / authoring agent** (approved) — guardrails, sandboxing, eval gate, admin-pushed; authors new tools and chart tools.
- **True multi-agent orchestration** (planner + tool-runner + verifier) for hard, decomposable tasks — only when a task earns the complexity.
- Chart tools, authored through the track above.
