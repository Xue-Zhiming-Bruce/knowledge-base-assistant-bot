# Monitoring, Projection Cutover, and Synthetic Evaluation Plan

## Why this document exists

The primary ingestion and grounded-question flows are implemented. This document turns
the remaining monitoring and evaluation architecture into an ordered implementation
plan. It also records and scopes a projection-generation defect that must be corrected
before evaluation results can be trusted across embedding or chunker changes.

This plan refines, but does not replace, the target contracts in
[Storage, Registry, and Derived Data](./05-storage-registry-and-derived-data.md),
[Evaluation Architecture](./08-evaluation-architecture.md), and
[Observability and Operations](./09-observability-and-operations.md).

## Implementation status

The first implementation slice is complete: the OpenTelemetry port and OTLP/HTTP
adapter, optional pinned local LGTM profile, safe building/validated/active projection
lifecycle, deterministic chunk sampler, Structured Outputs question generator,
retrieval evaluation runner, vector and lexical ablations, RRF hybrid, and bounded
agentic decomposition are present in code and covered by the automated suite.

The remaining items in this document are explicit future work: reviewed benchmark
artifacts and measured baselines, answer/citation evaluation, model-judge calibration,
curated dashboards and alerts, operator rollback and garbage-collection commands, and
an automated quality gate before projection activation. Exact executable workflows are
documented in [Retrieval Evaluation and Projection Operations](../operations/evaluation.md).

## Baseline when this plan was written

The current implementation provides:

- deterministic paragraph-based Markdown chunking;
- PostgreSQL chunk text, vector, and full-text projections;
- hybrid semantic and lexical retrieval over the active compatible generation;
- deterministic diversity reranking;
- structured answer generation and citation identifier validation;
- JSON process logs with selected model usage fields;
- durable bot and worker heartbeats and Compose health checks;
- temporary question sessions and versioned pipeline metadata.

It did not yet provide OpenTelemetry traces or metrics export, centralized dashboards
and alerts, a versioned evaluation dataset and runner, or safe full-corpus projection
build-and-switch behavior.

## Chunk persistence contract

Canonical Markdown in the vault remains authoritative. Chunks are rebuildable derived
records in PostgreSQL and must never become the only retained copy of knowledge.

### Chunk construction

`MarkdownChunker` groups Markdown blocks up to a configured character limit while
retaining heading ancestry. For every chunk it produces:

- `chunk_id`;
- zero-based `ordinal`;
- complete chunk `content`;
- SHA-256 `content_fingerprint`;
- `heading_path`;
- approximate `token_count`.

The chunk identifier is deterministic from the document revision ID, chunker version,
ordinal, and content fingerprint. It therefore changes when the canonical revision,
chunker version, ordering, or content changes.

### PostgreSQL representation

Each stored row contains:

| Field | Purpose |
| --- | --- |
| `generation_id` | Projection compatibility and lifecycle boundary |
| `chunk_id` | Stable evidence identity within a generation |
| `document_id`, `revision_id` | Canonical provenance |
| `ordinal` | Order within the revision |
| `content`, `content_fingerprint` | Derived evidence text and integrity check |
| `heading_path`, `citation_anchor` | Structural and citation provenance |
| `token_count` | Context sizing and evaluation sampling |
| `embedding` | Semantic retrieval vector |
| `search_vector` | PostgreSQL full-text retrieval representation |
| `metadata` | Bounded projection metadata extension |

The primary key is `(generation_id, chunk_id)`, permitting the same logical chunk in
multiple projection generations. Re-indexing a revision within the same generation
deletes that revision's existing rows and inserts the complete replacement set in one
transaction.

Normal retrieval considers only rows from the active compatible generation and only
the current revision of each document. Citation labels such as `E1` are assigned at
query time and are not persistent identities. Evaluation must compare `chunk_id`,
`document_id`, and `revision_id` values instead.

## Required projection-generation bug fix

### Original defect

The previous `ensure_projection_generation` implementation retired the existing active
generation and inserts a new active generation as soon as it encounters a new
embedding model, embedding dimension, or chunker version. The worker then stores only
the document associated with the current ingestion job.

Consequently, an incompatible configuration change can make all previously indexed
documents disappear from retrieval until they are independently rebuilt. This
contradicts the accepted build-validate-switch contract and can also make evaluation
results depend on ingestion order.

### Required lifecycle

Projection replacement must use these states and transitions:

```text
create building generation
        -> index every current canonical revision
        -> verify completeness and integrity
        -> run required retrieval evaluation
        -> mark validated
        -> atomically activate candidate and retire previous active generation
        -> retain previous generation for rollback
        -> garbage-collect after the rollback window
```

