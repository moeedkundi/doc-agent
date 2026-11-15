"""`python -m docagent_api.cleanup`: delete expired sessions and everything they left behind.

Run on a schedule (deploy/: hourly cron). Sessions live 24 h (config/limits.yaml); this:

1. deletes expired sessions via `cleanup_expired_sessions()` (migration 003) — documents,
   records, runs and traces cascade; the demo session never expires;
2. removes those sessions' uploaded files and cached page images (`<doc_id>.p<n>.webp`);
3. removes their LangGraph checkpoints (thread `<run_id>` and `ask:<session_id>`);
4. sweeps orphan files in the upload dir (no matching document) older than 24 h.

Connects with DATABASE_URL (the table owner: RLS isn't forced, so it sees every session).
Needs UPLOAD_DIR. Prints one summary line; never prints DSNs.
"""

import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg import sql

ORPHAN_AGE_S = 24 * 3600
_CHECKPOINT_TABLES = ("checkpoint_writes", "checkpoint_blobs", "checkpoints")


@dataclass(frozen=True)
class CleanupReport:
    sessions: int
    files: int
    checkpoint_threads: int
    orphans: int


def _doc_files(upload_dir: Path, storage_path: str) -> list[Path]:
    path = upload_dir / storage_path
    return [path, *upload_dir.glob(f"{path.stem}.p*.webp")]


def cleanup(
    conn: psycopg.Connection,
    upload_dir: Path,
    now: datetime | None = None,
    orphan_age_s: float = ORPHAN_AGE_S,
) -> CleanupReport:
    with conn.transaction():
        conn.execute(
            "CREATE TEMP TABLE expired ON COMMIT DROP AS SELECT id FROM sessions "
            "WHERE expires_at < coalesce(%s, now()) AND id <> demo_session_id()",
            (now,),
        )
        docs = conn.execute(
            "SELECT storage_path FROM documents WHERE session_id IN (SELECT id FROM expired)"
        ).fetchall()
        threads = [
            str(r[0])
            for r in conn.execute(
                "SELECT id::text FROM runs WHERE session_id IN (SELECT id FROM expired) "
                "UNION SELECT 'ask:' || id FROM expired"
            ).fetchall()
        ]
        sessions: list[UUID] = [
            r[0]
            for r in conn.execute(
                "SELECT * FROM cleanup_expired_sessions(coalesce(%s, now()))", (now,)
            ).fetchall()
        ]
        for table in _CHECKPOINT_TABLES:
            if threads and conn.execute("SELECT to_regclass(%s)", (table,)).fetchone() != (None,):
                delete = sql.SQL("DELETE FROM {} WHERE thread_id = ANY(%s)")
                conn.execute(delete.format(sql.Identifier(table)), (threads,))

    files = 0
    for (storage_path,) in docs:
        for f in _doc_files(upload_dir, str(storage_path)):
            if f.is_file():
                f.unlink()
                files += 1

    known = {Path(str(r[0])).stem for r in conn.execute("SELECT storage_path FROM documents")}
    orphans = 0
    cutoff = time.time() - orphan_age_s
    if upload_dir.is_dir():
        for f in upload_dir.iterdir():
            doc_stem = f.name.split(".", 1)[0]
            if f.is_file() and doc_stem not in known and f.stat().st_mtime < cutoff:
                f.unlink()
                orphans += 1
    return CleanupReport(len(sessions), files, len(threads), orphans)


def main() -> int:
    dsn, upload_dir = os.environ.get("DATABASE_URL"), os.environ.get("UPLOAD_DIR")
    if not dsn or not upload_dir:
        print("cleanup: DATABASE_URL and UPLOAD_DIR must be set", file=sys.stderr)
        return 2
    with psycopg.connect(dsn, autocommit=True) as conn:
        r = cleanup(conn, Path(upload_dir))
    print(
        f"cleanup: {r.sessions} expired sessions, {r.files} files, "
        f"{r.checkpoint_threads} checkpoint threads, {r.orphans} orphan files removed"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
