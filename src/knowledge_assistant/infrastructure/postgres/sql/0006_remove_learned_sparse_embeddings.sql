UPDATE projection_generations
SET compatibility_manifest = compatibility_manifest
    - 'sparse_embedding_model'
    - 'sparse_embedding_dimensions'
WHERE compatibility_manifest ? 'sparse_embedding_model'
   OR compatibility_manifest ? 'sparse_embedding_dimensions';

ALTER TABLE chunks
    DROP COLUMN IF EXISTS sparse_embedding;
