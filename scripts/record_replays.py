"""Record the replay fixtures: run the 6 public samples once for real and save their trace
events (with timing) and results to api/fixtures/replay/<sample_id>.json.

    uv run --env-file .env python scripts/record_replays.py

Needs the four MCP servers and a working model key. POs run before the invoices so the
invoices match against them, exactly as a visitor would see. A run that pauses for review
is approved as extracted (the samples are clean, so this should not happen).
"""

import json
import os
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from langgraph.types import Command
from psycopg.rows import dict_row

from docagent_agent.runtime import (
    AgentConfig,
    create_run,
    create_session,
    open_runtime,
    register_document,
    run_async,
)

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "evals" / "dataset" / "samples"
OUT = ROOT / "api" / "fixtures" / "replay"
ORDER = ("po_001", "po_002", "po_003", "inv_001", "inv_002", "inv_003")


def _jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, UUID):
        return str(v)
    return v


async def main() -> int:
    cfg = AgentConfig.from_env()
    owner = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True).cursor(
        row_factory=dict_row
    )
    session = create_session(cfg)
    print(f"recording session {session}")
    OUT.mkdir(parents=True, exist_ok=True)
    async with open_runtime(cfg) as rt:
        for sample_id in ORDER:
            doc = register_document(cfg, session, SAMPLES / f"{sample_id}.pdf")
            run = create_run(cfg, session, doc)
            config: dict[str, Any] = {"configurable": {"thread_id": str(run)}}
            inp: Any = {"run_id": str(run), "session_id": str(session),
                        "document_id": str(doc), "cost_usd": "0"}  # fmt: skip
            while True:
                await rt.graph.ainvoke(inp, config, durability="sync")  # pyright: ignore[reportArgumentType, reportUnknownMemberType]
                snap = await rt.graph.aget_state(config)  # pyright: ignore[reportArgumentType, reportUnknownMemberType]
                if not snap.interrupts:
                    break
                print(f"  {sample_id}: paused for review; approving as extracted")
                inp = Command(resume={"action": "approve"})
            row = owner.execute("SELECT status, result_json, total_cost_usd, escalated FROM runs "
                                "WHERE id = %s", (run,)).fetchone()  # fmt: skip
            events = owner.execute(
                "SELECT ts, node, kind, tool, model, tokens_in, tokens_out, latency_ms, cost_usd, "
                "output_json, error FROM trace_events WHERE run_id = %s ORDER BY seq",
                (run,),
            ).fetchall()
            if row is None or row["status"] != "completed" or not events:
                print(f"  {sample_id}: run {run} did not complete ({row}); aborting")
                return 1
            t0: datetime = events[0]["ts"]
            fixture: dict[str, Any] = {
                "sample_id": sample_id,
                "recorded_at": datetime.now(UTC).isoformat(),
                "source_run_id": str(run),
                "note": "Recorded from a real run; replayed when live processing is off.",
                "events": [
                    {"offset_ms": int((e["ts"] - t0).total_seconds() * 1000),
                     **{k: _jsonable(e[k]) for k in ("node", "kind", "tool", "model",
                        "tokens_in", "tokens_out", "latency_ms", "cost_usd", "error")},
                     "output": e["output_json"]}
                    for e in events
                ],
                "result": row["result_json"],
            }  # fmt: skip
            path = OUT / f"{sample_id}.json"
            path.write_text(json.dumps(fixture, indent=1, default=str) + "\n", encoding="utf-8")
            result: dict[str, Any] = row["result_json"] or {}
            summary = str(result.get("summary", ""))
            duration = int(fixture["events"][-1]["offset_ms"]) / 1000
            print(f"  {sample_id}: {len(events)} events, "
                  f"{duration:.1f}s, ${row['total_cost_usd']} "
                  f"-> {summary[:90]}")  # fmt: skip
    return 0


if __name__ == "__main__":
    stdout: Any = sys.stdout
    stdout.reconfigure(encoding="utf-8")
    sys.exit(run_async(main()))
