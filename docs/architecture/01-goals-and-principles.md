# Goals and Architecture Principles

## Why this document exists

This document defines what the architecture optimizes for and the constraints that guide trade-offs. It is the reference for resolving ambiguity when several technically valid designs are available.

## Product goals

Knowledge Assistant should:

1. Preserve useful long-form knowledge in a durable, human-readable personal archive.
2. Make that archive meaningfully queryable through grounded answers and precise citations.
3. Keep the Knowledge Engine reusable across Telegram, web, desktop, REST, and CLI clients.
4. Make new source types and infrastructure providers addable without rewriting the core.
5. Support production operation with measurable quality, reliability, privacy, and cost.

## Initial scope

The first release supports:

- ingestion of public Substack and Medium article URLs;
- ingestion of public rich X Articles through strict Xquik ordered blocks;
- normalization into Markdown with YAML frontmatter;
- storage in an Obsidian vault;
- asynchronous processing and completion notification;
- question sessions initiated with `/answer` and ended with `/end`;
- retrieval-augmented answers with citations;
- temporary conversational context that is discarded at session end.

The architecture prepares for, but does not initially implement:

- podcasts and audio transcription;
- PDF extraction and OCR;
- YouTube transcripts;
- arbitrary long-form web sources;
- multiple interactive clients;
- multiple users or shared knowledge bases.

Preparing for a capability means defining a stable extension boundary, not building speculative functionality.

## Quality attributes

The system prioritizes these attributes:

| Attribute | Architectural implication |
| --- | --- |
| Maintainability | Explicit module ownership, small public interfaces, dependency inversion, ADRs |
| Data durability | Markdown in the vault is canonical; derived state is rebuildable |
| Correctness | Idempotent workflows, schema validation, provenance, citation verification |
| Extensibility | Generic documents and pluggable source, model, index, and client adapters |
| Operability | Observable state transitions, traces, metrics, dashboards, and runbooks |
| Reliability | Persistent jobs, bounded retries, dead-letter handling, recovery tooling |
| Privacy | Data minimization, explicit retention, secret isolation, provider boundaries |
| Testability | Deterministic domain services, contract tests, golden documents, evaluation gates |
| Cost control | Per-operation usage accounting, quotas, caching, and budget alerts |

Quality is not traded away merely because the initial feature set is small.

## Governing principles

### 1. The Knowledge Engine owns business logic

Clients translate user interaction into engine commands and render engine events or results. They do not extract content, manage ingestion workflows, perform retrieval, construct prompts, or decide citation policy.

### 2. Canonical knowledge is portable and human-readable

Every accepted source becomes a Knowledge Document: Markdown content plus validated YAML frontmatter in an Obsidian vault. A user must retain useful knowledge even if every database, index, cache, or model provider disappears.

### 3. Derived data is disposable

Embeddings, chunks, vector entries, metadata projections, caches, and search indexes are projections of canonical documents. They may improve performance but never become the sole copy of user knowledge.

### 4. Domain policy is separated from providers

Core services depend on capabilities such as `ContentExtractor`, `EmbeddingProvider`, and `VectorIndex`, not on vendor SDKs. Adapters implement those capabilities and translate provider errors into domain-level outcomes.

### 5. Workflows are explicit and asynchronous

Ingestion is a durable workflow with named states and observable transitions. User-facing acknowledgement is independent from processing completion.

### 6. Provenance is preserved end to end

Every normalized passage must remain traceable to its source and canonical document location. Retrieval and answer generation must carry provenance forward rather than reconstructing it after generation.

### 7. Evaluation is part of delivery

Ingestion fidelity, retrieval relevance, answer groundedness, citation validity, and conversation quality have versioned evaluation datasets and release gates. Prompt, model, extraction, and retrieval changes are treated like code changes.

### 8. Observability is designed into interfaces

Commands, jobs, model calls, and state transitions carry correlation identifiers and emit structured telemetry. Failures must be diagnosable without reproducing them interactively.

### 9. Idempotency precedes concurrency

Retries, duplicate messages, and concurrent requests are normal operating conditions. Writes and jobs require stable identities, uniqueness rules, and safe replay behavior.

### 10. Prefer reversible decisions

Provider-specific and performance-sensitive decisions are isolated behind ports. Irreversible choices—especially canonical file format and identity—receive explicit ADRs and migration plans.

## Explicit non-goals

- Telegram-specific commands are not part of the engine domain.
- The vector store is not a system of record.
- Temporary question-session history is not a knowledge source.
- The system does not silently rewrite user-edited vault content.
- Initial production readiness does not imply premature microservices.
- Supporting future sources does not imply a universal web scraper in the first release.

## Decision tests

Before accepting an architectural proposal, ask:

1. Can the vault still be understood and backed up without this component?
2. Can the component be replaced without changing engine policy?
3. Can an operation be retried without corrupting or duplicating knowledge?
4. Can an answer claim be traced to a stable source passage?
5. Can quality and cost changes be measured before release?
6. Can an operator determine what happened from telemetry and persisted state?
