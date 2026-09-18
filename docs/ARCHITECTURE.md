# Architecture — research base

As of 2026-09-18, branch `research-base`. This document carries the design
reasoning; the code stays comment-free by convention.

## Baseline

LibreChat (chat UI) talks to a FastMCP server over streamable-HTTP, with
Dex/Traefik auth in the deploy stack only. Google Gemini is the public LLM
API for all MCP tools and agent tasks (default `gemini-2.5-flash-lite`), and
is also enabled in LibreChat's chat UI via its native Google endpoint
(`GOOGLE_KEY`).

The target the packages build toward: a user states a request in natural
language and gets the desired output — text, tables, and eventually charts —
with memory, model choice, and token spend handled by the pipeline, not the
user.

## Pipeline

Every `run_agent_task` call flows through all four research packages:

```
natural-language task
      │
      ▼
memory/    search stored memories, assemble the prompt context
      │
      ▼
models/    route to a provider (Gemini today), generate
      │
      ▼
tokens/    charge the budget, append a usage record (JSONL)
      │
      ▼
rendering/ JSON-array answers become markdown tables; else raw text
      │
      ▼
response to LibreChat
```

## Per-file I/O map (apps/mcp-server/src)

| File | In | Out | Depends on |
| --- | --- | --- | --- |
| `server.py` | MCP tool calls (`echo`, `run_agent_task`); OIDC env vars | Tool results; uvicorn on :8000 | fastmcp, `agent` (lazy, call-time) |
| `agent.py` | `task: str`, optional injected `LLMClient` / `MemoryStore` | Rendered response text; one usage record per run | all four packages |
| `models/base.py` | — | `LLMResponse` dataclass, `LLMClient` protocol | stdlib only |
| `models/gemini.py` | Env: `GEMINI_API_KEY` (required), `GEMINI_MODEL` (default `gemini-2.5-flash-lite`); prompt, system | `LLMResponse` (text, tokens, latency) | google-genai (lazy import) |
| `models/router.py` | — | `default_client()` → `GeminiClient` | `base`, `gemini` |
| `memory/context.py` | task, optional history, optional memories | single prompt string (memories → history → task) | stdlib only |
| `memory/store.py` | `add(text)`, `search(query, limit)` | `MemoryStore` protocol; `InMemoryStore` keyword-overlap baseline | stdlib only |
| `tokens/usage.py` | `LLMResponse` + task label + optional extra dict; env `USAGE_LOG_PATH` | one JSONL line per LLM call | `models.base` |
| `tokens/budget.py` | env `TASK_TOKEN_BUDGET` (0 = unlimited); charged per response | `exceeded` flag, recorded into usage entries | `models.base` |
| `rendering/render.py` | model output text | markdown table when the text is a JSON array of flat objects; passthrough otherwise | stdlib only |

## Design decisions

1. **One package per research thread.** `memory/`, `models/`, `tokens/`,
   `rendering/` each own a single concern, so a paper's methodology lands as
   files inside one package without touching the others.
2. **Protocols, not base classes.** `LLMClient` and `MemoryStore` are
   structural types: anything with the right methods plugs in. Tests inject
   fakes the same way future backends (vLLM/Ollama clients, vector stores)
   will.
3. **The router is the model-swap point.** `default_client()` is one function
   today; routing policies (cheap-first cascades, per-task-class routing)
   replace its body without changing any caller.
4. **Every LLM call is metered at the source.** `tokens/usage.py` writes
   append-only JSONL to a Docker volume (`/data/usage`), so baseline
   token/latency data accumulates before any optimization exists to compare
   against. `TokenBudget` (env `TASK_TOKEN_BUDGET`) marks over-budget runs in
   the log rather than blocking them — observation first, enforcement later.
5. **Rendering closes the NL→output loop.** The system prompt asks for a JSON
   array when the answer is tabular; `rendering/render.py` turns exactly that
   shape into a markdown table and passes everything else through. Chart
   output later means emitting a chart spec from the same seam.
6. **`server.py` imports `agent` lazily inside the tool body** so the module
   still loads standalone (the server test loads it by file path) and tool
   registration never requires google-genai.
7. **`InMemoryStore` is a keyword-overlap baseline on purpose** — the weakest
   defensible retrieval, so embedding/graph/bi-temporal methods from the
   literature have an honest control to beat.

## Research extension points

| Research thread | Package | First experiment shape |
| --- | --- | --- |
| Memory & context management | `memory/` — replace `InMemoryStore` (embeddings, temporal graphs); extend `assemble_context` (compaction, scratchpads, pruning) | Same task set with/without curation; compare answer quality vs tokens in `usage.jsonl` |
| Open-source model replacement | `models/` — add an OpenAI-compatible client (vLLM, Ollama, DeepInfra); implement routing in `router.py` | A/B Gemini vs open model per task class; measure quality against logged cost |
| Token management | `tokens/` — budgets already logged; add caching or compression in `agent.run_task` before `client.generate` | Prefix-stable prompts, response caching, output caps — against the logged baseline |
| Output rendering | `rendering/` — chart specs, richer table handling | NL request → chart spec → rendered chart in LibreChat |

## Non-goals for the base

No multi-turn loop, no tool use inside the agent, no persistent memory
backend, no real router — each arrives with the paper methodology that
motivates it, so the baseline stays a clean control.
