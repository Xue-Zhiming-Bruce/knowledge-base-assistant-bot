ALTER TABLE chunks
    ADD COLUMN sparse_embedding sparsevec;

COMMENT ON COLUMN chunks.sparse_embedding IS
    'Rebuildable learned-sparse projection. Model and dimensions are generation-specific.';
