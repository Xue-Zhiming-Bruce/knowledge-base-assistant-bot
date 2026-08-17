# Grafana Dashboard Load Verification

Verified 2026-08-16 against the live `grafana/otel-lgtm:0.30.0` container
(started via the `monitoring` Compose profile). The checks below are safe API
calls: they return only dashboard metadata (uid, title, panel names, and
PromQL expressions) and never expose credentials, prompts, questions, answers,
or document content.

## 1. Dashboard discovery

`GET /api/search?query=Knowledge` returns:

```json
[{"uid": "knowledge-assistant-overview", "title": "Knowledge Assistant",
  "url": "/d/knowledge-assistant-overview/knowledge-assistant"}]
```

## 2. Dashboard content

`GET /api/dashboards/uid/knowledge-assistant-overview` returns the provisioned
dashboard with **7 panels** (all `timeseries` on the Prometheus datasource):

| Panel | PromQL expression |
| --- | --- |
| Questions by outcome | `sum by (outcome) (rate(questions_total[5m]))` |
| Question latency (p95) | `histogram_quantile(0.95, sum by (le) (rate(question_stage_duration_seconds_bucket[5m])))` |
| Retrieval candidates (mean) | `sum(rate(retrieval_candidates_sum[5m])) / sum(rate(retrieval_candidates_count[5m]))` |
| Ingestion jobs by outcome | `sum by (outcome) (rate(ingestion_jobs_total[5m]))` |
| Ingestion duration (p95) | `histogram_quantile(0.95, sum by (le) (rate(ingestion_stage_duration_seconds_bucket[5m])))` |
| Citation-validation outcomes | `sum by (outcome) (rate(citations_total[5m]))` |
| Answer feedback up/down | `sum by (direction) (rate(feedback_total[5m]))` |

## 3. Provisioning method

The dashboard is provisioned through Docker Compose single-file bind mounts into
the lgtm container (`compose.yaml`):

- `config/grafana/dashboards/knowledge-assistant.json` →
  `/otel-lgtm/grafana-dashboards/knowledge-assistant.json`
- `config/grafana/provisioning/dashboards/99-knowledge-assistant.yaml` →
  `/otel-lgtm/grafana/conf/provisioning/dashboards/99-knowledge-assistant.yaml`

Grafana's file provider loads the dashboard at startup. This is the curated
provisioned dashboard, not a Grafana Explore session.

## Live-data verification (2026-08-17)

The dashboard is not only provisioned — its panel queries were exercised against
**real, safe traffic** and confirmed to return data. This section records the
exact evidence so "dashboard provisioned" and "dashboard contains live data" are
never conflated.

### How the traffic was generated (safe by construction)

1. A local process pointed `OpenTelemetryAdapter` at the stack's OTLP endpoint
   (`http://127.0.0.1:4318`, the host-mapped lgtm port) with the service name
   `knowledge-assistant-verification`.
2. It ran the **real** Question Mode path against the live knowledge base
   (session start, retrieval, bounded context, `grounded-answer-v2` generation,
   citation validation, `/feedback up`, `/end`) for several question cycles
   spanning multiple 60-second export windows.
3. One deliberately failing ingestion job (a nonexistent Substack URL, HTTP 404)
   was submitted through the real ingestion contract; the running worker
   processed it and emitted `ingestion_jobs_total{outcome="failed"}`. No vault
   content was created or modified.

No questions, answers, article text, prompts, source URLs, or credentials were
placed in telemetry attributes: exported labels are only `outcome`, `direction`,
`stage`, `retrieval_version`, `source_provider`, and service identity, exactly
as the telemetry code guarantees.

### PromQL metric names match exported metrics

Each of the 7 dashboard panels' PromQL expressions was checked against the
metrics actually exported by the application:

| Panel | PromQL | Exported by | Live data |
| --- | --- | --- | --- |
| Questions by outcome | `sum by (outcome) (rate(questions_total[5m]))` | `QuestionService.answer` | yes |
| Question latency (p95) | `histogram_quantile(0.95, sum by (le) (rate(question_stage_duration_seconds_bucket[5m])))` | `QuestionService.answer`, `RetrievalOrchestrator` | yes |
| Retrieval candidates (mean) | `sum(rate(retrieval_candidates_sum[5m])) / sum(rate(retrieval_candidates_count[5m]))` | `RetrievalOrchestrator` | yes |
| Ingestion jobs by outcome | `sum by (outcome) (rate(ingestion_jobs_total[5m]))` | `IngestionWorker` | yes |
| Ingestion duration (p95) | `histogram_quantile(0.95, sum by (le) (rate(ingestion_stage_duration_seconds_bucket[5m])))` | `IngestionWorker` | yes |
| Citation-validation outcomes | `sum by (outcome) (rate(citations_total[5m]))` | `QuestionService.answer` | yes |
| Answer feedback up/down | `sum by (direction) (rate(feedback_total[5m]))` | `QuestionService.feedback` | yes |

### Confirmed live values (Prometheus instant queries via the Grafana datasource proxy)

All **7 panels' PromQL queries returned data** after the traffic above (rate
queries evaluated over a 5-minute window with at least two samples):

| Panel | Live query result |
| --- | --- |
| Questions by outcome | `questions_total{outcome="success"}` → rate ~0.036/s |
| Question latency (p95) | ~6.0s |
| Retrieval candidates (mean) | 8 candidates |
| Ingestion jobs by outcome | `ingestion_jobs_total{outcome="failed"}` → rate ~0.008/s |
| Ingestion duration (p95) | ~4.75s |
| Citation-validation outcomes | `citations_total{outcome="valid"}` → rate ~0.08/s |
| Answer feedback up/down | `feedback_total{direction="up"}` → rate ~0.018/s |

Instant-vector confirmation (before the rate window filled):
`questions_total{outcome="success"}` → 2; `retrieval_candidates_count` → 2;
`ingestion_jobs_total{outcome="failed",source_provider="substack"}` → 1 (three
failed jobs total across the run); `citations_total{outcome="valid"}` → 7;
`feedback_total` → `{direction="up",outcome="recorded"}`,
`{direction="up",outcome="duplicate"}`, `{direction="down",outcome="duplicate"}`.

`rate(...[5m])` panel queries require at least two samples inside the window
(the exporter emits every 60s), so fresh traffic needs a couple of minutes
before the rate panels render; instant-vector values are visible immediately.
An ingestion-p95 panel queried before any ingestion activity inside the rate
window returns NaN (no observations in window), which is correct Prometheus
behavior, not a dashboard defect.

### Privacy-safe telemetry labels (spot-checked)

All exported series carry only the labels shown above. A full scan of the
exported label sets confirmed no attribute contains a question, answer, URL,
prompt, article text, or credential.

### Screenshot status — provisioned but NOT screenshot-verified

A real dashboard screenshot was **not** captured: the lgtm image has no Grafana
image-renderer plugin and no chromium binary, so a browser-rendered screenshot
cannot be produced in this environment. No fabricated screenshot is committed.
The live-data evidence above is the verification; a screenshot remains an
optional human task once a renderer is available (or via a manual browser
visit to http://localhost:3000, dashboard "Knowledge Assistant").
