# conversation — session memory

Split out from the former `memory/`. This package keeps a **long dialogue inside
the context window** by managing and compacting history. It scales with the
number of *turns* in a session, not with the size of the example library — that
is the sibling `retrieval/` package's job.

## Interface

- `ConversationHistory` — an ordered buffer of turns (`add`, `recent`).
- `Compactor` — a structural protocol, `compact(turns) -> turns`. Baseline
  `NullCompactor` passes turns through unchanged: the honest control that does no
  compaction, so any compaction policy has a baseline to beat.
- `assemble_context(task, examples, history)` — builds the final prompt in
  **cache-aware order**: stable `examples` (retrieved, unchanging within a turn)
  first, volatile `history` next, `task` last.

## The cache tension (why ordering matters)

Prompt caching only pays off on a **stable prefix** — the model reuses the KV
cache for the longest unchanged run of tokens from the front. Compaction rewrites
earlier history, which changes the prefix and invalidates the cache from the edit
point onward. So naive "compress every turn" is one of the most cache-destroying
things you can do.

The resolution is architectural, not a cleverer compressor:

1. Immutable content (system prompt, tool defs, retrieved examples) at the
   **front**, where it caches.
2. The volatile running summary at the **back**.
3. Compact on a **cadence you choose**, not every turn — each compaction is a
   deliberate cache burn traded for context room.

"Compress without destroying the cache" is a cadence decision, and its cost is
measurable: `usage.jsonl` (written by `tokens/usage.py`) logs the tokens each
policy actually spends.

## Roadmap

1. A summarizing `Compactor` (older turns → running summary), then sliding-window
   and salience-based eviction, all behind the same protocol.
2. Cache-aware compaction boundaries — compact only up to a point that preserves
   the cacheable prefix.
3. Wire history into the agent once a multi-turn loop exists (see non-goal below).

## Non-goal for now

`agent.run_task` is still single-shot and does not thread history through yet;
`ARCHITECTURE.md` lists "no multi-turn loop" as a baseline non-goal. This package
is the boilerplate that loop plugs into when it arrives.

## First experiment

Replay a long synthetic dialogue under different compaction policies
(`NullCompactor` vs. a summarizer at varying cadences) and compare answer quality
against tokens spent, using the eval harness and `usage.jsonl`.
