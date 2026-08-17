CREATE UNIQUE INDEX ingestion_jobs_one_active_source_idx
    ON ingestion_jobs (normalized_source_key)
    WHERE state NOT IN ('ready', 'rejected', 'failed');

-- A stable vault path points at the current canonical file. Historical revisions
-- retain the path and file fingerprint but do not each own a separate file.
ALTER TABLE document_revisions
    DROP CONSTRAINT IF EXISTS document_revisions_vault_path_key;

CREATE TABLE ingestion_subscribers (
    job_id uuid NOT NULL REFERENCES ingestion_jobs(job_id) ON DELETE CASCADE,
    client_type text NOT NULL,
    recipient_key text NOT NULL,
    request_message_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, client_type, recipient_key, request_message_id)
);

CREATE TABLE client_checkpoints (
    client_type text NOT NULL,
    checkpoint_key text NOT NULL,
    checkpoint_value bigint NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (client_type, checkpoint_key)
);

CREATE TABLE service_heartbeats (
    role text NOT NULL,
    instance_id text NOT NULL,
    last_seen_at timestamptz NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (role, instance_id)
);

CREATE INDEX service_heartbeats_role_seen_idx
    ON service_heartbeats (role, last_seen_at DESC);
