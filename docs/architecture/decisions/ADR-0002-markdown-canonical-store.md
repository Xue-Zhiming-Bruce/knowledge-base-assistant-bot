# ADR-0002: Use Vault Markdown as Canonical Knowledge

- **Status:** Accepted
- **Date:** 2026-07-28

## Context

The user requires Obsidian-compatible knowledge that remains useful independently of databases, indexes, and model providers. Retrieval systems favor specialized storage, but those systems are poor durable personal archives.

## Decision

The canonical representation of each accepted Document is a validated Knowledge Document consisting of Markdown and YAML frontmatter in an Obsidian vault. Embeddings, chunks, indexes, and metadata caches are derived.

## Consequences

- Knowledge is portable, inspectable, and directly usable in Obsidian.
- Schema validation, atomic file writes, and conflict handling are required.
- A registry is still needed for workflows and efficient operational queries.
- Registry/vault reconciliation is a core operational capability.
- Derived stores must offer complete rebuild paths.

## Alternatives considered

- Database as canonical with Markdown export: rejected because export can drift and makes portability contingent on application health.
- Vector store as canonical: rejected because it loses human-readable structure and cannot reliably reconstruct the source document.

## Related documents

- [Domain Model and Knowledge Document](../03-domain-model-and-knowledge-document.md)
- [Storage, Registry, and Derived Data](../05-storage-registry-and-derived-data.md)

