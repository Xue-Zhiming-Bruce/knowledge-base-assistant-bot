# ADR-0012: Use OpenTelemetry and the Grafana Stack for Initial Observability

- **Status:** Accepted
- **Date:** 2026-08-02

## Context

Knowledge Assistant needs correlated visibility across Telegram polling, asynchronous
ingestion, PostgreSQL, source providers, model calls, retrieval, answer generation,
and notification delivery. The system already emits JSON process logs, records token
usage in selected events, and maintains durable bot and worker heartbeats, but it does
not yet export traces or metrics to an observability backend.

The initial deployment is a small Docker Compose system. Its observability stack must
therefore be simple to run locally while preserving a vendor-neutral path to a larger
self-managed or hosted deployment. Telemetry failure must not prevent ingestion,
question answering, or durable state transitions.

## Decision

Instrument application-owned operations with the OpenTelemetry Python API and SDK and
export traces and metrics over OTLP/HTTP to an OpenTelemetry Collector.

Use the following initial backend components:

- Prometheus-compatible storage, initially Grafana Mimir in the local bundle, for
  metrics;
- Grafana Tempo for traces;
- Grafana Loki for structured logs;
- Grafana for dashboards, exploration, and alerting.

For local development and integration testing, run the Grafana OpenTelemetry LGTM
image as one optional Compose service. It packages a Collector and the Grafana, Loki,
Mimir, and Tempo development stack behind one OTLP endpoint. Production may deploy
these components separately or replace them with another OTLP-compatible backend.

Continue to write structured JSON application logs to standard output using Python's
logging system. Enrich those records with active trace and span identifiers, and ship
container logs through the deployment logging adapter. Do not make application code
depend directly on Loki or a vendor-specific logging SDK.

Keep PostgreSQL service heartbeats, job state, attempt history, and projection status
as durable operational truth. Telemetry backends complement this state; they do not
replace it.

Metric attributes are bounded. Provider, operation, stage, outcome, model, and coarse
source type are permitted. User, document, revision, session, job, raw URL, prompt,
answer, and content values are prohibited as metric attributes. High-cardinality safe
identifiers may appear in restricted traces and logs when required for correlation.

## Consequences

- Application instrumentation remains portable across self-managed and hosted
  observability products.
- Local development gains a usable metrics, logs, and traces UI without operating
  each backend separately.
- The application image gains OpenTelemetry SDK and OTLP exporter dependencies.
- Collector and backend configuration become deployable, versioned artifacts.
- Structured log collection requires deployment configuration in addition to
  application instrumentation.
- The all-in-one LGTM image is a development and test convenience, not the assumed
  production topology.
- Operators must manage telemetry retention, access, resource limits, and redaction.

## Alternatives considered

- Direct integration with one hosted vendor SDK: rejected because it would couple
  application instrumentation and configuration to that vendor.
- JSON logs and PostgreSQL health queries only: rejected because they do not provide
  latency distributions or causal traces across asynchronous stages.
- Separate Prometheus, Loki, Tempo, Grafana, and Collector containers from the first
  local release: rejected because the operational overhead is unnecessary for the
  current personal deployment.
- Store operational metrics in the application PostgreSQL database: rejected because
  it couples monitoring availability and load to the system being monitored.

## Related documents

- [Observability and Operations](../09-observability-and-operations.md)
- [Deployment and Evolution](../11-deployment-and-evolution.md)
- [Monitoring, Projection Cutover, and Synthetic Evaluation Plan](../14-monitoring-and-evaluation-implementation-plan.md)

