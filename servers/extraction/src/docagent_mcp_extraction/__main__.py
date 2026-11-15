"""`python -m docagent_mcp_extraction`: serve on the port in MCP_EXTRACTION_URL.

Needs GEMINI_API_KEY, DAILY_BUDGET_USD, SESSION_BUDGET_USD, UPLOAD_DIR and a ledger DSN
(DATABASE_APP_URL, falling back to DATABASE_URL).
"""

import os
import sys
from pathlib import Path

from docagent_gateway import Gateway, PostgresLedger, load_config
from docagent_mcp_common import load_limits, serve
from docagent_mcp_extraction.server import ExtractionSettings, create_server
from docagent_mcp_extraction.store import DocumentStore


def main() -> None:
    dsn = os.environ.get("DATABASE_APP_URL") or os.environ.get("DATABASE_URL")
    upload_dir = os.environ.get("UPLOAD_DIR")
    if not dsn or not upload_dir:
        print(
            "extraction: DATABASE_APP_URL (or DATABASE_URL) and UPLOAD_DIR must be set",
            file=sys.stderr,
        )
        raise SystemExit(2)
    try:
        gateway = Gateway(load_config(), PostgresLedger(dsn))
    except LookupError as exc:  # missing budget variables: never run uncapped
        print(f"extraction: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    limits = load_limits()
    settings = ExtractionSettings(
        render_dpi=limits["extraction"]["render_dpi"],
        max_pages=limits["uploads"]["max_pages"],
        reextract_deadline_s=limits["extraction"].get("reextract_deadline_s"),
    )
    serve(create_server(gateway, DocumentStore(Path(upload_dir)), settings), "MCP_EXTRACTION_URL")


if __name__ == "__main__":
    main()
