# LLM Zoomcamp 2026 — Completion Report

Generated 2026-08-17 by the Loop 3 final verification run. All numbers below
are real output re-derived from the result files
(`var/evaluation/sample-retrieval-results.jsonl`,
`var/evaluation/answer-results.jsonl`) recorded by real benchmark reruns on
2026-08-17; nothing is fabricated, and no benchmark result is carried forward
without a rerun.

## 1. Checks executed (all passed)

> **Validation mode:** the committed sample answer-evaluation run
> (`sample-docs-v1`) is document-level — cases carry no target chunks, so
> targets were validated by resolved document URL, and citation validity means
> every cited identifier resolves to retrieved evidence, not target-document
> membership. The `AnswerEvaluationRunner` primary path is chunk-level
> fingerprint validation (`validate_chunk` on target_chunk_id +
> content_fingerprint) for cases that carry a target chunk; document-level URL
> validation is retained only as the fallback for no-target cases (routing in
> `src/knowledge_assistant/application/evaluation_targets.py`).

> **Metric labels:** in the end-to-end tables, **No-answer abstention rate** is
> the fraction of insufficient-evidence (no-answer) cases in which the answer
> abstained (`sufficient_evidence=false`); **Unexpected abstention rate** is the
> fraction of answerable cases in which the answer abstained despite retrieved
> evidence (an over-cautious answer). Both are deterministic runner metrics.
> **No-answer cases are excluded from Hit@K and MRR** and are reported through
> the explicit no-answer false-positive metric instead.

> **Metric separation:** deterministic metrics (citation validity/coverage,
> required-fact **lexical** coverage — a token-overlap proxy, never presented as
> semantic factual correctness — abstention, latency, tokens), **model-judge
> scores** (structured `answer-judge-rubric-v1`, uncalibrated model opinions),
> and **human labels** (`data/sample/answer-human-labels.jsonl`, reviewed labels
> only) are kept strictly separate in every committed summary. Judge calibration
> is **`not_run`** until a human reviews a label subset (no fabricated labels are
> committed).

