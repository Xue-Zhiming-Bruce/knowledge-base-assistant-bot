# Architecture Decision Records

## Why this document exists

Architecture Decision Records preserve the context and consequences of choices that are costly to rediscover or reverse. They complement the thematic architecture documents without turning those documents into historical logs.

## Status vocabulary

- **Proposed** — under review and not yet binding.
- **Accepted** — part of the target architecture.
- **Superseded** — replaced by a later ADR.
- **Deprecated** — still present but should not be used for new work.
- **Rejected** — considered and intentionally not selected.

## Index

| ADR | Status | Decision |
| --- | --- | --- |
| [ADR-0001](./ADR-0001-knowledge-engine-boundary.md) | Accepted | Keep the Knowledge Engine independent of clients |
| [ADR-0002](./ADR-0002-markdown-canonical-store.md) | Accepted | Use vault Markdown as canonical knowledge |
| [ADR-0003](./ADR-0003-modular-monolith.md) | Accepted | Begin with a modular monolith and durable workers |
| [ADR-0004](./ADR-0004-asynchronous-idempotent-ingestion.md) | Accepted | Use asynchronous, idempotent ingestion workflows |
| [ADR-0005](./ADR-0005-versioned-derived-projections.md) | Accepted | Treat retrieval data as versioned, rebuildable projections |
| [ADR-0006](./ADR-0006-temporary-question-sessions.md) | Accepted | Keep question-session history temporary |
| [ADR-0007](./ADR-0007-postgresql-operational-and-rag-store.md) | Accepted | Use PostgreSQL for operational state and initial RAG projections |
| [ADR-0008](./ADR-0008-openai-initial-model-provider.md) | Accepted | Use OpenAI as the initial model provider |
| [ADR-0009](./ADR-0009-containers-as-runtime-boundary.md) | Accepted | Use containers as the standard runtime boundary |
| [ADR-0010](./ADR-0010-credentialed-x-api-acquisition.md) | Superseded | Use the official credentialed X API for Articles and threads |
| [ADR-0011](./ADR-0011-xquik-only-x-articles.md) | Accepted | Use Xquik directly for rich X Articles only |
| [ADR-0012](./ADR-0012-opentelemetry-grafana-observability.md) | Accepted | Use OpenTelemetry and the Grafana stack for initial observability |

## ADR template

New records should contain:

1. Title
2. Status
3. Date
4. Context
5. Decision
6. Consequences
7. Alternatives considered
8. Related documents

Accepted ADRs are immutable. A changed decision receives a new ADR that supersedes the old one.
