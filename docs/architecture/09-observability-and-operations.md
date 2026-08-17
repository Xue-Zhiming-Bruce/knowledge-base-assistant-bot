# Observability and Operations

## Why this document exists

Production behavior spans client updates, durable jobs, external sources, model providers, files, and indexes. This document defines the telemetry and operational control plane needed to understand correctness, reliability, performance, and cost without coupling the design to a particular vendor.

## Observability model

Knowledge Assistant emits OpenTelemetry-compatible traces and metrics and correlated
structured logs through an internal telemetry port. The initial implementation exports
OTLP/HTTP to an OpenTelemetry Collector and uses the Grafana observability stack as
defined by
[ADR-0012](./decisions/ADR-0012-opentelemetry-grafana-observability.md).

Telemetry answers:

- What request or job is this?
- Which document, revision, session, and projection generation were involved?
- Which stages ran, with which versions?
- What state transitions occurred?
- Where was time and money spent?
- Was the result correct, degraded, retried, or failed?
- Can an operator take a safe next action?

## Selected initial stack

- OpenTelemetry Python API and SDK for application instrumentation;
- OTLP/HTTP between application processes and an OpenTelemetry Collector;
- Prometheus-compatible metrics storage, initially Grafana Mimir in the local bundle;
- Grafana Tempo for traces;
- Grafana Loki for structured logs;
- Grafana for dashboards and alerting;
- the Grafana OpenTelemetry LGTM all-in-one image for local development and
  integration testing.

Python structured logging remains the application logging API. Logs are written as
JSON to standard output and enriched with active trace and span identifiers. The
current local implementation exports application traces and metrics through OTLP/HTTP;
logs remain available through Docker. A Loki logging adapter is future work.
Production may replace any backend with an OTLP-compatible service without changing
domain or application behavior.

PostgreSQL heartbeats, workflow state, and attempt history remain durable operational
truth. The telemetry stack is diagnostic and analytical; it is not a replacement for
durable state or readiness checks.

## Correlation model

The following identifiers connect signals:

- `trace_id` for one causal execution path;
- `correlation_id` across asynchronous boundaries;
- `request_id` for an application invocation;
- `job_id` and `attempt_id` for background work;
- `document_id` and `revision_id`;
- `session_id` and `turn_id`;
- `projection_generation`;
- provider request ID when safe to retain.

Raw source URLs, question text, document text, and generated answers are excluded from telemetry by default. Stable pseudonymous identifiers and coarse classifications support diagnosis without copying personal knowledge into monitoring systems.

## Distributed tracing

Trace boundaries include:

- client update reception and acknowledgement;
- application command handling;
- queue publish and worker consumption;
- each ingestion stage;
- vault and registry operations;
- chunking, embedding, and index writes;
- query understanding, retrieval, reranking, context assembly;
- model calls and citation validation;
- notification delivery;
- reconciliation and rebuild jobs.

Asynchronous trace context is propagated in job and event envelopes. Span attributes use bounded cardinality; unbounded IDs belong in traces/logs, not metric labels.

## Structured logging

Logs are machine-readable events with a stable schema:

- timestamp, severity, event name, component, environment;
- correlation identifiers;
- state before and after, where relevant;
- normalized error class and retryability;
- duration or quantity;
- version envelope;
- redaction classification.

Logs describe events rather than concatenated prose. Exceptions include stack traces in restricted diagnostic storage. Secrets, authorization headers, full prompts, source bodies, and session transcripts are never logged.

## Metrics

### Ingestion

- submissions, acceptances, rejections, duplicates;
- jobs by state and source provider;
- stage throughput, duration, and failure rate;
- retry counts, queue depth, age, and dead-letter volume;
- extraction quality warnings;
- time from acceptance to canonical commit and to fully ready;
- projection lag and rebuild progress.

### Query and sessions

- sessions started, ended, expired, and cleanup failures;
- questions, success, insufficient-evidence, degraded, and error rates;
- retrieval and reranking latency;
- candidate and context counts;
- citation validation and repair rates;
- end-to-end latency percentiles;
- rate-limit and concurrency rejections.

### Storage and consistency

- vault write and conflict rates;
- images discovered, persisted, deduplicated, omitted, and failed by reason;
- image acquisition bytes, latency, redirect count, and retry rate;
- canonical documents with missing or orphaned assets;
- registry/vault drift;
- orphan and tombstone counts;
- active projection completeness;
- index freshness;
- backup age and restore-drill status.

### External dependencies

- request volume, latency, timeout, error, and rate-limit rates by provider;
- circuit state;
- quota remaining when available;
- model token or unit consumption.

### Cost

- embedding units and cost per document/revision;
- generation and reranking tokens/cost per question;
- extraction/transcription/OCR cost by source type;
- X post-lookup and search resources returned per ingestion, estimated provider
  cost, credit exhaustion, and configured thread ceiling;