Interactive retrieval continues using the previous active generation throughout the
build and validation stages. A failed candidate becomes `failed` and never changes the
active generation.

### Implementation responsibilities

- `ProjectionCatalog` owns creation, progress, validation, activation, failure, and
  rollback state transitions.
- A rebuild command enumerates every current canonical revision, chunks and embeds it
  with the candidate manifest, and writes only to the building generation.
- `expected_document_count` is captured at build start; `indexed_document_count` and
  integrity findings are updated as work completes.
- Activation occurs in one database transaction under the projection advisory lock.
- Normal single-document ingestion writes to the active compatible generation only.
  It must not create and activate a partial incompatible generation.
- A configuration requiring a missing compatible generation fails readiness or
  requests an explicit rebuild; it does not silently replace the active generation.

### Acceptance criteria

- Changing the embedding model or chunker creates a building generation while the old
  generation remains queryable.
- A partially built or failed generation never appears in normal retrieval.
- Activation is rejected until every expected current revision is present and the
  generation passes integrity checks.
- Candidate activation and previous-generation retirement are atomic.
- Rollback restores the previous generation without re-embedding.
- Concurrent rebuild and ingestion behavior has an explicit snapshot or catch-up rule
  and is covered by integration tests.
- Retrieval evaluation identifies both the baseline and candidate generation
  explicitly.

## Monitoring implementation

### Selected stack

[ADR-0012](./decisions/ADR-0012-opentelemetry-grafana-observability.md) selects:

- OpenTelemetry Python API and SDK in application processes;
- OTLP/HTTP export;
- an OpenTelemetry Collector gateway;
- Grafana Mimir or another Prometheus-compatible backend for metrics;
- Grafana Tempo for traces;
- Grafana Loki for structured logs;
- Grafana for visualization and alerts;
- the Grafana OpenTelemetry LGTM all-in-one image for local development and tests.

### Application integration

Add an engine-owned `Telemetry` port and two initial adapters:

- a no-op adapter used by deterministic tests and when export is disabled;
- an OpenTelemetry adapter configured once at process startup.

Instrumentation must not contain business logic, modify domain outcomes, or make
telemetry availability part of a successful operation.

Create root spans for:

- one Telegram update;
- one ingestion attempt;
- one notification-delivery attempt;
- one question-answering turn;
- one projection rebuild;
- one evaluation run.

Create child spans for fetch, extraction, normalization, asset materialization, vault
commit, chunking, embedding, index write, query embedding, retrieval, reranking,
context assembly, answer generation, and citation validation.

### Initial metric set

| Metric | Type | Permitted attributes |
| --- | --- | --- |
| `ingestion_jobs_total` | Counter | `outcome`, `source_provider` |
| `ingestion_stage_duration_seconds` | Histogram | `stage`, `source_provider`, `outcome` |
| `ingestion_queue_depth` | Gauge | `state` |
| `ingestion_oldest_queued_seconds` | Gauge | none |
| `questions_total` | Counter | `outcome` |
| `question_stage_duration_seconds` | Histogram | `stage`, `outcome` |
| `retrieval_candidates` | Histogram | `retrieval_version` |
| `citations_total` | Counter | `outcome` |
| `model_input_tokens_total` | Counter | `capability`, `model` |
| `model_output_tokens_total` | Counter | `capability`, `model` |
| `provider_requests_total` | Counter | `provider`, `operation`, `outcome` |
| `provider_request_duration_seconds` | Histogram | `provider`, `operation`, `outcome` |
| `projection_documents` | Gauge | `state` |
| `projection_lag_documents` | Gauge | none |

Metric attributes never contain raw identifiers or content. Trace and log fields must
also exclude raw source URLs, document content, questions, answers, prompts, evidence,
authorization values, and provider credentials.

### Local rollout

1. Add SDK and OTLP exporter dependencies and configuration.
2. Add the optional LGTM Compose profile and send traces and metrics to its OTLP/HTTP
   endpoint.
3. Instrument Question Mode end to end and test success, insufficient evidence, model
   failure, and citation failure.
4. Instrument ingestion and notification stages.
5. Enrich JSON logs with `event`, `trace_id`, `span_id`, identifiers permitted for
   restricted logs, and structured error classification.
6. Add dashboards only after emitted names and attributes are stable.
7. Establish alert thresholds from measured baselines rather than guesses.

## Synthetic chunk-question evaluation

### Purpose and limits

