# Public-Safe Sample Corpus

This directory contains the public-safe sample corpus used to demonstrate and
benchmark Knowledge Assistant. It is committed because it contains no article
bodies, no private notes, no long excerpts, and no secrets — only:

- source titles and public URLs with provenance;
- human-authored evaluation questions;
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

`manifest.json` also contains eight evaluation cases: seven document-level
questions with reference answers and required facts, and one
insufficient-evidence (no-answer) case that must not receive a confident answer.

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

## Benchmark status

**Run on 2026-08-16** against the active projection
`bd3a3ba7-f427-42f0-91d4-0f0f5f0d3465` (text-embedding-3-small, 1536 dims). All
five strategies were evaluated on all eight cases. Full public-safe results and
breakdowns are in [`benchmark-summary.md`](./benchmark-summary.md); the raw
per-case JSONL is in git-ignored `var/evaluation/`.

Headline: `weighted-hybrid-v1` (the production default) scores Hit@5 1.000 / MRR
0.893 at 0.52s; `vector-only-v1` and `rrf-hybrid-v1` tie it; `lexical-only-v1`
scores 0.000 on natural user questions (paraphrase gap);
`agentic-decomposition-v1` scores MRR 0.821 at 2.60s with one planner call per
question. Applying the pre-registered
[selection policy](../../docs/operations/retrieval-selection-policy.md): **no
default change** — no candidate beats the default on quality, and agentic fails
the latency/cost gates. No-answer false-positive rate is 0.000 for all
strategies.

Any historical `synthetic-chunks-v1` results are **biased and superseded**: v1
generated each question from its own target chunk, inflating lexical overlap and
retrieval scores. They must not be presented as natural-user evidence. v2
datasets and this human-authored sample dataset are the evidence base going
forward.
