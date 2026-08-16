# ADR-0009: Use Containers as the Standard Runtime Boundary

- **Status:** Accepted
- **Date:** 2026-07-28

## Context

Knowledge Assistant needs a reproducible Python runtime, PostgreSQL with pgvector, explicit schema migration, and future independently runnable API and worker roles. Relying on developer-machine environments would make dependency, database-extension, and deployment behavior inconsistent.

The Obsidian vault must remain accessible as host-owned Markdown rather than becoming container-local data.

## Decision

Use OCI containers as the standard local and deployable runtime boundary. Build one immutable application image from the committed dependency lock and reuse it for migration, administration, the future API/Telegram role, and the future worker role.

Use Docker Compose as the supported local integration environment. Run PostgreSQL with pgvector as a pinned container image and persist its data in a named volume. Mount the host Obsidian vault into the roles that require it.

The initial foundation exposes only real runnable roles: PostgreSQL, migration, administration, and tests. API and worker services will be added when their entry points and health contracts exist.

## Consequences

- Local development and deployed execution share the same Python artifact.
- PostgreSQL and pgvector versions are reproducible.
- Schema migration is an explicit one-shot lifecycle step.
- Images and Compose definitions require security and supply-chain maintenance.
- Developers need a running Docker-compatible daemon for integration tests.
- Host vault permissions must allow the container's unprivileged runtime user to perform authorized writes.
- Production secret injection and storage orchestration may differ from local Compose while preserving the same contracts.

## Alternatives considered

- Run all components directly on the host: rejected because Python, PostgreSQL, and extension drift would weaken reproducibility.
- Put the Obsidian vault in a Docker named volume: rejected because it would reduce direct user ownership and Obsidian interoperability.
- Define placeholder API and worker containers immediately: rejected because a container that stays alive without a real service health contract creates false deployment confidence.

## Review triggers

Reconsider this decision if:

- the target deployment platform cannot execute OCI images;
- local vault filesystem semantics are incompatible with container bind mounts;
- a local-native distribution becomes an explicit product requirement;
- stronger isolation requires separating API and worker build artifacts.

## Related documents

- [Deployment and Evolution](../11-deployment-and-evolution.md)
- [Security, Privacy, and Reliability](../10-security-privacy-and-reliability.md)
- [Verification Strategy](../13-verification-strategy.md)

