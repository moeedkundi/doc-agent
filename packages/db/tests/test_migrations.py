import shutil
from pathlib import Path

import psycopg
import pytest

from docagent_db import MigrationError, apply_migrations, set_role_passwords
from docagent_db.migrate import discover

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "db" / "migrations"

EXPECTED_TABLES = {
    "sessions",
    "documents",
    "invoices",
    "invoice_lines",
    "purchase_orders",
    "po_lines",
    "invoice_po_matches",
    "runs",
    "trace_events",
    "review_actions",
    "llm_ledger",
    "schema_migrations",
}


def test_migration_files_are_numbered_uniquely() -> None:
    versions = [m.version for m in discover(MIGRATIONS_DIR)]
    assert versions == sorted(versions)
    assert versions[0].startswith("001_")


def test_migrations_apply_cleanly_and_are_idempotent(empty_db_dsn: str) -> None:
    with psycopg.connect(empty_db_dsn, autocommit=True) as conn:
        first = apply_migrations(conn, MIGRATIONS_DIR)
        assert first == [m.version for m in discover(MIGRATIONS_DIR)]
        assert apply_migrations(conn, MIGRATIONS_DIR) == []

        tables = {
            r[0]
            for r in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            ).fetchall()
        }
        assert tables == EXPECTED_TABLES

        rls = dict(
            conn.execute(
                "SELECT relname, relrowsecurity FROM pg_class "
                "WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'"
            ).fetchall()
        )
        assert rls["llm_ledger"] is False  # global budget needs cross-session totals
        assert all(rls[t] for t in EXPECTED_TABLES - {"llm_ledger", "schema_migrations"})


def test_edited_migration_is_rejected(empty_db_dsn: str, tmp_path: Path) -> None:
    # empty_db_dsn is module-scoped and already migrated by the test above or the fixture;
    # make sure it is, then tamper with a copy of an applied file.
    with psycopg.connect(empty_db_dsn, autocommit=True) as conn:
        apply_migrations(conn, MIGRATIONS_DIR)
        copy = tmp_path / "migrations"
        shutil.copytree(MIGRATIONS_DIR, copy)
        first = sorted(copy.glob("*.sql"))[0]
        first.write_text(first.read_text(encoding="utf-8") + "\n-- edited\n", encoding="utf-8")
        with pytest.raises(MigrationError, match="edited after being applied"):
            apply_migrations(conn, copy)


def test_role_passwords_come_from_env(owner: psycopg.Connection) -> None:
    env = {
        "DATABASE_READER_URL": "postgresql://docagent_reader:r%40nd@db:5432/docagent",
        "DATABASE_APP_URL": "postgresql://someone_else:x@db:5432/docagent",  # wrong role: ignored
    }
    assert set_role_passwords(owner, env) == ["docagent_reader"]
