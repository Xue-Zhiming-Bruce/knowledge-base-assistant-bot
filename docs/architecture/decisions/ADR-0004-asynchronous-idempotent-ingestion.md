# ADR-0004: Use Asynchronous, Idempotent Ingestion Workflows

- **Status:** Accepted
- **Date:** 2026-07-28

## Context

Fetching, extraction, model calls, and indexing have variable latency and failure modes. Telegram must acknowledge quickly, and client platforms may redeliver updates.

## Decision

Persist ingestion intent before acknowledgement and execute it as an explicit durable workflow. Assume at-least-once delivery and make submissions, stages, writes, and event consumers idempotent.

## Consequences

- The user receives immediate confirmation without waiting for extraction.
- The system needs job state, leases, retry policy, dead-letter handling, and completion events.
- Duplicate client updates and worker attempts are safe.
- Completion notification is decoupled from ingestion success.

## Alternatives considered

- Synchronous ingestion: rejected due to poor latency, timeout, and recovery behavior.
- Exactly-once queue semantics: rejected as an insufficient end-to-end guarantee across files, providers, and events.

## Related documents

- [Ingestion Architecture](../04-ingestion-architecture.md)
- [Observability and Operations](../09-observability-and-operations.md)

