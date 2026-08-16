"""Small transactional migration runner for packaged PostgreSQL migrations."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from importlib import resources

import psycopg
from psycopg import Connection

_MIGRATION_LOCK_ID = 4_884_076_585_490_192_465


class MigrationError(RuntimeError):
    """A database migration cannot be applied safely."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: str
    sql: str
    checksum: str


class MigrationRunner:
    """Apply immutable packaged migrations under a PostgreSQL advisory lock."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    def apply(self) -> tuple[str, ...]:
        applied_now: list[str] = []
        with psycopg.connect(self._database_url) as connection:
            self._prepare_migration_table(connection)
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK_ID,))
            applied: dict[str, str] = dict(
                connection.execute(
                    "SELECT version, checksum FROM knowledge_assistant_schema_migrations"
                ).fetchall()
            )
            for migration in self._load_migrations():
                previous_checksum = applied.get(migration.version)
                if previous_checksum is not None:
                    if previous_checksum != migration.checksum:
                        raise MigrationError(
                            f"migration {migration.version} changed after it was applied"
                        )
                    continue
                connection.execute(migration.sql)
                connection.execute(
                    """
                    INSERT INTO knowledge_assistant_schema_migrations (version, checksum)
                    VALUES (%s, %s)
                    """,
                    (migration.version, migration.checksum),
                )
                applied_now.append(migration.version)
        return tuple(applied_now)

    @staticmethod
    def _prepare_migration_table(connection: Connection[tuple[object, ...]]) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_assistant_schema_migrations (
                version text PRIMARY KEY,
                checksum text NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )

    @staticmethod
    def _load_migrations() -> tuple[Migration, ...]:
        directory = resources.files("knowledge_assistant.infrastructure.postgres").joinpath("sql")
        migrations: list[Migration] = []
        for item in sorted(directory.iterdir(), key=lambda entry: entry.name):
            if not item.name.endswith(".sql"):
                continue
            sql = item.read_text(encoding="utf-8")
            migrations.append(
                Migration(
                    version=item.name.removesuffix(".sql"),
                    sql=sql,
                    checksum=hashlib.sha256(sql.encode()).hexdigest(),
                )
            )
        return tuple(migrations)
