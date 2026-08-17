# Retrieval Evaluation and Projection Operations

## Scope

This runbook covers the implemented chunk persistence, reproducible synthetic
dataset workflow, retrieval-strategy comparison, end-to-end answer evaluation, and
safe projection cutover. The retrieval runner measures retrieval quality; the answer
runner scores generated answers deterministically and, optionally, with a structured
LLM judge.

## How chunks are saved

Canonical Markdown in the Obsidian vault is authoritative. After a document is
committed, `MarkdownChunker` deterministically groups Markdown blocks while preserving
heading ancestry. Each derived PostgreSQL `chunks` row stores:

- a projection `generation_id` and deterministic `chunk_id`;
- canonical `document_id` and `revision_id` provenance;
- ordinal, full chunk text, SHA-256 content fingerprint, heading path, and token count;
- a JSON citation anchor;
- the dense embedding vector and PostgreSQL `tsvector` used for retrieval.

The primary key is `(generation_id, chunk_id)`. Retrieval only uses chunks belonging
to the selected projection generation and the current canonical document revision.
Changing canonical content, chunk order, the chunker, or the revision changes the
derived identity; evaluation therefore validates both chunk ID and fingerprint before
running.

## Implemented retrieval strategies

| Strategy | Behavior | Extra generation calls |
| --- | --- | --- |
| `vector-only-v1` | Semantic rank only | None |
| `lexical-only-v1` | PostgreSQL full-text rank only | None |
| `weighted-hybrid-v1` | `0.75 * semantic + 0.25 * lexical` | None |
| `rrf-hybrid-v1` | Independent vector and lexical rankings fused with RRF using `k=60` | None |
| `agentic-decomposition-v1` | One planner route; up to three RRF subqueries, fusion, and diversity reranking | One planner call per question |

The lexical leg builds a disjunctive query of the question's distinctive content
terms (stopwords and single-character tokens removed via
`build_lexical_tsquery`) and scores matches with PostgreSQL `ts_rank_cd` — it is
never called BM25. Natural questions paraphrase article vocabulary, so OR
semantics are used instead of AND-ing every term (`websearch_to_tsquery`), which
previously returned zero hits for paraphrased questions.

`weighted-hybrid-v1` is the live default. Agentic retrieval is deliberately bounded:
it does not execute tools, mutate the vault, or retrieve indefinitely. A simple route
uses the original question; a complex route uses two or three standalone subqueries.

## Generate a private dataset

Prerequisites:

- PostgreSQL contains an active projection with eligible chunks;
- `.env` contains `OPENAI_API_KEY`, `KNOWLEDGE_ASSISTANT_GENERATION_MODEL`, and
  `KNOWLEDGE_ASSISTANT_EMBEDDING_MODEL`;
- the admin image is current.

Generate 20 cases:

```shell
docker compose --profile tools run --rm admin \
  eval-generate \
  --count 20 \
  --seed synthetic-chunks-v2 \
  --output /data/evaluation/synthetic-chunks-v2.jsonl
```

The default generator is `synthetic-question-v2` and the dataset schema version is
`synthetic-chunks-v2`. `--version synthetic-chunks-v1` reproduces the legacy v1
workflow for existing datasets. `--style-weights` accepts a JSON object such as
`{"fact":0.4,"explanation":0.3,"comparison":0.2,"exact_lookup":0.1}` to bias the
deterministic per-chunk question-style selection, and `--no-naturalize` disables the
optional source-blind naturalization pass.

Sampling is stable for a `(seed, chunk_id)` pair, only selects current-revision chunks
between 80 and 500 approximate tokens, and caps selection at two chunks per document.
The generator uses Structured Outputs and must return a standalone question, reference
answer, required facts, a supporting excerpt that occurs verbatim in the chunk, and an
estimated `supporting_chunk_count`.

### v2 question generation and remaining synthetic-data bias

v2 writes questions as a real user would ask them: conversational wording, the user's
underlying goal rather than an exact factual lookup, and no mention of the source. An
optional second pass rewrites the generated question into natural user language through
a source-blind naturalizer; the rewrite is re-validated against the source
deterministically, and if it fails or introduces source-oriented wording the original
question is kept and the naturalizer version is not recorded.

Because the question is still generated from the target chunk and must remain answerable
from it, the benchmark retains a residual bias: generated questions share topic
vocabulary with the target chunk and never exercise genuine retrieval failure modes the
way human questions do. Treat v2 scores as an upper-bound estimate of retrieval quality,
never as natural user behavior. Legacy `synthetic-chunks-v1` datasets remain loadable
and comparable, but their circular single-chunk construction (question generated from
the target chunk) is documented as biased and superseded by v2 for new datasets.

Deterministic controls applied before a question is accepted:

- forbidden source-oriented wording (for example "the passage", "the article",
  "according to the") is rejected;
