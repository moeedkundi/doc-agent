-- 002: roles, grants and row-level security.
--
-- Roles (cluster-wide, created idempotently; passwords are set by the migration runner
-- from DATABASE_APP_URL / DATABASE_READER_URL, never stored here):
--   docagent_app     read/write on data tables, always under RLS. Used by the API and by
--                    sqlstore's typed save_record.
--   docagent_reader  SELECT on document tables only, under RLS, read-only and 3s timeout.
--                    Used by sqlstore's run_select.
-- The migration owner (the DB owner/superuser) is used only for migrations and cleanup.
-- RLS is not FORCEd, so the owner bypasses it; app and reader never own tables.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'docagent_app') THEN
        CREATE ROLE docagent_app LOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'docagent_reader') THEN
        CREATE ROLE docagent_reader LOGIN;
    END IF;
END
$$;

ALTER ROLE docagent_reader SET default_transaction_read_only = on;
ALTER ROLE docagent_reader SET statement_timeout = '3s';

-- ---------------------------------------------------------------------------
-- Session helpers used by the policies.
-- ---------------------------------------------------------------------------

-- Shared read-only session holding the public sample documents.
CREATE FUNCTION demo_session_id() RETURNS uuid
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
    AS $$ SELECT '00000000-0000-0000-0000-000000000001'::uuid $$;

-- The caller's session, from `SET LOCAL app.session_id = '<uuid>'`. NULL when unset.
CREATE FUNCTION current_session_id() RETURNS uuid
    LANGUAGE sql STABLE PARALLEL SAFE
    AS $$ SELECT nullif(current_setting('app.session_id', true), '')::uuid $$;

INSERT INTO sessions (id, ip_hash, expires_at)
VALUES (demo_session_id(), NULL, 'infinity');

-- ---------------------------------------------------------------------------
-- Grants.
-- ---------------------------------------------------------------------------

GRANT USAGE ON SCHEMA public TO docagent_app, docagent_reader;

GRANT SELECT, INSERT, UPDATE, DELETE ON
    sessions, documents, invoices, invoice_lines, purchase_orders, po_lines,
    invoice_po_matches, runs, trace_events, review_actions
TO docagent_app;
GRANT SELECT, INSERT ON llm_ledger TO docagent_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO docagent_app;

-- The reader sees document data only: no sessions, runs, traces, reviews or ledger.
GRANT SELECT ON
    documents, invoices, invoice_lines, purchase_orders, po_lines, invoice_po_matches
TO docagent_reader;

-- ---------------------------------------------------------------------------
-- Row-level security.
-- Reads: own session + the demo session. Writes (app only): own session only, so demo
-- rows are read-only for everyone except the owner.
-- ---------------------------------------------------------------------------

ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;
CREATE POLICY sessions_own ON sessions TO docagent_app
    USING (id = current_session_id())
    WITH CHECK (id = current_session_id());

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'documents', 'invoices', 'invoice_lines', 'purchase_orders', 'po_lines',
        'invoice_po_matches', 'runs', 'trace_events', 'review_actions'
    ]
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY %I ON %I FOR SELECT TO docagent_app, docagent_reader
                 USING (session_id = current_session_id() OR session_id = demo_session_id())',
            t || '_read', t);
        EXECUTE format(
            'CREATE POLICY %I ON %I FOR ALL TO docagent_app
                 USING (session_id = current_session_id())
                 WITH CHECK (session_id = current_session_id())',
            t || '_write', t);
    END LOOP;
END
$$;
