# Security, Privacy, and Reliability

## Why this document exists

Knowledge Assistant processes personal knowledge, untrusted internet content, and model-provider requests. This document defines cross-cutting controls for confidentiality, integrity, availability, and recovery.

## Security objectives

- Only authorized principals can ingest, query, export, or delete their knowledge.
- Canonical files cannot be written outside the managed vault namespace.
- External content cannot cause network, filesystem, prompt, or tool abuse.
- Secrets and personal content are minimized and protected.
- Every material state change is attributable and auditable.
- Failures preserve existing knowledge and support safe recovery.

## Data classification

| Class | Examples | Handling |
| --- | --- | --- |
| Secrets | bot tokens, provider keys, database credentials | secret manager, never logged, least privilege, rotation |
| Personal knowledge | Markdown bodies, source images, questions, answers, session turns | encryption, access control, minimized transmission, explicit retention |
| Sensitive metadata | source URLs, titles, authors, document associations | restricted telemetry, encryption, access control |
| Operational metadata | job states, versions, timings, pseudonymous IDs | retained by policy, no content payloads |
| Public configuration | supported source types, schema versions | safe to expose |

## Identity and authorization

Telegram identity is mapped to an internal principal; it is not itself the domain identity. Authorization occurs at the application boundary and is rechecked for asynchronous operations using a durable knowledge-space reference.

The design begins with one personal knowledge space but preserves an explicit ownership boundary so a future multi-user deployment does not require retrofitting isolation into every query.

Administrative operations use separate roles and stronger authentication. Provider callbacks and webhooks are verified before processing.

## Source-fetching security

Fetching URLs is an SSRF-sensitive capability. Controls include:

- allowlisted schemes;
- hostname and source-provider policy;
- DNS and redirect revalidation;
- blocked loopback, link-local, private, metadata-service, and disallowed address ranges;
- response size, redirect count, and time limits;
- content-type validation;
- isolated network client with restricted egress;
- no ambient cloud or filesystem credentials;
- optional malware scanning for future binary inputs.

Article images are treated as untrusted binary inputs now. Their independent
fetcher requires public HTTPS, revalidates DNS and every redirect, bounds
download bytes and decoded pixels, rejects credentials and unsupported formats,
and validates decoded image bytes before persistence. SVG is excluded because
it is active, script-capable content in some renderers.

Source adapters do not execute page scripts unless a specifically sandboxed extraction strategy requires it.

Provider-owned alternate representations are permitted only through an
explicit adapter policy. The initial Medium adapter may use Medium's public RSS
feed after a direct HTTP 403. Feed acquisition receives the same network and
size controls as HTML; XML declarations capable of defining document types or
entities are rejected; and the adapter must match the requested story before
returning content. No browser challenge bypass, authenticated cookie reuse, or
third-party scraping proxy is part of this fallback.

X content is limited to rich Articles acquired from Xquik. The worker uses
either a worker-only Xquik API key or a scoped Tempo MPP wallet with a hard
per-request cap. Neither secret may appear in source URLs, logs, traces,
Markdown, or redacted configuration. Unknown or lossy blocks are rejected, and
ordinary posts, long posts, and threads are outside the adapter policy. Browser
cookie reuse, X bearer tokens, undocumented GraphQL calls, and HTML scraping are
not used.

## Content and prompt security

Source documents are data, never instructions. The answer pipeline:

- separates trusted instructions from evidence;
- labels evidence boundaries;
- strips or escapes control-like content where appropriate;
- gives the generator no side-effecting tools;
- tests indirect prompt injection;
- verifies that citations resolve to retrieved evidence;
- restricts retrieved evidence to the authorized knowledge space.

Model output is untrusted until validated and safely rendered by the client.

## Filesystem integrity

Vault access enforces:

- a configured absolute root;
- normalized, sanitized relative paths;
- rejection of traversal and unsafe symlinks;
- staged validation and atomic replace;
- content-addressed assets written only below the owning document's
  `Assets/<document_id>/` namespace;
- restrictive process permissions;
- conflict detection for external edits;
- backups and integrity scans.

