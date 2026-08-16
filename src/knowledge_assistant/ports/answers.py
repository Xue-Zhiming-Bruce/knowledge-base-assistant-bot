"""Provider-independent grounded answer generation contract."""

from __future__ import annotations

from typing import Protocol

from knowledge_assistant.domain.query import ConversationTurn, Evidence, GeneratedAnswer


class AnswerGenerator(Protocol):
    PROMPT_VERSION: str

    def generate(
        self,
        *,
        question: str,
        history: tuple[ConversationTurn, ...],
        evidence: tuple[Evidence, ...],
    ) -> GeneratedAnswer:
        """Generate one structured answer constrained to supplied evidence."""
