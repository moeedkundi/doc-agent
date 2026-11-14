"""Wiring for running the ingest graph against the real services.

- MCP servers at MCP_{EXTRACTION,VALIDATION,SQLSTORE,REFERENCE}_URL with MCP_SHARED_SECRET
- trace/run/session/document rows as `docagent_app` (DATABASE_APP_URL)
- LangGraph checkpoints in Postgres (DATABASE_URL; the checkpointer creates its own tables)
"""

# langgraph ships partial type information.
# pyright: reportMissingTypeStubs=false

import asyncio
import os
import shutil
from collections.abc import AsyncGenerator, Coroutine, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import yaml
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph.state import CompiledStateGraph

from docagent_agent.ask import AskState, build_ask_graph
from docagent_agent.ingest import IngestSettings, IngestState, build_ingest_graph
from docagent_agent.tools import McpHub, Server
from docagent_agent.trace import PgTraceSink, Tracer
from docagent_gateway import Gateway, PostgresLedger, load_config

_MAGIC = ((b"%PDF-", ".pdf"), (b"\x89PNG\r\n\x1a\n", ".png"), (b"\xff\xd8\xff", ".jpg"))


@dataclass(frozen=True)
class AgentConfig:
    mcp_urls: dict[Server, str]
    mcp_secret: str
    app_dsn: str
    checkpoint_dsn: str
    upload_dir: Path
    max_upload_bytes: int
    max_reextract_attempts: int

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AgentConfig":
        env = os.environ if env is None else env
        missing = [k for k in ("MCP_SHARED_SECRET", "DATABASE_APP_URL", "DATABASE_URL",
                               "UPLOAD_DIR") if not env.get(k)]  # fmt: skip
        urls: dict[Server, str] = {}
        for server in ("extraction", "validation", "sqlstore", "reference"):
            url = env.get(f"MCP_{server.upper()}_URL")
            if not url:
                missing.append(f"MCP_{server.upper()}_URL")
            else:
                urls[server] = url
        if missing:
            raise LookupError(f"missing environment variables: {', '.join(missing)}")
        config_dir = Path(env.get("DOCAGENT_CONFIG_DIR", "config"))
        limits: dict[str, Any] = yaml.safe_load((config_dir / "limits.yaml").read_text())
        return cls(
            mcp_urls=urls,
            mcp_secret=env["MCP_SHARED_SECRET"],
            app_dsn=env["DATABASE_APP_URL"],
            checkpoint_dsn=env["DATABASE_URL"],
            upload_dir=Path(env["UPLOAD_DIR"]),
            max_upload_bytes=int(limits["uploads"]["max_bytes"]),
            max_reextract_attempts=int(limits["extraction"]["max_reextract_attempts"]),
        )


# -- intake: session, document and run rows (what the API will do in Phase 5) -------------


def _scoped(dsn: str, session_id: UUID) -> psycopg.Connection:
    conn = psycopg.connect(dsn, autocommit=True, connect_timeout=5)
    return conn


def create_session(cfg: AgentConfig) -> UUID:
    sid = uuid4()
    with _scoped(cfg.app_dsn, sid) as conn, conn.transaction():
        conn.execute("SELECT set_config('app.session_id', %s, true)", (str(sid),))
        conn.execute("INSERT INTO sessions (id) VALUES (%s)", (sid,))
    return sid


def register_document(cfg: AgentConfig, session_id: UUID, path: Path) -> UUID:
    data = path.read_bytes()
    if len(data) > cfg.max_upload_bytes:
        raise ValueError(f"{path.name} is {len(data)} bytes; the limit is {cfg.max_upload_bytes}")
    ext = next((e for magic, e in _MAGIC if data.startswith(magic)), None)
    if ext is None:
        raise ValueError(f"{path.name} is not a PDF, PNG or JPEG")
    doc_id = uuid4()
    cfg.upload_dir.mkdir(parents=True, exist_ok=True)
    stored = cfg.upload_dir / f"{doc_id}{ext}"
    shutil.copyfile(path, stored)
    with _scoped(cfg.app_dsn, session_id) as conn, conn.transaction():
        conn.execute("SELECT set_config('app.session_id', %s, true)", (str(session_id),))
        conn.execute(
            "INSERT INTO documents (id, session_id, filename, storage_path) "
            "VALUES (%s, %s, %s, %s)",
            (doc_id, session_id, path.name, stored.name),
        )
    return doc_id


def create_run(cfg: AgentConfig, session_id: UUID, document_id: UUID) -> UUID:
    run_id = uuid4()
    with _scoped(cfg.app_dsn, session_id) as conn, conn.transaction():
        conn.execute("SELECT set_config('app.session_id', %s, true)", (str(session_id),))
        conn.execute(
            "INSERT INTO runs (id, session_id, document_id, status) VALUES (%s, %s, %s, 'queued')",
            (run_id, session_id, document_id),
        )
    return run_id


def create_ask_run(cfg: AgentConfig, session_id: UUID, question: str) -> UUID:
    run_id = uuid4()
    with _scoped(cfg.app_dsn, session_id) as conn, conn.transaction():
        conn.execute("SELECT set_config('app.session_id', %s, true)", (str(session_id),))
        conn.execute(
            "INSERT INTO runs (id, session_id, kind, question, status) "
            "VALUES (%s, %s, 'ask', %s, 'queued')",
            (run_id, session_id, question),
        )
    return run_id


def session_exists(cfg: AgentConfig, session_id: UUID) -> bool:
    with _scoped(cfg.app_dsn, session_id) as conn, conn.transaction():
        conn.execute("SELECT set_config('app.session_id', %s, true)", (str(session_id),))
        return (
            conn.execute("SELECT 1 FROM sessions WHERE id = %s", (session_id,)).fetchone()
            is not None
        )


# -- graph runtime ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Runtime:
    graph: CompiledStateGraph[IngestState, None, IngestState, IngestState]
    ask: CompiledStateGraph[AskState, None, AskState, AskState]
    tracer: Tracer
    config: AgentConfig


@asynccontextmanager
async def open_runtime(cfg: AgentConfig) -> AsyncGenerator[Runtime]:
    async with (
        AsyncPostgresSaver.from_conn_string(cfg.checkpoint_dsn) as checkpointer,
        McpHub(cfg.mcp_urls, cfg.mcp_secret) as hub,
    ):
        await checkpointer.setup()
        # All LLM calls go through the gateway (budget caps + ledger), never a provider SDK.
        gateway = Gateway(load_config(), PostgresLedger(cfg.app_dsn))
        tracer = Tracer(PgTraceSink(cfg.app_dsn), hub, gateway)
        graph = build_ingest_graph(tracer, checkpointer, IngestSettings(cfg.max_reextract_attempts))
        yield Runtime(graph, build_ask_graph(tracer, checkpointer), tracer, cfg)


def run_async(coro: Coroutine[Any, Any, int]) -> int:
    """psycopg's async driver needs a selector event loop on Windows."""
    if os.name == "nt":
        return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)
    return asyncio.run(coro)
