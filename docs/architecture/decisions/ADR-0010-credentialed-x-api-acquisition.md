# ADR-0010: Use the Official Credentialed X API for Articles and Threads

- **Status:** Superseded by [ADR-0011](./ADR-0011-xquik-only-x-articles.md)
- **Date:** 2026-07-30

## Context

Knowledge Assistant must ingest X Articles and author-written threads without
making Telegram or the core document model depend on X. Public X pages are
JavaScript-heavy, frequently require authentication, and expose unstable markup.
Unofficial reader proxies and private GraphQL endpoints would add an undeclared
data processor and a brittle dependency.

X API v2 exposes structured `article`, `note_tweet`, `conversation_id`, author,
entity, and media fields. Post lookup and full-archive search are metered,
credit-based reads. Thread discovery can therefore create both availability and
cost risk if it is unbounded.

## Decision

Use X API v2 with an application Bearer Token behind the source-fetcher port.
Normalize `x.com` and legacy `twitter.com` status URLs to a stable X post ID.

For an X Article, request the structured Article and media expansions and
translate DraftJS blocks into semantic content. For a long post or thread,
request the root post and use full-archive search with `conversation_id` and the
root author's username. Include only reachable self-replies by that author and
preserve chronological order.

Thread acquisition is bounded by
`KNOWLEDGE_ASSISTANT_X_MAX_THREAD_POSTS`, which defaults to 100 and may not
exceed 100. If the response indicates more results, reject partial ingestion
instead of silently truncating canonical knowledge. X API authentication,
billing, access, rate-limit, and network failures are mapped to typed ingestion
outcomes. Tokens never enter URLs, logs, Markdown, or diagnostic summaries.

Do not use browser automation, undocumented X endpoints, or third-party reader
proxies as automatic production fallbacks.

## Consequences

- X-specific acquisition is independently replaceable and the downstream
  normalization, asset, vault, retrieval, and citation pipeline is reused.
- Articles preserve headings, lists, quotations, code, inline formatting,
  links, and supported images.
- Threads are deterministic and exclude unrelated conversation replies.
- The operator must create an X developer app, configure a Bearer Token, enable
  billing/credits, and have full-archive search access for historical threads.
- A thread costs at least one post lookup plus the returned search resources;
  the configured bound limits worst-case reads but does not make them free.
- Protected, deleted, inaccessible, non-root, or over-limit threads fail
  explicitly.
- A single short X post is not accepted as long-form knowledge.

## Alternatives considered

- Scrape public X HTML: rejected because the page is JavaScript-dependent,
  access-controlled, and markup is not a stable extraction contract.
- Use an unofficial public reader API: rejected because it introduces an
  undeclared external processor and availability dependency.
- Ingest every reply in a conversation: rejected because other participants'
  replies are not part of the author's thread.
- Silently truncate long threads: rejected because canonical Markdown must not
  imply completeness when content is missing.

## Review triggers

Reconsider this decision if:

- X changes the Article or search contracts;
- full-archive access or pricing makes thread ingestion impractical;
- X provides a dedicated thread or Article-read endpoint;
- a user-approved alternative acquisition provider is introduced with an
  explicit privacy and reliability assessment.

## Related documents

- [Ingestion Architecture](../04-ingestion-architecture.md)
- [Security, Privacy, and Reliability](../10-security-privacy-and-reliability.md)
- [Extension Contracts](../12-extension-contracts.md)
- [Observability and Operations](../09-observability-and-operations.md)
