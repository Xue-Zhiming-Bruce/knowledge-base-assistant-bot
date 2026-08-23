# Knowledge Assistant

A personal knowledge engine that saves long-form articles (blog posts,
Substack, Medium, X Articles) as canonical Markdown in your own Obsidian vault
and answers questions with grounded, cited answers from what you saved — via a
Telegram bot and a CLI demo.

## Problem

Generic chat models answer from training data: no memory of the specific
articles you saved, hallucinated details about niche essays, no citations. This
project inverts that — your vault is the source of truth, every answer is
generated from retrieved evidence with validated citations, and if the answer
isn't in your knowledge base, it abstains instead of guessing.

## Demo

Send the bot an article URL — it's saved to your vault and indexed:

![Saving an article](docs/assets/images/screenshot-save.png)

Ask questions; answers cite the exact sources:

![Answering a question](docs/assets/images/screenshot-answer.png)

No Telegram needed to try the full RAG path:

```shell
docker compose up -d --build postgres migrate worker
docker compose --profile tools run --rm admin demo ingest
docker compose --profile tools run --rm admin \
  demo ask --question "What do engineers actually need to get good at?"
```

Real output (`weighted-hybrid-v1`, truncated):

> According to the essay, engineers need to become good at far more than
> programming: they need to navigate the surrounding human and organizational
> work. [E1]
>
> Sources:
> [E1] 21 Lessons from 14 Years at Google — https://addyo.substack.com/p/21-lessons-from-14-years-at-google

## Supported sources

| Source | Provider | Notes |
| --- | --- | --- |
| Any public blog/article page | `web` | static HTML pages from any HTTPS site |
| Substack essays | `substack` | including `.substack.com` publications |
| Medium articles | `medium` | with a bounded RSS fallback for HTTP 403 |
| X Articles | `xquik_mpp` / `xquik` | Article-only; ordinary posts/threads fail clearly |

Paywalled and JavaScript-only pages fail with explicit errors rather than
ingesting garbage.

## Evaluation

All scores are real runs against the committed `sample-docs-v1` dataset
(25 curated questions over 4 public essays) on projection `bd3a3ba7-…`
(`text-embedding-3-small`, 1536 dims). Rerun commands are in
[data/sample/README.md](./data/sample/README.md); full breakdowns in
[data/sample/benchmark-summary.md](./data/sample/benchmark-summary.md) and
[data/sample/answer-benchmark-summary.md](./data/sample/answer-benchmark-summary.md).

Retrieval (2026-08-17; no-answer cases excluded from Hit@K/MRR):

| Strategy | Hit@5 | Hit@20 | MRR | Latency | No-answer FP |
| --- | --- | --- | --- | --- | --- |
| vector-only-v1 | 1.000 | 1.000 | 0.871 | 0.61s | 0.333 |
| lexical-only-v1 | 0.773 | 0.909 | 0.656 | 0.48s | 0.667 |
| **weighted-hybrid-v1 (default)** | **0.909** | **1.000** | **0.814** | **0.47s** | 0.667 |
| rrf-hybrid-v1 | 0.909 | 1.000 | 0.848 | 0.45s | 0.333 |
| agentic-decomposition-v1 | 0.955 | 1.000 | 0.873 | 2.45s | 0.333 |

Per the pre-registered selection policy, no candidate clears all four gates
(exact-lookup slice regression, tie, latency, aggregate quality respectively),
so `weighted-hybrid-v1` stays the default.

End-to-end answers (structured LLM judge; uncalibrated model opinions until
human-labeled calibration is run — currently `not_run`):

| Approach | Citation validity | Coverage | No-answer abstention | Judge overall |
| --- | --- | --- | --- | --- |
| grounded-answer-v1 (baseline) | 96% | 0.36 | 0% | 2.56 |
| grounded-answer-v2 (default) | 100% | **0.48** | **66.7%** | **2.92** |

## Setup

Requirements: Python 3.12+ with [uv](https://docs.astral.sh/uv/), Docker
Desktop, OpenAI API key, and a folder to use as your Obsidian vault.

```shell
git clone https://github.com/Xue-Zhiming-Bruce/knowledge-base-assistant-bot.git
cd knowledge-base-assistant-bot
cp .env.example .env          # fill in OPENAI_API_KEY etc.; see notes below
uv sync --extra dev           # dev only: PYTHONPATH=src uv run pytest
docker compose config --quiet
docker compose up -d --build
```

Configuration lives in `.env` (required: PostgreSQL connection from the compose
stack, `KNOWLEDGE_ASSISTANT_VAULT_PATH`, `OPENAI_API_KEY`). Optional:

- **Telegram bot**: `TELEGRAM_TOKEN` + numeric `TELEGRAM_ALLOWED_USER_IDS`;
  runs under the `telegram` profile (`docker compose --profile telegram up -d bot`)
- **X Articles**: `X_ARTICLE_PROVIDER` = Tempo MPP (`tempo-auth`) or Xquik API
  key; ~$0.00075 per Article
- **Grafana monitoring**: `OTEL_EXPORTER_OTLP_ENDPOINT=http://lgtm:4318`

Never commit `.env`. Model calls cost cents on small runs; everything else
(`migrate`, `check-config`, `sample-ingest`, `demo ingest`) is free.

## How it works

```mermaid
flowchart LR
    S["Sources: any blog / Substack / Medium / X Article URLs"] -->|submit idempotent job| W[Worker]
    W -->|fetch + extract| V[(Obsidian vault<br/>canonical Markdown)]
    W -->|chunk + embed| P[(PostgreSQL + pgvector<br/>rebuildable projection)]
    Q[Question] --> R[RetrievalOrchestrator<br/>strategy: vector / lexical / hybrid / RRF / agentic]
    P --> R
    R -->|bounded evidence + rerank| A[Grounded answer generation<br/>grounded-answer-v1 / v2]
    A -->|citation validation| Out[Answer + Sources]
    Out -->|feedback up or down| FB[(answer_feedback<br/>safe metadata only)]
    Out -->|OTLP| G[Grafana dashboard<br/>7 curated panels]
    W -->|OTLP| G
```

**Ingestion**: the worker fetches the article, extracts canonical Markdown into
the vault (with content-addressed image assets), then chunks, embeds, and
writes projection rows scoped to a generation. The vault Markdown is the source
of truth — every database row can be rebuilt with `projection-rebuild` +
atomic `projection-activate`.

**Answering**: the question retrieves candidate chunks from the active
projection (semantic, lexical, weighted hybrid, RRF, or bounded agentic
decomposition), a diversity reranker bounds the context, and a structured
generator produces a cited answer. A deterministic validator rejects answers
whose citations don't resolve to the retrieved evidence.

**Monitoring**: `/feedback up`/`down` stores only safe metadata (never
questions, answers, or URLs) and feeds a Grafana dashboard provisioned
automatically from `config/grafana/`.

## Limitations

- The benchmark is a 25-case sample over 4 documents; scores are indicative,
  not significant. The judge scores are uncalibrated model opinions.
- JavaScript-only pages can't be extracted and fail explicitly.
- X Articles require the paid Xquik/Tempo path; other sources are free.
- Article bodies stay in your vault and are never committed; copyright belongs
  to the authors.
