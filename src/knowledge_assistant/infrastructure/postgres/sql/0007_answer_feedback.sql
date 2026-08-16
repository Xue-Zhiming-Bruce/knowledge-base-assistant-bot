ALTER TABLE session_turns
    ADD COLUMN answer_message_id text;

COMMENT ON COLUMN session_turns.answer_message_id IS
    'Telegram message id of the rendered answer, recorded for reply-based feedback targeting.';

CREATE TABLE answer_feedback (
    feedback_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    principal_id text NOT NULL,
    session_id uuid NOT NULL REFERENCES question_sessions(session_id) ON DELETE CASCADE,
    turn_number integer NOT NULL,
    direction text NOT NULL CHECK (direction IN ('up', 'down')),
    retrieval_strategy text NOT NULL,
    projection_generation text NOT NULL,
    generation_model text NOT NULL,
    answer_prompt_version text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT answer_feedback_turn_fk
        FOREIGN KEY (session_id, turn_number)
        REFERENCES session_turns (session_id, turn_number) ON DELETE CASCADE,
    CONSTRAINT answer_feedback_one_per_turn
        UNIQUE (principal_id, session_id, turn_number)
);

COMMENT ON TABLE answer_feedback IS
    'Privacy-safe answer feedback. Stores only safe pipeline metadata, never question '
    'or answer text, source URLs, evidence, prompts, or credentials.';
