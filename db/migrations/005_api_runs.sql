-- 005: what the API needs to report runs without re-reading graph checkpoints.

ALTER TABLE documents ADD COLUMN sample_id text;          -- set when copied from a public sample
ALTER TABLE runs ADD COLUMN replay boolean NOT NULL DEFAULT false;
ALTER TABLE runs ADD COLUMN error_code text;              -- e.g. budget_exceeded
ALTER TABLE runs ADD COLUMN error text;
ALTER TABLE runs ADD COLUMN result_json jsonb;            -- final record/issues/match/summary or answer
