# Knowledge Assistant

A personal knowledge engine that saves long-form articles (Substack, Medium, X
Articles) as canonical Markdown in your own Obsidian vault and answers questions
with grounded, cited answers from your saved knowledge — through a Telegram bot,
a non-Telegram CLI demo, and a reproducible evaluation/benchmark harness.

## Who this is for

You read a lot of long-form content (essays, newsletters, tech articles) and
keep losing the specifics: *"what did that essay say about X?"* You want a
personal assistant that answers **from what you actually saved** — with citations
back to the exact article — not from whatever the model remembers about the
internet.

## The problem and why generic ChatGPT isn't enough

Generic chat models answer from their training data, which:

- has **no memory of the specific articles you saved**;
- **hallucinates plausible-but-wrong details** about niche essays and authors;
- cannot **cite the exact source** for a claim;
- does not improve as **your** knowledge base grows.

Knowledge Assistant inverts this: your vault is the single source of truth, and
every answer is generated from retrieved evidence with validated citation
markers. If the knowledge base does not contain the answer, it says so instead
of guessing.

## What it does

- **Saves** public Substack, Medium, and rich X Articles as canonical Markdown in
  an Obsidian vault (X Articles are Article-only via Xquik, with strict
  lossless-block validation; anything lossy fails explicitly).
- **Indexes** every saved document into a rebuildable PostgreSQL projection
  (dense embeddings + full-text) scoped to a projection generation.
- **Answers** questions with a grounded, cited answer through five selectable
  retrieval strategies (`weighted-hybrid-v1` is the production default).
- **Evaluates** retrieval and end-to-end answers against a committed,
  human-authored, public-safe sample dataset — with real, reproducible numbers.
  Evaluation target validation is chunk-level (chunk id + content fingerprint)
  whenever a case carries a target chunk; document-level URL resolution is used
  only as the fallback for no-target cases. The committed sample dataset is
  document-level, so its answers are validated by document and cited evidence
  resolution — the project does not claim chunk-level citation support for that
  run.
- **Monitors** ingestion, questions, citations, and feedback on a curated
  Grafana dashboard.

## Supported sources

| Source | Provider | Notes |
| --- | --- | --- |
| Substack essays | `substack` | including `.substack.com` publications |
| Medium articles | `medium` | with a bounded RSS fallback for HTTP 403 |
| X Articles | `xquik_mpp` / `xquik` | Article-only; ordinary posts/threads fail clearly |

## Architecture

```mermaid
flowchart LR
    S["Sources: Substack / Medium / X Article URLs"] -->|submit idempotent job| W[Worker]
    W -->|fetch + extract| V[(Obsidian vault<br/>canonical Markdown)]
    W -->|chunk + embed| P[(PostgreSQL + pgvector<br/>rebuildable projection)]
    Q[Question] --> R[RetrievalOrchestrator<br/>strategy: vector / lexical / hybrid / RRF / agentic]
    P --> R
    R -->|bounded evidence + rerank| A[Grounded answer generation<br/>grounded-answer-v1 / v2]
    A -->|citation validation| Out[Answer + Sources]
    Out -->|/feedback up|down| FB[(answer_feedback<br/>safe metadata only)]
    Out -->|OTLP| G[Grafana dashboard<br/>7 curated panels]
    W -->|OTLP| G
```

The architecture source of truth starts at
[docs/architecture/README.md](./docs/architecture/README.md).

## Sample dataset (committed, public-safe)

`data/sample/manifest.json` is a reproducible, public-safe sample corpus: four
public Substack essays (Addy Osmani's *21 Lessons from 14 Years at Google* and
*Software Factories, Light and Dark*; Kent Beck's *Is Source Code Going Away?*
and *The Pinhole View of AI Value*) with **titles and URLs only — no article
bodies** — plus eight human-authored questions with reference answers and
required facts, including one insufficient-evidence case. All four URLs were
verified publicly fetchable in August 2026. See
[data/sample/README.md](./data/sample/README.md).

## Ingestion walkthrough

1. A source URL is submitted (Telegram message, `sample-ingest`, `demo ingest`,
   or the optional Prefect flow `prefect-ingest`).
2. The worker classifies the URL, fetches the public article, extracts it to
   canonical Markdown, and writes it into the Obsidian vault (plus
   content-addressed image assets).
