# Docker Operations Runbook

## Why this document exists

This runbook describes how to validate, start, inspect, smoke-test, and stop the
Docker deployment safely. It identifies commands that destroy local derived
state.

## Prerequisites

- Docker Desktop or another Docker-compatible daemon is running.
- Docker Compose v2 is available.
- The project `.env` contains:
  - a non-default `KNOWLEDGE_ASSISTANT_POSTGRES_PASSWORD`;
  - the host `KNOWLEDGE_ASSISTANT_VAULT_PATH`;
  - OpenAI and Telegram credentials when their adapters are enabled.
  - `KNOWLEDGE_ASSISTANT_X_ARTICLE_PROVIDER=xquik_mpp` to pay through Tempo,
    or `xquik` plus `KNOWLEDGE_ASSISTANT_XQUIK_API_KEY`;
  - `KNOWLEDGE_ASSISTANT_XQUIK_MPP_MAX_SPEND_USDC=0.001` when using MPP;
  - `KNOWLEDGE_ASSISTANT_EMBEDDING_MODEL`;
  - `KNOWLEDGE_ASSISTANT_TELEGRAM_ALLOWED_USER_IDS`, containing your numeric
    Telegram user ID (comma-separated when more than one user is authorized).
- The host vault directory exists and has permissions compatible with the container runtime user.

Never pass secrets as Docker build arguments. Do not copy `.env` into an image.
The Xquik key is injected only into the worker service and is omitted from
redacted configuration output.

For `xquik_mpp`, authorize the worker's dedicated Tempo wallet volume before
starting it:

```shell
docker compose --profile tools run --rm tempo-auth
```

Open the printed URL, confirm the displayed code and scoped spending permission,
then return to the terminal. The authorization is stored in the `tempo-wallet`
named volume and survives normal container rebuilds and restarts. It expires and
must be renewed according to the duration displayed by Tempo. Never paste a raw
wallet private key into `.env`.

## Validate configuration

Static Compose validation does not start containers:

```shell
docker compose config --quiet
```

Build the runtime and test targets:

```shell
docker compose build
docker compose --profile test build test
```

## Start the application

```shell
docker compose up -d --build
docker compose ps
```

Compose starts PostgreSQL, runs migrations once, then starts the bot and worker.
Running migration again is safe and should report that the schema is current:

```shell
docker compose run --rm migrate
```

## Verify configuration

```shell
docker compose --profile tools run --rm admin
```

The command prints only a redacted summary. It must never print API keys, bot tokens, or the database password.

## Run tests in the image

```shell
docker compose --profile test run --rm test
```

This executes the locked test environment from the Docker `test` target.
Coverage and test caches are written only to the container's bounded `/tmp`
filesystem so the test root filesystem remains read-only.

## Inspect state

```shell
docker compose ps
docker compose logs -f bot worker
docker compose images
```

PostgreSQL and application logs may contain operational metadata but should not
contain document bodies, API keys, or bot tokens.

## Local monitoring

Set the application OTLP/HTTP endpoint in `.env`:

```dotenv
KNOWLEDGE_ASSISTANT_OTEL_EXPORTER_OTLP_ENDPOINT=http://lgtm:4318
```

Start the application with the optional monitoring profile:

```shell
docker compose --profile monitoring up -d --build
```

Open `http://localhost:3000` and sign in with the local development credentials
`admin` / `admin`. The application emits bounded operational spans and metrics
for ingestion, retrieval, questions, citations, projection rebuilds,
evaluation, and answer feedback without including document content, prompts,
questions, source URLs, or credentials as telemetry attributes. Exact operations
are in the [Docker runbook](./docker.md).

A curated **Knowledge Assistant** dashboard is provisioned automatically from
`config/grafana/` (single-file bind mounts into the lgtm container) and is
available from the Grafana dashboard menu. It contains seven panels: questions
by outcome, question latency (p95), retrieval candidates (mean), ingestion jobs
by outcome, ingestion duration (p95), citation-validation outcomes, and answer
feedback up/down. Grafana Explore alone is not the dashboard; the provisioned
dashboard is the curated view.

