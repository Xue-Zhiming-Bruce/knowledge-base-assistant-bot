# LLM Zoomcamp 2026 — Final Project Requirements

Reference: [DataTalks.Club LLM Zoomcamp — Project page](https://datatalks.club/docs/courses/llm-zoomcamp/project/), [course `project.md`](https://github.com/DataTalksClub/llm-zoomcamp/blob/main/project.md), [Zoomcamp Logistics — Final Project](https://datatalks.club/docs/courses/zoomcamp-logistics/project/).

## 1. Objective

Build a working, end-to-end LLM-powered application on a knowledge base of your choice. It can be:

- a **RAG application**,
- an **agent application**,
- or a **combination of both**.

The project is the **only thing required for the certificate**. Homework is not required. There are **two submission attempts**; you must also peer-review other projects.

Apply course modules 1–5 (RAG/agentic RAG, vector search, orchestration, evaluation, monitoring) to **your own data**. The goal is to show you can build, evaluate, and document an end-to-end system.

## 2. Required components

Every project must include:

1. **Ingestion path** — turns the raw knowledge base into something searchable.
2. **Retrieval system** — text search, vector search, or hybrid.
3. **LLM-powered answering layer** — uses the retrieved documents (optionally calls tools).
4. **Retrieval evaluation** — which retrieval strategy you tried and how it scored.
5. **End-to-end evaluation** — of the full RAG/agent flow.
6. **An interface a reviewer can use** — even just a Python script or notebook counts; a web UI (Streamlit, Gradio, Flask) is welcome but not required.
7. **Documentation** — the README is decisive (see §6).

**Nice-to-haves (not required by the rubric):**

- Monitoring (Grafana, PostgreSQL, dashboards, user feedback collection).
- A deployed live system.

## 3. Dataset / knowledge base

- You **choose** the dataset or API-backed data source. It can be in any language, but the README must be in English.
- **Cannot use**: any dataset the course uses in lectures/homework (e.g. the DataTalks.Club FAQ corpus used in modules) — pick something new. Re-using course or homework datasets is disallowed.
- Avoid trivially small/toy data that does not exercise retrieval meaningfully.
- Good sources: open-source project docs, public policy/legal/government documents, product manuals, support pages, public text datasets (reviews, complaints, Q&A), podcast/YouTube transcripts, wiki subsets, articles, books, your own notes (if you can make enough public).
- You may **generate a dataset with an LLM** (e.g. if you can't release the real one).
- Dataset does **not** have to be Q&A-formatted.
- Prefer public data: peer reviewers must be able to reproduce. Private/proprietary data costs reproducibility points (document the trade-off).

## 4. Deadlines (2026 cohort)

- **Project Attempt 1:** 27 July 2026, 23:00
- **Project Attempt 2:** 10 August 2026, 23:00
- Peer review runs in the week after each submission window closes.
- Typical effort: 2–3 weeks of focused work.
- You can resubmit any number of times before a deadline — only the latest submission counts.

Attempt rules:

- Failed attempt 1 → improve and resubmit for attempt 2 (allowed).
- Passed attempt 1 → cannot resubmit the same project for attempt 2 (counts as self-plagiarism). A different project is required.
- Skipping attempt 1 and submitting only in attempt 2 is fine.
- Must also review **3 peers' projects** (3 extra points each) — without this the project can't be considered complete.

## 5. Evaluation criteria (rubric)

Scored 0/1/2 per criterion:

| Criterion | 0 pts | 1 pt | 2 pts |
| --- | --- | --- | --- |
| **Problem description** | Not described | Described briefly/unclearly | Well-described; clear what problem is solved |
| **Retrieval flow** | No KB or LLM used | No KB; LLM queried directly | Both a knowledge base and an LLM in the flow |
| **Retrieval evaluation** | None | Only one retrieval approach evaluated | Multiple approaches evaluated, best one used |
| **LLM evaluation** | None | Only one approach (e.g. one prompt) evaluated | Multiple approaches evaluated, best one used |
| **Interface** | No way to interact | CLI / script / Jupyter notebook | UI (Streamlit), web app (Django), or API (FastAPI) |
| **Ingestion pipeline** | No ingestion | Semi-automated (notebook or script) | Automated with a tool (Kestra, dlt, Airflow, Prefect) |
| **Monitoring** | No monitoring | User feedback collected **OR** a dashboard | User feedback **and** a dashboard with ≥5 charts |
| **Containerization** | None | Dockerfile for main app **OR** docker-compose for dependencies only | Everything in docker-compose |
| **Reproducibility** | No run instructions; data missing/unclear | Incomplete instructions, **or** complete but data missing | Clear instructions, accessible dataset, easy to run, works; versions pinned for all dependencies |

**Best practices (+1 each):**

- [ ] Hybrid search — combining text + vector search (at least evaluating it)
- [ ] Document re-ranking
- [ ] User query rewriting

**Bonus (not covered in the course):**

- [ ] Deployment to the cloud (+2)
- [ ] Up to +3 extra bonus points for something else (justify in feedback)

## 6. Documentation requirements

The project "rises or falls" with its documentation. Recommended:

- Write for people who **didn't take the course**: explain the problem, data, and flow without assuming course knowledge.
- Mention the evaluation criteria in the README so reviewers can find the relevant parts.
- Add screenshots (UI, dashboard, example answers).
- Explain how to run: setup steps, dependencies, configuration, environment variables.
- Show inputs/outputs, common use cases, or a short walkthrough.
- Optional: short preview video (e.g. record Streamlit app).
- Split long docs into `setup.md`, `usage.md`, etc. if needed.
- Keep README up to date.

## 7. Tech stack

- **Not restricted** to course technologies: any LLM provider (OpenAI, Ollama, Groq, AWS Bedrock…), any vector DB (including in-memory/SQLite/pgvector), any framework (LangChain, LlamaIndex, Haystack…), any language.
- Caveats:
  - Document choices clearly — reviewers may not know your stack.
  - Reproducibility matters: if a reviewer can't run it (or at least understand how), you lose reproducibility points.
  - Non-Python stacks need explicit setup instructions; **Python is the only stack you can assume reviewers have ready**.
  - Frameworks are allowed, but document why you chose them and what they give you.
- Credentials: use environment variables, never commit secrets; document how a reviewer obtains keys. If keys are required, you may lose reproducibility points.

## 8. Submission & repo mechanics

- Create a **separate, public GitHub repo** with a meaningful name (e.g. `recipes-rag-assistant`, not `homework-final`).
- You'll submit the repo URL + **commit hash** on the course management platform (`https://courses.datatalks.club/llm-zoomcamp-2026/project/project1` and `.../project2`).
- Make the repo public **before** submitting — private/deleted repos count as not submitted.
- Reviewers clone at your commit hash: `git clone <url> && git reset --hard <commit-hash>`.
- Update your "Certificate name" on the platform enrollment page.
- Never push secrets; if leaked, rotate + scrub history (don't delete the repo).
- After the cohort the project lands in the public project gallery — treat it as a portfolio piece.

## 9. Plagiarism rules (violations → 0 points)

- Copying others' notebooks/projects (in full or in part).
- Re-using your own projects from other courses/bootcamps.
- Re-submitting your passed attempt-1 project as attempt 2.
- Re-using a project from previous course iterations.

Re-using **some parts of the course code** is allowed.

## 10. How to pick a good project idea

Write down before choosing a stack:

- Who the user is.
- What they need help with.
- What knowledge base the assistant will use.
- What a useful answer should look like.
- How you will tell whether the answer is good.

Good ideas have: a specific domain (generic ChatGPT wouldn't be enough), a clean & sufficiently large knowledge base, answers checkable against source documents, scope that fits in a few weeks, and a README explainable to outsiders. Avoid vague "chatbot for documents" — make the user, documents, and task concrete.

## Mapping to this repository (Knowledge Base Assistant)

This repo is a strong fit for the rubric, with real, reviewer-visible evidence:

| Rubric item | Current state in this repo | Evidence |
| --- | --- | --- |
| Problem description | README landing page defines the target user, problem, and ChatGPT gap | `README.md` |
| Retrieval flow | Real RAG path: retrieval → rerank → bounded context → grounded generation → citation validation | `demo ask`, `src/knowledge_assistant/application/` |
| Retrieval evaluation | All five strategies run against the committed sample dataset with real Hit@5/MRR/latency/planner metrics | `data/sample/benchmark-summary.md` |
| End-to-end evaluation | `answer-eval-run` compares `grounded-answer-v1` vs `grounded-answer-v2` with deterministic citation/abstention metrics and an optional versioned LLM judge | `data/sample/answer-benchmark-summary.md`, `src/knowledge_assistant/application/evaluation.py` |
| Interface | Telegram bot **and** a non-Telegram CLI demo (`demo ask`) that runs without Telegram | `src/knowledge_assistant/application/bot.py`, `src/knowledge_assistant/cli.py` |
| Ingestion pipeline | Automated async worker plus an optional rubric-recognized Prefect flow (`prefect-ingest`, tools profile) | `src/knowledge_assistant/application/worker.py`, `src/knowledge_assistant/infrastructure/orchestration/prefect_flow.py` |
| Monitoring | `/feedback up`/`down` (idempotent, privacy-safe) + a curated 7-panel Grafana dashboard provisioned through Docker Compose | `config/grafana/`, `docs/operations/docker.md` |
| Containerization | Everything in `compose.yaml` (PostgreSQL, migration, bot, worker, admin, Prefect flow, Grafana) | `compose.yaml`, `Dockerfile` |
| Reproducibility | Pinned `uv.lock`, committed sample corpus + datasets, honest `not_run` reporting, atomic projection activation | `docs/operations/evaluation.md`, `data/sample/` |
| Best practices | Hybrid search is the default (plus RRF and agentic evaluation); deterministic diversity reranking implemented; query rewriting is only the bounded agentic planner, not a dedicated rewrite step | `docs/operations/retrieval-selection-policy.md` |

**Status of the earlier learned-sparse functionality:** the Granite learned-sparse
embedding path was **removed** (migration `0006_remove_learned_sparse_embeddings`
drops the `sparse_embedding` column). It is not present in the current code or
dependencies — no Granite, Hugging Face, sentence-transformers, Transformers, or
PyTorch functionality is used. Any old `granite-*` result files under
git-ignored `var/evaluation/` are stale artifacts, not current evidence.

The supported retrieval strategies are exactly: `vector-only-v1`,
`lexical-only-v1`, `weighted-hybrid-v1` (production default; PostgreSQL
`ts_rank_cd` lexical scoring — never called BM25), `rrf-hybrid-v1`, and
`agentic-decomposition-v1`.

Remaining gaps for max score: a dedicated query-rewriting best practice (only
bounded agentic decomposition exists), cloud deployment (bonus, not attempted),
and a larger reviewed mixed dataset (multi-document/human/follow-up cases are
architecturally supported but the committed sample is 8 cases over 4 documents).
The production retrieval default stays `weighted-hybrid-v1` until paired,
reproducible evidence from the pre-registered
[selection policy](docs/operations/retrieval-selection-policy.md) supports a
change; the committed benchmark shows no candidate beating it.
