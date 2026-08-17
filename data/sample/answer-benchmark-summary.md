# Answer Evaluation Summary

This summary is public-safe: it contains aggregates only and never questions,
answers, prompts, or source content.

Dataset: `sample-docs-v1` (25 curated document-level cases from
`data/sample/manifest.json` — 22 answerable, 3 insufficient-evidence).
Retrieval strategy: `weighted-hybrid-v1` on the active projection
`bd3a3ba7-f427-42f0-91d4-0f0f5f0d3465`. Both answer approaches ran through the
real RAG path (retrieve, rerank, bounded context, structured answer generation,
deterministic citation validation). Real rerun 2026-08-17 with a structured LLM
judge applied, using:

```shell
knowledge-assistant answer-eval-run \
  --dataset var/evaluation/sample-docs-v1.jsonl \
  --output var/evaluation/answer-results.jsonl \
  --output-markdown data/sample/answer-benchmark-summary.md \
  --strategy weighted-hybrid-v1 --approaches all \
  --judge-model <configured generation model>
```

**Validation mode:** the sample dataset is document-level (no target chunks), so
targets were validated by resolved document URL, and citation validity means
every cited identifier resolves to retrieved evidence, not target-document
membership. The runner's primary path is chunk-level fingerprint validation for
cases that carry a target chunk.

**Metric classes — kept strictly separate:**

- **Deterministic metrics** (no model involved): citation identifier validity,
  citation coverage, required-fact **lexical** coverage (a token-overlap proxy,
  NOT semantic factual correctness), abstention behavior, latency, tokens.
- **Model-judge scores**: the structured `answer-judge-rubric-v1` scores below.
  They are **uncalibrated model opinions** — no human labels have been reviewed
  yet, so they are evidence of relative behavior, never ground truth. They are
  never conflated with deterministic metrics.
- **Human labels**: reviewed labels belong in `data/sample/answer-human-labels.jsonl`
  (currently empty). Calibration status: **`not_run`** — see the Human-labels
  section of [data/sample/README.md](./README.md) for the exact procedure
  (`answer-eval-calibrate`).

## Approach comparison

| Approach | Cases | Citation validity | Citation coverage | No-answer abstention | Fact lexical coverage | Judge overall |
| --- | --- | --- | --- | --- | --- | --- |
| grounded-answer-v1 | 25 | 96.00% | 0.36 | 0.00% | 0.53 | 2.56 |
| grounded-answer-v2 | 25 | 100.00% | 0.48 | 66.67% | 0.49 | 2.92 |

## grounded-answer-v1

- Dataset version: `sample-docs-v1`
- Retrieval strategy: `weighted-hybrid-v1`
- Cases: 25 (answerable 22, no-answer 3)
- Citation validity: 96.00%
- Citation coverage (mean): 0.36
- Required-fact lexical coverage (mean): 0.53
- No-answer abstention rate: 0.00%
- Unexpected abstention rate: 13.64%
- Mean latency: 3.881s (generation 3.145s)
- Mean tokens: 4367 in / 154 out

- Judge model: `gpt-5.6-terra`; rubric `answer-judge-rubric-v1`
- Mean factual correctness: 3.36
- Mean groundedness: 1.00
- Mean completeness: 3.64
- Mean relevance/concision: 4.16
- Mean uncertainty: 2.52
- Mean overall: 2.56

> Judge scores are uncalibrated model opinions, not ground truth. Calibrate them against human labels before treating them as authoritative.

### By question type

| Slice | Cases | Citation validity | Citation coverage | Judge overall |
| --- | --- | --- | --- | --- |
| comparison | 2 | 100.00% | 0.45 | 3.00 |
| exact_lookup | 4 | 100.00% | 0.25 | 3.25 |
| explanation | 8 | 100.00% | 0.41 | 3.12 |
| fact | 1 | 100.00% | 0.67 | 2.00 |
| follow_up | 3 | 100.00% | 0.47 | 2.00 |
| hard_negative | 2 | 100.00% | 0.00 | 2.50 |
| insufficient_evidence | 3 | 66.67% | 0.00 | 1.33 |
| synthesis | 2 | 100.00% | 0.30 | 1.50 |

### By difficulty

| Slice | Cases | Citation validity | Citation coverage | Judge overall |
| --- | --- | --- | --- | --- |
| easy | 4 | 75.00% | 0.68 | 2.25 |
| hard | 8 | 100.00% | 0.48 | 2.12 |
| medium | 13 | 100.00% | 0.20 | 2.92 |

## grounded-answer-v2

- Dataset version: `sample-docs-v1`
- Retrieval strategy: `weighted-hybrid-v1`
- Cases: 25 (answerable 22, no-answer 3)
- Citation validity: 100.00%
- Citation coverage (mean): 0.48
- Required-fact lexical coverage (mean): 0.49
- No-answer abstention rate: 66.67%
- Unexpected abstention rate: 9.09%
- Mean latency: 3.501s (generation 2.858s)
- Mean tokens: 3447 in / 120 out

- Judge model: `gpt-5.6-terra`; rubric `answer-judge-rubric-v1`
- Mean factual correctness: 3.76
- Mean groundedness: 1.40
- Mean completeness: 3.32
- Mean relevance/concision: 4.52
- Mean uncertainty: 2.80
- Mean overall: 2.92

> Judge scores are uncalibrated model opinions, not ground truth. Calibrate them against human labels before treating them as authoritative.

### By question type

| Slice | Cases | Citation validity | Citation coverage | Judge overall |
| --- | --- | --- | --- | --- |
| comparison | 2 | 100.00% | 0.33 | 2.50 |
| exact_lookup | 4 | 100.00% | 0.50 | 3.25 |
| explanation | 8 | 100.00% | 0.55 | 3.25 |
| fact | 1 | 100.00% | 0.89 | 2.00 |
| follow_up | 3 | 100.00% | 0.72 | 1.67 |
| hard_negative | 2 | 100.00% | 0.00 | 3.00 |
| insufficient_evidence | 3 | 100.00% | 0.00 | 3.33 |
| synthesis | 2 | 100.00% | 0.25 | 3.00 |

### By difficulty

| Slice | Cases | Citation validity | Citation coverage | Judge overall |
| --- | --- | --- | --- | --- |
| easy | 4 | 100.00% | 0.85 | 3.25 |
| hard | 8 | 100.00% | 0.44 | 2.50 |
| medium | 13 | 100.00% | 0.42 | 3.08 |

## Calibration status

`answer-eval-calibrate` against `answer-human-labels.jsonl` is **`not_run`**:
the human-labels file is empty (no fabricated labels are committed). Until a
human reviewer scores a subset and the calibration command reports per-dimension
MAE/bias/correlation, the judge scores above must be read as **uncalibrated
model opinions**. Deterministic metrics (citation validity, coverage, lexical
coverage, abstention, latency, tokens) are model-free and stand as-is; the
lexical coverage column is explicitly a proxy, not factual correctness.

## Honest caveats

- Generation is nondeterministic; per-run numbers vary slightly.
- `grounded-answer-v2` (the configured production default) beats v1 on citation
  validity (100% vs 96%), citation coverage (0.48 vs 0.36), no-answer abstention
  (2/3 vs 0/3), unexpected abstention (9.1% vs 13.6%), and judge overall (2.92
  vs 2.56) at lower latency and tokens — consistent with the earlier 8-case run.
- `synthetic-chunks-v1` results are **biased and superseded** and are not
  presented as evidence.