3. The worker chunks the Markdown deterministically, embeds each chunk, and
   writes rebuildable projection rows into PostgreSQL, scoped to a projection
   generation.
4. The vault Markdown remains the source of truth; every PostgreSQL row is a
   disposable projection that can be rebuilt with `projection-rebuild` and
   activated atomically with `projection-activate`.

## Retrieval and answering walkthrough

1. `demo ask --question "…"` (or Telegram Question Mode) embeds the question.
2. The selected strategy retrieves candidate chunks from the active projection
   generation (semantic, lexical via PostgreSQL `ts_rank_cd`, weighted hybrid,
   RRF hybrid, or bounded agentic decomposition).
3. A deterministic diversity reranker bounds the context.
4. A structured answer generator produces the answer with citation markers
   (grounded-answer-v1 or the stricter grounded-answer-v2).
5. A deterministic validator rejects answers whose citations do not resolve to
   the retrieved evidence; the rendered answer includes a `Sources:` section.

## Setup

Requirements: Python 3.12+ (uv recommended), Docker Desktop for the full stack,
and an Obsidian vault directory (a local folder is enough; no Obsidian API
token needed).

```shell
uv sync --extra dev --extra orchestration   # include the optional Prefect extra
uv run pytest
docker compose config --quiet
docker compose up -d --build
```

> **macOS local note:** the local `.venv` editable-install marker can receive the
> macOS `UF_HIDDEN` flag, which CPython's `site` module silently skips, breaking
> bare `uv run pytest`/`uv run mypy` with `ModuleNotFoundError`. Use the reliable
> invocation `PYTHONPATH=src uv run pytest` and `MYPYPATH=src uv run mypy` (or
> `uv sync --no-editable` to avoid the marker entirely). The Docker test image
> runs plain `pytest` with no workaround.

### Environment and credentials

Copy `.env.example` to `.env` and fill in only what you use. Required: the
PostgreSQL connection (from the compose stack) and `KNOWLEDGE_ASSISTANT_VAULT_PATH`.
Optional components are marked `[optional]` in `.env.example`:

- **OpenAI** (`OPENAI_API_KEY`, generation/embedding models): required for
  embedding, answering, and evaluation. Sign up at platform.openai.com
  (pay-per-use; small demo runs cost cents).
- **Telegram** (`TELEGRAM_TOKEN`, numeric `TELEGRAM_ALLOWED_USER_IDS`): required
  only for the bot. Create a bot with @BotFather; use your numeric user id.
- **X Articles** (`X_ARTICLE_PROVIDER`): Tempo MPP (authorize with
  `tempo-auth`) or an Xquik API key; costs ~$0.00075 per Article.
- **Monitoring** (`OTEL_EXPORTER_OTLP_ENDPOINT=http://lgtm:4318`): optional
  Grafana dashboard.

Never commit `.env`. Secrets are never printed by `check-config` or telemetry.

### Which commands cost money

Pure setup (free): `migrate`, `check-config`, `sample-ingest`, `demo ingest`,
`sample-eval-prepare`, `projection-rebuild` (embedding cost for the corpus).
Model calls (small pay-per-use): `demo ask`, `eval-run`, `eval-generate`,
`answer-eval-run`, and the worker's embedding step.

## Reviewer quick start (no Telegram required)

```shell
# 1. Start the stack and ingest the public sample corpus
docker compose up -d --build
docker compose --profile tools run --rm admin demo ingest

# 2. Ask a real question through the full RAG path
docker compose --profile tools run --rm admin \
  demo ask --question "What do engineers actually need to get good at?"
```

Example output (real run, `weighted-hybrid-v1`, synthetic model-generated answer,
truncated; not a source excerpt):

> According to the essay, engineers need to become good at far more than
> programming: they need to navigate the surrounding human and organizational
> work. [E1]
>
> In practical terms, the essay emphasizes getting good at: deeply understanding
> and solving user problems... [E3] working effectively with people... [E2]
>
> Sources:
> [E1] 21 Lessons from 14 Years at Google — https://addyo.substack.com/p/21-lessons-from-14-years-at-google
> [E2] 21 Lessons from 14 Years at Google — https://addyo.substack.com/p/21-lessons-from-14-years-at-google
> [E3] 21 Lessons from 14 Years at Google — https://addyo.substack.com/p/21-lessons-from-14-years-at-google