Synthetic evaluation expands coverage cheaply by selecting stored chunks, asking a
model to create questions answerable from them, and sending those questions through
the normal RAG pipeline. It is useful for retrieval and grounded-answer regressions,
but it may overrepresent questions that resemble model-generated text. A smaller
human-authored set remains necessary for natural user intent, multi-document
synthesis, insufficient-evidence cases, and adversarial behavior.

Question generation, retrieval measurement, and answer judging are separate stages.
The question-generation model sees the selected source chunk. The RAG system receives
only the generated question. Evaluation judges receive only the fields required by
their rubric.

### Candidate sampling

Sample only chunks that:

- belong to the explicitly selected active or candidate generation;
- belong to the current canonical revision for their document;
- have a useful content size, initially 80 to 500 approximate tokens;
- contain enough declarative information to support a standalone question;
- satisfy the dataset's privacy and retention policy.

Sampling is reproducible. Order candidates by a stable hash of `chunk_id` and a
dataset seed rather than by PostgreSQL `random()`. Limit chunks per document and
stratify by source provider, document length, and available language so one source
does not dominate.

### Generated case schema

The generator returns structured data containing:

```json
{
  "case_id": "synthetic-v1-0001",
  "dataset_version": "synthetic-chunks-v1",
  "target_chunk_id": "chk_...",
  "target_document_id": "doc_...",
  "target_revision_id": "rev_...",
  "content_fingerprint": "sha256:...",
  "question": "...",
  "reference_answer": "...",
  "required_facts": ["..."],
  "supporting_excerpt": "...",
  "acceptable_chunk_ids": ["chk_..."],
  "slice": {
    "source_provider": "substack",
    "question_type": "fact",
    "difficulty": "medium"
  },
  "generator_model": "...",
  "generator_prompt_version": "synthetic-question-v1"
}
```

The generator must reject questions that refer to "the passage" or "the article",
are answerable without the selected evidence, require missing context, are ambiguous,
or merely copy a distinctive sentence into question form.

`acceptable_chunk_ids` initially contains the target chunk. A review step may add
adjacent or duplicate chunks that independently support the reference answer. Exact
target-chunk matching alone is too strict when the same fact appears elsewhere.

### Dataset persistence

Generated cases are written to versioned JSON Lines files and frozen after review.
They are not regenerated for every candidate run. Each run records the dataset
version and validates that referenced chunk fingerprints still match.

Personal source content and supporting excerpts must not be committed to a public
repository. A private evaluation corpus may store immutable source snapshots; an
alternative dataset may retain only identities and fingerprints and resolve content
from an access-controlled vault/database. The latter runner fails closed when the
referenced revision is unavailable or changed.

### Retrieval evaluation

Run query embedding, candidate retrieval, and reranking without answer generation.
Record:

- target and acceptable chunk rank;
- hit and recall at 5 and 20;
- mean reciprocal rank;
- document recall;
- retrieval and reranking latency;
- embedding tokens and candidate counts.

Compare persistent `chunk_id` values, never query-local citation labels. Report both
overall results and source, question-type, length, language, and difficulty slices.

### Answer and citation evaluation

Pass the same question through the normal answer pipeline and record:

- required-fact coverage;
- correctness relative to the reference answer and retrieved evidence;
- groundedness and unsupported claims;
- sufficient-evidence disposition;
- citation validity, correctness, completeness, and precision;
- answer latency and model token usage.

Begin with deterministic citation invariants and reviewed required-fact checks. Add a
model judge only after its fixed rubric has been calibrated against a human-scored
sample. The judge model, prompt, rubric, raw structured result, and disagreement with
human labels are versioned.

### Initial gates

- No unknown or unresolvable citations.
- No evaluation case references a missing or fingerprint-mismatched chunk.
- Candidate retrieval metrics are compared pairwise with the current baseline on the
  same frozen dataset.
- Slice regressions are visible and cannot be hidden by one aggregate score.
- Latency and token changes are reported with quality changes.
- Absolute quality thresholds are set only after a reviewed baseline exists.

## RAG strategy experiment program

### Baseline classification

The implemented question path is single-pass weighted hybrid RAG. It performs one
query embedding, evaluates semantic and PostgreSQL full-text relevance, combines raw
scores as `0.75 * semantic + 0.25 * lexical`, retrieves 20 candidates, and applies a
deterministic document-diversity reranker before bounded context assembly.

It is not agentic. It does not rewrite or decompose the query, inspect evidence gaps,
or perform a second retrieval round. Query-local citation labels are assigned only
after retrieval and are not evaluation identities.

### Experiment matrix

Implement each method behind the versioned `RetrievalStrategy` contract:

