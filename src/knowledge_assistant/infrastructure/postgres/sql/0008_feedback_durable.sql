-- Feedback must survive temporary question-session deletion (/end and expiry).
-- Drop the cascading foreign keys so recorded feedback rows persist as durable,
-- privacy-safe metadata after question_sessions and session_turns are deleted.
-- The (session_id, turn_number) pair is kept purely as a safe, opaque turn
-- reference: it carries no question, answer, evidence, source, or credential
-- content and remains the idempotency key for duplicate feedback.
ALTER TABLE answer_feedback
    DROP CONSTRAINT answer_feedback_session_id_fkey;

ALTER TABLE answer_feedback
    DROP CONSTRAINT answer_feedback_turn_fk;

COMMENT ON TABLE answer_feedback IS
    'Durable privacy-safe answer feedback. Survives temporary question-session '
    'deletion; stores only feedback direction, an opaque (session_id, turn_number) '
    'turn reference, retrieval strategy, projection generation, generation model, '
    'answer prompt version, and timestamp. Never question or answer text, prompts, '
    'evidence, citations, source URLs, or credentials.';
