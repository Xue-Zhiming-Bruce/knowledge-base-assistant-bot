# ADR-0006: Keep Question-Session History Temporary

- **Status:** Accepted
- **Date:** 2026-07-28

## Context

Follow-up questions require short-term conversational context. Persisting chats as knowledge by default would increase privacy risk, blur canonical knowledge boundaries, and violate the required `/end` behavior.

## Decision

Question sessions are explicitly started, stored in a TTL-backed session store, and deleted on `/end` or expiry. Session history is not written to the vault. Only minimized operational facts may remain under retention policy.

## Consequences

- Conversation privacy and lifecycle are clear.
- Session cleanup is a monitored, retryable operation.
- Session loss affects conversational continuity but not canonical knowledge.
- Recorded answer feedback is durable: it survives `/end` and expiry while the
  temporary conversation content is still deleted (migration `0008` decouples
  `answer_feedback` from the cascading session/turn deletion). Feedback rows
  retain only privacy-safe metadata plus the opaque `(session_id, turn_number)`
  turn reference, never question/answer text, prompts, evidence, or URLs.
- Exporting a conversation in the future requires a separate, explicit user action and ingestion path.

## Alternatives considered

- Persist all conversations: rejected due to privacy and knowledge-quality concerns.
- Keep state only inside the Telegram process: rejected because it harms reliability, scaling, and reuse by future clients.

## Related documents

- [Clients and Session Management](../07-clients-and-session-management.md)
- [Security, Privacy, and Reliability](../10-security-privacy-and-reliability.md)