| Version family | Variable under test | Additional model calls |
| --- | --- | --- |
| `vector-only-v1` | Semantic retrieval alone | none |
| `lexical-only-v1` | PostgreSQL full-text retrieval alone | none |
| `weighted-hybrid-v1` | Current raw-score fusion baseline | none |
| `rrf-hybrid-v1` | Rank-based vector and lexical fusion | none |
| `agentic-decomposition-v1` | Bounded query planning and iterative retrieval | planner calls only |

RRF produces independent vector and lexical rankings and calculates:

```text
rrf_score(chunk) = sum(1 / (k + rank_in_each_result_list))
```

The RRF constant, per-retriever candidate depth, union/deduplication behavior, and tie
breaking are versioned. The first experiment sweeps a small predeclared configuration
set on a development split, then evaluates one selected configuration once on the
held-out test split.

The bounded agentic strategy classifies the question as simple or complex. Simple
questions use one hybrid retrieval. Complex questions may generate at most three
subqueries, retrieve each through the selected hybrid method, merge and rerank the
evidence, inspect required evidence coverage, and perform at most one refined round.
It then answers or returns insufficient evidence under fixed context, token, latency,
cost, and model-call budgets.

### Evaluation case extensions

Single-chunk synthetic cases remain the direct-fact dataset. Add evidence-set cases
with this compatible extension:

```json
{
  "case_id": "multi-hop-v1-0001",
  "question": "...",
  "reference_answer": "...",
  "required_facts": ["fact-a", "fact-b"],
  "required_evidence_groups": [
    {
      "group_id": "fact-a",
      "acceptable_chunk_ids": ["chk_a", "chk_a_neighbor"]
    },
    {
      "group_id": "fact-b",
      "acceptable_chunk_ids": ["chk_b"]
    }
  ],
  "coverage_rule": "all_groups",
  "slice": {
    "question_type": "cross_document_synthesis",
    "difficulty": "hard"
  }
}
```

Construct and review separate slices for single-chunk facts, same-document multi-chunk
questions, cross-document synthesis, exact lexical lookup, semantic paraphrase,
ambiguity, insufficient evidence, false premises, and prompt injection in retrieved
content. Generate multi-hop questions from a selected set of chunks rather than asking
the generator to invent relationships without source evidence.

### Fair-comparison protocol

For a paired comparison, pin the corpus snapshot, complete projection generation,
dataset, embedding model, answer model and prompt, final context limit, citation
validator, provider policy, and runtime environment. Change only the retrieval
strategy and strategy-owned limits.

Persist one structured result per case containing:

- strategy and configuration version;
- ordered candidates and final evidence chunk IDs;
- vector, lexical, or fused ranks where applicable;
- route, subqueries, retrieval rounds, evidence coverage, and stop reason;
- retrieval, reranking, planning, generation, and total latency;
- model calls, input/output tokens, and attributed cost;
- required-evidence coverage and retrieval metrics;
- answer, grounding, sufficiency, and citation scores;
- raw structured judge output and judge version when a model judge is used.

### Decision rules

- Run vector-only and lexical-only ablations before changing fusion.
- Compare weighted hybrid and RRF before adding a planner; this distinguishes fusion
  gains from planning gains.
- Promote RRF only when paired quality is non-regressing and operational budgets pass.
- Route simple questions away from agentic planning unless evaluation shows a specific
  benefit.
- Promote agentic retrieval only on slices where its quality gain justifies added
  calls, latency, cost, nondeterminism, and failure modes.
- A higher aggregate judge score cannot override citation, privacy, authorization, or
  prompt-injection failures.
- Keep the current strategy available for immediate rollback.

## Implementation sequence

1. Correct projection building and atomic activation.
2. Add a read-only evaluation-corpus port for deterministic chunk sampling and lookup.
3. Add the OpenTelemetry port, local LGTM profile, and Question Mode instrumentation.
4. Generate, review, and freeze `synthetic-chunks-v1`.
5. Implement the `RetrievalStrategy` boundary, vector-only and lexical-only ablations,
   and immutable retrieval result artifacts.
6. Record the current weighted hybrid baseline and implement RRF hybrid.
7. Add and review multi-chunk, multi-document, ambiguous, negative, and adversarial
   evaluation slices.
8. Implement the bounded agentic strategy and its budgets, stop reasons, and traces.
9. Instrument ingestion, notifications, and projection rebuilds.
10. Implement deterministic answer and citation evaluation.
11. Calibrate and add the optional model judge.
12. Establish dashboards, baselines, regression gates, and actionable alerts.

The projection fix precedes the first trusted baseline because a partial active
generation would invalidate both random sampling and retrieval measurements.