- questions that copy a contiguous distinctive phrase of at least eight tokens from
  the source are rejected and regenerated with the rejection reason fed back;
- lexical overlap between the question and the source chunk is measured and capped
  (default maximum 0.9);
- the supporting excerpt must occur verbatim in the source chunk.

Every v2 case records the generator prompt version, the optional naturalizer prompt
version and model, the selected question style, and measurable difficulty properties
(lexical overlap ratio, required fact count, supporting chunk count, and whether the
question is estimated to require query decomposition). Difficulty is therefore not
solely model-assigned.

The evaluation schema also supports no-answer cases (`no_answer` with optional
`distractor_chunk_ids` and no target chunk) for future human-authored
insufficient-evidence questions. The runner excludes no-answer cases from Hit@K and
MRR aggregates and reports them through `no_answer_cases` and
`no_answer_false_positive_rate` instead of forcing them into target-chunk rankings.

The file is persisted at `var/evaluation/synthetic-chunks-v2.jsonl` on the host. That
directory is git-ignored. The file contains private source excerpts: do not commit it,
upload it to a public service, or use it outside the source material's retention policy.

Review the cases before treating them as a frozen dataset. Reject ambiguous or
context-dependent questions and add genuinely equivalent chunk IDs to
`acceptable_chunk_ids` when the same answer appears in more than one chunk. Keep the
reviewed file unchanged for paired comparisons.

## Compare retrieval methods

Run every strategy against the same frozen cases:

```shell
docker compose --profile tools run --rm admin \
  eval-run \
  --dataset /data/evaluation/synthetic-chunks-v1.jsonl \
  --output /data/evaluation/retrieval-results.jsonl \
  --strategy all
```

Use one strategy value instead of `all` for a focused run. Use
`--generation-id UUID` to evaluate a specific building, validated, active, or retired
projection instead of the active generation.

One JSONL result is written per `(case, strategy)` with target rank, hit@5, hit@20,
reciprocal rank, ordered retrieved chunk IDs, route, subqueries, retrieval rounds, stop
reason, latency, planner calls and tokens, and the case type and difficulty. Aggregate
hit@5, hit@20, MRR, mean latency, mean planner calls/tokens, question-type and
difficulty breakdowns, and no-answer metrics are printed to standard output.
No-answer cases are excluded from the Hit@K and MRR aggregates and are reported through
`no_answer_cases` and `no_answer_false_positive_rate`. A run fails closed when a target
chunk is missing or its fingerprint changed, or when a document-level target has not
been ingested.

## Public sample corpus and document-level evaluation

The committed `data/sample/manifest.json` provides a public-safe, reproducible corpus:
four public Substack essays (titles and URLs only, no bodies) plus **25
curated questions** with reference answers and required facts — including
two multi-document synthesis, three follow-up-style, two hard-negative, and
**three insufficient-evidence cases** (no-answer cases are excluded from Hit@K
and MRR and reported through explicit abstention and false-positive metrics).
Ingest it, build the document-level dataset, and run the full benchmark:

```shell
docker compose --profile tools run --rm admin \
  sample-ingest --manifest /data/sample/manifest.json

docker compose --profile tools run --rm admin \
  sample-eval-prepare --manifest /data/sample/manifest.json \
  --output /data/evaluation/sample-docs-v1.jsonl

docker compose --profile tools run --rm admin \
  eval-run \
  --dataset /data/evaluation/sample-docs-v1.jsonl \
  --output /data/evaluation/sample-retrieval-results.jsonl \
  --strategy all
```

`sample-eval-prepare` converts manifest cases into document-level evaluation cases
(`document_level=true` with a `target_url`): retrieval succeeds when any chunk of the
right document is retrieved, which suits natural questions that do not map to a single
chunk. The runner resolves the URL to the ingested document and scores it against that
document's chunks in the selected generation. Real results for this dataset are
committed in `data/sample/benchmark-summary.md`; applying the pre-registered
[selection policy](./retrieval-selection-policy.md), the production default
`weighted-hybrid-v1` is unchanged. When PostgreSQL or API credentials are unavailable,
`eval-run` reports a verified `{"status": "not_run", "reason": ...}` result and never
fabricates numbers. Historical `synthetic-chunks-v1` scores are biased (circular
chunk-to-question generation) and superseded; they must not be reported as natural-user
evidence.

Cost depends on the configured models and dataset size. Dataset generation makes one
generation call per case. Retrieval evaluation makes one embedding request per
question and strategy; the agentic strategy also makes one planner call and may embed
up to three subqueries. Review a small dataset before scaling up.

## End-to-end answer evaluation

The answer runner exercises the real RAG path for every case: question, retrieval,
reranking, bounded context assembly, structured answer generation, deterministic
citation validation, and answer evaluation. It compares two answer approaches:

- `grounded-answer-v1` — the explicit baseline override with a 16,000-character
  context budget (2,400 per item);