Optional Prefect orchestration (thin flow reusing the same ingestion contract,
never replaces the worker):

```shell
docker compose --profile tools run --rm prefect-ingest
```

## Evaluation

All scores below are **real output** from runs against the committed
`sample-docs-v1` dataset on projection `bd3a3ba7-…` (`text-embedding-3-small`,
1536 dims). Nothing is fabricated. Run commands and caveats are documented in
[docs/operations/evaluation.md](./docs/operations/evaluation.md); the selection
policy is pre-registered in
[docs/operations/retrieval-selection-policy.md](./docs/operations/retrieval-selection-policy.md).

### Retrieval (8 cases, 5 strategies)

Updated 2026-08-16 after the lexical retrieval fix (OR-of-content-terms tsquery,
still scored by PostgreSQL `ts_rank_cd`, never BM25):

| Strategy | Hit@5 | Hit@20 | MRR | Latency | Planner calls |
| --- | --- | --- | --- | --- | --- |
| vector-only-v1 | 1.000 | 1.000 | 0.893 | 0.99s | 0 |
| lexical-only-v1 | 0.857 | 1.000 | 0.673 | 0.54s | 0 |
| **weighted-hybrid-v1 (default)** | **1.000** | **1.000** | **0.929** | **0.60s** | 0 |
| rrf-hybrid-v1 | 1.000 | 1.000 | 0.929 | 0.53s | 0 |
| agentic-decomposition-v1 | 1.000 | 1.000 | 0.929 | 2.93s | 1 |

No-answer cases are excluded from Hit@K and MRR; they are reported through a
false-positive metric instead (lexical-only and weighted-hybrid surface
plausible distractors under OR semantics, which the answer layer must abstain on
— `grounded-answer-v2` abstains 100%). Applying the pre-registered policy, **no
candidate beats the default** (agentic fails the latency/cost gates), so
`weighted-hybrid-v1` stays the production default.
Full breakdowns: [data/sample/benchmark-summary.md](./data/sample/benchmark-summary.md).

### End-to-end answer evaluation (8 cases, weighted-hybrid-v1)

| Approach | Citation validity | Citation coverage | No-answer abstention | Unexpected abstention | Mean latency | Mean tokens (in/out) |
| --- | --- | --- | --- | --- | --- | --- |
| grounded-answer-v1 (baseline) | 88% | 0.27 | 0% | 14% | 7.97s | 4400 / 176 |
| grounded-answer-v2 (strict) | 100% | **0.60** | **100%** | 0% | **6.00s** | 3480 / 164 |

Metric definitions (deterministic, per the evaluation runner): **No-answer
abstention rate** = fraction of insufficient-evidence (no-answer) cases in which
the answer abstained (`sufficient_evidence=false`); **Unexpected abstention
rate** = fraction of answerable cases in which the answer abstained despite
retrieved evidence (an over-cautious answer). Generation is nondeterministic, so
per-run numbers vary slightly; the table reflects the recorded run.

`grounded-answer-v2` (per-sentence citation markers + explicit abstention
policy) materially improves citation coverage and correctly abstains on the
insufficient-evidence case, with fewer tokens and lower latency. Full
breakdowns: [data/sample/answer-benchmark-summary.md](./data/sample/answer-benchmark-summary.md).
Historical `synthetic-chunks-v1` scores are **biased and superseded** (v1
generated each question from its own target chunk) and are not presented as
evidence.

**Validation mode:** the committed sample dataset (`sample-docs-v1`) carries no
target chunks — its cases are document-level, so this run validated targets by
resolved document URL, and citation validity means every cited identifier
resolves to retrieved evidence (not target-document membership). The evaluation
runner's primary path is chunk-level: cases that carry a target chunk are
validated by chunk id + content fingerprint (`validate_chunk`), with the
document-level URL check retained only as the fallback for no-target cases. The
runner does not claim chunk-level citation support for the document-level sample
run.

## Monitoring and feedback

The bot supports `/answer` Question Mode and `/feedback up` / `/feedback down`.
Feedback is idempotent (one vote per turn per user), stores **only safe
metadata** (direction, session/turn ids, retrieval strategy, projection
generation, generation model, answer prompt version, timestamp) — never the
question, answer, URLs, evidence, prompts, or tokens — and feeds the
`feedback_total` metric. The curated **Knowledge Assistant** Grafana dashboard
(7 panels: questions by outcome, question latency, retrieval candidates,
ingestion jobs/duration, citation outcomes, feedback) is provisioned
automatically from `config/grafana/` via Docker Compose. See
[docs/operations/docker.md](./docs/operations/docker.md).

