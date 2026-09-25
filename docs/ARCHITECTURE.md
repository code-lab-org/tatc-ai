# Architecture — research base

As of 2026-09-25, branch `research-base`. This document carries the design
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

Every `run_agent_task` call flows through all five research packages:

```
natural-language task
      │
      ▼
retrieval/     search the example corpus for relevant scaffolds
      │
      ▼
conversation/  assemble the prompt context (cache-aware ordering)
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
| `agent.py` | `task: str`, optional injected `LLMClient` / retrieval `Store` | Rendered response text; one usage record per run | all five packages |
| `models/base.py` | — | `LLMResponse` dataclass, `LLMClient` protocol | stdlib only |
| `models/gemini.py` | Env: `GEMINI_API_KEY` (required), `GEMINI_MODEL` (default `gemini-2.5-flash-lite`); prompt, system | `LLMResponse` (text, tokens, latency) | google-genai (lazy import) |
| `models/router.py` | — | `default_client()` → `GeminiClient` | `base`, `gemini` |
| `retrieval/store.py` | `add(text)`, `search(query, limit)` | `Store` protocol; `KeywordStore` keyword-overlap baseline | stdlib only |
| `conversation/assembly.py` | task, optional examples, optional history | single prompt string, cache-aware order (examples → history → task) | stdlib only |
| `conversation/history.py` | turns | `ConversationHistory` buffer (`add`, `recent`) | stdlib only |
| `conversation/compaction.py` | `compact(turns)` | `Compactor` protocol; `NullCompactor` passthrough baseline | stdlib only |
| `tokens/usage.py` | `LLMResponse` + task label + optional extra dict; env `USAGE_LOG_PATH` | one JSONL line per LLM call | `models.base` |
| `tokens/budget.py` | env `TASK_TOKEN_BUDGET` (0 = unlimited); charged per response | `exceeded` flag, recorded into usage entries | `models.base` |
| `rendering/render.py` | model output text | markdown table when the text is a JSON array of flat objects; passthrough otherwise | stdlib only |

## Design decisions

1. **One package per research thread.** `retrieval/`, `conversation/`,
   `models/`, `tokens/`, `rendering/` each own a single concern, so a paper's
   methodology lands as files inside one package without touching the others.
   Memory is two packages, not one: `retrieval/` is corpus memory (find the
   right example; scales with the case library) and `conversation/` is session
   memory (keep a long dialogue in context; scales with turns) — different
   problems, different backends.
2. **Protocols, not base classes.** `LLMClient`, `Store` and `Compactor` are
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
7. **The memory baselines are honest controls on purpose.** `KeywordStore`
   (retrieval) is the weakest defensible retriever, so embedding/graph/
   bi-temporal methods have something to beat; `NullCompactor` (conversation)
   does no compaction, so any compaction policy has a baseline to beat.

## Research extension points

| Research thread | Package | First experiment shape |
| --- | --- | --- |
| Corpus memory | `retrieval/` — replace `KeywordStore` (embeddings, hybrid, reranking); build the scaffold corpus | Eval set with no / correct / wrong retrieval; answer quality vs tokens (`../eval`, `usage.jsonl`) |
| Session memory | `conversation/` — add a summarizing `Compactor`; cache-aware compaction cadence | Long dialogue under compaction policies; quality vs tokens (`usage.jsonl`) |
| Open-source model replacement | `models/` — add an OpenAI-compatible client (vLLM, Ollama, DeepInfra); implement routing in `router.py` | A/B Gemini vs open model per task class; measure quality against logged cost |
| Token management | `tokens/` — budgets already logged; add caching or compression in `agent.run_task` before `client.generate` | Prefix-stable prompts, response caching, output caps — against the logged baseline |
| Output rendering | `rendering/` — chart specs, richer table handling | NL request → chart spec → rendered chart in LibreChat |

## Non-goals for the base

No multi-turn loop (the `conversation/` package is scaffolding until one
exists), no tool use inside the agent, no persistent memory backend, no real
router — each arrives with the paper methodology that motivates it, so the
baseline stays a clean control.