> **Copyright review:** all public artifacts (README.md, requirements.md,
> data/sample/*, docs/*, config/grafana/*) were reviewed for long copyrighted
> excerpts and distinctive source-derived phrasing, not only for secrets.
> Reference answers and required facts in `data/sample/manifest.json` are
> original concise paraphrases written for evaluation; the README example output
> is labeled a synthetic model-generated answer; only article titles and URLs
> are committed, never bodies or excerpts.

> **Grafana dashboard evidence:** the curated 7-panel dashboard is provisioned
> **and** live-data-verified against real safe traffic (all seven panel PromQL
> queries returned data; telemetry labels carry no questions, answers, prompts,
> URLs, or credentials). See
> `docs/assets/grafana-dashboard-verification.md`, which explicitly
> distinguishes provisioning from live-data verification and honestly records
> that no screenshot was captured (no image renderer; none fabricated).

| Check | Result |
| --- | --- |
| `PYTHONPATH=src uv run pytest` (local; the macOS `.pth` quirk workaround) | 321 passed, exit 0 |
| Docker hermetic test container (`docker compose --profile test run --rm test`) | 319 passed, 2 skipped (live-DB lexical + feedback-durability tests skip without a database URL), **coverage 91.03% (gate ≥90% reached)** |
| `uv run ruff check .` | All checks passed |
| `MYPYPATH=src uv run mypy` (95 source files) | No issues found |
| `docker compose config --quiet` | OK |
| `uv lock --check` | Lock current |
| Live stack health (postgres, migrate, worker, bot, lgtm) | Up (healthy) |

The suite grew across Loop 3 from 291 to 321 tests: answer-prompt-version
configuration tests, centralized-generator tests, Telegram-free worker/Prefect
tests, durable-feedback integration and Grafana-metric tests, coverage tests
(bot command matrix, worker edge paths, domain validation, deletion paths),
and judge-calibration tests.

Commands not executed and why:

- **Bare `uv run pytest` / `uv run mypy`** without `PYTHONPATH=src` /
  `MYPYPATH=src`: the local `.venv` editable-install `.pth` file intermittently
  receives the macOS `UF_HIDDEN` flag, which CPython's `site` module skips; the
  documented workaround is used. The identical suite passes in the hermetic
  Docker test image, so this is a local-environment quirk, not a code issue.
- **Live Telegram `/feedback` conversation**: not exercised end-to-end (requires
  the user to message the bot); the bot runs healthy with a single allowlisted
  user, and the feedback path is covered by unit and integration tests.
- **Judge calibration (`answer-eval-calibrate`)**: executed and reported
  `{"status": "not_run"}` — no reviewed human labels exist yet; judge scores are
  uncalibrated model opinions until the human task is completed.
- **Dashboard screenshot**: not captured (the lgtm image has no image-renderer
  plugin/chromium); explicitly documented as not fabricated.
- **No destructive operations**: `docker compose down --volumes` was never run;
  the PostgreSQL volume, tempo wallet, and lgtm data volumes are intact, and the
  canonical Obsidian vault was never deleted.

## 2. Actual retrieval results (sample-docs-v1, projection bd3a3ba7-…)

Real rerun 2026-08-17 on the expanded 25-case dataset (22 answerable, 3
insufficient-evidence; no-answer cases excluded from Hit@K/MRR and reported via
the no-answer false-positive metric):

| Strategy | Hit@5 | Hit@20 | MRR | Latency | Planner calls | No-answer FP |
| --- | --- | --- | --- | --- | --- | --- |
| vector-only-v1 | 1.000 | 1.000 | 0.871 | 0.61s | 0.0 | 0.333 |
| lexical-only-v1 | 0.773 | 0.909 | 0.656 | 0.48s | 0.0 | 0.667 |
| weighted-hybrid-v1 (default) | 0.909 | 1.000 | 0.814 | 0.47s | 0.0 | 0.667 |
| rrf-hybrid-v1 | 0.909 | 1.000 | 0.848 | 0.45s | 0.0 | 0.333 |
| agentic-decomposition-v1 | 0.955 | 1.000 | 0.873 | 2.45s | 1.0 | 0.333 |

The expanded mix (synthesis, follow-up, hard-negative questions) is more
demanding than the original 8 cases: `lexical-only-v1` Hit@5 dropped to 0.773,
and no-answer false-positive rates are non-zero for several strategies (the
answer layer must abstain — `grounded-answer-v2` abstains on 2 of 3 no-answer
cases). Applying the pre-registered
[selection policy](./retrieval-selection-policy.md), **no candidate satisfies
all four gates**: `weighted-hybrid-v1` remains the production default.
`vector-only-v1` clears aggregate quality (+0.091 Hit@5, +0.057 MRR), latency
(1.31x), cost, operational complexity, and no-answer false positives (0.333 vs
0.667) but fails the pre-registered per-slice regression gate: exact_lookup MRR
drops from 1.000 to 0.833 (−0.167 beyond the 0.01 tolerance), driven by
`sample-q-09` ranking the target at position 3 (rr 0.333) vs position 1 for the
hybrid. `rrf-hybrid-v1` ties Hit@5 (0.909) and `agentic-decomposition-v1` fails
latency (≈5.2x) and adds planner cost.

Committed summary: `data/sample/benchmark-summary.md`.

## 3. Actual end-to-end LLM evaluation results (sample-docs-v1, weighted-hybrid-v1)

Real rerun 2026-08-17 on the 25-case dataset with the structured LLM judge
applied (`answer-judge-rubric-v1`, model = the configured generation model;
judge scores are uncalibrated model opinions):

| Approach | Citation validity | Citation coverage | No-answer abstention | Unexpected abstention | Judge overall | Latency | Tokens (in/out) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| grounded-answer-v1 (baseline) | 96% | 0.36 | 0% | 13.6% | 2.56 | 3.88s | 4367 / 154 |
| grounded-answer-v2 (production default) | 100% | 0.48 | 66.7% | 9.1% | 2.92 | 3.50s | 3447 / 120 |

Committed summary: `data/sample/answer-benchmark-summary.md`.

`grounded-answer-v2` is the configured production default
(`KNOWLEDGE_ASSISTANT_ANSWER_PROMPT_VERSION`, used by Telegram Question Mode and
`demo ask`); `grounded-answer-v1` remains the explicit baseline override.
Generation is nondeterministic, so per-run values vary slightly; this table
reflects the recorded run. The judge scores are **uncalibrated model opinions**;
calibration against reviewed human labels is `not_run` until
`data/sample/answer-human-labels.jsonl` is filled and `answer-eval-calibrate` is
run (procedure in `data/sample/README.md`).

## 4. Files changed across Loop 3

Core: `src/knowledge_assistant/config.py` (answer prompt version, Telegram-free
worker requirements), `cli.py` (centralized generator, `answer-eval-calibrate`,
Telegram-free sample/Prefect recipient resolution), `application/worker.py`
(optional Telegram client), `application/evaluation.py` (judge calibration),
`infrastructure/postgres/ingestion_repository.py` (optional recipient),
`infrastructure/postgres/sql/0008_feedback_durable.sql` (new forward migration),
`infrastructure/orchestration/prefect_flow.py` (no fake recipient).

Config/deploy: `compose.yaml` (bot on the optional `telegram` profile; admin
sample-data bind), `.env.example`, `pyproject.toml` (unchanged coverage
threshold).

Docs/evidence: `README.md`, `requirements.md`, `data/sample/manifest.json`
(8 → 25 cases), `data/sample/README.md`,
`data/sample/answer-human-labels.jsonl` (empty template),
`data/sample/benchmark-summary.md`, `data/sample/answer-benchmark-summary.md`,
`docs/operations/evaluation.md`, `docs/operations/docker.md`,
`docs/assets/grafana-dashboard-verification.md`,
`docs/architecture/10-security-privacy-and-reliability.md`,
`docs/architecture/decisions/ADR-0006-temporary-question-sessions.md`.

Tests: `tests/test_config.py`, `tests/test_demo_cli.py`,
`tests/test_application_services.py`, `tests/test_prefect_flow.py`,
`tests/test_feedback.py`, `tests/test_postgres_migrations.py`,
`tests/test_sample_corpus.py`, `tests/test_evaluation.py`,
`tests/test_retrieval_strategies.py`, `tests/test_deletion.py`.

## 5. Rubric gaps closed

- Retrieval evaluation: all five strategies benchmarked with Hit@5/Hit@20/MRR,
  latency, planner calls/tokens, type/difficulty breakdowns, and explicit
  no-answer false-positive/abstention metrics on a committed curated
  25-case dataset.
- LLM evaluation: end-to-end runner comparing two answer approaches with
  deterministic metrics **and** a structured LLM judge applied in a real rerun;
  judge scores clearly labeled uncalibrated; a calibration mechanism
  (`answer-eval-calibrate`) and a human-labels artifact are in place, with the
  not_run status documented honestly.
- Interface: Telegram bot plus a non-Telegram `demo ask` CLI over the real RAG
  path (reviewer quick start runs without Telegram).
- Ingestion pipeline: automated async worker plus an optional rubric-recognized
  Prefect flow (`prefect-ingest`); core ingestion is fully Telegram-independent.
- Monitoring: idempotent, privacy-safe, **durable** `/feedback up|down`
  (survives `/end` and expiry) + a curated 7-panel Grafana dashboard that is
  provisioned **and** live-data-verified.
- Containerization: everything in `docker compose` (PostgreSQL, migration, bot,
  worker, admin, prefect-ingest, test, lgtm; Telegram bot behind the explicit
  `telegram` profile).
- Reproducibility: pinned `uv.lock`, committed sample corpus + datasets +
  public-safe summaries, honest `not_run` reporting, atomic projection
  activation, real reruns only.
- Best practices: hybrid search default + RRF and agentic evaluation (+1),
  deterministic diversity reranking (+1).

## 6. Remaining rubric risks

- Benchmarks are from a 25-case / 4-document sample: indicative, not
  statistically significant; a larger reviewed dataset is still the strongest
  next step.
- The LLM judge is applied but **uncalibrated** — it needs human labels before
  its scores can be treated as authoritative (calibration is `not_run`).
- No dedicated query-rewriting best practice (only bounded agentic
  decomposition); no cloud deployment (bonus, not attempted).
- The production default stays `weighted-hybrid-v1` per the pre-registered
  selection policy — a deliberate, evidence-backed non-change.
- A real Grafana screenshot was not captured (no renderer in the stack image);
  the dashboard is provisioned and live-data-verified, not screenshot-verified.

## 7. Remaining credential/human tasks

- Fill `data/sample/answer-human-labels.jsonl` with reviewed labels and run
  `answer-eval-calibrate` (procedure in `data/sample/README.md`) to calibrate
  the judge.
- (Optional) exercise live Telegram `/feedback` and Question Mode with the real
  bot.
- (Optional) capture a real Grafana dashboard screenshot once a renderer is
  available, then link it from the verification doc.
- Obtain a larger reviewed mixed dataset for stronger paired evidence.

## 8. External tasks (separate, require human action)

- The working directory contains a `.git` directory that predates this loop.
  **Loop 3 performed no Git operations** — no `git init`, `add`, `commit`,
  `push`, `pull`, `fetch`, branch, or `.git` modification of any kind — and this
  report does not instruct any. Whether/how to publish or submit the project is
  entirely the user's decision; no public repository is claimed to exist.
- Course submission (repository URL + commit hash) on the course management
  platform, if the user chooses to submit.
- Complete the three required peer reviews.
- Update the certificate name on the platform enrollment page, if relevant.

## 9. Git state (Loop 3)

No Git operations were performed in Loop 3 and `.git` was not modified (verified
by directory mtimes and by never invoking Git). The earlier baseline commit
hash recorded in the Loop 2 version of this report is historical information
from a previous engineering loop and was **not re-verified** in Loop 3.

## 10. Loop 3 final verification (2026-08-17)

Final run with the documented reliable invocations:

| Check | Result |
| --- | --- |
| `PYTHONPATH=src uv run pytest` | 321 passed, exit 0, 0 tracebacks |
| Docker hermetic `docker compose --profile test run --rm test` | 319 passed, 2 skipped, **coverage 91.03%** (gate ≥90% reached), 0 tracebacks |
| `uv run ruff check .` | All checks passed |
| `MYPYPATH=src uv run mypy` | No issues found (95 source files) |
| `docker compose config --quiet` / `uv lock --check` | OK / lock current |
| Benchmark reruns | Retrieval: 5 strategies × 25 cases (real); answer: both approaches × 25 cases with judge (real) |
| Benchmark runs not completed | Judge calibration (`not_run`: no human labels); no screenshot (no renderer) |

No destructive operations were performed: the four named volumes
(postgres-data, tempo-wallet, lgtm-data, plus the project network) are intact,
`docker compose down --volumes` was never run, and the canonical vault Markdown
is untouched. The completion report reflects: the production answer prompt
default (`grounded-answer-v2` via `KNOWLEDGE_ASSISTANT_ANSWER_PROMPT_VERSION`),
the Telegram-free reviewer workflow and Telegram's optional single-user role,
durable feedback (migration `0008`), the real Docker coverage result (91.03%,
not a rounded claim), the Prefect shutdown traceback fix, the 25-case benchmark
with its limitations, lexical coverage as an explicit proxy (never semantic
factual correctness), `projection-rebuild` embedding cost, and the dashboard
provisioned-vs-live-data distinction.
