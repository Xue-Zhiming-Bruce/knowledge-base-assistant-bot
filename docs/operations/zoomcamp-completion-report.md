# LLM Zoomcamp 2026 — Completion Report

Generated 2026-08-16 by the final verification run. All numbers below are real
output re-derived read-only from the private result files
(`var/evaluation/sample-retrieval-results.jsonl`, `var/evaluation/answer-results.jsonl`)
recorded by the benchmark runs; nothing is fabricated.

## 1. Checks executed (all passed)

> **Validation mode:** the committed sample answer-evaluation run
> (`sample-docs-v1`) is document-level — cases carry no target chunks, so
> targets were validated by resolved document URL, and citation validity means
> every cited identifier resolves to retrieved evidence, not target-document
> membership. The `AnswerEvaluationRunner` primary path is chunk-level
> fingerprint validation (`validate_chunk` on target_chunk_id +
> content_fingerprint) for cases that carry a target chunk; document-level URL
> validation is retained only as the fallback for no-target cases (routing in
> `src/knowledge_assistant/application/evaluation_targets.py`). The refactor did
> not alter the sample run's results, so no benchmark re-run was required.

> **Metric labels:** in the end-to-end tables, **No-answer abstention rate** is
> the fraction of insufficient-evidence (no-answer) cases in which the answer
> abstained (`sufficient_evidence=false`); **Unexpected abstention rate** is the
> fraction of answerable cases in which the answer abstained despite retrieved
> evidence (an over-cautious answer). Both are deterministic runner metrics.

