"""Running graphs for API requests: background tasks, results, errors and replays.

A run's progress lives in Postgres (runs + trace_events), not in this process, so the SSE
endpoint can serve any client (or a reconnecting one) by reading from the database.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from langgraph.types import Command

from docagent_agent.ingest import ReviewDecision
from docagent_agent.runtime import Runtime
from docagent_agent.tools import ToolCallError
from docagent_agent.trace import TraceEvent, TraceSink
from docagent_api.db import Db
from docagent_gateway import BudgetExceeded

log = logging.getLogger("docagent.api")
Json = dict[str, Any]
TERMINAL = frozenset({"completed", "failed", "rejected"})


def error_code(exc: BaseException) -> str:
    """The most specific code in an exception chain (tool error codes win)."""
    seen: BaseException | None = exc
    while seen is not None:
        if isinstance(seen, BudgetExceeded):
            return "budget_exceeded"
        if isinstance(seen, ToolCallError):
            return seen.code
        seen = seen.__cause__ or seen.__context__
    return "internal_error"


@dataclass
class ReplayFixtures:
    """Recorded runs of the public samples (scripts/record_replays.py)."""

    by_sample: dict[str, Json]

    @classmethod
    def load(cls, directory: Path) -> "ReplayFixtures":
        fixtures: dict[str, Json] = {}
        if directory.is_dir():
            for path in sorted(directory.glob("*.json")):
                data: Json = json.loads(path.read_text(encoding="utf-8"))
                fixtures[str(data["sample_id"])] = data
        return cls(fixtures)


@dataclass
class RunService:
    db: Db
    sink: TraceSink
    runtime: Runtime | None
    """None in REPLAY_ONLY mode: no MCP servers, no gateway, no LLM."""
    fixtures: ReplayFixtures
    replay_speed: float = 1.0
    _tasks: set[asyncio.Task[None]] = field(default_factory=lambda: set())

    def _spawn(self, coro: Any) -> None:
        task: asyncio.Task[None] = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        """Wait for background runs (tests, shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def _require_runtime(self) -> Runtime:
        if self.runtime is None:
            raise RuntimeError("live runs are disabled (REPLAY_ONLY)")
        return self.runtime

    # -- ingest -------------------------------------------------------------------------------
    def start_ingest(self, sid: UUID, run_id: UUID, document_id: UUID) -> None:
        inp: Json = {
            "run_id": str(run_id),
            "session_id": str(sid),
            "document_id": str(document_id),
            "cost_usd": "0",
        }
        self._spawn(self._drive(sid, run_id, inp))

    def resume(self, sid: UUID, run_id: UUID, decision: ReviewDecision) -> None:
        self._spawn(self._drive(sid, run_id, Command(resume=decision.model_dump())))

    async def _drive(self, sid: UUID, run_id: UUID, inp: Any) -> None:
        rt = self._require_runtime()
        config: Json = {"configurable": {"thread_id": str(run_id)}}
        try:
            await rt.graph.ainvoke(inp, config, durability="sync")  # pyright: ignore[reportArgumentType, reportUnknownMemberType]
            # The graph records its own status and result (summarize / human_review).
        except Exception as exc:
            await self._fail(sid, run_id, exc)

    async def _fail(self, sid: UUID, run_id: UUID, exc: BaseException) -> None:
        code = error_code(exc)
        log.warning("run %s failed: %s (%s)", run_id, code, type(exc).__name__)
        await self.sink.set_run(
            run_id,
            sid,
            status="failed",
            error_code=code,
            error=str(exc)[:500],
            finished_at=datetime.now(UTC),
        )

    async def review_payload(self, run_id: UUID) -> Json | None:
        if self.runtime is None:
            return None
        config: Json = {"configurable": {"thread_id": str(run_id)}}
        snapshot = await self.runtime.graph.aget_state(config)  # pyright: ignore[reportArgumentType, reportUnknownMemberType]
        for interrupt in snapshot.interrupts:
            return dict(interrupt.value)
        return None

    # -- ask ----------------------------------------------------------------------------------
    def start_ask(self, sid: UUID, run_id: UUID, question: str) -> None:
        self._spawn(self._ask(sid, run_id, question))

    async def _ask(self, sid: UUID, run_id: UUID, question: str) -> None:
        rt = self._require_runtime()
        config: Json = {"configurable": {"thread_id": f"ask:{sid}"}}
        try:
            await rt.ask.ainvoke(  # pyright: ignore[reportUnknownMemberType]
                {"run_id": str(run_id), "session_id": str(sid), "question": question},
                config,  # pyright: ignore[reportArgumentType]
                durability="sync",
            )
        except Exception as exc:
            await self._fail(sid, run_id, exc)

    # -- replay -------------------------------------------------------------------------------
    def start_replay(self, sid: UUID, run_id: UUID, fixture: Json) -> None:
        self._spawn(self._replay(sid, run_id, fixture))

    async def _replay(self, sid: UUID, run_id: UUID, fixture: Json) -> None:
        """Re-emit a recorded run's trace events with their original spacing. No LLM."""
        try:
            await self.sink.set_run(run_id, sid, status="running")
            previous = 0
            for event in fixture["events"]:
                delay = (int(event["offset_ms"]) - previous) / 1000 / max(self.replay_speed, 1e-6)
                previous = int(event["offset_ms"])
                if delay > 0:
                    await asyncio.sleep(delay)
                await self.sink.write(
                    TraceEvent(
                        run_id=run_id,
                        session_id=sid,
                        node=event["node"],
                        kind=event["kind"],
                        tool=event.get("tool"),
                        model=event.get("model"),
                        tokens_in=event.get("tokens_in"),
                        tokens_out=event.get("tokens_out"),
                        latency_ms=event.get("latency_ms"),
                        cost_usd=Decimal(event["cost_usd"]) if event.get("cost_usd") else None,
                        output=event.get("output"),
                        error=event.get("error"),
                    )
                )
            final: Json = fixture["result"]
            await self.sink.set_run(
                run_id,
                sid,
                status="completed",
                result_json=final,
                total_cost_usd=Decimal(str(final.get("cost_usd") or "0")),
                escalated=bool(final.get("escalated")),
                finished_at=datetime.now(UTC),
            )
        except Exception as exc:
            await self._fail(sid, run_id, exc)
