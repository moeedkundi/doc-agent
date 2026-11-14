"""Trace events for every node and tool call (SPEC §7.1, §9 SSE shape).

Each node runs inside `Tracer.node(...)`, which
- hands the node its `NodeTools` (allowlisted tools only),
- writes `node_start`, one `tool_end` per tool call (tool, model, tokens, latency, cost)
  and `node_end` with the node's totals, or `error` / `interrupt`,
- keeps the node's spend so the graph can add it to the run's total.

Sinks: `PgTraceSink` (trace_events / runs / review_actions as `docagent_app`, RLS-scoped)
and `MemoryTraceSink` (tests). Listeners get every event as it is written (the CLI prints
them live; the API will stream them over SSE).
"""

import asyncio
import json
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, Protocol, cast
from uuid import UUID

import psycopg
from langgraph.errors import GraphInterrupt
from psycopg import sql
from pydantic import BaseModel

from docagent_agent.tools import NodeTools, ToolCaller
from docagent_gateway import Gateway
from docagent_schemas import ModelTier

EventKind = Literal["node_start", "tool_end", "node_end", "error", "interrupt"]
RunStatus = Literal["queued", "running", "awaiting_review", "completed", "failed", "rejected"]
_MAX_JSON = 20_000


@dataclass(frozen=True)
class TraceEvent:
    run_id: UUID
    session_id: UUID
    node: str
    kind: EventKind
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))
    seq: int = 0
    tool: str | None = None
    model: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    latency_ms: int | None = None
    cost_usd: Decimal | None = None
    input: Any = None
    output: Any = None
    error: str | None = None

    def sse(self) -> dict[str, Any]:
        """The SPEC §9 event shape."""
        return {
            "run_id": str(self.run_id),
            "seq": self.seq,
            "node": self.node,
            "kind": self.kind,
            "tool": self.tool,
            "model": self.model,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "latency_ms": self.latency_ms,
            "cost_usd": None if self.cost_usd is None else float(self.cost_usd),
            "payload": self.output,
        }


@dataclass(frozen=True)
class ReviewAction:
    field_path: str
    old_value: Any
    new_value: Any
    action: Literal["approve", "edit"]


class TraceSink(Protocol):
    async def write(self, event: TraceEvent) -> TraceEvent: ...

    async def set_run(self, run_id: UUID, session_id: UUID, **fields: Any) -> None: ...

    async def review(self, run_id: UUID, session_id: UUID, actions: list[ReviewAction]) -> None: ...


Listener = Callable[[TraceEvent], None]


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    text = json.dumps(value, default=str)
    if len(text) > _MAX_JSON:
        return {"truncated": True, "preview": text[:2000]}
    return json.loads(text)


@dataclass
class MemoryTraceSink:
    events: list[TraceEvent] = field(default_factory=lambda: [])
    runs: dict[UUID, dict[str, Any]] = field(default_factory=lambda: {})
    reviews: list[ReviewAction] = field(default_factory=lambda: [])

    async def write(self, event: TraceEvent) -> TraceEvent:
        seq = 1 + sum(1 for e in self.events if e.run_id == event.run_id)
        stored = replace(event, seq=seq)
        self.events.append(stored)
        return stored

    async def set_run(self, run_id: UUID, session_id: UUID, **fields: Any) -> None:
        self.runs.setdefault(run_id, {}).update(fields)

    async def review(self, run_id: UUID, session_id: UUID, actions: list[ReviewAction]) -> None:
        self.reviews.extend(actions)


