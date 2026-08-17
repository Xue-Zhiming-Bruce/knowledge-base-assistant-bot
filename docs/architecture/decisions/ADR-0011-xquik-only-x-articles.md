# ADR-0011: Use Xquik Directly for Rich X Articles Only

- **Status:** Accepted
- **Date:** 2026-07-31
- **Supersedes:** [ADR-0010](./ADR-0010-credentialed-x-api-acquisition.md)

## Context

The user archives rich X Articles but does not need ordinary posts, long posts,
or threads. The official X API was being called first only to classify the URL
and obtain metadata, creating a separate credential and metered read even though
Xquik returns the Article title, author, creation time, cover image, and ordered
body blocks.

## Decision

Send the stable status ID from each submitted X URL directly to Xquik's Article
endpoint. Use the scoped Tempo MPP transport by default, with the worker-only
Xquik API-key transport available as an alternative. Preserve the returned
block order and reject unknown or lossy representations.

Do not configure or call the official X API. Treat `article_not_found` as a
terminal, user-facing result explaining that ordinary posts, long posts, and
threads are unsupported.

## Consequences

- Rich X Articles require one provider call instead of an X lookup followed by
  an Xquik call.
- No X developer account, bearer token, or X API credits are required.
- Xquik supplies both Article content and source metadata.
- Ordinary X posts, long posts, and threads are intentionally unsupported.
- Xquik availability, pricing, and schema remain provider risks isolated behind
  the Article-provider port.

## Alternatives considered

- Keep X API classification before Xquik: rejected because it adds cost and a
  credential for content the user does not save.
- Try Xquik and then fall back to X API: rejected because the user does not need
  the fallback content types.
- Reconstruct Articles from plain text: rejected because it loses block order
  and formatting.

## Related documents

- [Ingestion Architecture](../04-ingestion-architecture.md)
- [Security, Privacy, and Reliability](../10-security-privacy-and-reliability.md)
- [Docker Operations Runbook](../../operations/docker.md)
