# Knowledge Assistant Architecture

## Why this documentation exists

This directory is the architectural source of truth for Knowledge Assistant. It defines the product boundaries, durable design decisions, component responsibilities, and quality expectations that implementation must follow.

The documentation is intentionally written before production code. Code may refine these documents, but it must not silently contradict them. Material architectural changes require an update here and, when appropriate, a new Architecture Decision Record (ADR).

## Scope

Knowledge Assistant is a personal knowledge system whose core product is a reusable **Knowledge Engine**. Telegram is the first client, not the product boundary. The engine ingests long-form sources, produces canonical Knowledge Documents in an Obsidian vault, builds rebuildable retrieval artifacts, and answers grounded questions with citations.

The supported sources are Substack and Medium articles plus public rich X Articles.
The architecture anticipates podcasts,
PDFs, YouTube transcripts, and other long-form content without encoding those
source types into the core domain.

## Document map

| Document | Responsibility |
| --- | --- |
| [01 — Goals and Principles](./01-goals-and-principles.md) | Product scope, quality attributes, constraints, and governing principles |
| [02 — System Context and Boundaries](./02-system-context-and-boundaries.md) | Actors, external systems, trust boundaries, and high-level component model |
| [03 — Domain Model and Knowledge Document](./03-domain-model-and-knowledge-document.md) | Core terminology, document lifecycle, identity, and canonical Markdown contract |
| [04 — Ingestion Architecture](./04-ingestion-architecture.md) | Asynchronous ingestion stages, state machine, idempotency, and failure handling |
| [05 — Storage, Registry, and Derived Data](./05-storage-registry-and-derived-data.md) | Vault authority, registry responsibilities, indexes, consistency, and rebuilds |
| [06 — Retrieval, Answers, and Citations](./06-retrieval-answers-and-citations.md) | Query pipeline, reranking, grounded generation, and citation integrity |
| [07 — Clients and Session Management](./07-clients-and-session-management.md) | Thin-client contract, Telegram behavior, and temporary conversation state |
| [08 — Evaluation Architecture](./08-evaluation-architecture.md) | Offline and online evaluation across ingestion, retrieval, answers, citations, and conversations |
| [09 — Observability and Operations](./09-observability-and-operations.md) | Logs, traces, metrics, dashboards, alerts, retries, and cost monitoring |
| [10 — Security, Privacy, and Reliability](./10-security-privacy-and-reliability.md) | Threat boundaries, data protection, resilience, recovery, and failure policy |
| [11 — Deployment and Evolution](./11-deployment-and-evolution.md) | Runtime topology, scaling boundaries, configuration, migrations, and release strategy |
| [12 — Extension Contracts](./12-extension-contracts.md) | Replaceable ports and future extension points for sources, models, stores, and clients |
| [13 — Verification Strategy](./13-verification-strategy.md) | Test layers, contract verification, deterministic fixtures, and production-readiness checks |
| [14 — Monitoring and Evaluation Implementation Plan](./14-monitoring-and-evaluation-implementation-plan.md) | Initial monitoring stack, projection cutover correction, and synthetic chunk-question evaluation rollout |
| [Decision Records](./decisions/README.md) | Durable architectural decisions and their consequences |

## How to use these documents

- Product and engineering discussions should use the defined domain terms.
- New modules must respect the ownership and dependency rules in these documents.
- Provider-specific choices belong behind the defined ports.
- Derived artifacts must remain disposable and reconstructable from canonical documents.
- A change that alters a system boundary, canonical schema, consistency rule, or quality target requires an ADR.
- Implementation details that do not affect architectural guarantees belong near the code, not here.

## Documentation status

These documents define the target architecture for the initial production system. Exact vendors, libraries, models, and deployment platforms remain open unless explicitly constrained by an ADR.