No source-provided title or path is used directly as a filesystem target.

## Provider privacy

Before content is sent to extraction, embedding, reranking, or generation providers, policy determines:

- whether the provider is approved for the data class;
- what minimum text is required;
- geographic and retention constraints;
- training or data-use settings;
- encryption in transit;
- deletion and incident terms.

Provider adapters expose usage and policy metadata. Changing providers requires privacy review as well as technical evaluation.

## Secret management

Secrets are injected at runtime from an approved secret store, scoped per capability, and never committed to the vault or code repository. Rotation should not require rebuilding canonical data. Workers and client processes receive only the secrets they need.

The local Docker foundation reads development values through Compose interpolation from the git-ignored `.env`; `.env` is excluded from the image build context. Production deployments should replace local environment files with their platform secret mechanism. Secrets must never be passed as Docker build arguments or stored in image layers.

Application containers run as an unprivileged user with privilege escalation disabled and a read-only root filesystem. Writable access is limited to explicit temporary filesystems and the vault mount required by the role. PostgreSQL is not publicly exposed by the local Compose configuration.

## Reliability model

### Failure domains

- Telegram or future client delivery;
- source-provider availability or markup changes;
- job queue and registry;
- vault filesystem;
- model and embedding providers;
- vector and lexical indexes;
- telemetry backend.

The architecture prevents a failure in a derived store, notification channel, or telemetry exporter from corrupting canonical documents.

### Resilience patterns

- durable command acceptance before acknowledgement;
- at-least-once processing with idempotent stages;
- timeouts, bounded retries, jitter, and circuit breakers;
- atomic file writes and optimistic concurrency;
- outbox-based event publication;
- dead-letter and replay workflows;
- bulkheads for interactive queries versus background work;
- load shedding and per-principal limits;
- last-known-compatible projection during rebuild;
- graceful shutdown with lease release or expiry.

## Recovery objectives

Numeric recovery-time and recovery-point objectives depend on deployment and backup capabilities, but priorities are:

1. No accepted canonical Knowledge Document is lost after confirmed commit.
2. The vault can be restored independently of providers and indexes.
3. Registry and job state restore minimizes duplicate work; idempotency makes duplicate work safe.
4. Derived indexes can be rebuilt from the restored vault.
5. Temporary session loss may interrupt a conversation but must not affect canonical knowledge.

Recovery objectives and restore time are validated through drills.

## Backup policy

Backups must cover:

- canonical vault, with version history where available;
- registry/job database;
- configuration and schema/version manifests;
- audit data required for recovery.

Derived stores may be backed up for faster recovery but are not trusted as the only recovery path. Backup encryption, access control, retention, and restore tests are mandatory.

## Data retention and deletion

- Canonical knowledge persists until the user deletes or archives it.
- Raw source snapshots use an explicit, configurable retention policy.
- Question sessions are deleted on `/end` and expire automatically.
- Telemetry excludes content by default and follows bounded retention.
- Provider-side retention follows approved contracts and settings.
- Deletion propagates to caches and derived stores with observable completion.

## Threat modeling and assurance

Threat modeling is repeated when adding:

- a new source adapter or binary parser;
- a new external provider;
- a new client or authentication method;
- multi-user support;
- remote vault access;
- tools or actions available to models.

Security testing includes dependency scanning, secret scanning, path and URL fuzzing, authorization tests, malformed-document tests, prompt-injection cases, and restore exercises.

## Trade-offs

### External model providers

They may provide quality and speed but introduce privacy, availability, and cost dependencies. Provider ports, minimization, local-compatible contracts, and evaluation reduce lock-in; approved-data policy determines actual use.

### Retaining source snapshots

Snapshots improve reproducibility and extraction debugging but increase storage, privacy, and copyright risk. Retention is therefore explicit and independent from the durable Markdown knowledge artifact.

## Future extension points

- end-to-end encrypted or local-only deployments;
- per-document access control;
- multiple principals and shared spaces;
- hardware-backed secret storage;
- data residency policies;
- provider-independent confidential inference;
- formal audit export and compliance controls.
