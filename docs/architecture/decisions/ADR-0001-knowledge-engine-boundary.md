# ADR-0001: Keep the Knowledge Engine Independent of Clients

- **Status:** Accepted
- **Date:** 2026-07-28

## Context

Telegram is the first interface, while future web, desktop, REST, and CLI interfaces are expected. Allowing Telegram commands, message types, or delivery behavior to shape core services would make reuse costly and business rules inconsistent.

## Decision

All business logic belongs to a client-independent Knowledge Engine exposed through use-case-oriented application contracts. Telegram and future interfaces are adapters that map identity, commands, results, and events.

## Consequences

- Clients remain small and independently replaceable.
- Application result models must be presentation-neutral.
- Asynchronous notification is expressed as domain events rather than Telegram callbacks.
- Some translation code exists even when there is only one client.

## Alternatives considered

- Build Telegram-first and extract a core later: rejected because accidental coupling becomes the de facto API.
- Expose infrastructure services directly to clients: rejected because it duplicates orchestration and weakens policy enforcement.

## Related documents

- [System Context and Boundaries](../02-system-context-and-boundaries.md)
- [Clients and Session Management](../07-clients-and-session-management.md)

