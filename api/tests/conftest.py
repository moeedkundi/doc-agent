"""The whole stack in one process for API tests.

Real: FastAPI app, ingest/ask graphs, all four MCP servers (in-process MCP clients),
sqlstore's SQL gate + RLS on a migrated throwaway Postgres, validation, reference data,
both gateways (budget checks, ledgers). Scripted: only the model's responses.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import httpx
import psycopg
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from psycopg import sql

from docagent_agent.runtime import AgentConfig, build_runtime
from docagent_agent.tools import McpHub
from docagent_agent.trace import PgTraceSink
from docagent_api.app import Services, create_app
from docagent_api.db import Db
from docagent_api.guards import RateLimiter, parse_rate
from docagent_api.runs import ReplayFixtures, RunService
from docagent_api.settings import ROOT, ApiSettings
from docagent_db.testing import empty_db_dsn, migrated_dsn, owner
from docagent_gateway import Gateway, MemoryLedger
from docagent_gateway.testing import NOW, FakeProvider, make_config, response
from docagent_mcp_extraction.server import create_server as extraction_server
from docagent_mcp_extraction.store import DocumentStore
from docagent_mcp_reference.server import create_server as reference_server
from docagent_mcp_sqlstore.server import create_server as sqlstore_server
from docagent_mcp_sqlstore.store import SqlStore
from docagent_mcp_validation.server import create_server as validation_server
from docagent_mcp_validation.settings import load_settings

__all__ = ["empty_db_dsn", "migrated_dsn", "owner"]

SAMPLES = ROOT / "evals" / "dataset" / "samples"


def as_role(dsn: str, role: str) -> Callable[[], psycopg.Connection]:
    def connect() -> psycopg.Connection:
        conn = psycopg.connect(dsn, autocommit=True)
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
        return conn

    return connect


def wrap(value: Any, conf: float = 0.97, low: frozenset[str] = frozenset(), path: str = "") -> Any:
    """Ground-truth record JSON -> extraction JSON; paths in `low` get confidence 0.5."""
    if isinstance(value, dict):
        d = cast(dict[str, Any], value)
        return {k: wrap(v, conf, low, f"{path}.{k}" if path else k) for k, v in d.items()}
    if isinstance(value, list):
        items = cast(list[Any], value)
        return [wrap(v, conf, low, f"{path}[{i}]") for i, v in enumerate(items)]
    if isinstance(value, str) and value.replace(".", "", 1).isdigit():
        value = float(Decimal(value))
    return {
        "value": value,
        "confidence": 0.5 if path in low else conf,
        "source_page": 1,
        "evidence": None,
    }


def truth(doc_id: str) -> dict[str, Any]:
    return json.loads((SAMPLES / f"{doc_id}.json").read_text(encoding="utf-8"))["record"]


def classify(doc_type: str) -> Any:
    return response(
        json.dumps({"doc_type": doc_type, "confidence": 0.96, "reason": "test"}), 900, 40
    )


@dataclass
class Stack:
    client: httpx.AsyncClient
    app: Any
    services: Services
    extraction_model: FakeProvider
    agent_model: FakeProvider
    extraction_ledger: MemoryLedger
    settings: ApiSettings
    dsn: str


def make_settings(upload_dir: Path, fixtures_dir: Path, **overrides: Any) -> ApiSettings:
    base = ApiSettings(
        app_dsn="",
        checkpoint_dsn="",
        upload_dir=upload_dir,
        allowed_origins=[],
        replay_only=False,
        replay_speed=200.0,
        cookie_secure=False,
        cookie_domain=None,
        ip_hash_salt="test",
        trust_proxy=False,
        daily_budget_usd=Decimal("1.00"),
        max_upload_bytes=5 * 1024 * 1024,
        max_pages=5,
        run_limit="5/10minutes",
        question_limit="20/10minutes",
        session_ttl_hours=24,
        samples_dir=SAMPLES,
        fixtures_dir=fixtures_dir,
        evals_report=upload_dir / "missing.json",
        mcp_urls={},
        mcp_secret="",
    )
    return replace(base, **overrides)


@pytest.fixture
async def stack_factory(migrated_dsn: str, tmp_path: Path) -> AsyncIterator[Callable[..., Any]]:
    holders: list[tuple[asyncio.Event, asyncio.Task[None]]] = []
    clients: list[httpx.AsyncClient] = []

    async def hold(hub: McpHub, ready: asyncio.Event, stop: asyncio.Event) -> None:
        # anyio needs the hub entered and exited in the same task.
        async with hub:
            ready.set()
            await stop.wait()

    async def build(
        replay_only: bool = False,
        budget: str = "1.00",
        fixtures: dict[str, Any] | None = None,
        run_limit: str = "5/10minutes",
        **overrides: Any,
    ) -> Stack:
        uploads = tmp_path / "uploads"
        fixtures_dir = tmp_path / "fixtures"
        fixtures_dir.mkdir(exist_ok=True)
        for sample_id, fx in (fixtures or {}).items():
            (fixtures_dir / f"{sample_id}.json").write_text(json.dumps(fx), encoding="utf-8")
        settings = make_settings(
            uploads, fixtures_dir, replay_only=replay_only, run_limit=run_limit, **overrides
        )
        app_conn = as_role(migrated_dsn, "docagent_app")
        db = Db(app_conn)
        ext_model, agent_model = FakeProvider(), FakeProvider()
        for p in (ext_model, agent_model):
            p.scripts = {"gemini/flash-model": [], "gemini/strong-model": []}
        cfg = make_config(daily=budget, session=budget)
        runtime, gateway = None, None
        ext_ledger = MemoryLedger()
        if not replay_only:
            ext_gateway = Gateway(cfg, ext_ledger, completion_fn=ext_model, now=lambda: NOW)
            hub = McpHub.in_process(
                {
                    "extraction": extraction_server(ext_gateway, DocumentStore(uploads)),
                    "validation": validation_server(load_settings(ROOT / "config")),
                    "sqlstore": sqlstore_server(
                        SqlStore(as_role(migrated_dsn, "docagent_reader"), app_conn)
                    ),
                    "reference": reference_server(),
                }
            )
            ready, stop = asyncio.Event(), asyncio.Event()
            holders.append((stop, asyncio.create_task(hold(hub, ready, stop))))
            await ready.wait()
            gateway = Gateway(cfg, MemoryLedger(), completion_fn=agent_model, now=lambda: NOW)
            agent_cfg = AgentConfig(
                mcp_urls={},
                mcp_secret="",
                app_dsn="",
                checkpoint_dsn="",
                upload_dir=uploads,
                max_upload_bytes=5 * 1024 * 1024,
                max_reextract_attempts=2,
            )
            runtime = build_runtime(agent_cfg, hub, InMemorySaver(), gateway, app_conn)

        async def probe() -> dict[str, str]:
            return {s: "ok" for s in ("extraction", "validation", "sqlstore", "reference")}

        services = Services(
            db=db,
            runs=RunService(
                db,
                PgTraceSink(app_conn),
                runtime,
                ReplayFixtures.load(fixtures_dir),
                settings.replay_speed,
            ),
            gateway=gateway,
            limiter=RateLimiter(
                {
                    "runs": parse_rate(settings.run_limit),
                    "questions": parse_rate(settings.question_limit),
                }
            ),
            mcp_probe=probe,
        )
        app = create_app(settings, services)
        app.state.services = services  # ASGITransport doesn't run lifespan
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver", timeout=60
        )
        clients.append(client)
        return Stack(
            client, app, services, ext_model, agent_model, ext_ledger, settings, migrated_dsn
        )

    yield build
    for c in clients:
        await c.aclose()
    for stop, task in holders:
        stop.set()
        await task
