"""One-off migration: file existing flat Articles/<provider>/*.md into topics.

Mapping reflects the manual classification agreed in brainstorming:
AI, Crypto, Career, Personal Growth. Idempotent: already-moved files are
skipped. Run from the repo root after `docker compose up -d`:

    PYTHONPATH=src uv run python scripts/migrate_topic_folders.py

Moves each file and updates the matching document_revisions.vault_path in
one transaction per document (move first, then path update validated by
rowcount; a failure leaves the file in place and the DB untouched).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path, PurePosixPath

from psycopg import Connection

TOPIC_FILES: dict[str, tuple[str, ...]] = {
    "AI": (
        "medium/a-second-note-on-claude-code-6ac328f2.md",
        "medium/how-i-use-claude-code-1b4ee0cd.md",
        "substack/ai-design-for-non-designers-a44858cf.md",
        "substack/ai-native-development-specifications-loop-and-graph-engineering-f3ff49ec.md",
        "substack/build-and-ship-a-full-stack-app-with-ai-coding-assistants-c2facc00.md",
        "substack/controlling-reasoning-effort-in-llms-1017201f.md",
        "substack/deploy-a-full-stack-app-with-ai-coding-assistants-10db409b.md",
        "substack/from-idea-to-production-in-28-prompts-da6bcce0.md",
        "substack/how-crisp-dm-still-applies-to-ai-engineering-5c14bca5.md",
        "substack/how-to-do-evals-in-2026-ae786d43.md",
        "substack/how-to-write-a-good-readme-5756dd08.md",
        "substack/it-s-hard-to-eval-is-a-product-smell-af1565c8.md",
        "substack/re-building-a-faq-system-for-datatalks-club-d6841234.md",
        "substack/software-factories-light-and-dark-9ca76055.md",
        "web/what-is-a-harness-earendil-417775fd.md",
        "x/22580-from-gpt2-to-kimi3-explained-b5fac6d0.md",
        "x/ai-engineering-skills-map-software-engineering-fundamentals-a83189cc.md",
        "x/how-to-build-an-ai-that-never-stops-learning-b7cfcfcb.md",
        "x/how-to-create-the-right-skill-for-your-ai-agent-ec058b85.md",
        "x/kv-prefix-prompt-and-semantic-caching-in-llms-clearly-explained-0447294d.md",
        "x/loop-engineering-clearly-explained-302e4cda.md",
        "x/pydantic-fixed-my-agent-s-memory-34347d6a.md",
        "x/show-me-compact-visual-representations-for-coding-agents-e60adbec.md",
    ),
    "Crypto": (
        "substack/same-same-but-different-777c7338.md",
    ),
    "Career": (
        "substack/choosing-a-portfolio-project-the-definite-guide-d3c5c9f1.md",
        "substack/getting-an-ai-engineering-job-e89e482e.md",
        "x/how-to-be-a-memory-engineer-from-the-perspective-of-stanford-microsoft-anthropic-83adccc1.md",
        "x/how-to-become-a-graph-architect-with-zero-experience-full-course-ff57ac5d.md",
    ),
    "Personal Growth": (
        "substack/strategy-vs-tactics-how-to-actually-get-ahead-of-99-of-people-03c5fc20.md",
        "substack/the-most-profitable-skill-of-the-21st-century-not-ai-105a33e9.md",
        "substack/the-one-human-business-how-to-earn-in-the-age-of-ai-d9f4f249.md",
        "substack/the-writing-habit-that-saved-my-brain-and-my-future-6b554727.md",
        "substack/we-are-in-the-middle-of-the-digital-renaissance-please-take-advantage-of-it-ed6a6306.md",
    ),
}


def load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    env = {**load_dotenv(repo_root / ".env"), **os.environ}
    vault = Path(env["KNOWLEDGE_ASSISTANT_VAULT_PATH"]).expanduser()
    database_url = env["KNOWLEDGE_ASSISTANT_DATABASE_URL"].replace(
        "postgresql+psycopg://", "postgresql://", 1
    )

    import psycopg

    moved = 0
    skipped = 0
    with psycopg.connect(database_url) as connection:
        for topic, relative_paths in TOPIC_FILES.items():
            for relative_str in relative_paths:
                relative = PurePosixPath(relative_str)
                source = vault / "Articles" / relative
                if not source.exists():
                    # Idempotency: the file may already sit in the topic folder.
                    target = (
                        vault
                        / "Articles"
                        / relative.parts[0]
                        / topic
                        / relative.parts[-1]
                    )
                    if target.exists():
                        skipped += 1
                        continue
                    print(f"MISSING: Articles/{relative}", file=sys.stderr)
                    continue
                target = (
                    vault / "Articles" / relative.parts[0] / topic / relative.parts[-1]
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                old_vault_path = f"Articles/{relative.as_posix()}"
                new_vault_path = target.relative_to(vault).as_posix()
                os.replace(source, target)
                result: Connection | object = connection.execute(
                    """
                    UPDATE document_revisions AS revision
                    SET vault_path = %s
                    WHERE revision.vault_path = %s
                      AND revision.revision_id = (
                          SELECT current_revision_id
                          FROM documents
                          WHERE document_id = revision.document_id
                      )
                    """,
                    (new_vault_path, old_vault_path),
                )
                if result.rowcount != 1:  # type: ignore[attr-defined]
                    connection.rollback()
                    os.replace(target, source)
                    print(
                        f"DB MISMATCH for {old_vault_path} "
                        f"(rowcount={result.rowcount}), file restored",  # type: ignore[attr-defined]
                        file=sys.stderr,
                    )
                    continue
                connection.commit()
                moved += 1
                print(f"moved: {old_vault_path} -> {new_vault_path}")
    print(f"done: moved={moved} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
