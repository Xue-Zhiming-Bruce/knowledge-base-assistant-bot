CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE documents (
    document_id text PRIMARY KEY
        CHECK (document_id ~ '^doc_[a-f0-9]{32}$'),
    current_revision_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE document_sources (
    source_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id text NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    normalized_source_key text NOT NULL UNIQUE,
    source_url text NOT NULL,
    source_type text NOT NULL,
    source_provider text NOT NULL,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_checked_at timestamptz
);

CREATE INDEX document_sources_document_id_idx
    ON document_sources (document_id);

CREATE TABLE document_revisions (
    revision_id text PRIMARY KEY
        CHECK (revision_id ~ '^rev_[a-f0-9]{32}$'),
    document_id text NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    schema_version integer NOT NULL CHECK (schema_version > 0),
    vault_path text NOT NULL UNIQUE,
    title text NOT NULL CHECK (length(title) > 0),
    source_url text NOT NULL,
    source_urls jsonb NOT NULL,
    source_type text NOT NULL,
    source_provider text NOT NULL,
    authors jsonb NOT NULL,
    published_at timestamptz,
    acquired_at timestamptz NOT NULL,
    content_fingerprint text NOT NULL
        CHECK (content_fingerprint ~ '^sha256:[a-f0-9]{64}$'),
    file_fingerprint text NOT NULL
        CHECK (file_fingerprint ~ '^sha256:[a-f0-9]{64}$'),
    language text NOT NULL,
    ingestion_provenance jsonb NOT NULL,
    status text NOT NULL DEFAULT 'accepted'
        CHECK (status IN ('accepted', 'superseded', 'conflict', 'quarantined')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, content_fingerprint)
);

ALTER TABLE documents
    ADD CONSTRAINT documents_current_revision_fk
    FOREIGN KEY (current_revision_id)
    REFERENCES document_revisions(revision_id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE INDEX document_revisions_document_id_idx
    ON document_revisions (document_id, created_at DESC);

CREATE TABLE ingestion_jobs (
    job_id uuid PRIMARY KEY,
    idempotency_key text NOT NULL UNIQUE,
    normalized_source_key text NOT NULL,
    source_url text NOT NULL,
    source_type text,
    source_provider text,
    state text NOT NULL CHECK (
        state IN (
            'accepted', 'queued', 'fetching', 'extracting', 'normalizing',
            'validating', 'committing', 'indexing', 'ready',
            'ready_degraded', 'retry_scheduled', 'rejected',
            'needs_review', 'conflict', 'failed'
        )
    ),
    document_id text REFERENCES documents(document_id),
    revision_id text REFERENCES document_revisions(revision_id),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_attempt_at timestamptz,
    lease_owner text,
    lease_expires_at timestamptz,
    error_class text,
    error_code text,
    error_detail jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ingestion_jobs_claim_idx
    ON ingestion_jobs (state, next_attempt_at, created_at)
    WHERE state IN ('queued', 'retry_scheduled');

CREATE TABLE ingestion_attempts (
    attempt_id uuid PRIMARY KEY,
    job_id uuid NOT NULL REFERENCES ingestion_jobs(job_id) ON DELETE CASCADE,
    attempt_number integer NOT NULL CHECK (attempt_number > 0),
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    outcome text,
    error_class text,
    error_code text,
    trace_id text,
    UNIQUE (job_id, attempt_number)
);

CREATE TABLE outbox_events (
    event_id uuid PRIMARY KEY,
    aggregate_type text NOT NULL,
    aggregate_id text NOT NULL,
    event_type text NOT NULL,
    event_version integer NOT NULL CHECK (event_version > 0),
    payload jsonb NOT NULL,
    occurred_at timestamptz NOT NULL,
    published_at timestamptz,
    delivery_attempts integer NOT NULL DEFAULT 0 CHECK (delivery_attempts >= 0)
);

CREATE INDEX outbox_events_pending_idx
    ON outbox_events (occurred_at)
    WHERE published_at IS NULL;

CREATE TABLE notification_deliveries (
    notification_id uuid PRIMARY KEY,
    event_id uuid NOT NULL REFERENCES outbox_events(event_id) ON DELETE CASCADE,
    client_type text NOT NULL,
    recipient_key text NOT NULL,
    idempotency_key text NOT NULL UNIQUE,
    state text NOT NULL CHECK (state IN ('pending', 'delivered', 'failed')),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_attempt_at timestamptz,
    delivered_at timestamptz,
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE question_sessions (
    session_id uuid PRIMARY KEY,
    principal_id text NOT NULL,
    knowledge_space_id text NOT NULL,
    client_type text NOT NULL,
    state text NOT NULL CHECK (state IN ('active', 'closed', 'expired')),
    version integer NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at timestamptz NOT NULL,
    last_activity_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    closed_at timestamptz
);

CREATE UNIQUE INDEX question_sessions_one_active_idx
    ON question_sessions (principal_id, knowledge_space_id)
    WHERE state = 'active';

CREATE INDEX question_sessions_expiry_idx
    ON question_sessions (expires_at)
    WHERE state = 'active';

CREATE TABLE session_turns (
    session_id uuid NOT NULL REFERENCES question_sessions(session_id) ON DELETE CASCADE,
    turn_number integer NOT NULL CHECK (turn_number > 0),
    client_message_id text NOT NULL,
    user_question text NOT NULL,
    assistant_answer text,
    citations jsonb,
    pipeline_version jsonb,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (session_id, turn_number),
    UNIQUE (session_id, client_message_id)
);

CREATE TABLE projection_generations (
    generation_id uuid PRIMARY KEY,
    state text NOT NULL CHECK (state IN ('building', 'validated', 'active', 'retired', 'failed')),
    compatibility_manifest jsonb NOT NULL,
    expected_document_count bigint,
    indexed_document_count bigint NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    activated_at timestamptz,
    retired_at timestamptz
);

CREATE UNIQUE INDEX projection_generations_one_active_idx
    ON projection_generations ((state))
    WHERE state = 'active';

CREATE TABLE chunks (
    generation_id uuid NOT NULL
        REFERENCES projection_generations(generation_id) ON DELETE CASCADE,
    chunk_id text NOT NULL,
    document_id text NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    revision_id text NOT NULL REFERENCES document_revisions(revision_id) ON DELETE CASCADE,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    content text NOT NULL,
    content_fingerprint text NOT NULL
        CHECK (content_fingerprint ~ '^sha256:[a-f0-9]{64}$'),
    heading_path jsonb NOT NULL,
    citation_anchor jsonb NOT NULL,
    token_count integer NOT NULL CHECK (token_count > 0),
    embedding vector,
    search_vector tsvector NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (generation_id, chunk_id),
    UNIQUE (generation_id, revision_id, ordinal)
);

CREATE INDEX chunks_document_revision_idx
    ON chunks (generation_id, document_id, revision_id);

CREATE INDEX chunks_search_vector_idx
    ON chunks USING gin (search_vector);

COMMENT ON COLUMN chunks.embedding IS
    'Rebuildable pgvector projection. Dimension and ANN indexes are generation-specific.';
