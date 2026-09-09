-- Topic filing: articles the classifier could not place in an existing
-- Articles/<provider>/<Topic>/ folder wait here until the user files them
-- from Telegram.

CREATE TABLE pending_topic_filings (
    document_id text PRIMARY KEY REFERENCES documents(document_id) ON DELETE CASCADE,
    title text NOT NULL,
    provider text NOT NULL,
    pending_vault_path text NOT NULL,
    chat_id text,
    question_message_id text,
    notified_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
