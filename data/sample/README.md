# Public-Safe Sample Corpus

This directory contains the public-safe sample corpus used to demonstrate and
benchmark Knowledge Assistant. It is committed because it contains no article
bodies, no private notes, no long excerpts, and no secrets — only:

- source titles and public URLs with provenance;
- curated evaluation questions (generator provenance was not recorded);
- concise reference answers and required facts written in original wording.

The four sources are public Substack essays about software engineering craft,
careers, and AI-assisted development:

| Source | Author | URL |
| --- | --- | --- |
| 21 Lessons from 14 Years at Google | Addy Osmani | https://addyo.substack.com/p/21-lessons-from-14-years-at-google |
| Software Factories, Light and Dark | Addy Osmani | https://addyo.substack.com/p/software-factories-light-and-dark |
| Is Source Code Going Away? | Kent Beck | https://tidyfirst.substack.com/p/is-source-code-going-away |
| The Pinhole View of AI Value | Kent Beck | https://tidyfirst.substack.com/p/the-pinhole-view-of-ai-value |

All four URLs were verified publicly fetchable in August 2026. Live pages can
change or disappear; if a fetch fails, ingestion reports a real error for that
source and the manifest can be updated.

`manifest.json` also contains **25 evaluation cases**: 22 answerable questions
with reference answers and required facts, and **3 insufficient-evidence
(no-answer) cases** that must not receive a confident answer (their reference
answers state that the knowledge base does not contain the information, and two
of them point at a distractor document with overlapping terminology so the
retrieval false-positive metric is exercised). The mix is deliberate:

- single-document fact and explanation questions;
- exact lookups (analogy, term origin, four NPV levers, rule of thumb);
- within-document comparisons (light vs dark factory, essay vs 20VC thesis);
- multi-document synthesis (`synthesis`: both Osmani essays on human attention;
  both Kent Beck essays on AI shifting value from artifact to outcome);
- follow-up-style user questions (`follow_up`);
- hard negatives with overlapping terminology (`hard_negative`);
- three `insufficient_evidence` (no-answer) cases.

Question-type and difficulty breakdowns are reported in the benchmark
summaries; no-answer cases are excluded from Hit@K and MRR and are instead
reported through explicit abstention and false-positive metrics.

**Copyright and provenance note:** the evaluation questions, reference answers,
and required facts in `manifest.json` are **original concise paraphrases written
for evaluation** — they restate the articles' factual content in new wording and
were checked against the articles for verbatim passages. The four source articles
are public; this repository commits only their titles and URLs, never their
bodies or excerpts. Reviewers may freely fetch the articles to verify the
reference answers.

## Ingest the sample corpus

```shell
docker compose --profile tools run --rm admin \
  sample-ingest --manifest /data/sample/manifest.json
```

`sample-ingest` classifies each URL and submits an idempotent ingestion job
(keyed `sample:<source_id>`) through the normal ingestion contract. Completion
notifications go to `--recipient <numeric chat id>` (default: the first
allowlisted user id). The running worker fetches each article, writes canonical
Markdown into the vault, and builds the rebuildable search projection. It
requires PostgreSQL but no API credentials for submission; the worker needs
OpenAI embedding credentials to complete the projection.

## Build the document-level evaluation dataset

```shell
docker compose --profile tools run --rm admin \
  sample-eval-prepare --manifest /data/sample/manifest.json \
  --output /data/evaluation/sample-docs-v1.jsonl
```

This writes a `synthetic-chunks-` compatible JSONL dataset whose cases target
whole documents (`document_level=true`) instead of single chunks, so retrieval
succeeds when any chunk of the right document is retrieved.

## Run the retrieval benchmark across all five strategies

```shell
docker compose --profile tools run --rm admin \
  eval-run \
  --dataset /data/evaluation/sample-docs-v1.jsonl \
  --output /data/evaluation/sample-retrieval-results.jsonl \
  --strategy all
```