In Grafana, open **Explore**, select the Tempo data source, and search service names
such as `knowledge-assistant-bot`, `knowledge-assistant-worker`,
`knowledge-assistant-admin`, or `knowledge-assistant-evaluation`. Select a trace to
inspect retrieval strategy, route, round count, stop reason, duration, and status.
For metrics, switch the Explore data source to Prometheus and search for
`question_stage_duration_seconds`, `retrieval_candidates`, `questions_total`, or
`ingestion_jobs_total`. Telemetry intentionally excludes question and document text.

Leaving `KNOWLEDGE_ASSISTANT_OTEL_EXPORTER_OTLP_ENDPOINT` empty selects the no-op
telemetry adapter. Monitoring availability does not alter ingestion or answer outcomes.

## Evaluation and projection administration

The admin profile exposes reproducible dataset generation, retrieval comparisons, and
safe projection build/activation commands. Outputs are persisted in the private,
git-ignored `var/evaluation/` directory. See the
[evaluation and projection runbook](./evaluation.md) before making model calls or
activating a candidate generation.

## Optional Prefect orchestration

A thin Prefect flow (the `orchestration` extra; never loaded by the normal bot or
worker runtime) submits every source in the public sample manifest through the
standard idempotent ingestion contract. It reuses the existing classifier,
repository, canonical-vault, and projection contracts and never replaces the
worker, which performs the actual fetching, Markdown writing, chunking, and
embedding:

```shell
docker compose --profile tools run --rm prefect-ingest
```

The service builds the `orchestration` Docker target (base image plus prefect),
submits idempotently (keyed `sample:<source_id>`), applies bounded retries (two
per source), and prints per-task state. Run it locally with
`uv sync --extra orchestration` and
`knowledge-assistant prefect-ingest --manifest data/sample/manifest.json`.

## Reviewer demo (no Telegram required)

Reviewers can verify the real RAG path without creating a Telegram bot:

```shell
docker compose --profile tools run --rm admin demo ingest
docker compose --profile tools run --rm admin \
  demo ask --question "What do engineers need to get good at?"
```

`demo ingest` submits the public sample manifest; `demo ask` embeds the question,
retrieves from the knowledge base, reranks, generates a grounded answer, validates
citations, and prints the answer plus its `Sources` section. It never sends the
question directly to the LLM without retrieval. `demo ask` (and `eval-*`,
`eval-generate`, `answer-eval-run`) incur small model costs; `demo ingest`,
`migrate`, and `sample-eval-prepare` do not.

## Telegram smoke test

1. Open a private chat with the configured bot and send a public Medium,
   Substack, or rich X Article URL.
2. Confirm the bot immediately replies that the knowledge is being saved.
3. Follow worker progress with `docker compose logs -f worker`.
4. Confirm a second Telegram message reports `Saved: <title>`.
5. Confirm a Markdown file appears below
   `Articles/medium/`, `Articles/substack/`, or `Articles/x/` in the configured
   host vault.
6. For an illustrated article, confirm its Markdown contains vault-aware
   `![[Assets/...]]` embeds and files exist below `Assets/<document_id>/`.
7. Send `/delete <exact article title>` and confirm the bot reports `Deleted:`.
8. Confirm the Markdown, its `Assets/<document_id>/` directory, registry row,
   and derived retrieval chunks were removed. Partial titles must only suggest
   possible exact titles and must not delete anything.

The worker log records aggregate `assets` and `omitted_images` counts but does
not log image bytes or article bodies. An omitted image indicates a permanent
invalid, unsupported, or unavailable asset; a transient image-host failure
causes the ingestion job to retry.

Inspect durable job state without exposing secrets:

```shell
docker compose exec postgres psql \
  -U knowledge_assistant \
  -d knowledge_assistant \
  -c "select job_id, source_provider, state, attempt_count, updated_at from ingestion_jobs order by created_at desc limit 10;"
```

