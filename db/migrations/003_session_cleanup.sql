-- 003: expired-session cleanup.
-- Deletes sessions past expires_at (all their data cascades). The demo session never
-- expires. Runs as the owner (SECURITY DEFINER) so RLS doesn't hide other sessions;
-- callable by docagent_app so a cron/endpoint can trigger it without owner credentials.
-- Uploaded files on disk are removed by the API using the returned ids.

CREATE FUNCTION cleanup_expired_sessions(now_ts timestamptz DEFAULT now())
    RETURNS SETOF uuid
    LANGUAGE sql
    SECURITY DEFINER
    SET search_path = public, pg_temp
    AS $$
        DELETE FROM sessions
        WHERE expires_at < now_ts
          AND id <> demo_session_id()
        RETURNING id
    $$;

REVOKE ALL ON FUNCTION cleanup_expired_sessions(timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION cleanup_expired_sessions(timestamptz) TO docagent_app;
