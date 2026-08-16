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
