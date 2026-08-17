"""Deterministic semantic chunk projection."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from knowledge_assistant.domain.documents import KnowledgeDocument


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    chunk_id: str
    ordinal: int
    content: str
    content_fingerprint: str
    heading_path: tuple[str, ...]
    token_count: int


class MarkdownChunker:
    """Chunk Markdown by paragraphs while retaining heading ancestry."""

    VERSION = "markdown-paragraphs-v1"

    def __init__(self, *, max_characters: int = 2_000) -> None:
        if max_characters < 500:
            raise ValueError("max_characters must be at least 500")
        self._max_characters = max_characters

    def chunk(self, document: KnowledgeDocument) -> tuple[DocumentChunk, ...]:
        blocks = [block.strip() for block in document.markdown_body.split("\n\n") if block.strip()]
        heading_levels: dict[int, str] = {}
        pending: list[str] = []
        pending_headings: tuple[str, ...] = ()
        outputs: list[DocumentChunk] = []

        def flush() -> None:
            if not pending:
                return
            content = "\n\n".join(pending).strip()
            ordinal = len(outputs)
            fingerprint = f"sha256:{hashlib.sha256(content.encode()).hexdigest()}"
            identity_material = (
                f"{document.revision.revision_id.value}\0{self.VERSION}\0{ordinal}\0{fingerprint}"
            )
            chunk_id = f"chk_{hashlib.sha256(identity_material.encode()).hexdigest()[:32]}"
            outputs.append(
                DocumentChunk(
                    chunk_id=chunk_id,
                    ordinal=ordinal,
                    content=content,
                    content_fingerprint=fingerprint,
                    heading_path=pending_headings,
                    token_count=max(1, len(content.split())),
                )
            )
            pending.clear()

        for block in blocks:
            if block.startswith("#"):
                first_line = block.splitlines()[0]
                level = len(first_line) - len(first_line.lstrip("#"))
                if 1 <= level <= 6 and first_line[level : level + 1] == " ":
                    flush()
                    heading_levels[level] = first_line[level + 1 :].strip()
                    for deeper in tuple(key for key in heading_levels if key > level):
                        del heading_levels[deeper]
                    pending_headings = tuple(heading_levels[key] for key in sorted(heading_levels))
            if pending and len("\n\n".join((*pending, block))) > self._max_characters:
                flush()
                pending_headings = tuple(heading_levels[key] for key in sorted(heading_levels))
            pending.append(block)
        flush()

        if not outputs:
            raise ValueError("document produced no chunks")
        return tuple(outputs)
