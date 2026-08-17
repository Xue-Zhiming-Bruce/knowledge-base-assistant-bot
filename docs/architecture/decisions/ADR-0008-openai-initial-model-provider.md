# ADR-0008: Use OpenAI as the Initial Model Provider

- **Status:** Accepted
- **Date:** 2026-07-28

## Context

Knowledge Assistant needs model capabilities for embeddings, grounded answer generation, and potentially reranking or metadata enrichment. Operating several model-provider integrations initially would add configuration, evaluation, security review, and failure-handling complexity without serving a current requirement.

## Decision

Use OpenAI as the initial provider for embedding and answer-generation capabilities. OpenAI may also be evaluated for optional reranking and metadata enrichment where it provides measurable value.

Model identifiers remain explicit configuration rather than hard-coded defaults. A model is selected only after the relevant quality, latency, privacy, and cost evaluation.

The Knowledge Engine continues to depend on provider-independent embedding, generation, and reranking ports. OpenAI SDK types, request formats, errors, and credentials remain inside the OpenAI infrastructure adapter.

## Consequences

- Local and production configuration require one `OPENAI_API_KEY`.
- There is one initial provider integration to secure, monitor, and evaluate.
- Embedding and generation model versions remain part of projection and answer provenance.
- Changing an embedding model creates a new projection generation.
- OpenAI availability, quotas, privacy settings, and cost require operational monitoring.
- Other providers can be added later without changing canonical documents or core domain policy.

## Alternatives considered

- Implement several providers immediately: rejected because it adds unneeded complexity and multiplies the evaluation surface.
- Couple the Knowledge Engine directly to OpenAI SDKs: rejected because it would weaken testability and provider replaceability.
- Use local models initially: not selected because it is not a current requirement; the provider ports preserve this future option.

## Review triggers

Reconsider this decision if:

- privacy or data-residency requirements prohibit the selected OpenAI service;
- evaluated quality, latency, or cost no longer meets release gates;
- local inference becomes an explicit product requirement;
- provider availability becomes an unacceptable reliability risk.

## Related documents

- [Retrieval, Answers, and Citations](../06-retrieval-answers-and-citations.md)
- [Evaluation Architecture](../08-evaluation-architecture.md)
- [Extension Contracts](../12-extension-contracts.md)
