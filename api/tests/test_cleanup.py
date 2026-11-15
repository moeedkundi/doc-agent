import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg

from docagent_api.cleanup import cleanup

DEMO = "00000000-0000-0000-0000-000000000001"


def _session(conn: psycopg.Connection, expires: datetime) -> str:
    sid = str(uuid4())
    conn.execute("INSERT INTO sessions (id, expires_at) VALUES (%s, %s)", (sid, expires))
    return sid


def _document(conn: psycopg.Connection, uploads: Path, sid: str) -> str:
    doc = str(uuid4())
    conn.execute(
        "INSERT INTO documents (id, session_id, filename, storage_path, pages) "
        "VALUES (%s, %s, 'x.pdf', %s, 1)",
        (doc, sid, f"{doc}.pdf"),
    )
    (uploads / f"{doc}.pdf").write_bytes(b"%PDF-")
    (uploads / f"{doc}.p1.webp").write_bytes(b"RIFF")
    return doc


def test_cleanup_removes_expired_sessions_files_and_orphans(
    owner: psycopg.Connection, tmp_path: Path
) -> None:
    now = datetime.now(UTC)
    old = _session(owner, now - timedelta(hours=1))
    live = _session(owner, now + timedelta(hours=1))
    gone, kept = _document(owner, tmp_path, old), _document(owner, tmp_path, live)
    demo_doc = _document(owner, tmp_path, DEMO)
    orphan, fresh_orphan = tmp_path / f"{uuid4()}.pdf", tmp_path / f"{uuid4()}.pdf"
    orphan.write_bytes(b"x")
    fresh_orphan.write_bytes(b"x")  # an upload still being stored: too new to sweep
    stale = time.time() - 2 * 24 * 3600
    os.utime(orphan, (stale, stale))

    run = str(uuid4())
    owner.execute(
        "INSERT INTO runs (id, session_id, document_id) VALUES (%s, %s, %s)", (run, old, gone)
    )
    owner.execute("CREATE TABLE checkpoints (thread_id text)")  # stand-in for LangGraph's
    owner.execute(
        "INSERT INTO checkpoints VALUES (%s), (%s), (%s)", (run, f"ask:{old}", f"ask:{live}")
    )

    report = cleanup(owner, tmp_path, now=now)

    assert (report.sessions, report.files, report.orphans) == (1, 2, 1)
    assert not (tmp_path / f"{gone}.pdf").exists() and not (tmp_path / f"{gone}.p1.webp").exists()
    for doc in (kept, demo_doc):  # live and demo sessions keep their files
        assert (tmp_path / f"{doc}.pdf").exists() and (tmp_path / f"{doc}.p1.webp").exists()
    assert not orphan.exists() and fresh_orphan.exists()
    ids = {r[0] for r in owner.execute("SELECT id::text FROM sessions")}
    assert old not in ids and {live, DEMO} <= ids
    left = [r[0] for r in owner.execute("SELECT thread_id FROM checkpoints")]
    assert left == [f"ask:{live}"] and report.checkpoint_threads == 2
