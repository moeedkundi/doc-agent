"""SSE stream of a run's trace events (SPEC §9 shape), read from the database.

- Events carry `id: <seq>`. A client that reconnects with `Last-Event-ID` (EventSource does
  this automatically) or `?after=<seq>` gets only what it missed.
- When the run pauses for review, a `review_required` event is sent once and the stream
  stays open (keep-alive pings) and continues after the resume.
- When the run finishes, an `end` event carries the final status and result, then the
  stream closes.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from sse_starlette import ServerSentEvent

from docagent_api.db import Db, Row
from docagent_api.runs import TERMINAL

POLL_SECONDS = 0.3
RETRY_MS = 2000


REDACTED_ARGS = frozenset({"session_id", "run_id"})


def redact_args(args: Any) -> Any:
    """Tool args as shown to the browser: internal ids (session, run) are left out."""
    if isinstance(args, dict):
        return {k: v for k, v in cast(dict[str, Any], args).items() if k not in REDACTED_ARGS}
    return args


def _event_json(run_id: UUID, row: Row) -> str:
    cost = row["cost_usd"]
    return json.dumps(
        {
            "run_id": str(run_id),
            "seq": row["seq"],
            "ts": row["ts"].isoformat(),
            "node": row["node"],
            "kind": row["kind"],
            "tool": row["tool"],
            "model": row["model"],
            "tokens_in": row["tokens_in"],
            "tokens_out": row["tokens_out"],
            "latency_ms": row["latency_ms"],
            "cost_usd": float(cost) if isinstance(cost, Decimal) else cost,
            "error": row["error"],
            "args": redact_args(row.get("input_json")),
            "payload": row["output_json"],
        },
        default=str,
    )


async def stream_run(
    db: Db,
    sid: UUID,
    run_id: UUID,
    after: int,
    review_payload: Callable[[UUID], Awaitable[dict[str, Any] | None]],
    poll_seconds: float = POLL_SECONDS,
) -> AsyncIterator[ServerSentEvent]:
    last = after
    status: str | None = None
    announced_review = False
    while True:
        rows = await asyncio.to_thread(db.events_after, sid, run_id, last)
        for row in rows:
            last = int(row["seq"])
            yield ServerSentEvent(
                data=_event_json(run_id, row), event=row["kind"], id=str(last), retry=RETRY_MS
            )
        run = await asyncio.to_thread(db.get_run, sid, run_id)
        if run is None:
            yield ServerSentEvent(data=json.dumps({"error": "run_not_found"}), event="error")
            return
        if run["status"] != status:
            status = str(run["status"])
            yield ServerSentEvent(data=json.dumps({"status": status}), event="status")
        if status == "awaiting_review" and not announced_review:
            # The status is set just before the graph's interrupt is checkpointed: until the
            # payload is readable, keep polling rather than announce an empty review.
            payload = await review_payload(run_id)
            if payload is not None:
                yield ServerSentEvent(
                    data=json.dumps(payload, default=str), event="review_required"
                )
                announced_review = True
        elif status != "awaiting_review":
            announced_review = False
        if status in TERMINAL:
            # Last read: anything written between the event read and the status read.
            for row in await asyncio.to_thread(db.events_after, sid, run_id, last):
                last = int(row["seq"])
                yield ServerSentEvent(
                    data=_event_json(run_id, row), event=row["kind"], id=str(last)
                )
            end = {"status": status, "error_code": run["error_code"], "result": run["result_json"]}
            yield ServerSentEvent(data=json.dumps(end, default=str), event="end")
            return
        await asyncio.sleep(poll_seconds)
