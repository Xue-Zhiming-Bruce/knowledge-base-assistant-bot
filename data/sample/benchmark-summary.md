# Retrieval Benchmark Summary — sample-docs-v1

Dataset: `sample-docs-v1` (8 human-authored document-level cases from
`data/sample/manifest.json`, 4 public Substack sources)
Projection: `bd3a3ba7-f427-42f0-91d4-0f0f5f0d3465` (active, `text-embedding-3-small`,
1536 dims, chunker `markdown-paragraphs-v1`)
Embedding model: `text-embedding-3-small`; planner model: the configured
generation model; reranker: deterministic diversity reranker.

Run command:

```shell
knowledge-assistant eval-run \
  --dataset var/evaluation/sample-docs-v1.jsonl \
  --output var/evaluation/sample-retrieval-results.jsonl \
  --strategy all
```

This summary is public-safe: it contains aggregates only, never questions,
answers, prompts, or source content. Raw per-case results remain in the
git-ignored `var/evaluation/` directory.

## Lexical retrieval fix (2026-08-16, second run)

The first run showed `lexical-only-v1` scoring 0.000 because the lexical leg
built the PostgreSQL query with `websearch_to_tsquery('simple', question)`,
which ANDs every space-separated term. Natural questions paraphrase article
vocabulary, and the `simple` text configuration performs no stemming, so almost
no single chunk contained *all* query terms and the strict `lexical_score > 0`
filter returned zero rows.

The lexical leg now builds a disjunctive query of the question's distinctive
content terms (English stopwords and single-character tokens removed) via
`build_lexical_tsquery` (`src/knowledge_assistant/infrastructure/postgres/question_repository.py`),
still scored by PostgreSQL `ts_rank_cd` (never called BM25). OR semantics recover
lexical recall while `ts_rank_cd` ranks chunks by matched-lexeme density, so the
most relevant chunks rise to the top. A regression test
(`tests/test_lexical_retrieval.py`) proves that the distinctive
"headcount reduction … AI's value" question retrieves the expected *Pinhole View
of AI Value* document lexically.

## Strategy comparison (after the fix)

| Strategy | Hit@5 | Hit@20 | MRR | Mean latency | Planner calls | No-answer false-positive |
| --- | --- | --- | --- | --- | --- | --- |
| vector-only-v1 | 1.000 | 1.000 | 0.893 | 0.99s | 0.0 | 0.000 |
| lexical-only-v1 | 0.857 | 1.000 | 0.673 | 0.54s | 0.0 | 1.000 |
| weighted-hybrid-v1 (default) | 1.000 | 1.000 | 0.929 | 0.60s | 0.0 | 1.000 |
| rrf-hybrid-v1 | 1.000 | 1.000 | 0.929 | 0.53s | 0.0 | 0.000 |
| agentic-decomposition-v1 | 1.000 | 1.000 | 0.929 | 2.93s | 1.0 | 0.000 |

Notes:

- `lexical-only-v1` improved from 0.000 to 0.857 Hit@5 / 0.673 MRR; the hybrid
  strategies' MRR improved from 0.893 to 0.929, reflecting a live lexical leg.
- The no-answer (insufficient-evidence) case stays **excluded from Hit@K and MRR
  aggregates**; it is reported through `no_answer_false_positive_rate` instead.
  Under OR semantics the no-answer question now surfaces plausible distractor
  chunks for `lexical-only-v1` and `weighted-hybrid-v1` (false-positive 1.000):
  the retrieval layer flags them, and the answer layer must abstain — which
  `grounded-answer-v2` does (100% abstention in the end-to-end run), while
  `grounded-answer-v1` does not (0% abstention). RRF and agentic keep the
  distractor below the evidence cutoff (0.000).

## Breakdowns (weighted-hybrid-v1)

By question type: fact 1/1, explanation 4/4, comparison 1/1, exact_lookup 1/1
(Hit@5); insufficient_evidence abstains via the answer layer.
By difficulty: easy 3/3, medium 2/2, hard 3/3 (Hit@5).

## Selection policy outcome

Applied the pre-registered
[retrieval selection policy](../../docs/operations/retrieval-selection-policy.md):

- Quality gate (Hit@5 **and** MRR at least 0.02 above the default): no candidate
  beats `weighted-hybrid-v1` (rrf and agentic tie its MRR; vector-only is lower).
- Latency gate (≤ 1.5x the default): `agentic-decomposition-v1` fails at 2.93s
  vs 0.60s (≈4.9x).
- Cost gate: agentic adds one planner call and ~219 input tokens per question
  without a quality gain.
- Operational complexity: agentic adds a planner failure surface without benefit.

**Decision: no change.** `weighted-hybrid-v1` remains the production default.
The lexical fix strengthened the hybrid's lexical leg (MRR 0.893 → 0.929) rather
than favoring any candidate.

## Honest caveats

- The sample is 8 cases over 4 documents; scores are indicative, not
  statistically significant.
- The no-answer false-positive behavior differs by strategy under OR semantics;
  the correct end-to-end test is answer-layer abstention (see
  [answer-benchmark-summary.md](./answer-benchmark-summary.md)).
- `synthetic-chunks-v1` results (if present in `var/evaluation/`) are **biased
  and superseded**: v1 generated each question from its own target chunk,
  inflating lexical overlap and scores. They are not natural-user evidence.
- All numbers above are real output from the recorded run; nothing was
  fabricated.