## Limitations

- Sources are limited to public Substack, Medium, and X Article URLs; paywalled
  or JavaScript-only pages fail with explicit errors (Medium has an RSS
  fallback).
- The committed benchmark is a small 8-case sample; scores are indicative, not
  statistically significant, and a larger mixed dataset is required before any
  strategy switch.
- The optional LLM judge exists but its scores are uncalibrated model opinions
  until validated against human labels (this run did not apply it).
- X Articles require a paid Xquik/Tempo path; the sample corpus deliberately
  uses only free-to-fetch Substack URLs so reviewers can reproduce without cost.
- Article bodies are stored in your own vault and are never committed;
  copyright belongs to the authors.

## Privacy and cost trade-offs

- Canonical Markdown stays in **your** Obsidian vault; only titles/URLs and
  aggregate summaries are committed to the repository.
- Telemetry and feedback contain **no document content, prompts, questions,
  answers, source URLs, or credentials**.
- Costs: embedding/generation are pay-per-use (cents for small runs); fetching
  Substack/Medium is free; X Articles cost ~$0.00075 each via Tempo MPP.

## Reproducibility

- `uv.lock` pins all dependencies; `docker compose config --quiet` validates the
  deployment; the hermetic test image runs the full suite with a coverage gate.
- The sample corpus, datasets, and both evaluation summaries are committed
  (`data/sample/`), and every benchmark command is documented with its exact
  invocation and projection identity.
- When credentials or a live projection are unavailable, the runners report a
  verified `{"status": "not_run", "reason": …}` result instead of fabricating
  numbers.
- Projection changes require `projection-rebuild` + atomic
  `projection-activate`; the live projection is never replaced by a partial
  index.

## Rubric mapping (LLM Zoomcamp 2026)

| Rubric item | Where to look | Evidence |
| --- | --- | --- |
| Problem description | README above; requirements.md | [requirements.md](./requirements.md) |
| Retrieval flow (KB + LLM) | `demo ask` real RAG path | [application/retrieval.py](./src/knowledge_assistant/application/retrieval.py), [application/questions.py](./src/knowledge_assistant/application/questions.py) |
| Retrieval evaluation | 5 strategies, real numbers | [data/sample/benchmark-summary.md](./data/sample/benchmark-summary.md) |
| LLM evaluation | 2 answer approaches, real numbers | [data/sample/answer-benchmark-summary.md](./data/sample/answer-benchmark-summary.md) |
| Interface | Telegram bot + non-Telegram CLI demo | [application/bot.py](./src/knowledge_assistant/application/bot.py), `demo ask` |
| Ingestion pipeline | async worker + optional Prefect flow | [application/worker.py](./src/knowledge_assistant/application/worker.py), [infrastructure/orchestration/prefect_flow.py](./src/knowledge_assistant/infrastructure/orchestration/prefect_flow.py) |
| Monitoring | `/feedback` + 7-panel Grafana dashboard | [docs/operations/docker.md](./docs/operations/docker.md) |
| Containerization | everything in docker-compose | [compose.yaml](./compose.yaml) |
| Reproducibility | pinned deps, committed data, not_run honesty | [docs/operations/evaluation.md](./docs/operations/evaluation.md) |
| Best practice: hybrid search | default weighted hybrid + RRF | [domain/retrieval.py](./src/knowledge_assistant/domain/retrieval.py) |
| Best practice: re-ranking | deterministic diversity reranker | [domain/retrieval.py](./src/knowledge_assistant/domain/retrieval.py) |
| Best practice: query rewriting | agentic decomposition (bounded) | [infrastructure/openai/planning.py](./src/knowledge_assistant/infrastructure/openai/planning.py) |

## Documentation

- [Architecture](./docs/architecture/README.md) — goals, boundaries, ADRs
- [Evaluation & projection operations](./docs/operations/evaluation.md)
- [Docker operations runbook](./docs/operations/docker.md)
- [Retrieval selection policy](./docs/operations/retrieval-selection-policy.md)
- [Sample corpus](./data/sample/README.md)
