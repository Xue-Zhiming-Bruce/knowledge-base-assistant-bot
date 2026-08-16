# ADR-0003: Begin with a Modular Monolith and Durable Workers

- **Status:** Accepted
- **Date:** 2026-07-28

## Context

The system needs strong internal boundaries and production operations, but the initial workload and team topology do not justify a distributed microservice estate.

## Decision

Use one codebase with explicit domain modules and independently runnable client/API, query, and background-worker roles. Begin with a small number of deployables and a shared transactional operational store. Preserve ports that allow later extraction.

## Consequences

- Development, testing, and deployment remain manageable.
- Background work is isolated from request handling.
- Local transactions simplify job, registry, and outbox consistency.
- Module boundaries require enforcement because process boundaries do not provide it.
- Independent scaling is available by role, but not by every logical module.

## Alternatives considered

- Microservices from the start: rejected due to coordination, observability, deployment, and consistency costs without demonstrated need.
- One synchronous process: rejected because ingestion latency and failure modes require durable asynchronous work.

## Related documents

- [System Context and Boundaries](../02-system-context-and-boundaries.md)
- [Deployment and Evolution](../11-deployment-and-evolution.md)

