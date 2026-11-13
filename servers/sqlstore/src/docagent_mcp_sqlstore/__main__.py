"""`python -m docagent_mcp_sqlstore`: serve on the port in MCP_SQLSTORE_URL.

Needs DATABASE_READER_URL (docagent_reader) and DATABASE_APP_URL (docagent_app). Never the
owner DSN: the reader/app roles are what make RLS and read-only grants apply.
"""

import os
import sys
from functools import partial

import psycopg

from docagent_mcp_common import load_limits, serve
from docagent_mcp_sqlstore.server import create_server
from docagent_mcp_sqlstore.store import SqlSettings, SqlStore


def main() -> None:
    reader_dsn = os.environ.get("DATABASE_READER_URL")
    app_dsn = os.environ.get("DATABASE_APP_URL")
    if not reader_dsn or not app_dsn:
        print("sqlstore: DATABASE_READER_URL and DATABASE_APP_URL must be set", file=sys.stderr)
        raise SystemExit(2)
    sql_limits = load_limits()["sql"]
    store = SqlStore(
        reader=partial(psycopg.connect, reader_dsn, connect_timeout=5),
        app=partial(psycopg.connect, app_dsn, connect_timeout=5),
        settings=SqlSettings(
            max_rows=sql_limits["max_rows"], statement_timeout_ms=sql_limits["statement_timeout_ms"]
        ),
    )
    serve(create_server(store), "MCP_SQLSTORE_URL")


if __name__ == "__main__":
    main()