class PgTraceSink:
    """Writes as `docagent_app` with `app.session_id` set, so RLS scopes every row."""

    _RUN_FIELDS = frozenset({"status", "total_cost_usd", "escalated", "finished_at"})

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _tx(self, session_id: UUID) -> psycopg.Connection:
        conn = psycopg.connect(self._dsn, autocommit=True, connect_timeout=5)
        return conn

    async def write(self, event: TraceEvent) -> TraceEvent:
        return await asyncio.to_thread(self._write, event)

    def _write(self, e: TraceEvent) -> TraceEvent:
        with self._tx(e.session_id) as conn, conn.transaction():
            conn.execute("SELECT set_config('app.session_id', %s, true)", (str(e.session_id),))
            # One writer per run (nodes run sequentially), so max+1 is safe.
            row = conn.execute(
                "INSERT INTO trace_events (session_id, run_id, seq, ts, node, kind, tool, model, "
                "input_json, output_json, tokens_in, tokens_out, latency_ms, cost_usd, error) "
                "SELECT %s, %s, coalesce(max(seq), 0) + 1, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "%s, %s, %s FROM trace_events WHERE run_id = %s RETURNING seq",
                (
                    e.session_id, e.run_id, e.ts, e.node, e.kind, e.tool, e.model,
                    json.dumps(_jsonable(e.input)), json.dumps(_jsonable(e.output)),
                    e.tokens_in, e.tokens_out, e.latency_ms, e.cost_usd, e.error, e.run_id,
                ),
            ).fetchone()  # fmt: skip
        return replace(e, seq=int(row[0]) if row else 0)

    async def set_run(self, run_id: UUID, session_id: UUID, **fields: Any) -> None:
        unknown = set(fields) - self._RUN_FIELDS
        if unknown:
            raise ValueError(f"unknown run fields: {sorted(unknown)}")
        await asyncio.to_thread(self._set_run, run_id, session_id, fields)

    def _set_run(self, run_id: UUID, session_id: UUID, fields: dict[str, Any]) -> None:
        cols = sorted(fields)
        assignments = sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(c)) for c in cols)
        query = sql.SQL("UPDATE runs SET {} WHERE id = %s").format(assignments)
        with self._tx(session_id) as conn, conn.transaction():
            conn.execute("SELECT set_config('app.session_id', %s, true)", (str(session_id),))
            conn.execute(query, [fields[c] for c in cols] + [run_id])

    async def review(self, run_id: UUID, session_id: UUID, actions: list[ReviewAction]) -> None:
        await asyncio.to_thread(self._review, run_id, session_id, actions)

    def _review(self, run_id: UUID, session_id: UUID, actions: list[ReviewAction]) -> None:
        with self._tx(session_id) as conn, conn.transaction():
            conn.execute("SELECT set_config('app.session_id', %s, true)", (str(session_id),))
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO review_actions (session_id, run_id, field_path, old_value, "
                    "new_value, action) VALUES (%s, %s, %s, %s, %s, %s)",
                    [
                        (session_id, run_id, a.field_path, json.dumps(a.old_value, default=str),
                         json.dumps(a.new_value, default=str), a.action)
                        for a in actions
                    ],
                )  # fmt: skip