- storage and index growth;
- cost per successful ingestion and answered question;
- daily/monthly budget utilization and forecast.

Metrics avoid user or document IDs as labels.

## Service-level objectives

Concrete targets are established after measured baselines, but the initial service-level indicators are defined now:

- durable acknowledgement availability and latency;
- successful supported-source ingestion rate;
- ingestion completion latency by source type;
- interactive answer availability and latency;
- citation-valid answer rate;
- session deletion completion time;
- projection freshness;
- notification delivery success.

Quality SLOs are paired with evaluation metrics; operational success alone is not sufficient.

## Planned dashboards

Minimum dashboards:

1. **System health** — request rates, error rates, latency, saturation, dependency health.
2. **Ingestion operations** — job states, oldest queued work, stage failures, retries, dead letters.
3. **Query quality and performance** — retrieval, answer, citation outcomes, latency, degraded paths.
4. **Data integrity** — reconciliation drift, projection completeness, conflicts, backups.
5. **Cost and quotas** — spend by capability/provider, forecasts, anomalies, budget status.
6. **Evaluation trends** — baseline/candidate quality and production feedback slices.

Dashboards link from aggregates to traces without exposing content broadly.

## Alerts

Alerts must be actionable and tied to a runbook. Candidate alerts include:

- durable acknowledgement failures;
- queue age or dead-letter growth;
- sustained ingestion or query error-budget burn;
- citation invariant violations;
- session cleanup failures;
- vault write or reconciliation anomalies;
- active projection incompleteness;
- provider quota or cost anomalies;
- backup freshness failures.

Paging is reserved for urgent user impact, data durability, privacy, or security risks. Lower-urgency quality drift creates tickets or review tasks.

## Retry, timeout, and circuit policy

Every external call defines:

- connection and total timeout;
- retryable error classes;
- maximum attempts and elapsed time;
- exponential backoff with jitter;
- concurrency and rate budgets;
- idempotency semantics;
- circuit-breaker behavior;
- fallback or failure result.

Retries are recorded as span events and metrics. Layered retries are coordinated to avoid multiplicative retry storms.

## Operational interfaces

Operators need safe, auditable commands to:

- inspect job state and attempt history;
- retry from a safe stage;
- cancel a job;
- replay a dead-letter item;
- reconcile one document or the full vault;
- rebuild and switch projection generations;
- quarantine a malformed document;
- test dependency health;
- inspect cost attribution;
- verify and trigger session cleanup.

Administrative actions use the same domain invariants as normal workflows.

## Container operations

Container-level health is distinct from application readiness:

- PostgreSQL health verifies that the configured database accepts connections.
- Migration success verifies that the required schema and extensions are installed.
- Bot readiness is represented by a recent PostgreSQL heartbeat written by its
  polling loop.
- Worker readiness is represented by a recent PostgreSQL heartbeat written by
  its claim loop; it does not invoke a paid provider.
- A future API readiness check must verify configuration, database
  compatibility, and ability to accept commands without invoking paid
  providers.

Container restarts, exits, OOM events, image versions, and migration outcomes become structured operational signals. Orchestrators must not treat a successful process start as proof that migrations or required dependencies are ready.

The current slice emits JSON-formatted process logs and durable service
heartbeats. Ingestion logs record embedding input-token usage; question logs
record embedding input tokens, generation input/output tokens, model identity,
and citation count without logging questions, answers, or evidence content.
OpenTelemetry traces and metrics export, the local LGTM deployment profile,
dashboards, cost aggregation, and alerts remain production-hardening work. The
identifier and privacy contracts above govern that implementation. The ordered
implementation and initial signal catalog are defined in
[Monitoring, Projection Cutover, and Synthetic Evaluation Plan](./14-monitoring-and-evaluation-implementation-plan.md).

## Failure handling

User-facing errors are stable categories with a support reference, not provider stack traces. Operator-facing diagnostics preserve provider codes, attempts, and version context.

Partial success is explicit:

- canonical commit succeeded but indexing failed → `ready_degraded`;
- ingestion succeeded but notification failed → document remains `ready`;
- retrieval degraded but policy permits answer → marked in telemetry and result metadata;
- citation validation failed → no apparently grounded answer is returned.

## Retention

Retention is data-class-specific:

- metrics: long enough for capacity and cost trends;
- traces: sampled and shorter-lived, with errors retained longer;
- structured logs: based on operational and audit needs;
- evaluation results: retained with dataset governance;
- content-bearing debug captures: disabled by default and tightly controlled if enabled.

## Extension points

- alternative OpenTelemetry collectors and backends;
- on-call and incident-management integrations;
- automated rollback signals;
- privacy-preserving quality analytics;
- per-tenant budgets in a multi-user future;
- sustainability or carbon-cost reporting.