If `bot` exits with an allowlist configuration error, set the numeric Telegram
user ID in `.env` and run `docker compose up -d bot`.

For X failures:

- `article_not_found` means the URL is an ordinary X post, long post, thread,
  or an unavailable Article; only rich X Articles are supported;
- HTTP 401 means the configured Xquik API key is invalid;
- HTTP 402 means billing or credits require operator action;
- `Tempo MPP wallet authorization is missing or expired` means rerunning the
  `tempo-auth` command above is required;
- a Tempo spending-cap failure means Xquik's live price exceeded
  `XQUIK_MPP_MAX_SPEND_USDC` or the scoped wallet lacks available USDC.e. The
  worker fails clearly and does not save a lossy fallback.

## Question Mode smoke test

After at least one document reaches `ready`:

1. Send `/answer`.
2. Confirm the bot reports that Question Mode started.
3. Ask a question whose answer appears in the saved document.
4. Confirm the answer contains evidence markers such as `[E1]` and a `Sources`
   section.
5. Ask a follow-up question to exercise bounded session history.
6. Send `/end` and confirm temporary history was deleted.

Question Mode requires `OPENAI_API_KEY`,
`KNOWLEDGE_ASSISTANT_EMBEDDING_MODEL`, and
`KNOWLEDGE_ASSISTANT_GENERATION_MODEL`. The optional reranking model is not used
by the initial deterministic diversity reranker.

## Answer feedback smoke test

Rate the most recent answer (or reply to the answer or to your own question to
rate that specific turn):

```text
/feedback up
/feedback down
```

The bot confirms the feedback was recorded. Feedback is idempotent: repeating a
vote on the same turn reports that it was already recorded instead of creating a
duplicate. One vote per turn per user is kept (the database constraint is
`UNIQUE (principal_id, session_id, turn_number)`).

**Privacy:** the `answer_feedback` table stores only safe pipeline metadata -
direction, session/turn identifiers, retrieval strategy, projection generation,
generation model, answer prompt version, and timestamp. It never stores the
question, the answer, source URLs, evidence, prompts, token counts, or
credentials, and no such content is placed in telemetry attributes. The
`feedback_total` metric carries only `direction` and `outcome` labels.

Feedback appears on the provisioned **Answer feedback up/down** dashboard panel;
stored rows are visible for audit with:

```shell
docker compose exec postgres psql \
  -U knowledge_assistant -d knowledge_assistant \
  -c "select principal_id, direction, retrieval_strategy, generation_model, \
      answer_prompt_version, created_at from answer_feedback order by created_at desc limit 10;"
```

## Stop or reset

Stop containers and retain database state:

```shell
docker compose down
```

Delete containers, networks, and the PostgreSQL named volume:

```shell
docker compose down --volumes
```

The second command irreversibly deletes local PostgreSQL operational and RAG state. Canonical Markdown remains in the host vault and should permit a future rebuild, but unfinished jobs and temporary sessions are lost.

## Current scope

This deployment supports ingestion of public Medium and Substack articles,
including Substack publications hosted on verified custom domains, and public X
rich X Articles through strict Xquik ordered blocks. Ordinary X posts, long
posts, and threads are intentionally unsupported. It also
supports temporary Telegram Question Mode with grounded hybrid retrieval and
citations.
Authorized private-chat users can also delete a saved article with
`/delete <exact article title>`. The case-insensitive title must resolve to one
current document; otherwise the bot leaves the vault unchanged and returns
suggestions when available.
After Question Mode, users can rate an answer with `/feedback up` or
`/feedback down`; feedback is stored privacy-safely and visible on the curated
Knowledge Assistant dashboard.
Supported article images (JPEG, PNG, WebP, and GIF) are copied into the vault as
content-addressed assets and linked relatively from Markdown.
When Medium denies direct HTML with HTTP 403, the worker attempts to locate the
exact requested story in Medium's public author or publication RSS feed. Older
stories absent from the current feed, authenticated content, and JavaScript-only
sites may still fail and are reported as failed jobs.
