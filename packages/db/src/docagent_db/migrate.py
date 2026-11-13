"""Plain-SQL migration runner.

Applies `db/migrations/NNN_*.sql` in order, each in its own transaction, recording a
checksum in `schema_migrations`. An already-applied file whose content changed is an error:
write a new migration instead of editing an old one.

After migrating, sets the passwords of the `docagent_app` / `docagent_reader` roles from
`DATABASE_APP_URL` / `DATABASE_READER_URL` when those are set, so no secret lives in SQL.

    uv run python -m docagent_db                    # uses DATABASE_URL
    uv run python -m docagent_db --dsn ... --dir db/migrations
"""

import argparse
import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

import psycopg
from psycopg import sql

DEFAULT_DIR = Path("db/migrations")
_ROLE_URL_ENV = {"docagent_app": "DATABASE_APP_URL", "docagent_reader": "DATABASE_READER_URL"}


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: str
    path: Path
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def discover(migrations_dir: Path) -> list[Migration]:
    files = sorted(migrations_dir.glob("*.sql"))
    if not files:
        raise MigrationError(f"no .sql files in {migrations_dir}")
    migrations = [
        Migration(version=p.stem, path=p, sql=p.read_text(encoding="utf-8")) for p in files
    ]
    prefixes = [m.version.split("_", 1)[0] for m in migrations]
    if len(set(prefixes)) != len(prefixes):
        raise MigrationError(f"duplicate migration numbers in {migrations_dir}")
    return migrations


def apply_migrations(conn: psycopg.Connection, migrations_dir: Path = DEFAULT_DIR) -> list[str]:
    """Apply pending migrations; return the versions applied (empty if up to date)."""
    migrations = discover(migrations_dir)
    with conn.transaction():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version     text PRIMARY KEY,
                checksum    text NOT NULL,
                applied_at  timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        rows = conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
    applied = {version: checksum for version, checksum in rows}

    done: list[str] = []
    for m in migrations:
        if m.version in applied:
            if applied[m.version] != m.checksum:
                raise MigrationError(
                    f"{m.path.name} was edited after being applied; add a new migration"
                )
            continue
        with conn.transaction():
            conn.execute(m.sql.encode("utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                (m.version, m.checksum),
            )
        done.append(m.version)
    return done


def set_role_passwords(conn: psycopg.Connection, env: dict[str, str] | None = None) -> list[str]:
    """Set role passwords from the role-specific DSNs in `env`; return roles updated."""
    env = dict(os.environ) if env is None else env
    updated: list[str] = []
    for role, var in _ROLE_URL_ENV.items():
        dsn = env.get(var)
        if not dsn:
            continue
        parts = urlsplit(dsn)
        if parts.username != role or not parts.password:
            continue
        with conn.transaction():
            conn.execute(
                sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(unquote(parts.password))
                )
            )
        updated.append(role)
    return updated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    args = parser.parse_args(argv)
    if not args.dsn:
        parser.error("set DATABASE_URL or pass --dsn")
    with psycopg.connect(args.dsn, autocommit=True) as conn:
        applied = apply_migrations(conn, args.dir)
        roles = set_role_passwords(conn)
    print(f"applied: {', '.join(applied) or 'nothing (up to date)'}")
    if roles:
        print(f"passwords set for: {', '.join(roles)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
