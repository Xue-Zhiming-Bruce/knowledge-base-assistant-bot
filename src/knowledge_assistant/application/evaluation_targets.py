"""Answer-evaluation target validation: chunk-level first, document fallback.

Kept as a small focused module so the routing rule is reviewable at a glance:
cases that carry a target chunk are validated by chunk id + content fingerprint;
cases without a target chunk fall back to document-level URL resolution; a case
with neither fails closed.
"""

from __future__ import annotations

from uuid import UUID

from knowledge_assistant.domain.evaluation import SyntheticEvaluationCase
from knowledge_assistant.ports.evaluation import EvaluationCorpus


def validate_answer_target(
    corpus: EvaluationCorpus,
    case: SyntheticEvaluationCase,
    generation_id: UUID,
) -> None:
    """Fail closed unless the answerable case's target resolves.

    Chunk-level path (primary): the target chunk must exist in the selected
    generation with an unchanged content fingerprint.
    Document-level path (fallback, only when no target chunk exists): the
    target document must be ingested (resolved by its canonical source URL).
    """

    if case.target_chunk_id is not None and case.content_fingerprint is not None:
        if not corpus.validate_chunk(
            generation_id=generation_id,
            chunk_id=case.target_chunk_id,
            content_fingerprint=case.content_fingerprint,
        ):
            raise RuntimeError(
                f"evaluation case {case.case_id} references a missing or changed chunk"
            )
        return
    if case.document_level and case.target_url is not None:
        if corpus.document_id_for_url(url=case.target_url) is None:
            raise RuntimeError(
                f"evaluation case {case.case_id} target document is not ingested: "
                f"{case.target_url}"
            )
        return
    raise ValueError(
        f"evaluation case {case.case_id} has neither a target chunk nor a "
        "document-level target to validate"
    )
