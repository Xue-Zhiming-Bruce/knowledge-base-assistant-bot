ALTER TABLE notification_deliveries
    DROP CONSTRAINT notification_deliveries_state_check;

ALTER TABLE notification_deliveries
    ADD CONSTRAINT notification_deliveries_state_check
    CHECK (state IN ('pending', 'delivering', 'delivered', 'failed'));

ALTER TABLE notification_deliveries
    ADD COLUMN lease_owner text,
    ADD COLUMN lease_expires_at timestamptz;

CREATE INDEX notification_deliveries_claim_idx
    ON notification_deliveries (state, next_attempt_at, created_at)
    WHERE state IN ('pending', 'delivering');
