# retrieval — corpus memory

Split out from the former `memory/`. This package is the **knowledge base of
worked examples**: a growing library of (natural-language question → `tatc`
code / answer) scaffolds that the agent retrieves from to condition its code
generation. It scales with the *case library* — tens today, thousands later —
not with the length of any one conversation. Session state lives in the sibling
`conversation/` package; keeping the two apart is deliberate (see "Why split").

## Interface

`Store` is a structural protocol — `add(text)` and `search(query, limit)`.
Anything with those methods plugs in, so the retrieval backend can change
without touching callers. `agent.run_task` calls `store.search(task)` and feeds
the hits to `conversation.assemble_context` as `examples`.

Baseline: `KeywordStore` — term-overlap ranking over an in-RAM list. It is the
weakest defensible retriever on purpose, so embedding and hybrid methods have an
honest control to beat.

## Why split from conversation

A retriever that finds the right example out of 10,000 does nothing to keep a
200-turn dialogue inside the context window, and vice versa. Fusing them
produces one component that half-solves both. The two packages own genuinely
different problems: this one is about **retrieval precision at scale**.

## Roadmap

1. Dense retrieval — embed questions and scaffolds, index (FAISS / a vector
   store), swap `KeywordStore` for an embedding store behind the same `Store`
   protocol.
2. Hybrid keyword + dense with reranking; metadata filters by case class
   (single-sat vs. constellation vs. coverage).
3. Corpus construction — turn the eval cases and future solved tasks into
   indexed scaffolds.

## The precision risk

More retrieval is not more accuracy. For code generation a *wrong* retrieved
example is worse than none: it anchors the model to the wrong scaffold and
yields confident, wrong `tatc` code. So retrieval needs high precision and an
explicit **"no good match → return nothing, generate from primitives"** path,
not top-k-always.

## First experiment

Run the pinned eval set (`../eval/`) three ways — no retrieval, correct
retrieval, deliberately-wrong retrieval — and compare score and token cost. If a
wrong example lowers the score, retrieval quality is proven to be a first-class
risk, not a nice-to-have. Token cost per configuration comes from
`usage.jsonl`.
