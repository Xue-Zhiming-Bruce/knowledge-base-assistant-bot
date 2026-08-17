# ADR-0007: Use PostgreSQL for Operational State and Initial RAG Projections

- **Status:** Accepted
- **Date:** 2026-07-28

## Context

Knowledge Assistant needs transactional workflow state, metadata filtering, temporary sessions, durable jobs, and hybrid retrieval. Deploying separate databases for each concern would add backup, monitoring, security, and consistency overhead that is not justified by the initial personal knowledge workload.

PostgreSQL can provide mature transactions and relational querying, vector similarity through an extension such as pgvector, and built-in full-text search. It therefore offers a practical initial platform for both operational persistence and rebuildable RAG projections.

## Decision

Use PostgreSQL as the initial production database for:

- document registry and revision projections;
- ingestion jobs, leases, attempt history, and outbox events;
- notification state;
- temporary question sessions and expiry;
- derived chunks and citation metadata;
- vector embeddings and similarity search;
- lexical search;
- projection manifests and reconciliation state.

Knowledge Document Markdown and YAML frontmatter in the Obsidian vault remain authoritative for accepted knowledge content. PostgreSQL chunks, embeddings, lexical records, and copied metadata are derived and rebuildable.

The Knowledge Engine accesses PostgreSQL only through domain-owned persistence and retrieval ports. Provider-specific SQL, vector operators, extensions, and migration details stay inside PostgreSQL adapters.

## Consequences

- The initial production system has fewer stateful services to operate.
- Registry filters and hybrid retrieval can share transactional identifiers and metadata.
- PostgreSQL backup, monitoring, migration, and connection management become critical operational capabilities.
- Interactive queries, background indexing, and workflow transactions require separate connection pools and resource budgets.
- Vector-extension availability and version compatibility must be validated in each deployment environment.
- A complete RAG rebuild from vault Markdown must remain supported and tested.
- Specialized vector or search infrastructure may replace the corresponding adapters later without changing canonical data or engine policy.

## Alternatives considered

- Separate relational, vector, lexical, job, and session stores from the start: rejected because the operational complexity outweighs the initial scaling benefit.
- PostgreSQL as the canonical document store with Markdown exports: rejected because it weakens user ownership and makes the Obsidian vault a potentially stale projection.
- A dedicated vector database as the only retrieval store: rejected because exact-term retrieval and operational metadata still require additional capabilities.

## Review triggers

Reconsider this decision if:

- corpus size or query volume exceeds acceptable PostgreSQL vector-search performance;
- specialized ranking, filtering, or index features materially improve evaluated quality;
- index workloads interfere with operational transactions despite isolation;
- independent scaling, security, or availability requirements justify separate stores;
- PostgreSQL full-text search is insufficient for supported languages.

## Related documents

- [Storage, Registry, and Derived Data](../05-storage-registry-and-derived-data.md)
- [Deployment and Evolution](../11-deployment-and-evolution.md)
- [Extension Contracts](../12-extension-contracts.md)
- [ADR-0002: Use Vault Markdown as Canonical Knowledge](./ADR-0002-markdown-canonical-store.md)
- [ADR-0005: Treat Retrieval Data as Versioned, Rebuildable Projections](./ADR-0005-versioned-derived-projections.md)