The benchmark covers `vector-only-v1`, `lexical-only-v1`, `weighted-hybrid-v1`,
`rrf-hybrid-v1`, and `agentic-decomposition-v1` and reports Hit@5, Hit@20, MRR,
latency, planner calls and tokens, question-type and difficulty breakdowns, and
no-answer false-positive behavior.

## Human labels for judge calibration

`answer-human-labels.jsonl` holds reviewed **human labels** used to calibrate
the structured LLM judge. It is intentionally **empty until a human reviewer
scores a subset**; no fabricated labels are committed. Each row follows the
fixed schema (one row per reviewed `(case_id, approach)`):

```json
{"case_id": "sample-q-01", "approach": "grounded-answer-v2",
 "factual_correctness": 4, "groundedness": 3, "completeness": 4,
 "relevance_concision": 4, "uncertainty": 3, "overall": 4}
```

Scores are integers 0-5 on the same rubric as `answer-judge-rubric-v1`.
`case_id`/`approach` values come from the `answer-results.jsonl` produced by
`answer-eval-run`. To calibrate:

1. Review a subset of `var/evaluation/answer-results.jsonl` (read the question,
   the answer, and the supporting evidence there) and score each dimension by
   hand.
2. Append one JSONL row per reviewed case to `answer-human-labels.jsonl`.
3. Run:

```shell
docker compose --profile tools run --rm admin \
  answer-eval-calibrate \
  --results /data/evaluation/answer-results.jsonl \
  --human-labels /data/sample/answer-human-labels.jsonl \
  --output-markdown /data/evaluation/judge-calibration.md
```

The command reports per-dimension mean absolute error, bias (judge minus
human), and Pearson correlation for the matched subset. Until human labels
exist it reports `{"status": "not_run", ...}` and every judge score remains an
**uncalibrated model opinion** — never ground truth, and never conflated with
deterministic metrics or human labels.

## Benchmark status

**Run on 2026-08-17** against the active projection
`bd3a3ba7-f427-42f0-91d4-0f0f5f0d3465` (text-embedding-3-small, 1536 dims). All
five strategies were evaluated on all **25 cases** (real rerun after the dataset
was expanded from 8 to 25 questions; 3 no-answer cases excluded from Hit@K/MRR
and reported through the no-answer false-positive metric instead). Full
public-safe results and breakdowns are in
[`benchmark-summary.md`](./benchmark-summary.md); the raw per-case JSONL is in
git-ignored `var/evaluation/`.

Headline: `weighted-hybrid-v1` (the production default) scores Hit@5 0.909 / MRR
0.814 at 0.47s; `vector-only-v1` reaches Hit@5 1.000 and MRR 0.871 at 0.61s
(higher MRR than both the default and `rrf-hybrid-v1` 0.848);
`agentic-decomposition-v1` scores the best MRR (0.873) and Hit@5 (0.955) but at
2.45s with one planner call per question; `lexical-only-v1` drops to Hit@5 0.773
on the expanded natural-user set. Applying the pre-registered
[selection policy](../../docs/operations/retrieval-selection-policy.md): **no
default change** — no candidate satisfies all four gates. `vector-only-v1` clears
aggregate quality (+0.091 Hit@5, +0.057 MRR), latency (1.31x), cost, and
operational complexity, and improves no-answer false positives (0.333 vs 0.667),
but it regresses the exact_lookup slice MRR from 1.000 to 0.833 (−0.167, beyond
the 0.01 per-slice tolerance; driven by `sample-q-09` ranking position 3 vs 1),
so it fails the pre-registered per-slice gate. `rrf-hybrid-v1` ties Hit@5
(0.909) and `agentic-decomposition-v1` fails latency (≈5.2x). No-answer
false-positive rates (the fraction of the 3 no-answer cases where retrieval
surfaced a distractor
document) are reported per strategy (0.333-0.667); the answer layer must abstain
on those.

Any historical `synthetic-chunks-v1` results are **biased and superseded**: v1
generated each question from its own target chunk, inflating lexical overlap and
retrieval scores. They must not be presented as natural-user evidence. v2
datasets and this curated sample dataset are the evidence base going
forward.