- `grounded-answer-v2` — the production default (validated setting
  `KNOWLEDGE_ASSISTANT_ANSWER_PROMPT_VERSION`, used by Telegram Question Mode
  and `demo ask`): stricter per-sentence citation markers, explicit abstention
  language, and a tighter 12,000-character context budget (1,600 per item).

Run both approaches against a frozen dataset:

```shell
docker compose --profile tools run --rm admin \
  answer-eval-run \
  --dataset /data/evaluation/synthetic-chunks-v2.jsonl \
  --output /data/evaluation/answer-results.jsonl \
  --output-markdown /data/evaluation/answer-summary.md \
  --strategy weighted-hybrid-v1 \
  --approaches all
```

`--judge-model <model>` additionally scores every answer with a structured LLM judge
under a fixed versioned rubric (`answer-judge-rubric-v1`). Judge input is bounded
(question, answer, reference answer, required facts, and the supporting excerpt are
truncated) and the judge model and prompt versions are stored with every result.

`answer-eval-run` always compares `grounded-answer-v1` and `grounded-answer-v2`
regardless of the configured production default; the configured version affects
only Telegram Question Mode and `demo ask`.

**Judge calibration against human labels.** Judge scores are model opinions, not
ground truth. Reviewed human labels belong in
`data/sample/answer-human-labels.jsonl` (schema in
[data/sample/README.md](../../data/sample/README.md)); the file is committed empty
until a human scores a subset. Once labels exist, compare them against the real
judge scores:

```shell
docker compose --profile tools run --rm admin \
  answer-eval-calibrate \
  --results /data/evaluation/answer-results.jsonl \
  --human-labels /data/sample/answer-human-labels.jsonl \
  --output-markdown /data/evaluation/judge-calibration.md
```

It reports per-dimension mean absolute error, bias (judge minus human), and
Pearson correlation on the matched `(case, approach)` subset. With no human
labels it reports `{"status": "not_run", ...}`: calibration is `not_run` and
every judge score remains an uncalibrated model opinion. Deterministic metrics,
human labels, model-judge scores, and uncalibrated opinions are kept strictly
separate in the committed summaries; lexical coverage is explicitly a proxy,
never semantic factual correctness.

Deterministic checks recorded per (case, approach) without any LLM:

- citation identifier validity — every declared or inline marker resolves to the
  retrieved evidence for the selected projection;
- citation coverage — fraction of material sentences carrying citation markers;
- required-fact lexical coverage — mean fraction of non-stopword required-fact tokens
  present in the answer (a proxy; the judge provides the authoritative per-fact
  support verdict);
- insufficient-evidence behavior — no-answer cases must abstain
  (`sufficient_evidence=false`), and answerable cases that abstain unexpectedly are
  flagged;
- latency and input/output token usage.

The private `--output` JSONL contains full questions, answers, and judge verdicts for
audit and is written below git-ignored `var/evaluation/`. The `--output-markdown`
file is public-safe: it contains aggregates and per-slice breakdowns (by question
type and difficulty) only, never questions, answers, prompts, or source content, and
it is suitable to copy into the repository README.

Judge scores are uncalibrated model opinions, not ground truth: calibrate them
against human labels before treating them as authoritative. Every run stores the
judge model, prompt version, and rubric version so scores can be audited and
reproduced.

## Rebuild and activate a projection safely

An incompatible embedding model, dimension, or chunker no longer retires the active
generation during normal ingestion. It creates or identifies a building candidate and
fails with an explicit rebuild-required error while the existing projection remains
queryable.

Build every current canonical revision and validate completeness:

```shell
docker compose --profile tools run --rm admin projection-rebuild
```

The command returns a JSON object containing the validated `generation_id`. If a build
fails after the candidate is created, that candidate is marked `failed` and cannot
become active.

Where the frozen dataset's chunk identities are compatible, evaluate the candidate:

```shell
docker compose --profile tools run --rm admin \
  eval-run \
  --dataset /data/evaluation/synthetic-chunks-v1.jsonl \
  --output /data/evaluation/candidate-results.jsonl \
  --strategy all \
  --generation-id GENERATION_ID
```

Then atomically activate the already-validated generation:

```shell
docker compose --profile tools run --rm admin \
  projection-activate GENERATION_ID
```

Activation retires the previous active generation in the same database transaction.
Keep `KNOWLEDGE_ASSISTANT_RETRIEVAL_STRATEGY=weighted-hybrid-v1` until the paired
evaluation supports a strategy change.

The retired rows are retained, but an operator-facing rollback command and automated
evaluation gate are not implemented yet. Do not delete a retired generation until a
rollback procedure and retention window are established.

For a first deployment or an intentionally combined build-and-cutover operation,
`projection-rebuild --activate` remains available. It should not be used when a
candidate must be evaluated before cutover.
