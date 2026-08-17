# ADR-0005: Treat Retrieval Data as Versioned, Rebuildable Projections

- **Status:** Accepted
- **Date:** 2026-07-28

## Context

Chunking, embeddings, and index schemas evolve and are provider-specific. Mixing versions can produce silent retrieval errors, while making them authoritative would undermine vault portability.

## Decision

Chunks, embeddings, vector entries, lexical records, and caches are derived projections keyed by explicit compatibility versions. Build incompatible changes as new generations, validate them, and atomically switch the active generation.

## Consequences

- All derived state can be discarded and reconstructed.
- Rebuild tooling and projection manifests are required from the first production release.
- Parallel generations temporarily increase storage and build cost.
- Rollback is fast and does not rewrite canonical documents.

## Alternatives considered

- Update entries in place: rejected because mixed states are difficult to detect and roll back.
- Pin one model and chunking strategy indefinitely: rejected because it prevents quality and cost evolution.

## Related documents

- [Storage, Registry, and Derived Data](../05-storage-registry-and-derived-data.md)
- [Evaluation Architecture](../08-evaluation-architecture.md)

