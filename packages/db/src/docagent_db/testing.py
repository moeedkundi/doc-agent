"""Pytest fixtures for tests that need a real Postgres (import them into a conftest.py).

Uses TEST_DATABASE_URL (a superuser DSN; defaults to the docker-compose Postgres). Each test
module gets a fresh, throwaway database with all migrations applied. If Postgres isn't
reachable the tests are skipped, unless REQUIRE_DB_TESTS=1 (set in CI), which fails instead.
"""

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from docagent_db import apply_migrations

MIGRATIONS_DIR = Path(__file__).resolve().parents[4] / "db" / "migrations"
ADMIN_DSN = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://docagent:docagent@127.0.0.1:5433/docagent"
)


def _admin_connect() -> psycopg.Connection:
    try:
        return psycopg.connect(ADMIN_DSN, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError as exc:
        if os.environ.get("REQUIRE_DB_TESTS") == "1":
            raise
        pytest.skip(f"Postgres not reachable at TEST_DATABASE_URL ({exc.__class__.__name__})")


@pytest.fixture(scope="module")
def empty_db_dsn() -> Iterator[str]:
    """DSN of a brand-new empty database, dropped after the module."""
    name = f"docagent_test_{uuid.uuid4().hex[:12]}"
    with _admin_connect() as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield make_conninfo(ADMIN_DSN, dbname=name)
    finally:
        with _admin_connect() as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture(scope="module")
def migrated_dsn(empty_db_dsn: str) -> str:
    with psycopg.connect(empty_db_dsn, autocommit=True) as conn:
        apply_migrations(conn, MIGRATIONS_DIR)
    return empty_db_dsn


@pytest.fixture
def owner(migrated_dsn: str) -> Iterator[psycopg.Connection]:
    """Connection as the migration owner (bypasses RLS) for arranging test data."""
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        yield conn
