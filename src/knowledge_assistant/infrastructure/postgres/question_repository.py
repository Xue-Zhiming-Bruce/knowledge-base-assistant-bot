"""PostgreSQL session and hybrid-retrieval repository."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from knowledge_assistant.domain.query import ConversationTurn, Evidence, FeedbackTurn
from knowledge_assistant.domain.retrieval import RetrievalStrategyName

_LEXICAL_TOKEN = re.compile(r"[a-z0-9]+")

# English question/function words excluded from the lexical OR query so a single
# common term cannot match the whole corpus. Kept deliberately small.
_LEXICAL_STOPWORDS = frozenset(
    {
        "a",
        "about",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "for",
        "from",
        "have",
        "how",
        "in",
        "is",
        "it",
        "its",
        "not",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "this",
        "to",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "will",
        "with",
        "would",
        "you",
        "your",
    }
)


def build_lexical_tsquery(question: str) -> str:
    """Build a disjunctive (OR) PostgreSQL tsquery from the question's content terms.

    Natural questions paraphrase article vocabulary, so AND-ing every term (the
    behavior of ``websearch_to_tsquery``) almost never matches a whole chunk.
    OR-ing the distinctive content terms (stopwords removed) recovers lexical
    recall; ``ts_rank_cd`` still ranks by matched-lexeme density, so the most
    relevant chunks rise to the top. Scoring stays PostgreSQL ``ts_rank_cd`` - it
    is never called BM25.
    """

    tokens = tuple(_LEXICAL_TOKEN.findall(question.lower()))
    content_terms = tuple(
        dict.fromkeys(
            token
            for token in tokens
            if token not in _LEXICAL_STOPWORDS and len(token) >= 2
        )
    )
    if not content_terms:
        content_terms = tuple(dict.fromkeys(tokens))
    if not content_terms:
        return "'zz_nomatch_zz'"
    return " | ".join(f"'{term}'" for term in content_terms)


@dataclass(frozen=True, slots=True)
class SessionStart:
    session_id: uuid.UUID
    created: bool


@dataclass(frozen=True, slots=True)
class StoredTurn:
    answer: str
    citations: tuple[dict[str, object], ...]
    model: str


class PostgresQuestionRepository:
    """Own temporary conversations and query rebuildable PostgreSQL projections."""

    def __init__(
        self,
        database_url: str,
        *,
        pool: ConnectionPool[Any] | None = None,
    ) -> None:
        conninfo = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        self._pool = pool or ConnectionPool(
            conninfo,
            min_size=1,
            max_size=8,
            kwargs={"row_factory": dict_row},
        )

    def close(self) -> None:
        self._pool.close()

    def start_session(
        self,
        *,
        principal_id: str,
        ttl_seconds: int,
    ) -> SessionStart:
        with self._pool.connection() as connection, connection.transaction():
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"question-session:{principal_id}",),
            )
            connection.execute(
                """
                DELETE FROM question_sessions
                WHERE principal_id = %s
                  AND knowledge_space_id = 'default'
                  AND state = 'active'
                  AND expires_at <= now()
                """,
                (principal_id,),
            )
            row = connection.execute(
                """
                SELECT session_id
                FROM question_sessions
                WHERE principal_id = %s
                  AND knowledge_space_id = 'default'
                  AND state = 'active'
                FOR UPDATE
                """,
                (principal_id,),
            ).fetchone()
            if row is not None:
                connection.execute(
                    """
                    UPDATE question_sessions
                    SET last_activity_at = now(),
                        expires_at = now() + (%s * interval '1 second')
                    WHERE session_id = %s
                    """,
                    (ttl_seconds, row["session_id"]),
                )
                return SessionStart(uuid.UUID(str(row["session_id"])), False)
            session_id = uuid.uuid4()
            connection.execute(
                """
                INSERT INTO question_sessions (
                    session_id, principal_id, knowledge_space_id, client_type,
                    state, created_at, last_activity_at, expires_at
                )
                VALUES (
                    %s, %s, 'default', 'telegram', 'active',
                    now(), now(), now() + (%s * interval '1 second')
                )
                """,
                (session_id, principal_id, ttl_seconds),
            )
            return SessionStart(session_id, True)

    def active_session(self, principal_id: str) -> uuid.UUID | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                """
                SELECT session_id
                FROM question_sessions
                WHERE principal_id = %s
                  AND knowledge_space_id = 'default'
                  AND state = 'active'
                  AND expires_at > now()
                """,
                (principal_id,),
            ).fetchone()
        return uuid.UUID(str(row["session_id"])) if row is not None else None

    def end_session(self, principal_id: str) -> bool:
        with self._pool.connection() as connection:
            result = connection.execute(
                """
                DELETE FROM question_sessions
                WHERE principal_id = %s
                  AND knowledge_space_id = 'default'
                  AND state = 'active'
                """,
                (principal_id,),
            )
        return int(result.rowcount) > 0

    def cleanup_expired(self) -> int:
        with self._pool.connection() as connection:
            result = connection.execute(
                """
                DELETE FROM question_sessions
                WHERE state = 'active' AND expires_at <= now()
                """
            )
        return int(result.rowcount)

    def find_turn(
        self,
        *,
        principal_id: str,
        client_message_id: str,
    ) -> StoredTurn | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                """
                SELECT turn.assistant_answer, turn.citations, turn.pipeline_version
                FROM session_turns AS turn
                JOIN question_sessions AS session USING (session_id)
                WHERE session.principal_id = %s
                  AND session.state = 'active'
                  AND turn.client_message_id = %s
                  AND turn.assistant_answer IS NOT NULL
                """,
                (principal_id, client_message_id),
            ).fetchone()
        if row is None:
            return None
        return StoredTurn(
            answer=row["assistant_answer"],
            citations=tuple(row["citations"] or ()),
            model=str((row["pipeline_version"] or {}).get("generation_model", "unknown")),
        )

    def history(
        self,
        session_id: uuid.UUID,
        *,
        limit: int = 6,
    ) -> tuple[ConversationTurn, ...]:
        with self._pool.connection() as connection:
            rows = connection.execute(
                """
                SELECT user_question, assistant_answer
                FROM session_turns
                WHERE session_id = %s AND assistant_answer IS NOT NULL
                ORDER BY turn_number DESC
                LIMIT %s
                """,
                (session_id, limit),
            ).fetchall()
        return tuple(
            ConversationTurn(question=row["user_question"], answer=row["assistant_answer"])
            for row in reversed(rows)
        )

    def record_turn(
        self,
        *,
        session_id: uuid.UUID,
        client_message_id: str,
        question: str,
        answer: str,
        citations: tuple[dict[str, object], ...],
        pipeline_version: dict[str, object],
        ttl_seconds: int,
    ) -> None:
        with self._pool.connection() as connection, connection.transaction():
            session = connection.execute(
                """
                SELECT session_id
                FROM question_sessions
                WHERE session_id = %s AND state = 'active' AND expires_at > now()
                FOR UPDATE
                """,
                (session_id,),
            ).fetchone()
            if session is None:
                raise RuntimeError("question session expired before answer was recorded")
            next_turn = connection.execute(
                """
                SELECT COALESCE(MAX(turn_number), 0) + 1 AS next_turn
                FROM session_turns
                WHERE session_id = %s
                """,
                (session_id,),
            ).fetchone()
            assert next_turn is not None
            connection.execute(
                """
                INSERT INTO session_turns (
                    session_id, turn_number, client_message_id, user_question,
                    assistant_answer, citations, pipeline_version, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, now())
                ON CONFLICT (session_id, client_message_id) DO NOTHING
                """,
                (
                    session_id,
                    next_turn["next_turn"],
                    client_message_id,
                    question,
                    answer,
                    json.dumps(citations),
                    json.dumps(pipeline_version),
                ),
            )
            connection.execute(
                """
                UPDATE question_sessions
                SET last_activity_at = now(),
                    expires_at = now() + (%s * interval '1 second'),
                    version = version + 1
                WHERE session_id = %s
                """,
                (ttl_seconds, session_id),
            )

    def retrieve(
        self,
        *,
        query_text: str,
        query_vector: tuple[float, ...] | None,
        embedding_model: str | None,
        dimensions: int | None,
        strategy: RetrievalStrategyName = RetrievalStrategyName.WEIGHTED_HYBRID,
        limit: int = 8,
        generation_id: uuid.UUID | None = None,
    ) -> tuple[Evidence, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        if strategy is RetrievalStrategyName.AGENTIC_DECOMPOSITION:
            raise ValueError("agentic decomposition must be orchestrated above the repository")
        vector_literal = (
            "[" + ",".join(str(value) for value in query_vector) + "]"
            if query_vector is not None
            else None
        )
        common_ctes = """
                WITH selected_generation AS (
                    SELECT generation_id
                    FROM projection_generations
                    WHERE (
                        %s::uuid IS NOT NULL
                        AND generation_id = %s::uuid
                        AND state IN ('building', 'validated', 'active', 'retired')
                    ) OR (
                        %s::uuid IS NULL
                        AND state = 'active'
                        AND (
                            %s::text IS NULL
                            OR compatibility_manifest->>'embedding_model' = %s
                        )
                        AND (
                            %s::integer IS NULL
                            OR (
                                compatibility_manifest->>'embedding_dimensions'
                            )::integer = %s
                        )
                    )
                    ORDER BY CASE WHEN state = 'active' THEN 0 ELSE 1 END
                    LIMIT 1
                ),
                scored AS (
                    SELECT
                        chunk.chunk_id,
                        chunk.document_id,
                        chunk.revision_id,
                        chunk.content,
                        chunk.heading_path,
                        revision.title,
                        revision.source_url,
                        revision.vault_path,
                        CASE
                            WHEN %s::vector IS NULL OR chunk.embedding IS NULL THEN 0
                            ELSE GREATEST(0, 1 - (chunk.embedding <=> %s::vector))
                        END AS semantic_score,
                        ts_rank_cd(
                            chunk.search_vector,
                            to_tsquery('simple', %s)
                        ) AS lexical_score
                    FROM chunks AS chunk
                    JOIN selected_generation AS generation USING (generation_id)
                    JOIN documents AS document
                      ON document.document_id = chunk.document_id
                     AND document.current_revision_id = chunk.revision_id
                    JOIN document_revisions AS revision
                      ON revision.revision_id = chunk.revision_id
                )
        """
        parameters: list[object] = [
            generation_id,
            generation_id,
            generation_id,
            embedding_model,
            embedding_model,
            dimensions,
            dimensions,
            vector_literal,
            vector_literal,
            build_lexical_tsquery(query_text),
        ]
        if strategy is RetrievalStrategyName.VECTOR_ONLY:
            query = (
                common_ctes
                + """
                SELECT *, semantic_score AS combined_score
                FROM scored
                WHERE semantic_score > 0
                ORDER BY semantic_score DESC, chunk_id
                LIMIT %s
            """
            )
            parameters.append(limit)
        elif strategy is RetrievalStrategyName.LEXICAL_ONLY:
            query = (
                common_ctes
                + """
                SELECT *, lexical_score AS combined_score
                FROM scored
                WHERE lexical_score > 0
                ORDER BY lexical_score DESC, chunk_id
                LIMIT %s
            """
            )
            parameters.append(limit)
        elif strategy is RetrievalStrategyName.WEIGHTED_HYBRID:
            query = (
                common_ctes
                + """
                SELECT *,
                    (0.75 * semantic_score + 0.25 * lexical_score) AS combined_score
                FROM scored
                WHERE semantic_score > 0 OR lexical_score > 0
                ORDER BY combined_score DESC, chunk_id
                LIMIT %s
            """
            )
            parameters.append(limit)
        elif strategy is RetrievalStrategyName.RRF_HYBRID:
            candidate_depth = max(40, limit * 2)
            query = (
                common_ctes
                + """
                , vector_ranked AS (
                    SELECT
                        chunk_id,
                        row_number() OVER (
                            ORDER BY semantic_score DESC, chunk_id
                        ) AS result_rank
                    FROM scored
                    WHERE semantic_score > 0
                    ORDER BY semantic_score DESC, chunk_id
                    LIMIT %s
                ),
                lexical_ranked AS (
                    SELECT
                        chunk_id,
                        row_number() OVER (
                            ORDER BY lexical_score DESC, chunk_id
                        ) AS result_rank
                    FROM scored
                    WHERE lexical_score > 0
                    ORDER BY lexical_score DESC, chunk_id
                    LIMIT %s
                ),
                fused AS (
                    SELECT
                        chunk_id,
                        SUM(1.0 / (60 + result_rank)) AS rrf_score
                    FROM (
                        SELECT chunk_id, result_rank FROM vector_ranked
                        UNION ALL
                        SELECT chunk_id, result_rank FROM lexical_ranked
                    ) AS ranked
                    GROUP BY chunk_id
                )
                SELECT scored.*, fused.rrf_score AS combined_score
                FROM fused
                JOIN scored USING (chunk_id)
                ORDER BY combined_score DESC, chunk_id
                LIMIT %s
            """
            )
            parameters.extend((candidate_depth, candidate_depth, limit))
        else:
            raise ValueError(f"unsupported retrieval strategy: {strategy}")
        with self._pool.connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(
            Evidence(
                citation_id=f"E{index}",
                chunk_id=row["chunk_id"],
                document_id=row["document_id"],
                revision_id=row["revision_id"],
                title=row["title"],
                source_url=row["source_url"],
                vault_path=row["vault_path"],
                heading_path=tuple(row["heading_path"]),
                content=row["content"],
                score=float(row["combined_score"]),
            )
            for index, row in enumerate(rows, start=1)
        )

    def active_generation_id(self) -> str | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                """
                SELECT generation_id
                FROM projection_generations
                WHERE state = 'active'
                LIMIT 1
                """
            ).fetchone()
        return str(row["generation_id"]) if row else None

    def feedback_turn(
        self,
        *,
        principal_id: str,
        client_message_id: str | None,
        answer_message_id: str | None,
    ) -> FeedbackTurn | None:
        """Resolve the answer turn for feedback: by answer, question, or latest."""

        if answer_message_id is not None:
            row = self._feedback_turn_query(
                """
                AND turn.answer_message_id = %s
                """,
                (principal_id, answer_message_id),
            )
            if row is not None:
                return row
        if client_message_id is not None:
            row = self._feedback_turn_query(
                """
                AND turn.client_message_id = %s
                """,
                (principal_id, client_message_id),
            )
            if row is not None:
                return row
        return self._feedback_turn_query("", (principal_id,))

    def _feedback_turn_query(
        self,
        extra_where: str,
        parameters: tuple[object, ...],
    ) -> FeedbackTurn | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                f"""
                SELECT turn.session_id, turn.turn_number, turn.pipeline_version
                FROM session_turns AS turn
                JOIN question_sessions AS session
                  ON session.session_id = turn.session_id
                WHERE session.principal_id = %s
                {extra_where}
                ORDER BY turn.created_at DESC, turn.turn_number DESC
                LIMIT 1
                """,
                parameters,
            ).fetchone()
        if row is None:
            return None
        pipeline = row["pipeline_version"]
        return FeedbackTurn(
            session_id=uuid.UUID(str(row["session_id"])),
            turn_number=int(row["turn_number"]),
            pipeline_version=dict(pipeline) if isinstance(pipeline, dict) else {},
        )

    def record_answer_message_id(
        self,
        *,
        principal_id: str,
        client_message_id: str,
        answer_message_id: str,
    ) -> None:
        with self._pool.connection() as connection:
            connection.execute(
                """
                UPDATE session_turns AS turn
                SET answer_message_id = %s
                FROM question_sessions AS session
                WHERE session.session_id = turn.session_id
                  AND session.principal_id = %s
                  AND turn.client_message_id = %s
                """,
                (answer_message_id, principal_id, client_message_id),
            )

    def record_feedback(
        self,
        *,
        principal_id: str,
        session_id: uuid.UUID,
        turn_number: int,
        direction: str,
        retrieval_strategy: str,
        projection_generation: str,
        generation_model: str,
        answer_prompt_version: str,
    ) -> bool:
        if direction not in ("up", "down"):
            raise ValueError("feedback direction must be up or down")
        with self._pool.connection() as connection:
            row = connection.execute(
                """
                INSERT INTO answer_feedback (
                    principal_id, session_id, turn_number, direction,
                    retrieval_strategy, projection_generation, generation_model,
                    answer_prompt_version
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (principal_id, session_id, turn_number) DO NOTHING
                RETURNING feedback_id
                """,
                (
                    principal_id,
                    session_id,
                    turn_number,
                    direction,
                    retrieval_strategy,
                    projection_generation,
                    generation_model,
                    answer_prompt_version,
                ),
            ).fetchone()
        return row is not None
