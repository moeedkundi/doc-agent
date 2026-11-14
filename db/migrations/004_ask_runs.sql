-- 004: runs also record Q&A ("ask") turns, which have a question instead of a document.
-- Trace events and ledger rows reference runs, so asks need a row here too.

ALTER TABLE runs ALTER COLUMN document_id DROP NOT NULL;
ALTER TABLE runs ADD COLUMN kind text NOT NULL DEFAULT 'ingest'
    CHECK (kind IN ('ingest', 'ask'));
ALTER TABLE runs ADD COLUMN question text;
ALTER TABLE runs ADD CONSTRAINT runs_kind_shape
    CHECK ((kind = 'ingest') = (document_id IS NOT NULL));
