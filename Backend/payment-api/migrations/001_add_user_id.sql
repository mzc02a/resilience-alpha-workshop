BEGIN;

ALTER TABLE payments
ADD COLUMN IF NOT EXISTS user_id VARCHAR(36);

CREATE INDEX IF NOT EXISTS ix_payments_user_id
ON payments (user_id);

COMMIT;