# Knowledge Base Assistant (Knowledge Assistant)

A personal knowledge engine (Python) that ingests long-form content (Medium, Substack, X Articles via Xquik) into canonical Markdown in an Obsidian vault and answers questions through rebuildable RAG projections over PostgreSQL/pgvector, exposed today through a Telegram bot client.

Keywords that refer to this project: "Knowledge Base Assistant", "Knowledge Assistant", "knowledge engine", "知识库助手".

## Directory overview

- `src/knowledge_assistant/` — application code: config, CLI, application layer (bot, worker, questions, retrieval, projections, deletion, assets, evaluation)
- `docs/architecture/` — architecture source of truth; starts at `docs/architecture/README.md`; ADRs under `docs/architecture/decisions/`
- `docs/operations/` — runbooks: `docker.md` (deployment/ops) and `evaluation.md` (storage contract, safe rebuild workflow)
- `tests/` — test suite
- `config/` — configuration files
- `compose.yaml` — Docker Compose: PostgreSQL 17 + pgvector, migration, bot, worker, admin tooling, optional test/monitoring profiles
- `var/` — git-ignored local runtime data (e.g. `var/evaluation/`)

## Common commands

Development (Python 3.12+, uv):

```shell
uv sync --extra dev
PYTHONPATH=src uv run pytest       # see the macOS note below
uv run ruff check .
MYPYPATH=src uv run mypy          # see the macOS note below
```

> **macOS note:** the local `.venv` editable-install marker can intermittently
> receive the macOS `UF_HIDDEN` flag, which CPython's `site` module skips,
> breaking plain `uv run pytest`/`uv run mypy`. The reliable invocations are
> `PYTHONPATH=src uv run pytest` and `MYPYPATH=src uv run mypy`; the Docker test
> image runs plain `pytest` with no workaround.

Docker (primary runtime boundary — start Docker Desktop first):

```shell
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs -f bot worker
docker compose --profile tools run --rm admin
docker compose --profile test run --rm test
docker compose down            # keep PostgreSQL volume
docker compose down --volumes  # destructive: deletes DB volume only; host vault Markdown is separate
```

## Key conventions

- Never commit real credentials. A git-ignored `.env` holds local config (`KNOWLEDGE_ASSISTANT_*` prefix, `OPENAI_API_KEY`).
- Canonical Markdown in the vault is the single source of truth; all PostgreSQL rows (chunks, embeddings, full-text) are rebuildable projections scoped to a generation.
- Production default retrieval strategy is `weighted-hybrid-v1`; other strategies are explicit experiments. Don't change the default without paired evaluation on a reviewed local dataset.
- X sources are Article-only via Xquik (`xquik_mpp` Tempo default, or `xquik` API key). Unknown/lossy blocks and non-Article URLs fail explicitly — never reconstruct.
- `projection-rebuild` / `projection-activate` must be used when embedding model/dimension/chunker version changes; never swap the live projection with a partial index.
- Bot fails closed when the Telegram allowlist is empty; use numeric user IDs.
- Telemetry must never include document content, prompts, questions, source URLs, or credentials as attributes.
- Configuration is redacted in diagnostics; `OPENAI_API_KEY` and Xquik keys are never printed.
