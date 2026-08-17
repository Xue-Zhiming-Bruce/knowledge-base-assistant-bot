"""Grounded question-answering value objects and invariants."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from knowledge_assistant.domain.errors import DomainError

_CITATION_PATTERN = re.compile(r"\[(E[1-9][0-9]*)\]")


def extract_citation_markers(text: str) -> tuple[str, ...]:
    """Return distinct citation markers such as E1 in order of first appearance."""

    return tuple(dict.fromkeys(_CITATION_PATTERN.findall(text)))


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    """Deterministic context budget used to bound retrieved evidence."""

    total_limit: int
    per_item_limit: int

    def __post_init__(self) -> None:
        if self.total_limit < 1 or self.per_item_limit < 1:
            raise ValueError("context limits must be positive")
        if self.per_item_limit > self.total_limit:
            raise ValueError("per_item_limit must not exceed total_limit")


def bound_evidence(
    evidence: tuple[Evidence, ...],
    *,
    policy: ContextPolicy,
) -> tuple[Evidence, ...]:
    """Truncate evidence deterministically to a bounded answer context."""

    remaining = policy.total_limit
    selected: list[Evidence] = []
    for item in evidence:
        if remaining <= 0:
            break
        content = item.content[: min(policy.per_item_limit, remaining)]
        if not content.strip():
            continue
        selected.append(
            Evidence(
                citation_id=item.citation_id,
                chunk_id=item.chunk_id,
                document_id=item.document_id,
                revision_id=item.revision_id,
                title=item.title,
                source_url=item.source_url,
                vault_path=item.vault_path,
                heading_path=item.heading_path,
                content=content,
                score=item.score,
            )
        )
        remaining -= len(content)
    return tuple(selected)


class NoActiveSessionError(DomainError):
    """A question was submitted outside explicit Question Mode."""


class AnswerValidationError(DomainError):
    """A generated answer violated the grounding or citation contract."""


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    question: str
    answer: str


@dataclass(frozen=True, slots=True)
class FeedbackTurn:
    """Safe pipeline metadata of one answer turn, without any question or answer text."""

    session_id: UUID
    turn_number: int
    pipeline_version: dict[str, object]


@dataclass(frozen=True, slots=True)
class FeedbackResult:
    status: str
    session_id: UUID | None = None
    turn_number: int | None = None
    direction: str | None = None


@dataclass(frozen=True, slots=True)
class Evidence:
    citation_id: str
    chunk_id: str
    document_id: str
    revision_id: str
    title: str
    source_url: str
    vault_path: str
    heading_path: tuple[str, ...]
    content: str
    score: float


@dataclass(frozen=True, slots=True)
class GeneratedAnswer:
    answer: str
    citation_ids: tuple[str, ...]
    sufficient_evidence: bool
    model: str
    input_tokens: int | None
    output_tokens: int | None


@dataclass(frozen=True, slots=True)
class AnswerResult:
    rendered_text: str
    citations: tuple[Evidence, ...]
    sufficient_evidence: bool
    model: str


class CitationValidator:
    """Enforce that generated citation markers resolve to supplied evidence."""

    VERSION = "deterministic-citations-v1"

    def validate(
        self,
        generated: GeneratedAnswer,
        evidence: tuple[Evidence, ...],
    ) -> tuple[Evidence, ...]:
        available = {item.citation_id: item for item in evidence}
        declared = tuple(dict.fromkeys(generated.citation_ids))
        markers = extract_citation_markers(generated.answer)
        unknown = (set(declared) | set(markers)) - available.keys()
        if unknown:
            raise AnswerValidationError(
                f"answer referenced unknown evidence: {', '.join(sorted(unknown))}"
            )
        if generated.sufficient_evidence and not declared:
            raise AnswerValidationError("grounded answer must declare at least one citation")
        if generated.sufficient_evidence and not markers:
            raise AnswerValidationError("grounded answer must contain citation markers")
        if set(markers) - set(declared):
            raise AnswerValidationError("answer markers must be included in declared citations")
        return tuple(available[citation_id] for citation_id in declared)
