ALTER TABLE document_revisions
    ADD COLUMN assets jsonb NOT NULL DEFAULT '[]'::jsonb;

-- A canonical vault path is stable across revisions, and normalizer/schema
-- changes may intentionally preserve the same body fingerprint. Revision ID is
-- the correct uniqueness boundary.
ALTER TABLE document_revisions
    DROP CONSTRAINT IF EXISTS document_revisions_vault_path_key,
    DROP CONSTRAINT IF EXISTS document_revisions_document_id_content_fingerprint_key;

CREATE INDEX document_revisions_vault_path_idx
    ON document_revisions (vault_path);

COMMENT ON COLUMN document_revisions.assets IS
    'Canonical image asset metadata mirrored from Knowledge Document frontmatter.';