> **Copyright review:** all public artifacts (README.md, requirements.md,
> data/sample/*, docs/*, config/grafana/*) were reviewed for long copyrighted
> excerpts and distinctive source-derived phrasing, not only for secrets.
> Reference answers and required facts in `data/sample/manifest.json` are
> original concise paraphrases written for evaluation (source-close fragments
> were reworded in this pass); the README example output is labeled a synthetic
> model-generated answer; only article titles and URLs are committed, never
> bodies or excerpts.

> **Grafana dashboard evidence:** the curated 7-panel dashboard loads — safe API
> proof (no credentials or private data) is committed at
> `docs/assets/grafana-dashboard-verification.md`.

| Check | Result |
| --- | --- |
| `uv run pytest` (291 tests, `PYTHONPATH=src` workaround for the local macOS .pth quirk) | 291 passed |
| Docker hermetic test container (`docker compose --profile test run --rm test`) | 290 passed, 1 skipped (live-DB lexical test skips without a database), coverage gate satisfied (≥90%) |
| `uv run ruff check .` | All checks passed |
| `uv run mypy` (93 source files, `MYPYPATH=src`) | No issues found |
| `docker compose config --quiet` | OK |
| `uv lock --check` | Resolved 143 packages, lock current |
| Live stack health (bot, worker, postgres, lgtm) | All Up (healthy) |

The suite grew from 284 to 291 tests across the run: 4 lexical-retrieval tests
(`tests/test_lexical_retrieval.py`) and 3 answer-evaluation target-validation
tests were added in Loop 2.

Commands not executed and why:

- **Bare `uv run pytest` / `uv run mypy`** without `PYTHONPATH=src` /
  `MYPYPATH=src`: the local `.venv` editable-install `.pth` file intermittently
  receives the macOS `UF_HIDDEN` flag, which CPython's `site` module skips; the
  documented workaround is used. The identical suite passes in the hermetic
  Docker test image, so this is a local-environment quirk, not a code issue.
- **`answer-eval-run --judge-model …`** (LLM judge): not executed. Judge scores
  are uncalibrated model opinions; the committed end-to-end tables use
  deterministic metrics instead, and the judge path remains unit-tested.
- **Live Telegram `/feedback` conversation**: not exercised end-to-end (requires
  the user to message the bot); covered by unit tests, and the bot runs the new
  image healthy.
- **Re-ingestion / re-benchmark**: not repeated; the sample corpus was ingested
  and both benchmarks ran once with their results recorded and committed.
- **No destructive operations**: `docker compose down --volumes` was never run;
  the PostgreSQL volume, tempo wallet, and lgtm data volumes are intact, and the
  canonical Obsidian vault was never deleted.

## 2. Actual retrieval results (sample-docs-v1, projection bd3a3ba7-…)

Run 2 (after the lexical retrieval fix; see below):

| Strategy | Hit@5 | Hit@20 | MRR | Latency | Planner calls | No-answer FP |
| --- | --- | --- | --- | --- | --- | --- |
| vector-only-v1 | 1.000 | 1.000 | 0.893 | 0.99s | 0.0 | 0.000 |
| lexical-only-v1 | 0.857 | 1.000 | 0.673 | 0.54s | 0.0 | 1.000 |
| weighted-hybrid-v1 (default) | 1.000 | 1.000 | 0.929 | 0.60s | 0.0 | 1.000 |
| rrf-hybrid-v1 | 1.000 | 1.000 | 0.929 | 0.53s | 0.0 | 0.000 |
| agentic-decomposition-v1 | 1.000 | 1.000 | 0.929 | 2.93s | 1.0 | 0.000 |

**Lexical retrieval fix (Loop 2):** the first run's `lexical-only-v1` 0.000 was
root-caused to `websearch_to_tsquery` AND-ing every query term against an
unstemmed `simple` tsvector, so natural paraphrased questions almost never
matched a whole chunk. The lexical leg now builds an OR-of-content-terms query
(`build_lexical_tsquery`, `question_repository.py`) scored by PostgreSQL
`ts_rank_cd` (never BM25), with a regression test proving the "headcount
reduction … AI's value" question retrieves the expected *Pinhole View of AI
Value* document. Lexical-only rose to 0.857 Hit@5 / 0.673 MRR and the hybrid
strategies' MRR rose 0.893 → 0.929 with a live lexical leg. No-answer cases
remain excluded from Hit@K/MRR; the OR semantics surface plausible distractors
for lexical/weighted (no-answer FP 1.000) which the answer layer must abstain on
(`grounded-answer-v2` abstains 100%).

Committed summary: `data/sample/benchmark-summary.md`.

## 3. Actual end-to-end LLM evaluation results (sample-docs-v1, weighted-hybrid-v1)

| Approach | Citation validity | Citation coverage | No-answer abstention | Unexpected abstention | Latency | Tokens (in/out) |
| --- | --- | --- | --- | --- | --- | --- |
| grounded-answer-v1 (baseline) | 88% | 0.27 | 0% | 14% | 7.97s | 4400 / 176 |
| grounded-answer-v2 (strict) | 100% | 0.60 | 100% | 0% | 6.00s | 3480 / 164 |

Committed summary: `data/sample/answer-benchmark-summary.md`.

These numbers are from the 2026-08-16 re-run after the public-safety pass
reworded the distinctive required facts and reference answers in
`data/sample/manifest.json` (required facts feed the fact-lexical-coverage
metric, so the affected answer-evaluation benchmark was re-run rather than
keeping stale numbers). Generation is nondeterministic, so per-run values vary
slightly; this table reflects the recorded run.

**Validation mode:** the committed sample dataset (`sample-docs-v1`) is
document-level — cases carry no target chunks, so this run validated targets by
resolved document URL, and citation validity means every cited identifier
resolves to retrieved evidence, not target-document membership. The
`AnswerEvaluationRunner` primary path is chunk-level fingerprint validation
(`validate_chunk` on target_chunk_id + content_fingerprint) for cases that carry
a target chunk; document-level URL validation is retained only as the fallback
for no-target cases, and a case with neither fails closed. The validation-mode
refactor (Loop 2) did not alter results (every sample case takes the same
document-level fallback path); the separate 2026-08-16 re-run above was
required by the public-safety required-fact rewording, not by the refactor.

## 4. Files changed across this run

Core: `src/knowledge_assistant/domain/evaluation.py`, `domain/query.py`,
`domain/retrieval.py`, `ports/evaluation.py`, `ports/answers.py`,
`application/evaluation.py`, `application/questions.py`, `application/bot.py`,
`application/retrieval.py`, `infrastructure/openai/evaluation.py`,
`infrastructure/openai/answers.py`, `infrastructure/openai/planning.py`,
`infrastructure/postgres/question_repository.py`,
`infrastructure/postgres/evaluation_repository.py`,
`infrastructure/postgres/sql/0007_answer_feedback.sql`,
`infrastructure/telegram/client.py`,
`infrastructure/orchestration/prefect_flow.py`, `cli.py`, `config.py`.

Config/deploy: `compose.yaml`, `Dockerfile`, `pyproject.toml`, `uv.lock`,
`.env.example`, `config/grafana/dashboards/knowledge-assistant.json`,
`config/grafana/provisioning/dashboards/99-knowledge-assistant.yaml`.

Docs/evidence: `README.md`, `requirements.md`, `data/sample/manifest.json`,
`data/sample/README.md`, `data/sample/benchmark-summary.md`,
`data/sample/answer-benchmark-summary.md`,
`docs/operations/evaluation.md`, `docs/operations/docker.md`,
`docs/operations/retrieval-selection-policy.md`,
`docs/architecture/08-evaluation-architecture.md`.

Tests: `tests/test_evaluation.py`, `tests/test_synthetic_v2.py`,
`tests/test_answer_evaluation.py`, `tests/test_sample_corpus.py`,
`tests/test_feedback.py`, `tests/test_prefect_flow.py`,
`tests/test_demo_cli.py`, `tests/test_questions.py`,
`tests/test_telegram.py`, `tests/test_postgres_migrations.py`.

## 5. Rubric gaps closed

- Retrieval evaluation: all five strategies benchmarked with Hit@5/Hit@20/MRR,
  latency, planner calls/tokens, type/difficulty breakdowns, and no-answer
  false-positive metrics on a committed human-authored dataset.
- LLM evaluation: end-to-end runner comparing two meaningfully different answer
  approaches (grounded-answer-v1/v2) with deterministic citation validity,
  coverage, abstention, latency, and token metrics; optional versioned LLM judge.
- Interface: Telegram bot plus a non-Telegram `demo ask` CLI over the real RAG
  path (reviewer quick start without Telegram).
- Ingestion pipeline: automated async worker plus an optional rubric-recognized
  Prefect flow (`prefect-ingest`).
- Monitoring: idempotent, privacy-safe `/feedback up|down` + a curated 7-panel
  Grafana dashboard provisioned through Docker Compose.
- Containerization: everything in `docker compose` (PostgreSQL, migration, bot,
  worker, admin, prefect-ingest, test, lgtm).
- Reproducibility: pinned `uv.lock`, committed sample corpus + datasets +
  public-safe summaries, honest `not_run` reporting when credentials/projection
  are unavailable, atomic projection activation.
- Best practices: hybrid search default + RRF and agentic evaluation (+1),
  deterministic diversity reranking (+1).

## 6. Remaining rubric risks

- Benchmarks are from an 8-case / 4-document sample: indicative, not
  statistically significant; a larger mixed dataset is needed before any
  strategy switch.
- No dedicated query-rewriting best practice (only bounded agentic
  decomposition); no cloud deployment (bonus, not attempted).
- The optional LLM judge is uncalibrated (requires validation against human
  labels); this run used deterministic metrics.
- The production default stays `weighted-hybrid-v1` per the pre-registered
  selection policy — a deliberate, evidence-backed non-change.

## 7. Remaining credential/human tasks

- (Optional) exercise live Telegram `/feedback` and Question Mode with the real
  bot; a human reviewer should review the frozen sample dataset.
- Obtain a larger reviewed mixed dataset (multi-document, human-authored,
  follow-up, hard-negative cases) for stronger paired evidence.
- Calibrate the LLM judge against human labels if judge scores are used.

## 8. External tasks (separate, require human action)

- Publish a separate public GitHub repository: the working directory has **no
  `.git` repository yet** (git init + baseline commit + push required; the
  .gitignore already excludes `.env`, `var/`, and caches).
- Submit the repository URL and commit hash on the course management platform
  (Project Attempt 1: 27 July 2026 23:00; Attempt 2: 10 August 2026 23:00).
- Complete the three required peer reviews.
- Update the certificate name on the platform enrollment page.
- Obtain organizer approval if the submission deadline has passed.

## 9. Git baseline (Loop 2)

A Git repository was initialized in the working tree on 2026-08-16 and the
corrected tree was captured in the baseline commit:

- **Commit hash:** `95713058bc701945666dac1729a28ba576923507`
- Files committed: 153 (source, tests, docs, `data/sample/`, `config/grafana/`,
  `compose.yaml`, `Dockerfile`, `pyproject.toml`, `uv.lock`, `.env.example`,
  `.gitignore`, `.dockerignore`, `AGENTS.md`, `README.md`, `requirements.md`)

Safety verification before and after the commit:

- `git status --short` is clean after the baseline commit (no unintended files).
- `git ls-files` contains no `.env`, `var/`, `*.log`, `.DS_Store`, or
  `__pycache__` paths; the only `.env*` file tracked is the placeholder
  `.env.example`.
- `git grep` across tracked files finds no real secret values (env-var names in
  code like `environ.get("OPENAI_API_KEY")` are references, not values; docs
  contain no `KEY=<value>` assignments or private keys).
- `.vscode/` was added to `.gitignore` (personal editor config, consistent with
  `.idea/`); the private Obsidian vault lives outside the repository and `var/`
  (vault copies, evaluation results, granite-* artifacts) remains ignored.

`git rev-parse HEAD` returns the latest commit; the baseline hash above plus
any follow-up commits (such as this report appendix) form the history.
