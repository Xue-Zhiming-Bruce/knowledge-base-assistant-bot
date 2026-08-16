# Answer Evaluation Summary

This summary is public-safe: it contains aggregates only and never questions,
answers, prompts, or source content.

Dataset: `sample-docs-v1` (8 human-authored document-level cases from
`data/sample/manifest.json`). Retrieval strategy: `weighted-hybrid-v1` on the
active projection `bd3a3ba7-f427-42f0-91d4-0f0f5f0d3465`. Both answer approaches
ran through the real RAG path (retrieve, rerank, bounded context, structured
answer generation, deterministic citation validation). Run 2026-08-16
(post-public-safety reword of required facts and reference answers) with:

```shell
knowledge-assistant answer-eval-run \
  --dataset var/evaluation/sample-docs-v1.jsonl \
  --output var/evaluation/answer-results.jsonl \
  --output-markdown data/sample/answer-benchmark-summary.md \
  --strategy weighted-hybrid-v1 --approaches all
```

No LLM judge was applied in this run (`--judge-model` omitted); all numbers are
deterministic metrics. Generation is nondeterministic, so per-run numbers vary
slightly; this table reflects the recorded run.

**Validation mode:** the sample dataset is document-level (no target chunks), so
targets were validated by resolved document URL, and citation validity means
every cited identifier resolves to retrieved evidence, not target-document
membership. The runner's primary path is chunk-level fingerprint validation for
cases that carry a target chunk.

Metric definitions (deterministic): **Citation validity** = fraction of
(case, approach) runs whose citations passed the deterministic validator;
**Citation coverage** = mean fraction of material answer sentences carrying a
citation marker; **No-answer abstention** = fraction of insufficient-evidence
cases in which the answer abstained (`sufficient_evidence=false`); **Fact
lexical coverage** = mean fraction of non-stopword required-fact tokens present
in the answer (a lexical proxy, not a semantic check).

## Approach comparison

| Approach | Cases | Citation validity | Citation coverage | No-answer abstention | Fact lexical coverage | Judge overall |
| --- | --- | --- | --- | --- | --- | --- |
| grounded-answer-v1 | 8 | 87.50% | 0.27 | 0.00% | 0.50 | n/a |
| grounded-answer-v2 | 8 | 100.00% | 0.60 | 100.00% | 0.54 | n/a |

## grounded-answer-v1

- Dataset version: `sample-docs-v1`
- Retrieval strategy: `weighted-hybrid-v1`
- Cases: 8 (answerable 7, no-answer 1)
- Citation validity: 87.50%
- Citation coverage (mean): 0.27
- Required-fact lexical coverage (mean): 0.50
- No-answer abstention rate: 0.00%
- Unexpected abstention rate: 14.29%
- Mean latency: 7.968s (generation 4.020s)
- Mean tokens: 4400 in / 176 out

### By question type

| Slice | Cases | Citation validity | Citation coverage | Judge overall |
| --- | --- | --- | --- | --- |
| comparison | 1 | 100.00% | 0.17 | n/a |
| exact_lookup | 1 | 100.00% | 0.00 | n/a |
| explanation | 4 | 100.00% | 0.24 | n/a |
| fact | 1 | 100.00% | 0.78 | n/a |
| insufficient_evidence | 1 | 0.00% | 0.00 | n/a |

### By difficulty

| Slice | Cases | Citation validity | Citation coverage | Judge overall |
| --- | --- | --- | --- | --- |
| easy | 3 | 66.67% | 0.49 | n/a |
| hard | 3 | 100.00% | 0.20 | n/a |
| medium | 2 | 100.00% | 0.17 | n/a |

## grounded-answer-v2

- Dataset version: `sample-docs-v1`
- Retrieval strategy: `weighted-hybrid-v1`
- Cases: 8 (answerable 7, no-answer 1)
- Citation validity: 100.00%
- Citation coverage (mean): 0.60
- Required-fact lexical coverage (mean): 0.54
- No-answer abstention rate: 100.00%
- Unexpected abstention rate: 0.00%
- Mean latency: 6.001s (generation 5.067s)
- Mean tokens: 3480 in / 164 out

### By question type

| Slice | Cases | Citation validity | Citation coverage | Judge overall |
| --- | --- | --- | --- | --- |
| comparison | 1 | 100.00% | 0.67 | n/a |
| exact_lookup | 1 | 100.00% | 0.00 | n/a |
| explanation | 4 | 100.00% | 0.67 | n/a |
| fact | 1 | 100.00% | 0.89 | n/a |
| insufficient_evidence | 1 | 100.00% | 0.00 | n/a |

### By difficulty

| Slice | Cases | Citation validity | Citation coverage | Judge overall |
| --- | --- | --- | --- | --- |
| easy | 3 | 100.00% | 0.82 | n/a |
| hard | 3 | 100.00% | 0.64 | n/a |
| medium | 2 | 100.00% | 0.33 | n/a |