@dataclass
class NodeContext:
    """What a node gets: its tools, the LLM (via the gateway), and what those calls cost."""

    tools: NodeTools
    tracer: "Tracer"
    run_id: UUID
    session_id: UUID
    cost: Decimal = Decimal(0)
    tokens_in: int = 0
    tokens_out: int = 0
    models: list[str] = field(default_factory=lambda: [])
    called: list[str] = field(default_factory=lambda: [])

    async def call(self, tool: str, **args: Any) -> Any:
        started = time.perf_counter()
        out: Any = await self.tools.call(tool, **args)  # raises ToolNotAllowed / ToolCallError
        latency = int((time.perf_counter() - started) * 1000)
        raw: Any = cast(dict[str, Any], out).get("usage") if isinstance(out, dict) else None
        usage = cast(dict[str, Any], raw) if isinstance(raw, dict) else {}
        cost = Decimal(str(usage["cost_usd"])) if "cost_usd" in usage else None
        self.called.append(tool)
        if cost is not None:
            self.cost += cost
            self.tokens_in += int(usage.get("tokens_in", 0))
            self.tokens_out += int(usage.get("tokens_out", 0))
            self.models.append(str(usage.get("model")))
        await self.tracer.emit(
            TraceEvent(
                run_id=self.run_id,
                session_id=self.session_id,
                node=self.tools.node,
                kind="tool_end",
                tool=tool,
                model=usage.get("model"),
                tokens_in=usage.get("tokens_in"),
                tokens_out=usage.get("tokens_out"),
                latency_ms=latency,
                cost_usd=cost,
                input=args,
                output=out,
            )
        )
        return cast(Any, out)

    async def llm[T: BaseModel](
        self,
        purpose: str,
        messages: list[dict[str, Any]],
        schema: type[T],
        tier: ModelTier = "default",
    ) -> T:
        """A structured LLM call through the gateway (budget, ledger), traced like a tool."""
        gateway = self.tracer.gateway
        if gateway is None:
            raise RuntimeError("this tracer has no gateway configured")
        out = await gateway.complete(
            messages, tier=tier, response_schema=schema, run_id=self.run_id,
            session_id=self.session_id,
        )  # fmt: skip
        self.called.append(f"llm:{purpose}")
        self.cost += out.cost_usd
        self.tokens_in += out.tokens_in
        self.tokens_out += out.tokens_out
        self.models.append(out.model)
        await self.tracer.emit(
            TraceEvent(
                run_id=self.run_id, session_id=self.session_id, node=self.tools.node,
                kind="tool_end", tool=f"llm:{purpose}", model=out.model,
                tokens_in=out.tokens_in, tokens_out=out.tokens_out, latency_ms=out.latency_ms,
                cost_usd=out.cost_usd, output=out.parsed.model_dump(mode="json")
                if out.parsed is not None else None,
            )
        )  # fmt: skip
        if out.parsed is None:
            raise RuntimeError("gateway returned no parsed output")
        return out.parsed


class Tracer:
    def __init__(self, sink: TraceSink, caller: ToolCaller, gateway: Gateway | None = None) -> None:
        self.sink = sink
        self._caller = caller
        self.gateway = gateway
        self._listeners: list[Listener] = []

    def listen(self, listener: Listener) -> None:
        self._listeners.append(listener)

    async def emit(self, event: TraceEvent) -> TraceEvent:
        stored = await self.sink.write(event)
        for listener in self._listeners:
            listener(stored)
        return stored

    @asynccontextmanager
    async def node(self, name: str, run_id: UUID, session_id: UUID) -> AsyncGenerator[NodeContext]:
        ctx = NodeContext(NodeTools(name, self._caller), self, run_id, session_id)
        await self.emit(
            TraceEvent(run_id=run_id, session_id=session_id, node=name, kind="node_start")
        )
        started = time.perf_counter()
        try:
            yield ctx
        except GraphInterrupt:
            await self.emit(TraceEvent(
                run_id=run_id, session_id=session_id, node=name, kind="interrupt",
                latency_ms=int((time.perf_counter() - started) * 1000),
                output={"status": "awaiting_review"},
            ))  # fmt: skip
            raise
        except Exception as exc:
            await self.emit(TraceEvent(
                run_id=run_id, session_id=session_id, node=name, kind="error",
                tool=",".join(ctx.called) or None,
                latency_ms=int((time.perf_counter() - started) * 1000),
                cost_usd=ctx.cost or None, error=f"{type(exc).__name__}: {exc}",
            ))  # fmt: skip
            raise
        await self.emit(TraceEvent(
            run_id=run_id, session_id=session_id, node=name, kind="node_end",
            tool=",".join(ctx.called) or None,
            model=",".join(dict.fromkeys(ctx.models)) or None,
            tokens_in=ctx.tokens_in or None, tokens_out=ctx.tokens_out or None,
            latency_ms=int((time.perf_counter() - started) * 1000),
            cost_usd=ctx.cost,
        ))  # fmt: skip
