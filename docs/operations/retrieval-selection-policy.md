# Retrieval Strategy Selection Policy

This policy is pre-registered: it is defined **before** any winner is selected and
before the production retrieval default may change. It exists to prevent the
production default from being switched on isolated or biased evidence.

## Current default

`weighted-hybrid-v1` (0.75 semantic + 0.25 lexical with deterministic
document-diversity reranking) is the production default and stays the default
until paired, reproducible evidence satisfies the decision rule below. Merely
configuring another strategy never changes live retrieval.

## Evidence base

The default may only be compared on the **same reviewed dataset**, pinned to the
same projection generation, canonical snapshot, embedding model, reranker, and
environment. Acceptable datasets:

- `sample-docs-v1` — human-authored document-level cases from
  `data/sample/manifest.json` (natural intent, including one insufficient-evidence case);
- `synthetic-chunks-v2` — generated with lexical-overlap controls and the
  source-blind naturalizer (documented residual bias: questions are still
  generated from target chunks).

**`synthetic-chunks-v1` scores are excluded as evidence.** v1 generated each
question from its own target chunk, which inflates lexical overlap and retrieval
scores; any v1 results in `var/evaluation/` are labeled biased and superseded.

## Decision rule

A candidate strategy may replace the default only when **all** of the following
hold on the same dataset:

1. **Quality** — candidate Hit@5 and MRR are both at least 0.02 higher than
   `weighted-hybrid-v1` on the aggregate, with no per-slice regression worse
   than 0.01 on fact/exact_lookup/easy slices.
2. **Latency** — candidate mean latency does not exceed the default by more than
   1.5x (agentic strategies are expected to be slower; this gate is explicit).
3. **Cost** — candidate mean planner calls, input tokens, and output tokens are
   within the budget recorded for the default; any strategy with planner calls
   must justify them with quality gains on complex/comparison slices.
4. **Operational complexity** — the candidate adds no unrecoverable state, no
   new external service requirement, and remains within the existing projection
   contract (rebuildable rows, atomic activation, no learned-sparse or
   Hugging Face/PyTorch dependencies).

If no candidate satisfies all four gates, `weighted-hybrid-v1` remains the
default. Partial wins (e.g., agentic on complex slices only) are documented but
do not change the global default without a per-route promotion decision.

## Reporting

Every comparison run must record the dataset version, projection generation,
embedding model, candidate list, and the full metric table (Hit@5, Hit@20, MRR,
mean latency, planner calls/tokens, question-type and difficulty breakdowns,
no-answer false-positive rate). Results without credentials or a live projection
are reported as verified `not_run` and are never filled with fabricated numbers.
